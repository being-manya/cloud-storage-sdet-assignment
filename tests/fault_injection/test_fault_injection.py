import io
import pytest
from starlette.testclient import TestClient
from src.storage_service import app, files_metadata, files_content
from tests.conftest import make_file_bytes, upload_file, upload_and_get_id, ONE_MB


class TestMalformedRequests:

    def test_upload_no_file_field(self, client):
        """POST /files with no file part should 422."""
        resp = client.post("/files")
        assert resp.status_code == 422

    def test_upload_empty_filename(self, client, valid_file_bytes):
        resp = client.post(
            "/files",
            files={"file": ("", io.BytesIO(valid_file_bytes), "application/octet-stream")},
        )
        assert resp.status_code in (201, 400, 422)

    def test_metadata_with_empty_id(self, client):
        resp = client.get("/files//metadata")
        assert resp.status_code != 500

    def test_delete_with_sql_injection_id(self, client):
        resp = client.delete("/files/'; DROP TABLE files;--")
        assert resp.status_code == 404

    def test_update_last_accessed_negative_days(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        resp = client.post(
            f"/admin/files/{fid}/update-last-accessed",
            json={"days_ago": -5},
        )
        assert resp.status_code in (200, 400, 422)

    def test_update_last_accessed_missing_body(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        resp = client.post(f"/admin/files/{fid}/update-last-accessed")
        assert resp.status_code == 422



class TestRapidRepeatedOperations:

    def test_download_same_file_100_times(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        for _ in range(100):
            resp = client.get(f"/files/{fid}")
            assert resp.status_code == 200

    def test_metadata_same_file_100_times(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        for _ in range(100):
            resp = client.get(f"/files/{fid}/metadata")
            assert resp.status_code == 200

    def test_run_tiering_repeatedly(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        client.post(
            f"/admin/files/{fid}/update-last-accessed",
            json={"days_ago": 31},
        )
        first = client.post("/admin/tiering/run").json()
        assert first["files_moved"] == 1

        second = client.post("/admin/tiering/run").json()
        assert second["files_moved"] == 0  # already moved



class TestEdgeConditions:

    def test_tiering_on_empty_storage(self, client):
        result = client.post("/admin/tiering/run").json()
        assert result["files_moved"] == 0
        assert result["status"] == "success"

    def test_stats_on_empty_storage(self, client):
        body = client.get("/admin/stats").json()
        assert body["total_files"] == 0

    def test_upload_then_immediately_delete_then_metadata(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        client.delete(f"/files/{fid}")
        resp = client.get(f"/files/{fid}/metadata")
        assert resp.status_code == 404

    def test_tiering_ignores_deleted_files(self, client, valid_file_bytes):
        fid = upload_and_get_id(client, valid_file_bytes)
        client.post(
            f"/admin/files/{fid}/update-last-accessed",
            json={"days_ago": 31},
        )
        client.delete(f"/files/{fid}")
        result = client.post("/admin/tiering/run").json()
        assert result["status"] == "success"
        assert result["files_moved"] == 0
