"""F-18 — Real CLI integration tests.

These tests call the actual ``taxspine-nor-report`` binary (no mocking).
They are skipped automatically if the CLI is not installed so the test suite
remains green in pure-orchestrator environments without the tax-spine package.

Run only these tests:
    pytest tests/test_cli_integration.py -v

Run as part of the full suite:
    pytest --tb=short -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

# ── Availability guard ────────────────────────────────────────────────────────
#
# Skip all tests in this module if the CLI is not on PATH.
# This keeps CI green in environments that only have the orchestrator installed
# (e.g. Docker builds before tax-nor is injected).
#
_CLI = "taxspine-nor-report"
_CLI_AVAILABLE = shutil.which(_CLI) is not None

pytestmark = pytest.mark.skipif(
    not _CLI_AVAILABLE,
    reason=f"{_CLI} is not installed — skipping real-CLI integration tests",
)

# Force UTF-8 output encoding so Unicode header characters don't fail on
# Windows consoles that default to cp1252.
_CLI_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


# ── Minimal generic-events CSV fixtures ──────────────────────────────────────

_HEADER = (
    "event_id,timestamp,event_type,source,account,"
    "asset_in,amount_in,asset_out,amount_out,"
    "fee_asset,fee_amount,tx_hash,exchange_tx_id,label,"
    "complex_tax_treatment,note"
)

# Simplest valid scenario: one buy event only (no disposals → no gains)
_SINGLE_BUY_CSV = textwrap.dedent(f"""\
    {_HEADER}
    buy_001,2025-01-01T10:00:00Z,TRADE,binance,user,BTC,1.0,NOK,600000,,,,,,,
""")

# Buy + sell in the same year (realised gain)
_BUY_SELL_CSV = textwrap.dedent(f"""\
    {_HEADER}
    buy_001,2025-01-01T10:00:00Z,TRADE,binance,user,BTC,1.0,NOK,600000,,,,,,,
    sell_001,2025-09-01T14:00:00Z,TRADE,binance,user,NOK,700000,BTC,1.0,NOK,350,,,,,
""")

# Staking reward (income event)
_STAKING_CSV = textwrap.dedent(f"""\
    {_HEADER}
    buy_001,2025-01-01T10:00:00Z,TRADE,binance,user,BTC,1.0,NOK,600000,,,,,,,
    reward_001,2025-06-01T00:00:00Z,INCOME,binance,user,BTC,0.01,,,,,,,,staking,
""")


# ── Helper ────────────────────────────────────────────────────────────────────


def _run_cli(csv_content: str, tmp_path: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Write *csv_content* to a temp file and invoke taxspine-nor-report."""
    csv_file = tmp_path / "events.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    html_out = tmp_path / "report.html"
    cmd = [
        _CLI,
        "--year", "2025",
        "--generic-events-csv", str(csv_file),
        "--html-output", str(html_out),
    ]
    if extra_args:
        cmd.extend(extra_args)

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_CLI_ENV,
        timeout=60,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestCliExitCode:
    """Verify that the CLI exits with code 0 on valid inputs."""

    def test_single_buy_exits_zero(self, tmp_path: Path) -> None:
        result = _run_cli(_SINGLE_BUY_CSV, tmp_path)
        assert result.returncode == 0, (
            f"CLI exited {result.returncode}\nstderr: {result.stderr}"
        )

    def test_buy_sell_exits_zero(self, tmp_path: Path) -> None:
        result = _run_cli(_BUY_SELL_CSV, tmp_path)
        assert result.returncode == 0, (
            f"CLI exited {result.returncode}\nstderr: {result.stderr}"
        )

    def test_staking_reward_exits_zero(self, tmp_path: Path) -> None:
        result = _run_cli(_STAKING_CSV, tmp_path)
        assert result.returncode == 0, (
            f"CLI exited {result.returncode}\nstderr: {result.stderr}"
        )

    def test_nonexistent_csv_exits_nonzero(self, tmp_path: Path) -> None:
        """Passing a path that does not exist must cause a non-zero exit."""
        result = subprocess.run(
            [_CLI, "--year", "2025", "--generic-events-csv", str(tmp_path / "missing.csv")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_CLI_ENV,
            timeout=30,
        )
        assert result.returncode != 0, "Expected non-zero exit for missing CSV"


class TestCliOutputFiles:
    """Verify that the CLI writes expected output artefacts."""

    def test_html_report_is_written(self, tmp_path: Path) -> None:
        result = _run_cli(_BUY_SELL_CSV, tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        html_out = tmp_path / "report.html"
        assert html_out.exists(), "HTML report was not written"
        assert html_out.stat().st_size > 0, "HTML report is empty"

    def test_html_report_contains_rf1159(self, tmp_path: Path) -> None:
        """The generated HTML report must reference RF-1159 (Norwegian form)."""
        result = _run_cli(_BUY_SELL_CSV, tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        html_out = tmp_path / "report.html"
        content = html_out.read_text(encoding="utf-8", errors="replace")
        assert "RF-1159" in content or "rf-1159" in content.lower(), (
            "Expected 'RF-1159' in report HTML"
        )

    def test_rf1159_json_is_written(self, tmp_path: Path) -> None:
        """The --rf1159-json flag produces a JSON export artefact."""
        json_out = tmp_path / "rf1159.json"
        result = _run_cli(
            _BUY_SELL_CSV,
            tmp_path,
            extra_args=["--rf1159-json", str(json_out)],
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert json_out.exists(), "RF-1159 JSON was not written"
        assert json_out.stat().st_size > 0, "RF-1159 JSON is empty"


class TestCliStdout:
    """Verify that the CLI produces expected stdout / file output."""

    def test_html_file_is_produced(self, tmp_path: Path) -> None:
        """A valid job must produce an HTML file (primary artefact)."""
        result = _run_cli(_BUY_SELL_CSV, tmp_path)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        html_out = tmp_path / "report.html"
        assert html_out.exists(), "CLI produced no HTML artefact"
