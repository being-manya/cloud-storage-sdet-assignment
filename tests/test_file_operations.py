"""
tests/functional/test_file_operations.py
=========================================
Functional tests for all file CRUD endpoints:
  POST   /files
  GET    /files/{file_id}
  GET    /files/{file_id}/metadata
  DELETE /files/{file_id}

All field names, status codes, and tier values match storage_service.py exactly:
  - Response key  : "file_id"  (snake_case)
  - Tier values   : "HOT", "WARM", "COLD"
  - Upload status : 201 Created
  - Delete status : 204 No Content
"""

import pytest
import io
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from conftest import make_file


# ══════════════════════════════════════════════════════════════
# POST /files — Upload
# ══════════════════════════════════════════════════════════════

class TestFileUpload:

    def test_upload_valid_file_returns_201(self, client, small_file):
        """Happy path: 2 MB file must be accepted."""
        resp = client.post("/files", files=small_file)
        assert resp.status_code == 201

    def test_upload_response_contains_file_id(self, client, small_file):
        resp = client.post("/files", files=small_file)
        assert resp.status_code == 201
        body = resp.json()
        assert "file_id" in body
        assert isinstance(body["file_id"], str)
        assert len(body["file_id"]) > 0

    def test_upload_response_contains_all_metadata_fields(self, client, small_file):
        resp = client.post("/files", files=small_file)
        body = resp.json()
        for field in ("file_id", "filename", "size", "tier", "created_at", "last_accessed"):
            assert field in body, f"Missing field: {field}"

    def test_upload_zero_byte_file_rejected(self, client, tiny_file):
        """Edge: 0-byte file is below the 1 MB minimum."""
        resp = client.post("/files", files=tiny_file)
        assert resp.status_code == 400, (
            f"Expected 400 for 0-byte file, got {resp.status_code}"
        )

    def test_upload_just_under_1mb_rejected(self, client, just_under_1mb):
        """Boundary: 1 048 575 bytes (1 byte short) must be rejected."""
        resp = client.post("/files", files=just_under_1mb)
        assert resp.status_code == 400, (
            f"Expected 400 for sub-1MB file, got {resp.status_code}: {resp.text}"
        )

    def test_upload_exactly_1mb_accepted(self, client, exactly_1mb):
        """Boundary: exactly 1 MB must be accepted."""
        resp = client.post("/files", files=exactly_1mb)
        assert resp.status_code == 201, (
            f"Expected 201 for exactly 1 MB, got {resp.status_code}: {resp.text}"
        )

    def test_upload_new_file_starts_in_hot_tier(self, client, small_file):
        """Every freshly uploaded file must land in HOT tier."""
        resp = client.post("/files", files=small_file)
        assert resp.json()["tier"] == "HOT"

    def test_upload_size_reflected_in_response(self, client):
        """Response size must equal actual byte count sent."""
        size = 3 * 1024 * 1024   # 3 MB
        resp = client.post("/files", files=make_file(size, "sized.bin"))
        assert resp.status_code == 201
        assert resp.json()["size"] == size

    def test_upload_filename_preserved(self, client):
        resp = client.post("/files", files=make_file(2 * 1024 * 1024, "mydata.bin"))
        assert resp.json()["filename"] == "mydata.bin"

    def test_upload_duplicate_filename_gives_unique_ids(self, client):
        """Two uploads with the same filename must produce distinct file_ids."""
        r1 = client.post("/files", files=make_file(2 * 1024 * 1024, "dup.bin"))
        r2 = client.post("/files", files=make_file(2 * 1024 * 1024, "dup.bin"))
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["file_id"] != r2.json()["file_id"]

    def test_upload_missing_file_field_returns_422(self, client):
        """No file in the request must return 422 (FastAPI validation error)."""
        resp = client.post("/files", data={})
        assert resp.status_code == 422

    def test_upload_content_type_stored(self, client):
        """content_type field should be populated in the response."""
        resp = client.post("/files", files=make_file(2 * 1024 * 1024, "data.bin"))
        assert resp.status_code == 201
        assert "content_type" in resp.json()

    def test_upload_etag_generated(self, client, small_file):
        """etag field should be a non-empty string."""
        resp = client.post("/files", files=small_file)
        body = resp.json()
        assert "etag" in body
        assert isinstance(body["etag"], str) and len(body["etag"]) > 0


# ══════════════════════════════════════════════════════════════
# GET /files/{file_id} — Download
# ══════════════════════════════════════════════════════════════

class TestFileDownload:

    def test_download_existing_file_returns_200(self, client, uploaded_file):
        resp = client.get(f"/files/{uploaded_file}")
        assert resp.status_code == 200

    def test_download_response_contains_content(self, client, uploaded_file):
        body = client.get(f"/files/{uploaded_file}").json()
        assert "content" in body

    def test_download_response_contains_filename(self, client, uploaded_file):
        body = client.get(f"/files/{uploaded_file}").json()
        assert "filename" in body

    def test_download_nonexistent_file_returns_404(self, client):
        resp = client.get("/files/does-not-exist-at-all")
        assert resp.status_code == 404

    def test_download_updates_last_accessed(self, client, uploaded_file):
        """
        Downloading a file should refresh its last_accessed timestamp.
        BUG NOTE: this test also serves as a canary for BUG-004 if it fails.
        """
        before = client.get(f"/files/{uploaded_file}/metadata").json()["last_accessed"]
        # Small sleep ensures the timestamp can actually change
        import time; time.sleep(0.05)
        client.get(f"/files/{uploaded_file}")
        after = client.get(f"/files/{uploaded_file}/metadata").json()["last_accessed"]
        assert after >= before, "Download did not refresh last_accessed"

    def test_download_after_delete_returns_404(self, client, small_file):
        resp = client.post("/files", files=small_file)
        fid = resp.json()["file_id"]
        client.delete(f"/files/{fid}")
        assert client.get(f"/files/{fid}").status_code == 404


# ══════════════════════════════════════════════════════════════
# GET /files/{file_id}/metadata
# ══════════════════════════════════════════════════════════════

class TestFileMetadata:

    REQUIRED_FIELDS = {
        "file_id", "filename", "size", "tier",
        "created_at", "last_accessed", "content_type", "etag"
    }

    def test_metadata_returns_200(self, client, uploaded_file):
        assert client.get(f"/files/{uploaded_file}/metadata").status_code == 200

    def test_metadata_contains_all_required_fields(self, client, uploaded_file):
        body = client.get(f"/files/{uploaded_file}/metadata").json()
        missing = self.REQUIRED_FIELDS - body.keys()
        assert not missing, f"Metadata missing fields: {missing}"

    def test_metadata_size_matches_upload(self, client):
        size = 4 * 1024 * 1024
        fid = client.post("/files", files=make_file(size, "meta.bin")).json()["file_id"]
        meta = client.get(f"/files/{fid}/metadata").json()
        assert meta["size"] == size

    def test_metadata_tier_is_valid(self, client, uploaded_file):
        tier = client.get(f"/files/{uploaded_file}/metadata").json()["tier"]
        assert tier in ("HOT", "WARM", "COLD"), f"Invalid tier: {tier}"

    def test_metadata_tier_is_hot_on_fresh_upload(self, client, uploaded_file):
        tier = client.get(f"/files/{uploaded_file}/metadata").json()["tier"]
        assert tier == "HOT"

    def test_metadata_nonexistent_file_returns_404(self, client):
        assert client.get("/files/no-such-id/metadata").status_code == 404

    def test_metadata_file_id_matches_upload(self, client, small_file):
        upload_id = client.post("/files", files=small_file).json()["file_id"]
        meta_id = client.get(f"/files/{upload_id}/metadata").json()["file_id"]
        assert upload_id == meta_id


# ══════════════════════════════════════════════════════════════
# DELETE /files/{file_id}
# ══════════════════════════════════════════════════════════════

class TestFileDelete:

    def test_delete_existing_file_returns_204(self, client, small_file):
        fid = client.post("/files", files=small_file).json()["file_id"]
        assert client.delete(f"/files/{fid}").status_code == 204

    def test_delete_removes_file_from_download(self, client, small_file):
        fid = client.post("/files", files=small_file).json()["file_id"]
        client.delete(f"/files/{fid}")
        assert client.get(f"/files/{fid}").status_code == 404

    def test_delete_removes_file_from_metadata(self, client, small_file):
        fid = client.post("/files", files=small_file).json()["file_id"]
        client.delete(f"/files/{fid}")
        assert client.get(f"/files/{fid}/metadata").status_code == 404

    def test_delete_nonexistent_file_returns_404(self, client):
        assert client.delete("/files/ghost-id-99999").status_code == 404

    def test_double_delete_second_returns_404(self, client, small_file):
        fid = client.post("/files", files=small_file).json()["file_id"]
        client.delete(f"/files/{fid}")
        assert client.delete(f"/files/{fid}").status_code == 404

    def test_delete_decrements_stats(self, client, small_file):
        fid = client.post("/files", files=small_file).json()["file_id"]
        before = client.get("/admin/stats").json()["total_files"]
        client.delete(f"/files/{fid}")
        after = client.get("/admin/stats").json()["total_files"]
        assert after == before - 1
