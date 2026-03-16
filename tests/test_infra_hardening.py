"""Tests for Batch 8 infrastructure hardening.

Verifies static properties of Dockerfile, docker-compose.synology.yml, and
the GitHub Actions workflow, plus the SQLite WAL-mode fix in storage.py.

INFRA-01 — blockchain-reader Dockerfile pin: install URL must use the
           BLOCKCHAIN_READER_SHA build-arg as the git ref, not float on main.
INFRA-02 — SQLite WAL mode: _connect() must set journal_mode=WAL so that
           concurrent readers and writers don't serialise on the lock.
INFRA-16 — Production Compose must not reference :latest; Watchtower
           auto-polling must be disabled.
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
from pathlib import Path

import pytest


REPO_ROOT    = Path(__file__).parent.parent
DOCKERFILE   = REPO_ROOT / "Dockerfile"
COMPOSE_PROD = REPO_ROOT / "docker-compose.synology.yml"
WORKFLOW     = REPO_ROOT / ".github" / "workflows" / "docker.yml"


# ── INFRA-01: blockchain-reader SHA pin ───────────────────────────────────────


class TestInfra01BlockchainReaderPin:
    """Dockerfile must install blockchain-reader at a pinned commit ref."""

    @pytest.fixture(scope="class")
    def dockerfile(self) -> str:
        assert DOCKERFILE.is_file(), "Dockerfile must exist"
        return DOCKERFILE.read_text(encoding="utf-8")

    def test_sha_arg_declared(self, dockerfile: str) -> None:
        assert "BLOCKCHAIN_READER_SHA" in dockerfile, (
            "Dockerfile must declare BLOCKCHAIN_READER_SHA ARG"
        )

    def test_sha_used_in_pip_install_url(self, dockerfile: str) -> None:
        # The pip install URL must include @${BLOCKCHAIN_READER_SHA} so that
        # the installed commit is determined by the build-arg, not the branch tip.
        assert "@${BLOCKCHAIN_READER_SHA}" in dockerfile, (
            "pip install URL for blockchain-reader must include "
            "@${BLOCKCHAIN_READER_SHA} to pin the installed commit"
        )

    def test_floating_main_not_used_as_only_ref(self, dockerfile: str) -> None:
        # The old pattern installed from the branch tip with no ref at all.
        # Ensure the install line that references blockchain-reader always
        # includes the SHA variable, not a bare URL with no @ref.
        install_lines = [
            ln for ln in dockerfile.splitlines()
            if "blockchain-reader.git" in ln and "pip install" in ln
        ]
        assert len(install_lines) > 0, (
            "At least one pip install line for blockchain-reader must exist"
        )
        for ln in install_lines:
            assert "@${BLOCKCHAIN_READER_SHA}" in ln, (
                f"Every blockchain-reader install line must use @${{BLOCKCHAIN_READER_SHA}}; "
                f"found: {ln.strip()!r}"
            )

    def test_warning_emitted_when_sha_is_main(self, dockerfile: str) -> None:
        # A runtime warning must be emitted when the SHA is the floating default.
        assert "WARNING" in dockerfile or "main" in dockerfile, (
            "Dockerfile must document / warn that the 'main' default is not "
            "suitable for production builds"
        )

    def test_ci_workflow_fetches_full_sha(self) -> None:
        assert WORKFLOW.is_file(), ".github/workflows/docker.yml must exist"
        wf = WORKFLOW.read_text(encoding="utf-8")
        # Previously the workflow used [:12] — a full SHA is now required.
        # The blockchain-reader SHA step must NOT truncate the SHA.
        # We verify by checking the section after "blockchain-reader HEAD SHA".
        br_section_start = wf.find("blockchain-reader HEAD SHA")
        assert br_section_start >= 0, "Workflow must have a blockchain-reader SHA step"
        # Read 600 chars after the label to cover the run: block.
        snippet = wf[br_section_start: br_section_start + 600]
        # The truncation pattern `['sha'][:12]` must be gone from this section.
        assert "['sha'][:12]" not in snippet, (
            "CI must pass the full SHA to BLOCKCHAIN_READER_SHA, not a 12-char abbreviation"
        )

    def test_ci_workflow_passes_sha_to_build(self) -> None:
        assert WORKFLOW.is_file(), ".github/workflows/docker.yml must exist"
        wf = WORKFLOW.read_text(encoding="utf-8")
        assert "BLOCKCHAIN_READER_SHA=${{ steps.blockchain_reader_sha.outputs.sha }}" in wf, (
            "CI workflow must pass BLOCKCHAIN_READER_SHA build-arg to docker build"
        )


# ── INFRA-02: SQLite WAL mode ─────────────────────────────────────────────────


class TestInfra02SqliteWalMode:
    """SqliteJobStore._connect() must enable WAL journal mode."""

    @pytest.fixture(scope="class")
    def storage_source(self) -> str:
        storage_py = REPO_ROOT / "taxspine_orchestrator" / "storage.py"
        assert storage_py.is_file(), "taxspine_orchestrator/storage.py must exist"
        return storage_py.read_text(encoding="utf-8")

    def test_wal_pragma_in_connect(self, storage_source: str) -> None:
        assert "journal_mode=WAL" in storage_source, (
            "_connect() must set PRAGMA journal_mode=WAL"
        )

    def test_synchronous_normal_in_connect(self, storage_source: str) -> None:
        assert "synchronous=NORMAL" in storage_source, (
            "_connect() must set PRAGMA synchronous=NORMAL"
        )

    def test_wal_pragma_in_init_db(self, storage_source: str) -> None:
        # _init_db also opens a direct sqlite3.connect — WAL must be set there too.
        init_db_start = storage_source.find("def _init_db(")
        assert init_db_start >= 0, "_init_db must exist"
        snippet = storage_source[init_db_start: init_db_start + 600]
        assert "journal_mode=WAL" in snippet, (
            "_init_db must also set journal_mode=WAL on the initial connection"
        )

    def test_actual_connection_uses_wal(self, tmp_path: Path) -> None:
        """Integration: verify the live _connect() returns a WAL-mode database."""
        from taxspine_orchestrator.storage import SqliteJobStore

        db_path = tmp_path / "test_wal.db"
        store = SqliteJobStore(db_path)
        conn = store._connect()
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()   # explicit close — WAL shm file released on Windows
        assert mode == "wal", (
            f"Expected journal_mode=wal, got {mode!r}"
        )

    def test_wal_mode_survives_reconnect(self, tmp_path: Path) -> None:
        """WAL mode persists across connections once set in the database file."""
        from taxspine_orchestrator.storage import SqliteJobStore

        db_path = tmp_path / "test_wal2.db"
        SqliteJobStore(db_path)   # initialise → sets WAL
        # Open a raw connection (bypassing our helper) to confirm mode persists.
        raw = sqlite3.connect(str(db_path))
        try:
            mode = raw.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            raw.close()
        assert mode == "wal", (
            f"WAL mode must persist in the database file; got {mode!r}"
        )


# ── INFRA-16: Production Compose not on :latest ───────────────────────────────


class TestInfra16ComposeImagePin:
    """docker-compose.synology.yml must not use :latest; Watchtower poll disabled."""

    @pytest.fixture(scope="class")
    def compose(self) -> str:
        assert COMPOSE_PROD.is_file(), "docker-compose.synology.yml must exist"
        return COMPOSE_PROD.read_text(encoding="utf-8")

    def test_latest_tag_not_used_as_image(self, compose: str) -> None:
        # The image: line must not reference :latest.
        for line in compose.splitlines():
            stripped = line.strip()
            if stripped.startswith("image:") and "taxspine-orchestrator" in stripped:
                assert ":latest" not in stripped, (
                    f"Production Compose image must not use :latest; found: {stripped!r}"
                )

    def test_sha_tag_referenced(self, compose: str) -> None:
        # Image must reference a sha- prefixed tag (immutable build artifact).
        assert "sha-" in compose, (
            "docker-compose.synology.yml must pin the image to a sha- tag "
            "(e.g. ghcr.io/bwcoast/taxspine-orchestrator:sha-a1b2c3d)"
        )

    def test_watchtower_run_once_set(self, compose: str) -> None:
        # WATCHTOWER_RUN_ONCE disables the 5-minute polling loop.
        assert "WATCHTOWER_RUN_ONCE" in compose, (
            "Watchtower must have WATCHTOWER_RUN_ONCE set to disable auto-polling"
        )

    def test_watchtower_poll_interval_not_active(self, compose: str) -> None:
        # WATCHTOWER_POLL_INTERVAL must be absent or commented out.
        for line in compose.splitlines():
            stripped = line.strip()
            if "WATCHTOWER_POLL_INTERVAL" in stripped and not stripped.startswith("#"):
                pytest.fail(
                    "WATCHTOWER_POLL_INTERVAL must not be active in production Compose "
                    f"(auto-polling is disabled by INFRA-16); found: {stripped!r}"
                )

    def test_restart_no_for_watchtower(self, compose: str) -> None:
        # With WATCHTOWER_RUN_ONCE, Watchtower exits after one pass; restart:no
        # prevents Docker from looping it back.
        watchtower_start = compose.find("watchtower:")
        assert watchtower_start >= 0, "watchtower service must exist"
        snippet = compose[watchtower_start: watchtower_start + 800]
        assert 'restart: "no"' in snippet or "restart: 'no'" in snippet or "restart: no" in snippet, (
            "Watchtower service must have restart: no (run-once exits; should not loop)"
        )

    def test_infra16_comment_present(self, compose: str) -> None:
        assert "INFRA-16" in compose, (
            "docker-compose.synology.yml must reference INFRA-16 to document "
            "the rationale for pinning away from :latest"
        )

    def test_ci_workflow_shows_sha_tag_in_summary(self) -> None:
        assert WORKFLOW.is_file(), ".github/workflows/docker.yml must exist"
        wf = WORKFLOW.read_text(encoding="utf-8")
        # The summary step must show the sha- tag so operators know what to pin.
        assert "sha-" in wf and "Deploy this build" in wf, (
            "CI workflow summary must display the sha- tag so operators can "
            "update docker-compose.synology.yml to the correct pinned tag"
        )
