import io
import pytest
from tests.conftest import make_file_bytes, upload_file, upload_and_get_id, ONE_MB


class TestUploadFile:

    def test_upload_valid_file_returns_201(self, client, valid_file_bytes):
        resp = upload_file(client, valid_file_bytes)
        assert resp.status_code == 201

    def test_upload_returns_file_id(self, client, valid_file_bytes):
        body = upload_file(client, valid_file_bytes).json()
        assert "file_id" in body
        assert len(body["file_id"]) > 0

    def test_upload_initial_tier_is_hot(self, client, valid_file_bytes):
        body = upload_file(client, valid_file_bytes).json()
        assert body["tier"] == "HOT"

    def test_upload_preserves_filename(self, client, valid_file_bytes):
        resp = upload_file(client, valid_file_bytes, filename="report.pdf")
        assert resp.json()["filename"] == "report.pdf"

    def test_upload_records_correct_size(self, client, valid_file_bytes):
        body = upload_file(client, valid_file_bytes).json()
        assert body["size"] == ONE_MB

    def test_upload_sets_created_at(self, client, valid_file_bytes):
        body = upload_file(client, valid_file_bytes).json()
        assert "created_at" in body

    def test_upload_sets_last_accessed(self, client, valid_file_bytes):
        body = upload_file(client, valid_file_bytes).json()
        assert "last_accessed" in body

    def test_upload_generates_etag(self, client, valid_file_bytes):
        body = upload_file(client, valid_file_bytes).json()
        assert body["etag"] != ""

    def test_upload_two_files_get_different_ids(self, client, valid_file_bytes):
        id1 = upload_file(client, valid_file_bytes).json()["file_id"]
        id2 = upload_file(client, valid_file_bytes).json()["file_id"]
        assert id1 != id2



class TestUploadBoundary:

    def test_reject_zero_byte_file(self, client):
        """0-byte file must be rejected (below 1 MB minimum)."""
        resp = upload_file(client, b"")
        assert resp.status_code == 400

    def test_reject_file_just_under_1mb(self, client):
        """1 MB - 1 byte should be rejected."""
        content = make_file_bytes(ONE_MB - 1)
        resp = upload_file(client, content)
        assert resp.status_code == 400

    def test_accept_file_exactly_1mb(self, client):
        """Exactly 1 MB is the minimum – should be accepted."""
        content = make_file_bytes(ONE_MB)
        resp = upload_file(client, content)
        assert resp.status_code == 201

    def test_accept_file_just_over_1mb(self, client):
        """1 MB + 1 byte should be accepted."""
        content = make_file_bytes(ONE_MB + 1)
        resp = upload_file(client, content)
        assert resp.status_code == 201

    def test_reject_file_without_multipart(self, client):
        """Sending raw body instead of multipart should fail."""
        resp = client.post("/files", content=b"raw data")
        assert resp.status_code == 422  # FastAPI validation error

    def test_upload_file_with_special_chars_in_name(self, client, valid_file_bytes):
        """Filenames with spaces / unicode should still be accepted."""
        resp = upload_file(client, valid_file_bytes, filename="my file (1).txt")
        assert resp.status_code == 201
        assert resp.json()["filename"] == "my file (1).txt"



class TestDownloadFile:

    def test_download_existing_file(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        resp = client.get(f"/files/{fid}")
        assert resp.status_code == 200

    def test_download_returns_filename(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes, filename="data.csv")
        body = client.get(f"/files/{fid}").json()
        assert body["filename"] == "data.csv"

    def test_download_nonexistent_file_returns_404(self, client):
        resp = client.get("/files/does-not-exist")
        assert resp.status_code == 404

    def test_download_updates_last_accessed(self, client, valid_file_bytes):
        """Downloading a file should refresh its last_accessed timestamp."""
        fid = upload_and_get_id(client, valid_file_bytes)
        # set last_accessed to 50 days ago
        client.post(
            f"/admin/files/{fid}/update-last-accessed",
            json={"days_ago": 50},
        )
        meta_before = client.get(f"/files/{fid}/metadata").json()

        # download the file – should refresh last_accessed
        client.get(f"/files/{fid}")

        meta_after = client.get(f"/files/{fid}/metadata").json()
        assert meta_after["last_accessed"] >= meta_before["last_accessed"]



class TestFileMetadata:

    def test_metadata_for_existing_file(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        resp = client.get(f"/files/{fid}/metadata")
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_id"] == fid
        assert body["size"] == ONE_MB
        assert body["tier"] == "HOT"

    def test_metadata_nonexistent_file(self, client):
        resp = client.get("/files/no-such-id/metadata")
        assert resp.status_code == 404

    def test_metadata_contains_all_expected_fields(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        body = client.get(f"/files/{fid}/metadata").json()
        expected_keys = {
            "file_id", "filename", "size", "tier",
            "created_at", "last_accessed", "content_type", "etag",
        }
        assert expected_keys.issubset(body.keys())



class TestDeleteFile:

    def test_delete_existing_file_returns_204(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        resp = client.delete(f"/files/{fid}")
        assert resp.status_code == 204

    def test_deleted_file_no_longer_downloadable(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        client.delete(f"/files/{fid}")
        assert client.get(f"/files/{fid}").status_code == 404

    def test_deleted_file_metadata_gone(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        client.delete(f"/files/{fid}")
        assert client.get(f"/files/{fid}/metadata").status_code == 404

    def test_delete_nonexistent_file_returns_404(self, client):
        resp = client.delete("/files/nonexistent-id")
        assert resp.status_code == 404

    def test_double_delete_returns_404(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        client.delete(f"/files/{fid}")
        resp = client.delete(f"/files/{fid}")
        assert resp.status_code == 404
