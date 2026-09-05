"""Normalize upload transports; archive validation remains in stage11_operations."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from tempfile import TemporaryDirectory


MAX_UPLOAD_BYTES = 2 * 1024**3
MAX_PARTS = 128
PART = re.compile(r"(?P<name>[^/\\]+\.zip)\.part(?P<number>[0-9]{3,6})(?:-of(?P<total>[0-9]{3,6})-sha256-(?P<sha>[a-f0-9]{64}))?\Z")


class BackupUploadError(ValueError):
    def __init__(self, code: str, **details):
        super().__init__(code)
        self.code = code
        self.details = details


@dataclass(frozen=True)
class CompleteBackup:
    path: Path
    size_bytes: int
    part_count: int
    sha256: str


@contextmanager
def complete_upload(uploads, *, max_bytes=MAX_UPLOAD_BYTES, temp_dir=None):
    """Yield one temporary archive. Never derive a filesystem path from a filename.

    Legacy .part001 sets have no count/hash metadata. Contiguity is checked here;
    the canonical ZIP and member checksums must subsequently verify completeness.
    """
    if not uploads or len(uploads) > MAX_PARTS:
        raise BackupUploadError("backup_file_count_invalid")
    names = [item.filename or "" for item in uploads]
    matches = [PART.fullmatch(name) for name in names]
    expected_hash = None
    if any(matches):
        if not all(matches):
            raise BackupUploadError("backup_mixed_sets")
        identities = {(m["name"], m["total"], m["sha"]) for m in matches}
        if len(identities) != 1:
            raise BackupUploadError("backup_mixed_sets")
        numbers = [int(m["number"]) for m in matches]
        if len(set(numbers)) != len(numbers):
            raise BackupUploadError("backup_duplicate_part")
        total = int(matches[0]["total"]) if matches[0]["total"] else max(numbers)
        if not 1 <= total <= MAX_PARTS or any(n < 1 or n > total for n in numbers):
            raise BackupUploadError("backup_part_number_invalid")
        missing = sorted(set(range(1, total + 1)) - set(numbers))
        if missing:
            raise BackupUploadError("backup_missing_part", part=missing[0], total=total)
        uploads = [upload for _, upload in sorted(zip(numbers, uploads), key=lambda pair: pair[0])]
        expected_hash = matches[0]["sha"]
    elif len(uploads) != 1 or not names[0].lower().endswith(".zip"):
        raise BackupUploadError("backup_file_type_invalid")
    with TemporaryDirectory(prefix="panel-upload-", dir=temp_dir) as directory:
        path = Path(directory) / "complete.zip"
        size = 0
        digest = hashlib.sha256()
        with path.open("xb") as target:
            path.chmod(0o600)
            for upload in uploads:
                part_size = 0
                while chunk := upload.file.read(1024**2):
                    size += len(chunk)
                    part_size += len(chunk)
                    if size > max_bytes:
                        raise BackupUploadError("backup_too_large")
                    digest.update(chunk)
                    target.write(chunk)
                if not part_size:
                    raise BackupUploadError("backup_archive_empty")
        actual_hash = digest.hexdigest()
        if expected_hash and actual_hash != expected_hash:
            raise BackupUploadError("backup_checksum_mismatch")
        yield CompleteBackup(path, size, len(uploads), actual_hash)
