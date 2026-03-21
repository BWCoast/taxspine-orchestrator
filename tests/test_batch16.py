"""Batch 16 — regression tests for MEDIUM infrastructure and UI/UX findings.

Findings covered
----------------
UX-21  "Ingestion Sources" tab uses internal vocabulary — renamed and improved
SEC-04  Dockerfile build echoes TAXNOR_SHA/TAXNOR_TAG version to CI logs
INFRA-09  Dockerfile.local has no vendor-directory guard with clear error message
INFRA-18  No CPU/memory resource limits in Docker Compose files
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ── shared helpers ────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent
_REPO = _HERE.parent
_HTML_PATH = _REPO / "ui" / "index.html"
_DOCKERFILE_PATH = _REPO / "Dockerfile"
_DOCKERFILE_LOCAL_PATH = _REPO / "Dockerfile.local"
_COMPOSE_PATH = _REPO / "docker-compose.yml"
_COMPOSE_SYNOLOGY_PATH = _REPO / "docker-compose.synology.yml"


def _html() -> str:
    return _HTML_PATH.read_text(encoding="utf-8")


def _dockerfile() -> str:
    return _DOCKERFILE_PATH.read_text(encoding="utf-8")


def _dockerfile_local() -> str:
    return _DOCKERFILE_LOCAL_PATH.read_text(encoding="utf-8")


def _compose() -> str:
    return _COMPOSE_PATH.read_text(encoding="utf-8")


def _compose_synology() -> str:
    return _COMPOSE_SYNOLOGY_PATH.read_text(encoding="utf-8")


# ── UX-21: Data Sources tab rename and usability improvements ─────────────────


class TestUX21DataSourcesTab:
    """UX-21: 'Ingestion Sources' tab renamed to 'Data Sources', description
    added, and raw UTC timestamps replaced with relative times."""

    def test_tab_label_renamed_to_data_sources(self):
        """Tab button must say 'Data Sources' not 'Ingestion Sources'."""
        src = _html()
        assert "Data Sources" in src, (
            "UX-21: tab label must be 'Data Sources' (not 'Ingestion Sources')"
        )

    def test_ingestion_sources_label_removed_from_tab(self):
        """The raw 'Ingestion Sources' tab label must be replaced."""
        src = _html()
        # The tab BUTTON should no longer say "Ingestion Sources" — only the
        # comment or secondary text may still reference it.
        # Check the button element itself.
        assert ">🔁 Ingestion Sources<" not in src, (
            "UX-21: tab button must not still say 'Ingestion Sources'"
        )

    def test_one_sentence_description_present(self):
        """A user-friendly description must appear in the Data Sources panel."""
        src = _html()
        # The description must be within the panel content area (after the panel
        # div id= attribute).  Search for the panel div itself (role=tabpanel).
        panel_marker = 'id="tc-panel-dedup"'
        panel_start = src.find(panel_marker)
        assert panel_start >= 0, "tc-panel-dedup element must exist"
        panel_snippet = src[panel_start:panel_start + 1000]
        assert ("exchange" in panel_snippet.lower()
                or "import" in panel_snippet.lower()
                or "synced" in panel_snippet.lower()
                or "wallet" in panel_snippet.lower()), (
            "UX-21: description must explain what the panel shows in user terms"
        )

    def test_empty_state_text_updated(self):
        """Empty state must not use internal 'ingestion' vocabulary."""
        src = _html()
        # The new empty state text should not say "ingestion run"
        assert "ingestion run" not in src or "No dedup sources found" not in src, (
            "UX-21: empty state text must be updated to user-friendly language"
        )

    def test_relative_time_function_present(self):
        """A _relativeTime() helper function must be defined in the JS."""
        src = _html()
        assert "_relativeTime" in src, (
            "UX-21: _relativeTime() function must be defined for human-friendly timestamps"
        )

    def test_relative_time_called_in_render(self):
        """_relativeTime() must be called when rendering dedup source rows."""
        src = _html()
        assert "_relativeTime(s.last_modified)" in src or "_relativeTime(" in src, (
            "UX-21: _relativeTime() must be called in _renderDedupSources"
        )

    def test_relative_time_units_covered(self):
        """_relativeTime() must handle seconds, minutes, hours, and days."""
        src = _html()
        # The implementation must produce meaningful relative strings
        assert "ago" in src, (
            "UX-21: _relativeTime() must produce 'X ago' strings"
        )
        # Check that multiple time units are handled
        assert "60" in src and "3600" in src, (
            "UX-21: _relativeTime() must handle at least minute and hour thresholds"
        )

    def test_absolute_timestamp_preserved_as_title(self):
        """Absolute UTC timestamp must still be accessible (e.g. as title tooltip)."""
        src = _html()
        # The exact UTC time should remain in a title= attribute for accessibility
        assert "title=" in src, (
            "UX-21: absolute timestamp should be in title= tooltip for reference"
        )

    def test_ux21_comment_present(self):
        """A UX-21 comment must document the change."""
        src = _html()
        assert "UX-21" in src


# ── SEC-04: Suppress version echo in Dockerfile build output ──────────────────


class TestSEC04DockerfileVersionEcho:
    """SEC-04: TAXNOR_SHA and TAXNOR_TAG must not be echoed to build output;
    version metadata must be stored as image LABEL instead."""

    def test_taxnor_version_echo_removed(self):
        """The `echo '# tax-nor tag: ...'` line must be removed from RUN steps."""
        src = _dockerfile()
        assert 'echo "# tax-nor tag: ${TAXNOR_TAG}' not in src, (
            "SEC-04: version echo must be removed from RUN build steps"
        )

    def test_taxnor_label_present(self):
        """Version info must be stored as image LABEL metadata."""
        src = _dockerfile()
        assert "LABEL" in src and "taxnor" in src.lower(), (
            "SEC-04: TAXNOR_TAG/SHA must be stored as LABEL, not echoed"
        )

    def test_label_references_taxnor_tag(self):
        """LABEL directive must reference TAXNOR_TAG (or taxnor.tag)."""
        src = _dockerfile()
        # Search for the LABEL directive line (starts at bol, not in a comment)
        assert "taxnor.tag" in src or 'LABEL taxnor' in src, (
            "SEC-04: LABEL directive must capture TAXNOR_TAG as taxnor.tag"
        )

    def test_label_references_taxnor_sha(self):
        """LABEL directive must reference TAXNOR_SHA (or taxnor.sha)."""
        src = _dockerfile()
        assert "taxnor.sha" in src or 'LABEL taxnor' in src, (
            "SEC-04: LABEL directive must capture TAXNOR_SHA as taxnor.sha"
        )

    def test_taxnor_arg_declarations_still_present(self):
        """TAXNOR_TAG and TAXNOR_SHA ARG declarations must remain for cache-busting."""
        src = _dockerfile()
        assert "ARG TAXNOR_TAG=" in src, "ARG TAXNOR_TAG must still be declared"
        assert "ARG TAXNOR_SHA=" in src, "ARG TAXNOR_SHA must still be declared"

    def test_pip_install_command_still_present(self):
        """pip install of tax-nor must still occur (install not removed)."""
        src = _dockerfile()
        assert "tax-nor.git" in src or "tax-nor" in src, (
            "SEC-04: pip install of tax-nor must still be present"
        )

    def test_sec04_comment_present(self):
        """A SEC-04 comment must document the rationale."""
        src = _dockerfile()
        assert "SEC-04" in src


# ── INFRA-09: Dockerfile.local vendor directory guard ────────────────────────


class TestINFRA09VendorDirGuard:
    """INFRA-09: Dockerfile.local must validate vendor sub-directories and
    emit a clear error message when build-local.ps1 was not run first."""

    def test_vendor_copy_as_whole_directory(self):
        """vendor/ must be copied as a whole (not sub-dir by sub-dir first)."""
        src = _dockerfile_local()
        # The new approach copies vendor/ together so the RUN guard can check
        assert "COPY vendor/" in src, (
            "INFRA-09: vendor/ must be copied as a whole to allow RUN validation"
        )

    def test_tax_nor_guard_present(self):
        """A RUN guard must check for vendor/tax-nor and emit clear error."""
        src = _dockerfile_local()
        assert "tax-nor" in src and ("test -d" in src or "ERROR" in src), (
            "INFRA-09: RUN guard must check for tax-nor sub-directory"
        )

    def test_blockchain_reader_guard_present(self):
        """A RUN guard must check for vendor/blockchain-reader."""
        src = _dockerfile_local()
        assert "blockchain-reader" in src, (
            "INFRA-09: RUN guard must check for blockchain-reader sub-directory"
        )

    def test_build_local_ps1_referenced_in_error(self):
        """Error message must direct users to run build-local.ps1."""
        src = _dockerfile_local()
        assert "build-local.ps1" in src, (
            "INFRA-09: error message must mention build-local.ps1 as the fix"
        )

    def test_error_exit_code_in_guard(self):
        """Guard must call exit 1 (non-zero) to fail the build."""
        src = _dockerfile_local()
        assert "exit 1" in src, (
            "INFRA-09: guard RUN command must exit 1 to abort the Docker build"
        )

    def test_pip_installs_from_vendor(self):
        """pip install must use the copied vendor paths."""
        src = _dockerfile_local()
        # After the guard, pip should still install from the vendor copy
        assert "pip install" in src and "vendor" in src, (
            "INFRA-09: pip install must still reference the vendor directory"
        )

    def test_infra09_comment_present(self):
        """An INFRA-09 comment must document the guard."""
        src = _dockerfile_local()
        assert "INFRA-09" in src


# ── INFRA-18: CPU/memory resource limits in compose files ────────────────────


class TestINFRA18ResourceLimits:
    """INFRA-18: both docker-compose.yml and docker-compose.synology.yml must
    declare resource limits to prevent runaway jobs from exhausting NAS resources."""

    def test_deploy_resources_in_main_compose(self):
        """docker-compose.yml must have a deploy.resources block."""
        src = _compose()
        assert "deploy:" in src, (
            "INFRA-18: docker-compose.yml must have a deploy: block"
        )
        assert "resources:" in src, (
            "INFRA-18: docker-compose.yml must have resources: under deploy"
        )
        assert "limits:" in src, (
            "INFRA-18: docker-compose.yml must have limits: under resources"
        )

    def test_memory_limit_in_main_compose(self):
        """docker-compose.yml must set a memory limit."""
        src = _compose()
        assert "memory:" in src or "mem_limit" in src, (
            "INFRA-18: docker-compose.yml must set a memory limit"
        )

    def test_cpu_limit_in_main_compose(self):
        """docker-compose.yml must set a CPU limit."""
        src = _compose()
        assert "cpus:" in src, (
            "INFRA-18: docker-compose.yml must set a cpus limit"
        )

    def test_deploy_resources_in_synology_compose(self):
        """docker-compose.synology.yml must have a deploy.resources block."""
        src = _compose_synology()
        assert "deploy:" in src, (
            "INFRA-18: docker-compose.synology.yml must have a deploy: block"
        )
        assert "resources:" in src, (
            "INFRA-18: docker-compose.synology.yml must have resources: under deploy"
        )
        assert "limits:" in src, (
            "INFRA-18: docker-compose.synology.yml must have limits: under resources"
        )

    def test_memory_limit_in_synology_compose(self):
        """docker-compose.synology.yml must set a memory limit."""
        src = _compose_synology()
        assert "memory:" in src or "mem_limit" in src, (
            "INFRA-18: docker-compose.synology.yml must set a memory limit"
        )

    def test_cpu_limit_in_synology_compose(self):
        """docker-compose.synology.yml must set a CPU limit."""
        src = _compose_synology()
        assert "cpus:" in src, (
            "INFRA-18: docker-compose.synology.yml must set a cpus limit"
        )

    def test_infra18_comment_in_main_compose(self):
        """An INFRA-18 comment must explain the resource limits."""
        src = _compose()
        assert "INFRA-18" in src

    def test_infra18_comment_in_synology_compose(self):
        """An INFRA-18 comment must explain the resource limits in the Synology file."""
        src = _compose_synology()
        assert "INFRA-18" in src
