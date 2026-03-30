"""
tests/performance/test_concurrency_bulk.py
==========================================
Bonus: Concurrency, I/O burst, and bulk upload/delete scenarios.

Uses threading against the in-process TestClient.
TestClient is thread-safe when used with httpx (FastAPI default).
"""

import pytest
import threading
import concurrent.futures
import time
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from conftest import make_file


# ── Helpers ────────────────────────────────────────────────────────────────────

def do_upload(client, size_mb: int = 2, filename: str = "concurrent.bin") -> dict:
    resp = client.post("/files", files=make_file(size_mb * 1024 * 1024, filename))
    return {"status": resp.status_code, "body": resp.json() if resp.status_code == 201 else {}}


def do_delete(client, file_id: str) -> int:
    return client.delete(f"/files/{file_id}").status_code


def do_download(client, file_id: str) -> int:
    return client.get(f"/files/{file_id}").status_code


def bulk_upload(client, n: int) -> list[str]:
    ids = []
    lock = threading.Lock()

    def worker(_):
        r = do_upload(client)
        if r["status"] == 201:
            with lock:
                ids.append(r["body"]["file_id"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(worker, range(n)))
    return ids


# ══════════════════════════════════════════════════════════════
# Concurrent Uploads
# ══════════════════════════════════════════════════════════════

class TestConcurrentUploads:

    WORKERS = 10

    def test_concurrent_uploads_all_succeed(self, client):
        """10 simultaneous uploads must all return 201."""
        results = []
        lock = threading.Lock()

        def worker():
            r = do_upload(client)
            with lock:
                results.append(r["status"])

        threads = [threading.Thread(target=worker) for _ in range(self.WORKERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        failed = [s for s in results if s != 201]
        assert not failed, f"{len(failed)}/{self.WORKERS} concurrent uploads failed"

    def test_concurrent_uploads_produce_unique_ids(self, client):
        """No two concurrent uploads should share a file_id."""
        ids = bulk_upload(client, self.WORKERS)
        assert len(ids) == self.WORKERS, f"Expected {self.WORKERS} IDs, got {len(ids)}"
        assert len(ids) == len(set(ids)), "Duplicate file_ids detected under concurrency"


# ══════════════════════════════════════════════════════════════
# Concurrent Downloads
# ══════════════════════════════════════════════════════════════

class TestConcurrentDownloads:

    def test_50_concurrent_downloads_same_file(self, client, uploaded_file):
        """50 concurrent reads of the same file must all return 200."""
        statuses = []
        lock = threading.Lock()

        def worker():
            s = do_download(client, uploaded_file)
            with lock:
                statuses.append(s)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        failed = [s for s in statuses if s != 200]
        assert not failed, f"{len(failed)}/50 concurrent downloads failed: {set(failed)}"

    def test_concurrent_downloads_different_files(self, client):
        """Concurrent downloads of distinct files must all succeed."""
        ids = bulk_upload(client, 5)
        assert len(ids) == 5

        statuses = {}
        lock = threading.Lock()

        def worker(fid):
            s = do_download(client, fid)
            with lock:
                statuses[fid] = s

        threads = [threading.Thread(target=worker, args=(fid,)) for fid in ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for fid, s in statuses.items():
            assert s == 200, f"Download failed for {fid}: status {s}"


# ══════════════════════════════════════════════════════════════
# Concurrent Deletes
# ══════════════════════════════════════════════════════════════

class TestConcurrentDeletes:

    def test_double_delete_exactly_one_wins(self, client, small_file):
        """
        Two simultaneous DELETEs on the same file:
        exactly one should succeed (204), the other should get 404.
        """
        fid = client.post("/files", files=small_file).json()["file_id"]
        results = []
        lock = threading.Lock()

        def worker():
            s = do_delete(client, fid)
            with lock:
                results.append(s)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start(); t2.start()
        t1.join(); t2.join()

        successes = [s for s in results if s == 204]
        not_founds = [s for s in results if s == 404]
        assert len(successes) == 1 and len(not_founds) == 1, (
            f"Expected 1×204 + 1×404, got: {results}"
        )


# ══════════════════════════════════════════════════════════════
# I/O Burst
# ══════════════════════════════════════════════════════════════

class TestIOBurst:

    def test_mixed_crud_burst_no_5xx(self, client):
        """
        Burst: 10 uploads, 20 downloads, 5 deletes running concurrently.
        No request should result in a 5xx server error.
        """
        seed_ids = bulk_upload(client, 10)
        errors = []
        lock = threading.Lock()

        def burst_upload():
            r = do_upload(client)
            if r["status"] >= 500:
                with lock:
                    errors.append(("upload", r["status"]))
            elif r["status"] == 201:
                do_delete(client, r["body"]["file_id"])

        def burst_download(fid):
            s = do_download(client, fid)
            if s >= 500:
                with lock:
                    errors.append(("download", s))

        def burst_delete(fid):
            s = do_delete(client, fid)
            if s >= 500:
                with lock:
                    errors.append(("delete", s))

        tasks = []
        for _ in range(10):
            tasks.append(threading.Thread(target=burst_upload))
        for fid in seed_ids[:5]:
            for _ in range(4):
                tasks.append(threading.Thread(target=burst_download, args=(fid,)))
        for fid in seed_ids[5:]:
            tasks.append(threading.Thread(target=burst_delete, args=(fid,)))

        for t in tasks:
            t.start()
        for t in tasks:
            t.join()

        assert not errors, f"5xx errors during burst: {errors}"

    def test_upload_throughput_baseline(self, client):
        """
        Performance gate: 5 sequential 2 MB uploads should complete in < 5 seconds
        (in-process TestClient — no network overhead).
        """
        MAX_SECONDS = 5
        ids = []
        start = time.time()
        for _ in range(5):
            r = do_upload(client)
            assert r["status"] == 201
            ids.append(r["body"]["file_id"])
        elapsed = time.time() - start

        for fid in ids:
            do_delete(client, fid)

        assert elapsed < MAX_SECONDS, (
            f"5 in-process uploads took {elapsed:.2f}s — exceeded {MAX_SECONDS}s"
        )


# ══════════════════════════════════════════════════════════════
# Bulk Upload
# ══════════════════════════════════════════════════════════════

BULK_N = 20


class TestBulkUpload:

    def test_bulk_upload_all_accepted(self, client):
        ids = bulk_upload(client, BULK_N)
        assert len(ids) == BULK_N, f"Expected {BULK_N} uploads, got {len(ids)}"

    def test_bulk_upload_all_ids_unique(self, client):
        ids = bulk_upload(client, BULK_N)
        assert len(ids) == len(set(ids)), "Bulk upload produced duplicate file_ids"

    def test_bulk_upload_stats_consistent(self, client):
        before = client.get("/admin/stats").json()["total_files"]
        ids = bulk_upload(client, BULK_N)
        after = client.get("/admin/stats").json()["total_files"]
        assert after == before + BULK_N, (
            f"Stats after bulk upload: expected {before + BULK_N}, got {after}"
        )

    def test_bulk_upload_each_file_retrievable(self, client):
        ids = bulk_upload(client, 10)
        for fid in ids:
            assert do_download(client, fid) == 200, (
                f"File {fid} not retrievable after bulk upload"
            )

    def test_bulk_upload_all_start_in_hot_tier(self, client):
        ids = bulk_upload(client, 10)
        for fid in ids:
            tier = client.get(f"/files/{fid}/metadata").json()["tier"]
            assert tier == "HOT", f"File {fid} not in HOT after upload, got {tier}"


# ══════════════════════════════════════════════════════════════
# Bulk Delete
# ══════════════════════════════════════════════════════════════

class TestBulkDelete:

    def test_bulk_delete_all_succeed(self, client):
        ids = bulk_upload(client, BULK_N)
        statuses = []
        lock = threading.Lock()

        def worker(fid):
            s = do_delete(client, fid)
            with lock:
                statuses.append(s)

        threads = [threading.Thread(target=worker, args=(fid,)) for fid in ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        failed = [s for s in statuses if s != 204]
        assert not failed, f"{len(failed)}/{BULK_N} bulk deletes failed: {set(failed)}"

    def test_bulk_delete_files_not_accessible_after(self, client):
        ids = bulk_upload(client, 10)
        for fid in ids:
            do_delete(client, fid)
        for fid in ids:
            assert do_download(client, fid) == 404, (
                f"File {fid} still accessible after bulk delete"
            )

    def test_bulk_delete_stats_decremented(self, client):
        ids = bulk_upload(client, BULK_N)
        before = client.get("/admin/stats").json()["total_files"]
        for fid in ids:
            do_delete(client, fid)
        after = client.get("/admin/stats").json()["total_files"]
        assert after == before - BULK_N

    def test_full_upload_verify_delete_cycle(self, client):
        """Complete cycle: upload → verify accessible → delete → verify gone."""
        ids = bulk_upload(client, 15)
        assert len(ids) == 15

        for fid in ids:
            assert do_download(client, fid) == 200

        for fid in ids:
            assert do_delete(client, fid) == 204

        for fid in ids:
            assert do_download(client, fid) == 404
