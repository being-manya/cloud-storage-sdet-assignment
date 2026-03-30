import io
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from fastapi.testclient import TestClient
from storage_service import app, files_metadata, files_content


@pytest.fixture(autouse=True)
def reset_storage():
    files_metadata.clear()
    files_content.clear()
    yield
    files_metadata.clear()
    files_content.clear()


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def make_file(size_bytes: int, filename: str = "test.bin") -> dict:
    return {"file": (filename, io.BytesIO(b"x" * size_bytes), "application/octet-stream")}


@pytest.fixture
def tiny_file():
    return make_file(0, "zero.bin")


@pytest.fixture
def just_under_1mb():
    return make_file(1 * 1024 * 1024 - 1, "under1mb.bin")


@pytest.fixture
def exactly_1mb():
    return make_file(1 * 1024 * 1024, "exactly1mb.bin")


@pytest.fixture
def small_file():
    return make_file(2 * 1024 * 1024, "small.bin")


@pytest.fixture
def uploaded_file(client, small_file):
    resp = client.post("/files", files=small_file)
    assert resp.status_code == 201, f"Setup upload failed: {resp.text}"
    file_id = resp.json()["file_id"]
    yield file_id
    client.delete(f"/files/{file_id}")


def set_last_accessed(file_id: str, days_ago: int, client: TestClient):
    resp = client.post(
        f"/admin/files/{file_id}/update-last-accessed",
        json={"days_ago": days_ago},
    )
    assert resp.status_code == 200, f"update-last-accessed failed for {file_id}: {resp.text}"
    return resp
