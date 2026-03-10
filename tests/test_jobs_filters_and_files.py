"""Tests for job filtering (GET /jobs?…) and file-listing / download endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from taxspine_orchestrator.main import app
from taxspine_orchestrator.models import JobOutput, JobStatus


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_store() -> None:
    """Clear the in-memory store between tests so they don't leak state."""
    from taxspine_orchestrator import main as _m

    _m._job_store._jobs.clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _create_job(
    client: TestClient,
    country: str = "norway",
    tax_year: int = 2025,
    case_name: str | None = None,
) -> dict:
    """Helper — create a job and return the response body."""
    payload: dict = {
        "xrpl_accounts": ["rEXAMPLE1"],
        "tax_year": tax_year,
        "country": country,
    }
    if case_name is not None:
        payload["case_name"] = case_name
    resp = client.post("/jobs", json=payload)
    assert resp.status_code == 200
    return resp.json()


def _force_status(job_id: str, status: JobStatus, output: JobOutput | None = None) -> None:
    """Directly mutate the in-memory store to set a job's status."""
    from taxspine_orchestrator import main as _m

    fields: dict = {"status": status}
    if output is not None:
        fields["output"] = output
    _m._job_store.update_job(job_id, **fields)


# ── GET /jobs  filtering ────────────────────────────────────────────────────


class TestListJobsFiltering:
    """Tests for ``GET /jobs?status=…&country=…``."""

    def _seed_three_jobs(self, client: TestClient) -> list[str]:
        """Create three jobs with distinct status/country combinations.

        Returns [completed-norway-id, failed-uk-id, pending-norway-id].
        """
        j1 = _create_job(client, country="norway")
        j2 = _create_job(client, country="uk")
        j3 = _create_job(client, country="norway")

        _force_status(j1["id"], JobStatus.COMPLETED)
        _force_status(j2["id"], JobStatus.FAILED)
        # j3 stays PENDING
        return [j1["id"], j2["id"], j3["id"]]

    def test_no_filter_returns_all(self, client: TestClient) -> None:
        self._seed_three_jobs(client)

        resp = client.get("/jobs")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_filter_by_status_completed(self, client: TestClient) -> None:
        ids = self._seed_three_jobs(client)

        resp = client.get("/jobs", params={"status": "completed"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["id"] == ids[0]

    def test_filter_by_status_pending(self, client: TestClient) -> None:
        ids = self._seed_three_jobs(client)

        resp = client.get("/jobs", params={"status": "pending"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["id"] == ids[2]

    def test_filter_by_country_norway(self, client: TestClient) -> None:
        ids = self._seed_three_jobs(client)

        resp = client.get("/jobs", params={"country": "norway"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        returned_ids = {j["id"] for j in body}
        assert returned_ids == {ids[0], ids[2]}

    def test_filter_by_country_uk(self, client: TestClient) -> None:
        ids = self._seed_three_jobs(client)

        resp = client.get("/jobs", params={"country": "uk"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["id"] == ids[1]

    def test_filter_by_status_and_country(self, client: TestClient) -> None:
        ids = self._seed_three_jobs(client)

        resp = client.get(
            "/jobs", params={"status": "pending", "country": "norway"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["id"] == ids[2]

    def test_filter_no_match_returns_empty(self, client: TestClient) -> None:
        self._seed_three_jobs(client)

        resp = client.get(
            "/jobs", params={"status": "running"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_invalid_status_returns_422(self, client: TestClient) -> None:
        resp = client.get("/jobs", params={"status": "banana"})
        assert resp.status_code == 422

    def test_invalid_country_returns_422(self, client: TestClient) -> None:
        resp = client.get("/jobs", params={"country": "narnia"})
        assert resp.status_code == 422


# ── GET /jobs/{id}/files ────────────────────────────────────────────────────


class TestListJobFiles:
    """Tests for ``GET /jobs/{id}/files``."""

    def test_pending_job_has_no_files(self, client: TestClient) -> None:
        job = _create_job(client)

        resp = client.get(f"/jobs/{job['id']}/files")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_completed_job_lists_all_kinds(self, client: TestClient) -> None:
        job = _create_job(client)
        output = JobOutput(
            gains_csv_path="/out/gains.csv",
            wealth_csv_path="/out/wealth.csv",
            summary_json_path="/out/summary.json",
            log_path="/out/execution.log",
        )
        _force_status(job["id"], JobStatus.COMPLETED, output=output)

        resp = client.get(f"/jobs/{job['id']}/files")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "gains": "/out/gains.csv",
            "wealth": "/out/wealth.csv",
            "summary": "/out/summary.json",
            "log": "/out/execution.log",
        }

    def test_failed_job_lists_only_populated(self, client: TestClient) -> None:
        job = _create_job(client)
        output = JobOutput(
            log_path="/out/execution.log",
            error_message="boom",
        )
        _force_status(job["id"], JobStatus.FAILED, output=output)

        resp = client.get(f"/jobs/{job['id']}/files")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"log": "/out/execution.log"}

    def test_nonexistent_job_returns_404(self, client: TestClient) -> None:
        resp = client.get("/jobs/does-not-exist/files")
        assert resp.status_code == 404


# ── GET /jobs/{id}/files/{kind}  — real file downloads ─────────────────────


class TestDownloadFile:
    """Tests for ``GET /jobs/{id}/files/{kind}`` (streaming file response)."""

    def test_download_gains_csv(self, client: TestClient, tmp_path: Path) -> None:
        job = _create_job(client)
        dummy = tmp_path / "gains.csv"
        dummy.write_text("asset,amount\nXRP,100\n")

        output = JobOutput(gains_csv_path=str(dummy))
        _force_status(job["id"], JobStatus.COMPLETED, output=output)

        resp = client.get(f"/jobs/{job['id']}/files/gains")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/csv; charset=utf-8"
        assert resp.text == "asset,amount\nXRP,100\n"
        assert f"gains-{job['id']}.csv" in resp.headers["content-disposition"]

    def test_download_wealth_csv(self, client: TestClient, tmp_path: Path) -> None:
        job = _create_job(client)
        dummy = tmp_path / "wealth.csv"
        dummy.write_text("date,value\n2025-01-01,5000\n")

        output = JobOutput(wealth_csv_path=str(dummy))
        _force_status(job["id"], JobStatus.COMPLETED, output=output)

        resp = client.get(f"/jobs/{job['id']}/files/wealth")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/csv; charset=utf-8"
        assert resp.text == "date,value\n2025-01-01,5000\n"
        assert f"wealth-{job['id']}.csv" in resp.headers["content-disposition"]

    def test_download_summary_json(self, client: TestClient, tmp_path: Path) -> None:
        job = _create_job(client)
        dummy = tmp_path / "summary.json"
        dummy.write_text('{"total_gains": 42}')

        output = JobOutput(summary_json_path=str(dummy))
        _force_status(job["id"], JobStatus.COMPLETED, output=output)

        resp = client.get(f"/jobs/{job['id']}/files/summary")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json"
        assert resp.text == '{"total_gains": 42}'
        assert f"summary-{job['id']}.json" in resp.headers["content-disposition"]

    def test_download_log_txt(self, client: TestClient, tmp_path: Path) -> None:
        job = _create_job(client)
        dummy = tmp_path / "execution.log"
        dummy.write_text("$ blockchain-reader ...\n  rc=0\n")

        output = JobOutput(log_path=str(dummy))
        _force_status(job["id"], JobStatus.COMPLETED, output=output)

        resp = client.get(f"/jobs/{job['id']}/files/log")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"
        assert resp.text == "$ blockchain-reader ...\n  rc=0\n"
        assert f"log-{job['id']}.txt" in resp.headers["content-disposition"]

    def test_file_missing_on_disk_returns_404(self, client: TestClient) -> None:
        job = _create_job(client)
        output = JobOutput(gains_csv_path="/nonexistent/gains.csv")
        _force_status(job["id"], JobStatus.COMPLETED, output=output)

        resp = client.get(f"/jobs/{job['id']}/files/gains")
        assert resp.status_code == 404

    def test_no_path_recorded_returns_404(self, client: TestClient) -> None:
        job = _create_job(client)
        output = JobOutput(log_path="/out/execution.log")
        _force_status(job["id"], JobStatus.COMPLETED, output=output)

        resp = client.get(f"/jobs/{job['id']}/files/gains")
        assert resp.status_code == 404
        assert "gains" in resp.json()["detail"]

    def test_nonexistent_job_returns_404(self, client: TestClient) -> None:
        resp = client.get("/jobs/does-not-exist/files/gains")
        assert resp.status_code == 404

    def test_invalid_kind_returns_422(self, client: TestClient) -> None:
        job = _create_job(client)
        resp = client.get(f"/jobs/{job['id']}/files/banana")
        assert resp.status_code == 422

    def test_all_four_kinds_download(self, client: TestClient, tmp_path: Path) -> None:
        """Verify every valid kind streams the correct file content."""
        job = _create_job(client)
        gains = tmp_path / "gains.csv"
        wealth = tmp_path / "wealth.csv"
        summary = tmp_path / "summary.json"
        log = tmp_path / "execution.log"
        for f in (gains, wealth, summary, log):
            f.write_text(f"content-of-{f.stem}")

        output = JobOutput(
            gains_csv_path=str(gains),
            wealth_csv_path=str(wealth),
            summary_json_path=str(summary),
            log_path=str(log),
        )
        _force_status(job["id"], JobStatus.COMPLETED, output=output)

        for kind, stem in [
            ("gains", "gains"),
            ("wealth", "wealth"),
            ("summary", "summary"),
            ("log", "execution"),
        ]:
            resp = client.get(f"/jobs/{job['id']}/files/{kind}")
            assert resp.status_code == 200, f"Failed for kind={kind}"
            assert resp.text == f"content-of-{stem}"


# ── case_name round-trip & query filter ─────────────────────────────────────


class TestCaseName:
    """Tests for the optional ``case_name`` field on JobInput."""

    def test_round_trip_case_name(self, client: TestClient) -> None:
        resp = client.post(
            "/jobs",
            json={
                "xrpl_accounts": ["rEXAMPLE1"],
                "tax_year": 2025,
                "country": "norway",
                "csv_files": [],
                "case_name": "2025 Norway \u2013 main wallets",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["input"]["case_name"] == "2025 Norway \u2013 main wallets"

        # Also confirm GET /jobs/{id} returns it
        get_resp = client.get(f"/jobs/{body['id']}")
        assert get_resp.json()["input"]["case_name"] == "2025 Norway \u2013 main wallets"

    def test_case_name_defaults_to_none(self, client: TestClient) -> None:
        resp = client.post(
            "/jobs",
            json={"tax_year": 2025, "country": "uk"},
        )
        assert resp.status_code == 200
        assert resp.json()["input"]["case_name"] is None

    def test_filter_by_query_substring(self, client: TestClient) -> None:
        _create_job(client, country="norway", case_name="2025 Norway \u2013 main wallets")
        _create_job(client, country="uk", case_name="2025 UK \u2013 cold storage")
        _create_job(client, country="norway", case_name="2024 Norway \u2013 test run")

        resp = client.get("/jobs", params={"query": "norway"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        names = {j["input"]["case_name"] for j in body}
        assert names == {"2025 Norway \u2013 main wallets", "2024 Norway \u2013 test run"}

    def test_query_is_case_insensitive(self, client: TestClient) -> None:
        _create_job(client, case_name="Big XRPL Wallets")
        _create_job(client, case_name="Small csv import")

        resp = client.get("/jobs", params={"query": "xrpl"})
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["input"]["case_name"] == "Big XRPL Wallets"

    def test_query_excludes_jobs_without_case_name(self, client: TestClient) -> None:
        _create_job(client, case_name="Norway test")
        _create_job(client)  # no case_name

        resp = client.get("/jobs", params={"query": "norway"})
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_query_combined_with_status_and_country(self, client: TestClient) -> None:
        j1 = _create_job(client, country="norway", case_name="2025 Norway wallets")
        j2 = _create_job(client, country="norway", case_name="2025 Norway cold")
        _force_status(j1["id"], JobStatus.COMPLETED)
        # j2 stays PENDING

        resp = client.get(
            "/jobs",
            params={"status": "completed", "country": "norway", "query": "wallets"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["id"] == j1["id"]
