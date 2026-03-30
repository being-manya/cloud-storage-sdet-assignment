# Bug Reports — Cloud Storage Tiering System

---

## BUG-001: Cold-tier files are never promoted on access

**Severity:** High  
**Component:** `POST /admin/tiering/run` (tiering logic)

### Description

The problem statement specifies that recently accessed files should promote upward through tiers: Cold → Warm → Hot. However, the current `run_tiering()` function only handles **demotion** (Hot → Warm → Cold). When a COLD file is downloaded (which updates `last_accessed` to now), a subsequent tiering run does **not** promote it back to WARM or HOT.

### Steps to Reproduce

1. Upload a 1 MB file → starts in HOT.
2. Set `last_accessed` to 31 days ago → run tiering → file moves to WARM.
3. Set `last_accessed` to 91 days ago → run tiering → file moves to COLD.
4. Download the file (GET /files/{id}) → `last_accessed` updates to now.
5. Run tiering again (POST /admin/tiering/run).
6. Check metadata → file is **still COLD**.

### Expected Behaviour

After step 5, the file should be promoted to WARM (or HOT) because it was recently accessed.

### Actual Behaviour

The file remains in COLD. The tiering loop has an early `continue` for COLD files:

```python
if metadata.tier == StorageTier.COLD:
    continue  # Files in COLD tier don't move up automatically
```

### Suggested Fix

Add promotion logic: if a COLD file's `last_accessed` is recent (e.g., within the last 30 days), promote it to WARM. If a WARM file's `last_accessed` is recent, promote to HOT.

```python
# After the existing demotion logic, add:
if metadata.tier == StorageTier.COLD and days_since_access < 30:
    metadata.tier = StorageTier.WARM
    moved_count += 1
elif metadata.tier == StorageTier.WARM and days_since_access < 7:
    metadata.tier = StorageTier.HOT
    moved_count += 1
```

---

## BUG-002: `datetime.utcnow()` deprecated in Python 3.12+

**Severity:** Low (warning now, will break in future Python)  
**Component:** `src/storage_service.py` — multiple locations

### Description

The service uses `datetime.utcnow()` in at least four places (lines 86, 110, 140, 178, 212). Python 3.12 deprecates this method and emits `DeprecationWarning` on every call, generating 300+ warnings during a test run.

### Steps to Reproduce

1. Run `pytest tests/ -v` on Python 3.12+.
2. Observe `DeprecationWarning: datetime.datetime.utcnow() is deprecated`.

### Expected Behaviour

No deprecation warnings.

### Actual Behaviour

334 warnings in a single test run.

### Suggested Fix

Replace all occurrences:

```python
# Before
from datetime import datetime
now = datetime.utcnow()

# After
from datetime import datetime, timezone
now = datetime.now(timezone.utc)
```

---

## BUG-003: `is_priority()` and `is_legal_document()` check `file_id` but `apply_special_rules()` checks `filename`

**Severity:** Medium  
**Component:** `FileMetadata` model + `apply_special_rules()`

### Description

The `FileMetadata` model defines two helper methods:

- `is_priority()` — checks `self.file_id.upper()` for `_PRIORITY_`
- `is_legal_document()` — checks `self.file_id.upper()` for `LEGAL_` prefix

But the actual tiering logic in `apply_special_rules()` checks `file_metadata.filename.upper()` instead. This means:

1. The helper methods are dead code (never called anywhere).
2. There is an inconsistency: if someone relies on the model methods, they will get different results than the actual tiering behaviour.

Since `file_id` is a UUID (e.g., `a1b2c3d4-...`), the model methods will **always return False** — they can never match `_PRIORITY_` or `LEGAL_` in a UUID string.

### Steps to Reproduce

1. Upload a file named `LEGAL_contract.pdf`.
2. Call `files_metadata[fid].is_legal_document()` → returns `False` (checks UUID).
3. Run tiering → `apply_special_rules` correctly detects it via `filename`.

### Expected Behaviour

Model methods and tiering logic should agree on what constitutes a priority or legal file.

### Suggested Fix

Update the model methods to check `self.filename` instead of `self.file_id`, or remove the dead methods and keep the logic only in `apply_special_rules()`.

---

## BUG-004: No input validation on `days_ago` allows negative values (future timestamps)

**Severity:** Low  
**Component:** `POST /admin/files/{fileId}/update-last-accessed`

### Description

The `UpdateLastAccessedRequest` model accepts any integer for `days_ago`, including negative numbers. A negative value sets `last_accessed` to a **future** date, which could confuse the tiering logic (a file would never be eligible for demotion since it appears to have been accessed "in the future").

### Steps to Reproduce

1. Upload a file.
2. Call `POST /admin/files/{id}/update-last-accessed` with `{"days_ago": -100}`.
3. Check metadata → `last_accessed` is 100 days in the future.

### Expected Behaviour

The endpoint should reject negative values with a 400 error, or at minimum clamp to 0.

### Suggested Fix

Add a Pydantic validator:

```python
class UpdateLastAccessedRequest(BaseModel):
    days_ago: int = Field(..., ge=0)
```

---

## BUG-005: `parse_date()` function is defined but never used

**Severity:** Low  
**Component:** `src/storage_service.py`, lines 185-200

### Description

The function `parse_date()` contains date-parsing logic with special handling for pre-2023 dates, but it is never called anywhere in the codebase. This is dead code that could mislead future developers.

### Suggested Fix

Remove the function, or integrate it into an endpoint that accepts date strings from callers.
