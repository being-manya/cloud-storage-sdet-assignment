# Test Strategy — Cloud Storage Tiering System

## 1. Scope and Objectives

This document describes the testing approach for Lucidity's Cloud Storage Tiering Service, a FastAPI application that manages automatic file placement across Hot (SSD), Warm (HDD), and Cold (Object Storage) tiers based on access patterns.

**Goals:**

- Validate all six API endpoints behave as specified.
- Confirm tier-transition logic matches the documented rules (30-day Hot→Warm, 90-day Warm→Cold, promotion on access).
- Uncover boundary, concurrency, and fault-tolerance issues.
- Provide a repeatable, CI-friendly test suite.

## 2. Test Levels

| Level | Purpose | Location |
|-------|---------|----------|
| **Unit / Functional** | Verify each endpoint in isolation: correct status codes, response bodies, error handling. | `tests/functional/` |
| **Integration** | Verify multi-step workflows: upload → age → tier → verify metadata → stats. | `tests/functional/test_tiering.py` |
| **Performance** | Concurrent access, bulk operations, tiering throughput benchmarks. | `tests/performance/` |
| **Fault Injection** | Malformed requests, rapid repeated ops, race conditions, negative inputs. | `tests/fault_injection/` |

## 3. Functional Scenarios

### 3.1 File Operations

| Scenario | Endpoint | Expected |
|----------|----------|----------|
| Upload valid 1 MB file | POST /files | 201, tier=HOT, file_id returned |
| Upload 0-byte file | POST /files | 400 — below minimum |
| Upload file at 1 MB - 1 byte | POST /files | 400 — boundary reject |
| Upload file at exactly 1 MB | POST /files | 201 — boundary accept |
| Upload file > 10 GB | POST /files | 400 — exceeds maximum |
| Download existing file | GET /files/{id} | 200, correct content |
| Download missing file | GET /files/{id} | 404 |
| Get metadata | GET /files/{id}/metadata | 200, all fields present |
| Delete existing file | DELETE /files/{id} | 204 |
| Delete already-deleted file | DELETE /files/{id} | 404 |
| Download after delete | GET /files/{id} | 404 |

### 3.2 Tier Transitions

| Scenario | Setup | Expected Tier |
|----------|-------|---------------|
| Fresh upload | — | HOT |
| 29 days idle | last_accessed = 29d ago | HOT (no change) |
| 30 days idle | last_accessed = 30d ago | WARM |
| 89 days idle (WARM) | already WARM, 89d ago | WARM |
| 90 days idle (WARM) | already WARM, 90d ago | COLD |
| 365 days idle (COLD) | already COLD | COLD (no further demotion) |
| PRIORITY file, 60d idle | filename contains `_PRIORITY_` | HOT |
| LEGAL_ file, 100d in WARM | filename starts with `LEGAL_` | WARM |
| LEGAL_ file, 181d in WARM | filename starts with `LEGAL_` | COLD |
| Cold file accessed then tiered | download + run tiering | Should promote (see Bug #1) |

### 3.3 Admin Endpoints

| Scenario | Endpoint | Expected |
|----------|----------|----------|
| Stats on empty storage | GET /admin/stats | total_files=0 |
| Stats after N uploads | GET /admin/stats | Correct counts and sizes |
| Stats after delete | GET /admin/stats | Counts decrease |
| Tiering run, no eligible files | POST /admin/tiering/run | files_moved=0 |
| Tiering run, N eligible files | POST /admin/tiering/run | files_moved=N |

## 4. Edge Cases and Boundary Conditions

- **0-byte file upload** — must reject.
- **File at exactly 1 MB** — minimum boundary; must accept.
- **File at 1 MB - 1 byte** — must reject.
- **Empty filename** — should handle gracefully (not 500).
- **Special characters in filename** — spaces, parentheses, unicode.
- **SQL-injection-style file_id** — must return 404, not crash.
- **Negative `days_ago`** — sets future timestamp; should not crash.
- **Double delete** — second call returns 404.
- **Tiering on empty storage** — should return success with 0 moved.
- **Rapid repeated downloads** (100×) — must remain stable.
- **Idempotent tiering** — running twice should not re-move files.

## 5. Performance and Reliability Considerations

- **Concurrent uploads**: 10 parallel threads uploading simultaneously must all succeed.
- **Concurrent downloads**: 10 threads downloading the same file must all get 200.
- **Upload/delete race**: simultaneous download + delete on the same file must not produce a 500.
- **Bulk upload (20 files)**: all should succeed; stats should reflect the count.
- **Bulk delete (10 files)**: all should return 204; stats should reset.
- **Tiering throughput**: 50 files with 25 eligible should complete in under 2 seconds.

## 6. Security Considerations

- **Input validation**: The service validates file size boundaries (1 MB–10 GB). Malformed multipart bodies return 422.
- **Path traversal / injection**: File IDs are UUIDs; arbitrary strings in the path return 404.
- **No authentication layer**: The mock service has no auth — noted as an area for future hardening.
- **In-memory storage**: No persistence, so no data-at-rest concerns for this mock, but a real implementation would need encryption.

## 7. Test Data Strategy

- Tests generate payloads programmatically via `make_file_bytes(size)` — no external test fixtures needed.
- `autouse` fixture clears in-memory storage before and after every test for full isolation.
- Mocked timestamps via `POST /admin/files/{id}/update-last-accessed` let us simulate time passage without `sleep()` or `freezegun`.

## 8. Tools and Framework

| Tool | Role |
|------|------|
| **pytest** | Test runner and assertion library |
| **starlette TestClient** | Synchronous HTTP client for FastAPI |
| **pytest-cov** | Code coverage reporting |
| **pytest-xdist** | Parallel test execution |
| **pytest-benchmark** | Performance benchmarking |
| **GitHub Actions** | CI pipeline for automated test runs |

## 9. CI/CD Integration

A GitHub Actions workflow (`.github/workflows/tests.yml`) runs the full suite on every push and PR against `main`. It:

1. Installs dependencies.
2. Runs `pytest tests/ --cov=src --cov-report=xml`.
3. Uploads coverage to Codecov.
4. Runs performance benchmarks as a separate job.

## 10. Risk Areas

1. **No tier promotion logic** — the biggest functional gap (Bug #1).
2. **`datetime.utcnow()` deprecation** — Python 3.12+ emits warnings; should migrate to `datetime.now(UTC)`.
3. **No thread safety** — in-memory dicts are not protected by locks; concurrent writes could corrupt state under real load.
4. **No pagination** — `GET /admin/stats` iterates all files; will degrade with large datasets.
