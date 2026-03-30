import io
import threading
import time
import pytest
from starlette.testclient import TestClient
from src.storage_service import app, files_metadata, files_content
from tests.conftest import make_file_bytes, ONE_MB


# TestClient is not thread-safe; serialise concurrent calls with a lock
_lock = threading.Lock()


class TestConcurrentAccess:

    def test_concurrent_uploads(self, client, valid_file_bytes):
        results = []

        def _upload(idx):
            with _lock:
                resp = client.post(
                    "/files",
                    files={
                        "file": (
                            f"thread_{idx}.bin",
                            io.BytesIO(valid_file_bytes),
                            "application/octet-stream",
                        )
                    },
                )
                results.append(resp.status_code)

        threads = [threading.Thread(target=_upload, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(201) == 10

    def test_concurrent_downloads(self, client, valid_file_bytes):
        fid = client.post(
            "/files",
            files={"file": ("f.bin", io.BytesIO(valid_file_bytes), "application/octet-stream")},
        ).json()["file_id"]

        results = []

        def _download():
            with _lock:
                resp = client.get(f"/files/{fid}")
                results.append(resp.status_code)

        threads = [threading.Thread(target=_download) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r == 200 for r in results)

    def test_upload_and_delete_race(self, client, valid_file_bytes):
        # neither concurrent op should 500
        fid = client.post(
            "/files",
            files={"file": ("race.bin", io.BytesIO(valid_file_bytes), "application/octet-stream")},
        ).json()["file_id"]

        results = {}

        def _download():
            with _lock:
                results["download"] = client.get(f"/files/{fid}").status_code

        def _delete():
            with _lock:
                results["delete"] = client.delete(f"/files/{fid}").status_code

        t1 = threading.Thread(target=_download)
        t2 = threading.Thread(target=_delete)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results["download"] in (200, 404)
        assert results["delete"] in (204, 404)


class TestBulkOperations:

    def test_bulk_upload_20_files(self, client):
        content = make_file_bytes(ONE_MB)
        ids = []
        for i in range(20):
            resp = client.post(
                "/files",
                files={
                    "file": (f"bulk_{i}.bin", io.BytesIO(content), "application/octet-stream")
                },
            )
            assert resp.status_code == 201
            ids.append(resp.json()["file_id"])

        stats = client.get("/admin/stats").json()
        assert stats["total_files"] == 20

    def test_bulk_delete_all_files(self, client):
        content = make_file_bytes(ONE_MB)
        ids = []
        for i in range(10):
            resp = client.post(
                "/files",
                files={
                    "file": (f"del_{i}.bin", io.BytesIO(content), "application/octet-stream")
                },
            )
            ids.append(resp.json()["file_id"])

        for fid in ids:
            resp = client.delete(f"/files/{fid}")
            assert resp.status_code == 204

        stats = client.get("/admin/stats").json()
        assert stats["total_files"] == 0



class TestTieringPerformance:

    def test_tiering_50_files_under_2_seconds(self, client):
        content = make_file_bytes(ONE_MB)
        ids = []
        for i in range(50):
            resp = client.post(
                "/files",
                files={
                    "file": (f"perf_{i}.bin", io.BytesIO(content), "application/octet-stream")
                },
            )
            ids.append(resp.json()["file_id"])

        # age first 25 files
        for fid in ids[:25]:
            client.post(
                f"/admin/files/{fid}/update-last-accessed",
                json={"days_ago": 31},
            )

        start = time.time()
        result = client.post("/admin/tiering/run").json()
        elapsed = time.time() - start

        assert result["files_moved"] == 25
        assert elapsed < 2.0, f"Tiering took {elapsed:.2f}s, expected < 2s"
