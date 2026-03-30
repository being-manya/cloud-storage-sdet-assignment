import io
import pytest
from starlette.testclient import TestClient
from src.storage_service import app, files_metadata, files_content


ONE_MB = 1024 * 1024
TEN_GB = 10 * 1024 * 1024 * 1024


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_storage():
    files_metadata.clear()
    files_content.clear()
    yield
    files_metadata.clear()
    files_content.clear()


def make_file_bytes(size_bytes: int, pattern: bytes = b"A") -> bytes:
    return (pattern * size_bytes)[:size_bytes]


@pytest.fixture()
def valid_file_bytes():
    return make_file_bytes(ONE_MB)


def upload_file(client, content: bytes, filename: str = "test.bin"):
    return client.post(
        "/files",
        files={"file": (filename, io.BytesIO(content), "application/octet-stream")},
    )


def upload_and_get_id(client, content: bytes, filename: str = "test.bin") -> str:
    resp = upload_file(client, content, filename)
    assert resp.status_code == 201, f"Upload failed: {resp.text}"
    return resp.json()["file_id"]
