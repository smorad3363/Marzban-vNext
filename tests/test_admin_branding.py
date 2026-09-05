import asyncio
from io import BytesIO

import pytest
import sqlalchemy as sa
from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Admin
from app.models.admin import Admin as APIAdmin, BrandingUpdate, SystemBrandingUpdate
from app.routers import branding


def test_admin_branding_theme_logo_upload_and_reset(tmp_path, monkeypatch):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'branding.sqlite3'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    row = Admin(username="brand-admin", hashed_password="x")
    other = Admin(username="other-admin", hashed_password="x")
    db.add_all([row, other])
    db.commit()
    actor = APIAdmin(username=row.username, is_sudo=False)
    monkeypatch.setattr(branding, "BRANDING_LOGO_DIRECTORY", str(tmp_path / "logos"))

    changed = branding.update_branding(BrandingUpdate(dashboard_theme="black_gold"), db, actor)
    assert changed.dashboard_theme == "black_gold"
    assert other.dashboard_theme == "heisenberg"

    uploaded = asyncio.run(
        branding.upload_logo(
            UploadFile(filename="logo.png", file=BytesIO(b"\x89PNG\r\n\x1a\ncontent")),
            db,
            actor,
        )
    )
    assert uploaded.logo_url == f"/api/branding/logo/{row.id}"
    assert (tmp_path / "logos" / f"admin-{row.id}.png").is_file()
    assert other.logo_filename is None

    removed = branding.remove_logo(db, actor)
    assert removed.logo_url is None
    assert not (tmp_path / "logos" / f"admin-{row.id}.png").exists()
    db.close()
    engine.dispose()


def test_admin_branding_rejects_non_image(tmp_path, monkeypatch):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'branding-invalid.sqlite3'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    row = Admin(username="brand-invalid", hashed_password="x")
    db.add(row)
    db.commit()
    monkeypatch.setattr(branding, "BRANDING_LOGO_DIRECTORY", str(tmp_path / "logos"))
    actor = APIAdmin(username=row.username, is_sudo=False)
    try:
        asyncio.run(
            branding.upload_logo(
                UploadFile(filename="logo.svg", file=BytesIO(b"<svg></svg>")),
                db,
                actor,
            )
        )
        raise AssertionError("invalid logo was accepted")
    except HTTPException as exc:
        assert exc.status_code == 422
        assert exc.detail["code"] == "invalid_logo_type"
    finally:
        db.close()
        engine.dispose()


def test_owner_can_update_public_system_branding_and_upload_assets(tmp_path, monkeypatch):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'system-branding.sqlite3'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    owner = Admin(username="owner", hashed_password="x", is_sudo=True)
    db.add(owner)
    db.commit()
    actor = APIAdmin(username=owner.username, is_sudo=True)
    monkeypatch.setattr(branding, "BRANDING_LOGO_DIRECTORY", str(tmp_path / "logos"))

    changed = branding.update_system_branding(
        SystemBrandingUpdate(panel_name="Northstar", login_title="Operator access", description="Private control plane"),
        db,
        actor,
    )
    assert changed.panel_name == "Northstar"
    assert branding.public_branding(db).login_title == "Operator access"

    uploaded = asyncio.run(branding.upload_system_logo(
        UploadFile(filename="logo.png", file=BytesIO(b"\x89PNG\r\n\x1a\ncontent")),
        db,
        actor,
    ))
    assert uploaded.logo_url == "/api/branding/system/logo"
    assert (tmp_path / "logos" / "system-logo.png").is_file()
    db.close()
    engine.dispose()


def test_non_owner_cannot_update_system_branding(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'system-branding-denied.sqlite3'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    admin = Admin(username="admin", hashed_password="x", is_sudo=False)
    db.add(admin)
    db.commit()

    with pytest.raises(HTTPException) as denied:
        branding.update_system_branding(
            SystemBrandingUpdate(panel_name="Denied", login_title="Denied", description=None),
            db,
            APIAdmin(username=admin.username, is_sudo=False),
        )
    assert denied.value.status_code == 403
    db.close()
    engine.dispose()
