"""
tests/functional/test_tiering.py
==================================
Tests for tier transitions and admin operations:
  POST /admin/tiering/run
  POST /admin/files/{file_id}/update-last-accessed   (mock timestamp)
  GET  /admin/stats

Tier rules from storage_service.py:
  HOT  → WARM  when days_since_access >= 30
  WARM → COLD  when days_since_access >= 90
  COLD         never auto-promoted (BUG-003: by design or bug — tested)

Known bugs surfaced by these tests:
  BUG-003: COLD files are never promoted on access
  BUG-005: Exactly 30 / 90 day boundary behaviour (off-by-one risk)
"""

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from conftest import make_file, set_last_accessed


def upload(client, size_mb: int = 2, filename: str = "test.bin") -> str:
    """Upload a file and return its file_id."""
    resp = client.post("/files", files=make_file(size_mb * 1024 * 1024, filename))
    assert resp.status_code == 201
    return resp.json()["file_id"]


def tier_of(client, file_id: str) -> str:
    return client.get(f"/files/{file_id}/metadata").json()["tier"]


def run_tiering(client):
    resp = client.post("/admin/tiering/run")
    assert resp.status_code == 200
    return resp.json()


# ══════════════════════════════════════════════════════════════
# POST /admin/tiering/run — basic
# ══════════════════════════════════════════════════════════════

class TestTieringRun:

    def test_tiering_run_returns_200(self, client):
        resp = client.post("/admin/tiering/run")
        assert resp.status_code == 200

    def test_tiering_run_returns_status_and_files_moved(self, client):
        body = client.post("/admin/tiering/run").json()
        assert "status" in body
        assert "files_moved" in body

    def test_tiering_run_status_is_success(self, client):
        assert client.post("/admin/tiering/run").json()["status"] == "success"

    def test_tiering_run_with_no_files_moves_zero(self, client):
        body = run_tiering(client)
        assert body["files_moved"] == 0

    def test_tiering_run_fresh_file_not_moved(self, client, small_file):
        """A just-uploaded file must stay HOT after a tiering run."""
        fid = upload(client)
        run_tiering(client)
        assert tier_of(client, fid) == "HOT"


# ══════════════════════════════════════════════════════════════
# HOT → WARM transitions
# ══════════════════════════════════════════════════════════════

class TestHotToWarm:

    def test_hot_to_warm_after_31_days(self, client):
        """File idle for 31 days must move from HOT to WARM."""
        fid = upload(client)
        set_last_accessed(fid, days_ago=31, client=client)
        run_tiering(client)
        assert tier_of(client, fid) == "WARM", (
            "Expected WARM after 31 days idle"
        )

    def test_hot_stays_at_29_days(self, client):
        """File idle for 29 days must stay HOT."""
        fid = upload(client)
        set_last_accessed(fid, days_ago=29, client=client)
        run_tiering(client)
        assert tier_of(client, fid) == "HOT", (
            "Expected HOT at 29 days idle"
        )

    def test_hot_to_warm_at_exactly_30_days(self, client):
        """
        Boundary: exactly 30 days idle.
        TIER_CONFIG uses >= 30, so transition SHOULD fire.
        """
        fid = upload(client)
        set_last_accessed(fid, days_ago=30, client=client)
        run_tiering(client)
        assert tier_of(client, fid) == "WARM", (
            "Expected WARM at exactly 30 days (>= 30 rule)"
        )

    def test_tiering_run_increments_files_moved(self, client):
        fid = upload(client)
        set_last_accessed(fid, days_ago=31, client=client)
        body = run_tiering(client)
        assert body["files_moved"] >= 1

    def test_multiple_hot_files_all_moved(self, client):
        """Three files all idle 35 days should all move to WARM."""
        fids = [upload(client, filename=f"f{i}.bin") for i in range(3)]
        for fid in fids:
            set_last_accessed(fid, days_ago=35, client=client)
        body = run_tiering(client)
        assert body["files_moved"] == 3
        for fid in fids:
            assert tier_of(client, fid) == "WARM"


# ══════════════════════════════════════════════════════════════
# WARM → COLD transitions
# ══════════════════════════════════════════════════════════════

class TestWarmToCold:

    def _put_in_warm(self, client) -> str:
        """Helper: upload → set 31 days idle → run tiering → now in WARM."""
        fid = upload(client)
        set_last_accessed(fid, days_ago=31, client=client)
        run_tiering(client)
        assert tier_of(client, fid) == "WARM", "Setup: could not move file to WARM"
        return fid

    def test_warm_to_cold_after_91_days(self, client):
        """File in WARM idle for 91 days must move to COLD."""
        fid = self._put_in_warm(client)
        set_last_accessed(fid, days_ago=91, client=client)
        run_tiering(client)
        assert tier_of(client, fid) == "COLD"

    def test_warm_stays_at_89_days(self, client):
        """File in WARM idle for 89 days must stay WARM."""
        fid = self._put_in_warm(client)
        set_last_accessed(fid, days_ago=89, client=client)
        run_tiering(client)
        assert tier_of(client, fid) == "WARM"

    def test_warm_to_cold_at_exactly_90_days(self, client):
        """Boundary: exactly 90 days must trigger WARM → COLD."""
        fid = self._put_in_warm(client)
        set_last_accessed(fid, days_ago=90, client=client)
        run_tiering(client)
        assert tier_of(client, fid) == "COLD", (
            "Expected COLD at exactly 90 days (>= 90 rule)"
        )


# ══════════════════════════════════════════════════════════════
# COLD tier — known bug
# ══════════════════════════════════════════════════════════════

class TestColdTierBehavior:

    def _put_in_cold(self, client) -> str:
        fid = upload(client)
        set_last_accessed(fid, days_ago=31, client=client)
        run_tiering(client)                        # → WARM
        set_last_accessed(fid, days_ago=91, client=client)
        run_tiering(client)                        # → COLD
        assert tier_of(client, fid) == "COLD", "Setup failed: file not in COLD"
        return fid

    def test_cold_file_stays_cold_after_tiering_run(self, client):
        """
        Files in COLD never auto-move — the TIER_CONFIG has next_tier=None.
        This is expected behaviour per the service implementation.
        """
        fid = self._put_in_cold(client)
        run_tiering(client)
        assert tier_of(client, fid) == "COLD"

    @pytest.mark.xfail(
        reason=(
            "BUG-003: COLD files are never promoted on access. "
            "The spec says Cold → Warm → Hot on recent access, "
            "but storage_service.py skips COLD files entirely in run_tiering(). "
            "This test documents the gap and should be fixed."
        )
    )
    def test_cold_file_promoted_to_warm_after_access(self, client):
        """
        Spec: accessing a COLD file should promote it toward WARM/HOT.
        Currently broken — COLD files are skipped in run_tiering().
        Marked xfail to document the bug without blocking CI.
        """
        fid = self._put_in_cold(client)
        # Simulate recent access
        client.get(f"/files/{fid}")
        set_last_accessed(fid, days_ago=0, client=client)
        run_tiering(client)
        assert tier_of(client, fid) in ("WARM", "HOT"), (
            "Cold file should be promoted after recent access"
        )


# ══════════════════════════════════════════════════════════════
# Special business rules
# ══════════════════════════════════════════════════════════════

class TestSpecialBusinessRules:

    def test_priority_file_stays_hot_despite_age(self, client):
        """
        Files with '_PRIORITY_' in filename must stay HOT regardless of age.
        apply_special_rules() forces them to HOT tier.
        """
        fid = upload(client, filename="report_PRIORITY_2024.bin")
        set_last_accessed(fid, days_ago=60, client=client)
        run_tiering(client)
        assert tier_of(client, fid) == "HOT", (
            "PRIORITY file should stay HOT regardless of idle days"
        )

    def test_legal_document_retained_in_warm_beyond_90_days(self, client):
        """
        Files starting with 'LEGAL_' get 180-day extended retention in WARM.
        They must NOT drop to COLD at 91 days idle.
        """
        fid = upload(client, filename="LEGAL_contract_2024.bin")
        # Move to WARM first
        set_last_accessed(fid, days_ago=31, client=client)
        run_tiering(client)
        assert tier_of(client, fid) == "WARM", "Setup: LEGAL_ file should be in WARM"

        # Now simulate 120 days idle (within 180-day legal retention)
        set_last_accessed(fid, days_ago=120, client=client)
        run_tiering(client)
        assert tier_of(client, fid) == "WARM", (
            "LEGAL_ document should stay WARM until 180 days"
        )

    def test_legal_document_moves_to_cold_after_180_days(self, client):
        """LEGAL_ files should move to COLD after 180-day retention expires."""
        fid = upload(client, filename="LEGAL_old_contract.bin")
        set_last_accessed(fid, days_ago=31, client=client)
        run_tiering(client)  # → WARM

        set_last_accessed(fid, days_ago=181, client=client)
        run_tiering(client)
        assert tier_of(client, fid) == "COLD", (
            "LEGAL_ file should move to COLD after 180 days"
        )

    def test_non_priority_filename_transitions_normally(self, client):
        """A file without _PRIORITY_ should transition normally at 31 days."""
        fid = upload(client, filename="normal_report.bin")
        set_last_accessed(fid, days_ago=31, client=client)
        run_tiering(client)
        assert tier_of(client, fid) == "WARM"


# ══════════════════════════════════════════════════════════════
# Admin mock-timestamp endpoint
# ══════════════════════════════════════════════════════════════

class TestUpdateLastAccessed:

    def test_update_last_accessed_returns_200(self, client, uploaded_file):
        resp = client.post(
            f"/admin/files/{uploaded_file}/update-last-accessed",
            json={"days_ago": 10},
        )
        assert resp.status_code == 200

    def test_update_last_accessed_reflects_in_metadata(self, client, uploaded_file):
        """After the update, metadata.last_accessed should be ~10 days ago."""
        from datetime import datetime, timedelta
        client.post(
            f"/admin/files/{uploaded_file}/update-last-accessed",
            json={"days_ago": 10},
        )
        last = client.get(f"/files/{uploaded_file}/metadata").json()["last_accessed"]
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00").rstrip("+00:00"))
        expected = datetime.utcnow() - timedelta(days=10)
        diff = abs((last_dt - expected).total_seconds())
        assert diff < 5, f"last_accessed off by {diff:.1f}s — expected ~10 days ago"

    def test_update_last_accessed_nonexistent_file_returns_404(self, client):
        resp = client.post(
            "/admin/files/no-such-id/update-last-accessed",
            json={"days_ago": 5},
        )
        assert resp.status_code == 404


# ══════════════════════════════════════════════════════════════
# GET /admin/stats
# ══════════════════════════════════════════════════════════════

class TestAdminStats:

    def test_stats_returns_200(self, client):
        assert client.get("/admin/stats").status_code == 200

    def test_stats_has_required_keys(self, client):
        body = client.get("/admin/stats").json()
        assert "total_files" in body
        assert "total_size" in body
        assert "tiers" in body

    def test_stats_tiers_has_hot_warm_cold(self, client):
        tiers = client.get("/admin/stats").json()["tiers"]
        for tier in ("HOT", "WARM", "COLD"):
            assert tier in tiers, f"Missing tier '{tier}' in stats"

    def test_stats_tiers_has_count_and_size(self, client):
        tiers = client.get("/admin/stats").json()["tiers"]
        for tier in ("HOT", "WARM", "COLD"):
            assert "count" in tiers[tier]
            assert "size" in tiers[tier]

    def test_stats_empty_storage_returns_zeros(self, client):
        body = client.get("/admin/stats").json()
        assert body["total_files"] == 0
        assert body["total_size"] == 0

    def test_stats_total_files_increments_on_upload(self, client, small_file):
        before = client.get("/admin/stats").json()["total_files"]
        client.post("/files", files=small_file)
        after = client.get("/admin/stats").json()["total_files"]
        assert after == before + 1

    def test_stats_hot_count_increments_on_upload(self, client, small_file):
        before = client.get("/admin/stats").json()["tiers"]["HOT"]["count"]
        client.post("/files", files=small_file)
        after = client.get("/admin/stats").json()["tiers"]["HOT"]["count"]
        assert after == before + 1

    def test_stats_total_size_reflects_upload(self, client):
        size = 2 * 1024 * 1024
        client.post("/files", files=make_file(size, "sized.bin"))
        body = client.get("/admin/stats").json()
        assert body["total_size"] >= size

    def test_stats_tier_count_sum_equals_total_files(self, client):
        """HOT + WARM + COLD counts must equal total_files."""
        for _ in range(3):
            client.post("/files", files=make_file(2 * 1024 * 1024, "f.bin"))
        body = client.get("/admin/stats").json()
        tier_sum = sum(body["tiers"][t]["count"] for t in ("HOT", "WARM", "COLD"))
        assert tier_sum == body["total_files"], (
            f"Tier sum {tier_sum} != total_files {body['total_files']}"
        )

    def test_stats_hot_count_decrements_on_delete(self, client, small_file):
        fid = client.post("/files", files=small_file).json()["file_id"]
        before = client.get("/admin/stats").json()["tiers"]["HOT"]["count"]
        client.delete(f"/files/{fid}")
        after = client.get("/admin/stats").json()["tiers"]["HOT"]["count"]
        assert after == before - 1

    def test_stats_reflects_tier_change(self, client):
        """After a tier transition, stats counts must shift accordingly."""
        fid = upload(client)
        set_last_accessed(fid, days_ago=31, client=client)

        hot_before = client.get("/admin/stats").json()["tiers"]["HOT"]["count"]
        warm_before = client.get("/admin/stats").json()["tiers"]["WARM"]["count"]

        run_tiering(client)

        hot_after = client.get("/admin/stats").json()["tiers"]["HOT"]["count"]
        warm_after = client.get("/admin/stats").json()["tiers"]["WARM"]["count"]

        assert hot_after == hot_before - 1
        assert warm_after == warm_before + 1
