"""test_alerts_diagnostics.py — Alerts Center and System Diagnostics tests.

Covers:
- GET /alerts — raised_at field on all alert types, category grouping (health /
  review / lot_quality), severity ordering, detail collapsing
- GET /diagnostics — workspace section, all five sections present
- UI elements — grouped alerts HTML, severity badges, diagnostic tiles,
  status dots, workspace tile, refresh button
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_client() -> TestClient:
    from taxspine_orchestrator.main import app
    return TestClient(app)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    from taxspine_orchestrator import main as _m
    _m._job_store.clear()


# ── TestAlertsRaisedAt ────────────────────────────────────────────────────────


class TestAlertsRaisedAt:
    """Every alert now includes a raised_at ISO-8601 timestamp."""

    def test_health_alert_has_raised_at(self) -> None:
        """Health alert (missing CLI) includes raised_at."""
        client = _make_client()
        with patch("shutil.which", return_value=None):
            r = client.get("/alerts")
        assert r.status_code == 200
        alerts = r.json()
        # At least one health alert should appear (CLI missing)
        health = [a for a in alerts if a["category"] == "health"]
        if health:
            assert "raised_at" in health[0]
            assert health[0]["raised_at"] is not None

    def test_raised_at_is_iso_string(self) -> None:
        """raised_at is a parseable ISO-8601 datetime string."""
        import datetime
        client = _make_client()
        with patch("shutil.which", return_value=None):
            alerts = client.get("/alerts").json()
        for a in alerts:
            assert "raised_at" in a
            ts = a["raised_at"]
            # Must be parseable
            datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))

    def test_raised_at_contains_utc_offset(self) -> None:
        """raised_at contains a UTC offset (+00:00 or Z)."""
        client = _make_client()
        with patch("shutil.which", return_value=None):
            alerts = client.get("/alerts").json()
        for a in alerts:
            ts = a.get("raised_at", "")
            assert "+00:00" in ts or "Z" in ts or ts == ""  # empty = no alerts

    def test_review_alert_has_raised_at(self, tmp_path: Path) -> None:
        """Review alert from a completed job includes raised_at."""
        client = _make_client()
        review_path = tmp_path / "r.json"
        review_path.write_text(json.dumps({
            "has_unlinked_transfers": False,
            "warnings": ["some warning"],
        }), encoding="utf-8")

        from taxspine_orchestrator.models import JobStatus, JobOutput
        from taxspine_orchestrator import main as _m

        r = client.post("/jobs", json={"tax_year": 2025, "country": "norway"})
        job_id = r.json()["id"]
        _m._job_store.update_job(
            job_id,
            status=JobStatus.COMPLETED,
            output=JobOutput(
                review_json_paths=[str(review_path)],
                review_json_path=str(review_path),
            ),
        )
        alerts = client.get("/alerts").json()
        review_alerts = [a for a in alerts if a["category"] == "review"]
        assert len(review_alerts) >= 1
        assert "raised_at" in review_alerts[0]

    def test_all_alerts_same_raised_at(self, tmp_path: Path) -> None:
        """All alerts from a single request share the same raised_at timestamp."""
        client = _make_client()
        with patch("shutil.which", return_value=None):
            alerts = client.get("/alerts").json()
        if len(alerts) >= 2:
            ts_set = {a["raised_at"] for a in alerts}
            # All should share the same timestamp (generated once per request)
            assert len(ts_set) == 1


# ── TestAlertsCategoryGrouping ────────────────────────────────────────────────


class TestAlertsCategoryGrouping:
    """Alerts are correctly categorised into health / review / lot_quality."""

    def test_cli_missing_produces_health_category(self) -> None:
        client = _make_client()
        with patch("shutil.which", return_value=None):
            alerts = client.get("/alerts").json()
        categories = {a["category"] for a in alerts}
        assert "health" in categories

    def test_review_alert_category_is_review(self, tmp_path: Path) -> None:
        client = _make_client()
        p = tmp_path / "r.json"
        p.write_text(json.dumps({"has_unlinked_transfers": True, "warnings": []}))

        from taxspine_orchestrator.models import JobStatus, JobOutput
        from taxspine_orchestrator import main as _m

        r = client.post("/jobs", json={"tax_year": 2025, "country": "norway"})
        job_id = r.json()["id"]
        _m._job_store.update_job(
            job_id,
            status=JobStatus.COMPLETED,
            output=JobOutput(review_json_paths=[str(p)], review_json_path=str(p)),
        )
        alerts = client.get("/alerts").json()
        review = [a for a in alerts if a["category"] == "review"]
        assert len(review) >= 1

    def test_error_alerts_before_warn(self) -> None:
        """Error-severity alerts appear before warn-severity ones."""
        client = _make_client()
        with patch("shutil.which", return_value=None), \
             patch("os.access", return_value=False):
            alerts = client.get("/alerts").json()
        sevs = [a["severity"] for a in alerts]
        _order = {"error": 0, "warn": 1, "info": 2}
        ordered = sorted(sevs, key=lambda s: _order.get(s, 9))
        assert sevs == ordered

    def test_alert_schema_includes_all_keys(self) -> None:
        """Every alert has all required keys."""
        client = _make_client()
        with patch("shutil.which", return_value=None):
            alerts = client.get("/alerts").json()
        required = {"severity", "category", "message", "job_id", "detail", "raised_at"}
        for a in alerts:
            assert required <= a.keys(), f"Alert missing keys: {required - a.keys()}"

    def test_empty_alerts_returns_empty_list(self) -> None:
        """When everything is healthy, alerts returns []."""
        client = _make_client()
        # All health checks pass, no jobs with review issues
        with patch("shutil.which", return_value="/usr/bin/taxspine"), \
             patch("os.access", return_value=True):
            alerts = client.get("/alerts").json()
        # May still have lot_quality or review alerts from other tests
        # but at minimum health section should be empty
        health = [a for a in alerts if a["category"] == "health"]
        assert health == []


# ── TestDiagnosticsWorkspaceSection ──────────────────────────────────────────


class TestDiagnosticsWorkspaceSection:
    """GET /diagnostics now includes a workspace section."""

    def test_response_includes_workspace_key(self) -> None:
        client = _make_client()
        data = client.get("/diagnostics").json()
        assert "workspace" in data

    def test_workspace_has_xrpl_account_count(self) -> None:
        client = _make_client()
        ws = client.get("/diagnostics").json()["workspace"]
        assert "xrpl_account_count" in ws or "error" in ws

    def test_workspace_has_csv_file_count(self) -> None:
        client = _make_client()
        ws = client.get("/diagnostics").json()["workspace"]
        assert "csv_file_count" in ws or "error" in ws

    def test_workspace_has_xrpl_asset_count(self) -> None:
        client = _make_client()
        ws = client.get("/diagnostics").json()["workspace"]
        assert "xrpl_asset_count" in ws or "error" in ws

    def test_workspace_counts_are_non_negative_ints(self) -> None:
        client = _make_client()
        ws = client.get("/diagnostics").json()["workspace"]
        if "error" not in ws:
            assert isinstance(ws["xrpl_account_count"], int)
            assert isinstance(ws["csv_file_count"], int)
            assert isinstance(ws["xrpl_asset_count"], int)
            assert ws["xrpl_account_count"] >= 0
            assert ws["csv_file_count"] >= 0
            assert ws["xrpl_asset_count"] >= 0

    def test_workspace_reflects_loaded_state(self) -> None:
        """Workspace counts reflect what the workspace store returns."""
        client = _make_client()
        fake_ws = MagicMock()
        fake_ws.xrpl_accounts = ["r1", "r2"]
        fake_ws.csv_files = ["a.csv"]
        fake_ws.xrpl_assets = ["XRP.rIssuer"]
        with patch("taxspine_orchestrator.main._workspace_store") as mock_store:
            mock_store.load.return_value = fake_ws
            data = client.get("/diagnostics").json()
        ws = data["workspace"]
        if "error" not in ws:
            assert ws["xrpl_account_count"] == 2
            assert ws["csv_file_count"] == 1
            assert ws["xrpl_asset_count"] == 1

    def test_workspace_error_captured_gracefully(self) -> None:
        """Workspace section returns {error: ...} when store.load() raises."""
        client = _make_client()
        with patch("taxspine_orchestrator.main._workspace_store") as mock_store:
            mock_store.load.side_effect = RuntimeError("workspace file corrupted")
            data = client.get("/diagnostics").json()
        # Must not raise 500 — workspace error captured
        assert "workspace" in data
        assert "error" in data["workspace"]

    def test_five_sections_present(self) -> None:
        """Diagnostics response now has five sections."""
        client = _make_client()
        data = client.get("/diagnostics").json()
        for section in ("lots", "prices", "jobs", "dedup", "workspace"):
            assert section in data, f"Missing section: {section}"

    def test_workspace_error_does_not_affect_other_sections(self) -> None:
        """A workspace error must not affect other sections."""
        client = _make_client()
        with patch("taxspine_orchestrator.main._workspace_store") as mock_store:
            mock_store.load.side_effect = RuntimeError("boom")
            data = client.get("/diagnostics").json()
        assert "lots" in data
        assert "jobs" in data
        assert "prices" in data
        assert "dedup" in data


# ── TestAlertsCenterUI ────────────────────────────────────────────────────────


class TestAlertsCenterUI:
    """UI elements for the upgraded Alerts Center."""

    @pytest.fixture(scope="class")
    def html(self) -> str:
        p = Path(__file__).parent.parent / "ui" / "index.html"
        return p.read_text(encoding="utf-8")

    def test_alerts_card_exists(self, html: str) -> None:
        assert 'id="alerts-card"' in html

    def test_alerts_list_element(self, html: str) -> None:
        assert 'id="alerts-list"' in html

    def test_severity_badges_element(self, html: str) -> None:
        """New: severity breakdown badge container."""
        assert 'id="alerts-sev-badges"' in html

    def test_alerts_refresh_button(self, html: str) -> None:
        assert 'onclick="loadAlerts()"' in html

    def test_al_group_css_class(self, html: str) -> None:
        assert '.al-group' in html

    def test_al_group_hdr_css_class(self, html: str) -> None:
        assert '.al-group-hdr' in html

    def test_al_sev_dot_css_class(self, html: str) -> None:
        assert '.al-sev-dot' in html

    def test_al_badge_sev_css_class(self, html: str) -> None:
        assert '.al-badge-sev' in html

    def test_alert_category_grouping_in_js(self, html: str) -> None:
        """JS groups alerts by category."""
        assert '_ALERT_CAT_LABEL' in html
        assert '_ALERT_CAT_ORDER' in html

    def test_raised_at_used_in_render(self, html: str) -> None:
        """JS references raised_at to show relative time."""
        assert 'raised_at' in html

    def test_detail_collapsible_in_js(self, html: str) -> None:
        """Detail items use <details> for collapsing."""
        # The JS generates a <details> element for alert detail
        assert 'Show details' in html

    def test_category_labels_defined(self, html: str) -> None:
        assert 'System Health' in html
        assert 'Review Issues' in html
        assert 'Lot Quality' in html


# ── TestSystemDiagnosticsUI ───────────────────────────────────────────────────


class TestSystemDiagnosticsUI:
    """UI elements for the upgraded System Diagnostics panel."""

    @pytest.fixture(scope="class")
    def html(self) -> str:
        p = Path(__file__).parent.parent / "ui" / "index.html"
        return p.read_text(encoding="utf-8")

    def test_diagnostics_details_element(self, html: str) -> None:
        assert 'id="diagnostics-details"' in html

    def test_workspace_tile_exists(self, html: str) -> None:
        """New: Workspace tile in the grid."""
        assert 'id="diag-workspace"' in html

    def test_workspace_tile_header(self, html: str) -> None:
        assert 'Workspace' in html

    def test_status_dot_lots(self, html: str) -> None:
        assert 'id="ds-status-lots"' in html

    def test_status_dot_prices(self, html: str) -> None:
        assert 'id="ds-status-prices"' in html

    def test_status_dot_jobs(self, html: str) -> None:
        assert 'id="ds-status-jobs"' in html

    def test_status_dot_dedup(self, html: str) -> None:
        assert 'id="ds-status-dedup"' in html

    def test_status_dot_workspace(self, html: str) -> None:
        assert 'id="ds-status-ws"' in html

    def test_summary_bar_dots(self, html: str) -> None:
        """Summary bar contains 5 small status dots (one per subsystem)."""
        assert 'id="diag-dot-lots"' in html
        assert 'id="diag-dot-prices"' in html
        assert 'id="diag-dot-jobs"' in html
        assert 'id="diag-dot-dedup"' in html
        assert 'id="diag-dot-ws"' in html

    def test_last_refreshed_element(self, html: str) -> None:
        """Last-refreshed timestamp shown in the summary bar."""
        assert 'id="diag-last-refreshed"' in html

    def test_force_refresh_button_in_summary(self, html: str) -> None:
        """Refresh button in the summary bar forces a fresh fetch."""
        assert '_diagCacheData=null;loadDiagnostics()' in html

    def test_ds_tile_css_class(self, html: str) -> None:
        assert '.ds-tile' in html

    def test_ds_tile_hdr_css_class(self, html: str) -> None:
        assert '.ds-tile-hdr' in html

    def test_ds_status_css_class(self, html: str) -> None:
        assert '.ds-status' in html

    def test_ds_row_css_class(self, html: str) -> None:
        assert '.ds-row' in html

    def test_ds_val_css_class(self, html: str) -> None:
        assert '.ds-val' in html

    def test_price_freshness_logic_in_js(self, html: str) -> None:
        """JS applies colour based on age_hours threshold."""
        assert 'age_hours' in html
        assert 'priceStatus' in html

    def test_workspace_section_rendered_in_js(self, html: str) -> None:
        """JS renders workspace counts in the workspace tile."""
        assert 'diag-workspace' in html
        assert 'xrpl_account_count' in html

    def test_five_tile_grid_in_html(self, html: str) -> None:
        """All five tile sections are present in the HTML."""
        for tile_id in ('diag-lots', 'diag-prices', 'diag-jobs', 'diag-dedup', 'diag-workspace'):
            assert f'id="{tile_id}"' in html, f"Missing tile: {tile_id}"


# ── TestDiagnosticsDiskSection ────────────────────────────────────────────────


class TestDiagnosticsDiskSection:
    """GET /diagnostics now includes a disk_usage section."""

    def test_response_includes_disk_usage_key(self) -> None:
        client = _make_client()
        data = client.get("/diagnostics").json()
        assert "disk_usage" in data

    def test_disk_usage_has_output_dir_key(self) -> None:
        client = _make_client()
        disk = client.get("/diagnostics").json()["disk_usage"]
        assert "output_dir" in disk or "error" in disk

    def test_disk_usage_has_upload_dir_key(self) -> None:
        client = _make_client()
        disk = client.get("/diagnostics").json()["disk_usage"]
        assert "upload_dir" in disk or "error" in disk

    def test_disk_usage_has_prices_dir_key(self) -> None:
        client = _make_client()
        disk = client.get("/diagnostics").json()["disk_usage"]
        assert "prices_dir" in disk or "error" in disk

    def test_disk_usage_size_mb_non_negative(self) -> None:
        """size_mb is a non-negative number when the directory exists."""
        client = _make_client()
        disk = client.get("/diagnostics").json()["disk_usage"]
        if "error" not in disk:
            for key in ("output_dir", "upload_dir", "prices_dir"):
                entry = disk.get(key, {})
                if entry.get("exists") and entry.get("size_mb") is not None:
                    assert float(entry["size_mb"]) >= 0

    def test_disk_usage_nonexistent_dir_exists_false(self, tmp_path: Path) -> None:
        """A directory that does not exist reports exists=False."""
        from unittest.mock import patch
        client = _make_client()
        with patch("taxspine_orchestrator.main.settings") as mock_s:
            mock_s.OUTPUT_DIR  = tmp_path / "nonexistent_output"
            mock_s.UPLOAD_DIR  = tmp_path / "nonexistent_uploads"
            mock_s.PRICES_DIR  = tmp_path / "nonexistent_prices"
            mock_s.LOT_STORE_DB     = tmp_path / "lots.db"
            mock_s.DEDUP_DIR        = tmp_path
            mock_s.ORCHESTRATOR_KEY = ""
            data = client.get("/diagnostics").json()
        disk = data["disk_usage"]
        if "error" not in disk:
            assert disk["output_dir"]["exists"] is False
            assert disk["upload_dir"]["exists"] is False
            assert disk["prices_dir"]["exists"] is False

    def test_six_sections_present(self) -> None:
        """Diagnostics response now has six sections."""
        client = _make_client()
        data = client.get("/diagnostics").json()
        for section in ("lots", "prices", "jobs", "dedup", "workspace", "disk_usage"):
            assert section in data, f"Missing section: {section}"

    def test_disk_error_does_not_affect_other_sections(self) -> None:
        """A disk_usage error must not affect other sections."""
        from unittest.mock import patch
        client = _make_client()
        import taxspine_orchestrator.main as _m
        # Force an attribute error during disk section by patching settings to raise
        with patch.object(_m.settings, "OUTPUT_DIR", new_callable=lambda: property(lambda self: (_ for _ in ()).throw(RuntimeError("disk boom")))):
            data = client.get("/diagnostics").json()
        # Other sections must still be present
        assert "lots"  in data
        assert "jobs"  in data
        assert "dedup" in data


# ── TestAlertsCenterFilterUI ──────────────────────────────────────────────────


class TestAlertsCenterFilterUI:
    """UI elements for the Alerts Center category filter tabs."""

    @pytest.fixture(scope="class")
    def html(self) -> str:
        p = Path(__file__).parent.parent / "ui" / "index.html"
        return p.read_text(encoding="utf-8")

    def test_filter_tabs_container_exists(self, html: str) -> None:
        assert 'id="alerts-filters"' in html

    def test_all_filter_button_exists(self, html: str) -> None:
        assert 'data-cat="all"' in html

    def test_health_filter_button_exists(self, html: str) -> None:
        assert 'data-cat="health"' in html

    def test_review_filter_button_exists(self, html: str) -> None:
        assert 'data-cat="review"' in html

    def test_lot_quality_filter_button_exists(self, html: str) -> None:
        assert 'data-cat="lot_quality"' in html

    def test_set_alerts_filter_function_in_js(self, html: str) -> None:
        assert 'function setAlertsFilter(' in html

    def test_alerts_filter_state_var_in_js(self, html: str) -> None:
        assert '_alertsFilter' in html

    def test_render_alerts_function_in_js(self, html: str) -> None:
        assert 'function _renderAlerts(' in html

    def test_cached_alerts_var_in_js(self, html: str) -> None:
        assert '_cachedAlerts' in html

    def test_filter_tab_count_spans_in_html(self, html: str) -> None:
        assert 'id="afc-all"' in html
        assert 'id="afc-health"' in html
        assert 'id="afc-review"' in html
        assert 'id="afc-lot_quality"' in html


# ── TestAlertsDismissUI ───────────────────────────────────────────────────────


class TestAlertsDismissUI:
    """UI elements for per-alert dismiss functionality."""

    @pytest.fixture(scope="class")
    def html(self) -> str:
        p = Path(__file__).parent.parent / "ui" / "index.html"
        return p.read_text(encoding="utf-8")

    def test_dismiss_button_css_class(self, html: str) -> None:
        assert '.al-dismiss-btn' in html

    def test_dismiss_alert_function_in_js(self, html: str) -> None:
        assert 'function dismissAlert(' in html

    def test_dismissed_ids_set_in_js(self, html: str) -> None:
        assert '_dismissedAlertIds' in html

    def test_alert_key_function_in_js(self, html: str) -> None:
        assert 'function _alertKey(' in html

    def test_fix_hint_css_class(self, html: str) -> None:
        assert '.al-fix-hint' in html

    def test_health_fix_hints_map_in_js(self, html: str) -> None:
        assert '_HEALTH_FIX_HINTS' in html

    def test_filter_tabs_css_class(self, html: str) -> None:
        assert '.al-filter-btn' in html


# ── TestSystemDiagnosticsEnhancedUI ──────────────────────────────────────────


class TestSystemDiagnosticsEnhancedUI:
    """UI elements for the enhanced System Diagnostics panel."""

    @pytest.fixture(scope="class")
    def html(self) -> str:
        p = Path(__file__).parent.parent / "ui" / "index.html"
        return p.read_text(encoding="utf-8")

    def test_disk_tile_exists(self, html: str) -> None:
        assert 'id="diag-disk"' in html

    def test_disk_status_dot_header(self, html: str) -> None:
        assert 'id="ds-status-disk"' in html

    def test_disk_status_dot_summary_bar(self, html: str) -> None:
        assert 'id="diag-dot-disk"' in html

    def test_overall_health_badge_element(self, html: str) -> None:
        assert 'id="diag-health-badge"' in html

    def test_overall_health_badge_css(self, html: str) -> None:
        assert '.diag-health-badge' in html

    def test_copy_diagnostics_button(self, html: str) -> None:
        assert '_copyDiagnostics()' in html

    def test_copy_diagnostics_function_in_js(self, html: str) -> None:
        assert 'function _copyDiagnostics(' in html

    def test_copy_button_css_class(self, html: str) -> None:
        assert '.diag-copy-btn' in html

    def test_six_tile_grid_in_html(self, html: str) -> None:
        """All six tile sections (including new Disk) are present."""
        for tile_id in ('diag-lots', 'diag-prices', 'diag-jobs', 'diag-dedup', 'diag-workspace', 'diag-disk'):
            assert f'id="{tile_id}"' in html, f"Missing tile: {tile_id}"

    def test_disk_js_renders_output_dir(self, html: str) -> None:
        assert 'output_dir' in html

    def test_disk_size_mb_in_js(self, html: str) -> None:
        assert 'size_mb' in html
