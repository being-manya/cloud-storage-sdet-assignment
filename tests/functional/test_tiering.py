import pytest
from tests.conftest import make_file_bytes, upload_file, upload_and_get_id, ONE_MB

def set_last_accessed(client, file_id: str, days_ago: int):
    resp = client.post(
        f"/admin/files/{file_id}/update-last-accessed",
        json={"days_ago": days_ago},
    )
    assert resp.status_code == 200
    return resp.json()


def run_tiering(client) -> dict:
    resp = client.post("/admin/tiering/run")
    assert resp.status_code == 200
    return resp.json()


def get_tier(client, file_id: str) -> str:
    resp = client.get(f"/files/{file_id}/metadata")
    assert resp.status_code == 200
    return resp.json()["tier"]


class TestTierTransitions:

    def test_new_file_starts_in_hot(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        assert get_tier(client, fid) == "HOT"

    def test_hot_to_warm_after_30_days(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        set_last_accessed(client, fid, days_ago=31)
        run_tiering(client)
        assert get_tier(client, fid) == "WARM"

    def test_stays_hot_at_29_days(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        set_last_accessed(client, fid, days_ago=29)
        run_tiering(client)
        assert get_tier(client, fid) == "HOT"

    def test_hot_to_warm_exactly_30_days(self, client, valid_file_bytes):
        """At exactly 30 days the file should transition (>= threshold)."""
        fid = upload_and_get_id(client, valid_file_bytes)
        set_last_accessed(client, fid, days_ago=30)
        run_tiering(client)
        assert get_tier(client, fid) == "WARM"

    def test_warm_to_cold_after_90_days(self, client, valid_file_bytes):
        """File already in WARM should move to COLD after 90 days no access."""
        fid = upload_and_get_id(client, valid_file_bytes)
        # First move to WARM
        set_last_accessed(client, fid, days_ago=31)
        run_tiering(client)
        assert get_tier(client, fid) == "WARM"
        # Now set to 91 days and run tiering again
        set_last_accessed(client, fid, days_ago=91)
        run_tiering(client)
        assert get_tier(client, fid) == "COLD"

    def test_warm_stays_warm_at_89_days(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        set_last_accessed(client, fid, days_ago=31)
        run_tiering(client)
        assert get_tier(client, fid) == "WARM"
        set_last_accessed(client, fid, days_ago=89)
        run_tiering(client)
        assert get_tier(client, fid) == "WARM"

    def test_cold_files_do_not_move_further(self, client, valid_file_bytes):
        """Once COLD, a file should not demote further on repeated tiering."""
        fid = upload_and_get_id(client, valid_file_bytes)
        set_last_accessed(client, fid, days_ago=31)
        run_tiering(client)
        set_last_accessed(client, fid, days_ago=91)
        run_tiering(client)
        assert get_tier(client, fid) == "COLD"
        # run tiering many more times
        set_last_accessed(client, fid, days_ago=365)
        run_tiering(client)
        assert get_tier(client, fid) == "COLD"

    def test_tiering_reports_moved_count(self, client, valid_file_bytes):
        """The tiering endpoint should report how many files moved."""
        fid = upload_and_get_id(client, valid_file_bytes)
        set_last_accessed(client, fid, days_ago=31)
        result = run_tiering(client)
        assert result["files_moved"] == 1

    def test_tiering_no_moves_when_all_fresh(self, client, valid_file_bytes):
        upload_and_get_id(client, valid_file_bytes)
        result = run_tiering(client)
        assert result["files_moved"] == 0


class TestTierPromotion:
    # BUG-001: tiering only demotes, never promotes. Cold files stay cold
    # even after a recent access. See docs/BUG_REPORTS.md for the fix.

    @pytest.mark.xfail(
        strict=False,
        reason="BUG-001: cold files are not promoted on access",
    )
    def test_download_cold_file_should_promote(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        set_last_accessed(client, fid, days_ago=31)
        run_tiering(client)
        set_last_accessed(client, fid, days_ago=91)
        run_tiering(client)
        assert get_tier(client, fid) == "COLD"

        client.get(f"/files/{fid}")
        run_tiering(client)

        assert get_tier(client, fid) in ("WARM", "HOT")



class TestSpecialRules:

    def test_priority_file_stays_hot(self, client, valid_file_bytes):
        fid = upload_and_get_id(
            client, valid_file_bytes, filename="report_PRIORITY_q4.pdf"
        )
        set_last_accessed(client, fid, days_ago=60)
        run_tiering(client)
        assert get_tier(client, fid) == "HOT"

    def test_priority_case_insensitive(self, client, valid_file_bytes):
        """_PRIORITY_ matching is upper-cased, so mixed case should work."""
        fid = upload_and_get_id(
            client, valid_file_bytes, filename="data_priority_2024.bin"
        )
        set_last_accessed(client, fid, days_ago=60)
        run_tiering(client)
        assert get_tier(client, fid) == "HOT"

    def test_legal_doc_extended_warm_retention(self, client, valid_file_bytes):
        """LEGAL_ files should stay WARM for up to 180 days."""
        fid = upload_and_get_id(
            client, valid_file_bytes, filename="LEGAL_contract.pdf"
        )
        # move to WARM first
        set_last_accessed(client, fid, days_ago=31)
        run_tiering(client)
        assert get_tier(client, fid) == "WARM"

        # at 100 days (within 180-day window) it should stay WARM
        set_last_accessed(client, fid, days_ago=100)
        run_tiering(client)
        assert get_tier(client, fid) == "WARM"

    def test_legal_doc_moves_cold_after_180_days(self, client, valid_file_bytes):
        """After 180 days, legal docs should eventually move to COLD."""
        fid = upload_and_get_id(
            client, valid_file_bytes, filename="LEGAL_nda.pdf"
        )
        set_last_accessed(client, fid, days_ago=31)
        run_tiering(client)
        assert get_tier(client, fid) == "WARM"

        set_last_accessed(client, fid, days_ago=181)
        run_tiering(client)
        assert get_tier(client, fid) == "COLD"



class TestAdminStats:

    def test_stats_empty_storage(self, client):
        resp = client.get("/admin/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_files"] == 0
        assert body["total_size"] == 0

    def test_stats_after_upload(self, client, valid_file_bytes):
        upload_and_get_id(client, valid_file_bytes)
        body = client.get("/admin/stats").json()
        assert body["total_files"] == 1
        assert body["total_size"] == ONE_MB

    def test_stats_tier_counts_after_tiering(self, client, valid_file_bytes):
        """Upload two files, age one past 30 days, check tier counts."""
        upload_and_get_id(client, valid_file_bytes)  # stays HOT
        fid2 = upload_and_get_id(client, valid_file_bytes)
        set_last_accessed(client, fid2, days_ago=31)
        run_tiering(client)

        body = client.get("/admin/stats").json()
        assert body["total_files"] == 2
        # tiers dict is keyed by StorageTier enum values
        hot_count = body["tiers"]["HOT"]["count"]
        warm_count = body["tiers"]["WARM"]["count"]
        assert hot_count == 1
        assert warm_count == 1

    def test_stats_after_delete(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        client.delete(f"/files/{fid}")
        body = client.get("/admin/stats").json()
        assert body["total_files"] == 0
        assert body["total_size"] == 0



class TestUpdateLastAccessed:

    def test_update_last_accessed_success(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        resp = client.post(
            f"/admin/files/{fid}/update-last-accessed",
            json={"days_ago": 45},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

    def test_update_last_accessed_nonexistent_file(self, client):
        resp = client.post(
            "/admin/files/no-such-id/update-last-accessed",
            json={"days_ago": 10},
        )
        assert resp.status_code == 404
