import hashlib
from io import BytesIO
import json
from types import SimpleNamespace
import zipfile

import pytest

from app.utils.backup_upload import BackupUploadError, complete_upload
from app.utils.stage11_operations import validate_panel_backup


def archive_bytes():
    sql = b"CREATE TABLE example (id INT);"
    target = BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("database.sql", sql)
        archive.writestr("manifest.json", json.dumps({"format": "panel-backup-v1", "database_engine": "mysql", "files": {"database.sql": hashlib.sha256(sql).hexdigest()}}))
    return target.getvalue()


def upload(name, data):
    return SimpleNamespace(filename=name, file=BytesIO(data))


def parts(data, legacy=False):
    digest = hashlib.sha256(data).hexdigest()
    chunks = [data[:100], data[100:200], data[200:]]
    return [upload(f"backup.zip.part{i:03d}" + ("" if legacy else f"-of003-sha256-{digest}"), chunk) for i, chunk in enumerate(chunks, 1)]


@pytest.mark.parametrize("kind", ["complete", "parts", "legacy"])
def test_transport_converges_and_cleans(tmp_path, kind):
    data = archive_bytes()
    inputs = [upload("backup.zip", data)] if kind == "complete" else parts(data, legacy=kind == "legacy")[::-1]
    with complete_upload(inputs, temp_dir=tmp_path) as result:
        assert result.path.read_bytes() == data
        assert validate_panel_backup(result.path)["database_engine"] == "mysql"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("fault,code", [("missing", "backup_missing_part"), ("duplicate", "backup_duplicate_part"), ("mixed", "backup_mixed_sets"), ("tamper", "backup_checksum_mismatch"), ("number", "backup_part_number_invalid")])
def test_reject_parts(tmp_path, fault, code):
    inputs = parts(archive_bytes())
    if fault == "missing": inputs.pop(1)
    if fault == "duplicate": inputs[1] = inputs[0]
    if fault == "mixed": inputs[1].filename = inputs[1].filename.replace("backup.zip", "other.zip")
    if fault == "tamper": inputs[1].file = BytesIO(b"tampered")
    if fault == "number": inputs[0].filename = inputs[0].filename.replace("part001", "part000")
    with pytest.raises(BackupUploadError, match=code) as error:
        with complete_upload(inputs, temp_dir=tmp_path):
            pytest.fail("invalid set accepted")
    if fault == "missing": assert error.value.details == {"part": 2, "total": 3}
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", ["size", "archive", "restore", "read"])
def test_cleanup_on_every_failure(tmp_path, failure):
    data = archive_bytes() if failure != "archive" else b"invalid ZIP"
    item = upload("backup.zip", data)
    if failure == "read":
        item.file.read = lambda *_: (_ for _ in ()).throw(OSError("read failed"))
    with pytest.raises((ValueError, RuntimeError, OSError)):
        with complete_upload([item], max_bytes=1 if failure == "size" else 10000, temp_dir=tmp_path) as result:
            validate_panel_backup(result.path)
            if failure == "restore": raise RuntimeError("offline restore failed")
    assert not list(tmp_path.iterdir())


def test_legacy_missing_tail_rejected_by_canonical_validator(tmp_path):
    with pytest.raises(ValueError, match="backup_archive_invalid"):
        with complete_upload(parts(archive_bytes(), legacy=True)[:-1], temp_dir=tmp_path) as result:
            validate_panel_backup(result.path)
    assert not list(tmp_path.iterdir())


def test_http_upload_contract_and_owner_boundary(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers import backup as endpoint

    application = FastAPI()
    application.include_router(endpoint.router)
    application.dependency_overrides[endpoint.get_db] = lambda: None
    application.dependency_overrides[endpoint.Admin.get_current] = lambda: SimpleNamespace()
    monkeypatch.setattr(endpoint.admin_hierarchy, "is_owner", lambda *_: True)
    with TestClient(application) as client:
        data = archive_bytes()
        full = client.post('/api/owner/backups/validate', files={'backup': ('backup.zip', data)})
        assert full.status_code == 200
        split = client.post('/api/owner/backups/validate', files=[('backups', (p.filename, p.file.getvalue())) for p in parts(data)[::-1]])
        assert split.status_code == 200
        assert split.json()['validation_token'] == full.json()['validation_token']
        assert split.json()['part_count'] == 3
        missing = client.post('/api/owner/backups/validate', files=[('backups', (p.filename, p.file.getvalue())) for p in parts(data)[1:]])
        assert missing.status_code == 422
        assert missing.json()['detail'] == {'code': 'backup_missing_part', 'part': 1, 'total': 3}
        monkeypatch.setattr(endpoint.admin_hierarchy, "is_owner", lambda *_: False)
        assert client.post('/api/owner/backups/validate', files={'backup': ('backup.zip', data)}).status_code == 403
