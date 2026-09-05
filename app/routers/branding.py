from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.db import Session, crud, get_db
from app.db.models import Admin as DBAdmin, SystemBrandingSettings
from app.models.admin import Admin, BrandingResponse, BrandingUpdate, SystemBrandingResponse, SystemBrandingUpdate
from app.utils import admin_hierarchy, responses
from config import BRANDING_LOGO_DIRECTORY


router = APIRouter(tags=["Branding"], prefix="/api", responses={401: responses._401})
MAX_LOGO_BYTES = 1024 * 1024
ALLOWED_LOGOS = {
    "png": ("image/png", b"\x89PNG\r\n\x1a\n"),
    "jpg": ("image/jpeg", b"\xff\xd8\xff"),
    "webp": ("image/webp", b"RIFF"),
}


def _directory() -> Path:
    path = Path(BRANDING_LOGO_DIRECTORY).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _db_admin(db: Session, admin: Admin) -> DBAdmin:
    row = crud.get_admin(db, admin.username)
    if row is None:
        raise HTTPException(status_code=404, detail="Admin not found")
    return row


def _logo_type(payload: bytes) -> tuple[str, str] | None:
    if payload.startswith(ALLOWED_LOGOS["png"][1]):
        return "png", ALLOWED_LOGOS["png"][0]
    if payload.startswith(ALLOWED_LOGOS["jpg"][1]):
        return "jpg", ALLOWED_LOGOS["jpg"][0]
    if len(payload) >= 12 and payload.startswith(ALLOWED_LOGOS["webp"][1]) and payload[8:12] == b"WEBP":
        return "webp", ALLOWED_LOGOS["webp"][0]
    return None


def _system(db: Session) -> SystemBrandingSettings:
    row = db.get(SystemBrandingSettings, 1)
    if row is None:
        row = SystemBrandingSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _system_response(row: SystemBrandingSettings) -> SystemBrandingResponse:
    return SystemBrandingResponse(
        panel_name=row.panel_name,
        login_title=row.login_title,
        description=row.description,
        logo_url="/api/branding/system/logo" if row.logo_filename else None,
        favicon_url="/api/branding/system/favicon" if row.favicon_filename else None,
    )


def _require_owner(db: Session, actor: Admin) -> None:
    dbactor = _db_admin(db, actor)
    if not admin_hierarchy.is_owner(db, dbactor):
        raise HTTPException(status_code=403, detail={"code": "owner_required", "message": "Owner access required"})


@router.get("/branding/public", response_model=SystemBrandingResponse)
def public_branding(db: Session = Depends(get_db)):
    return _system_response(_system(db))


@router.put("/branding/system", response_model=SystemBrandingResponse)
def update_system_branding(
    values: SystemBrandingUpdate,
    db: Session = Depends(get_db),
    actor: Admin = Depends(Admin.get_current),
):
    _require_owner(db, actor)
    row = _system(db)
    row.panel_name = values.panel_name.strip()
    row.login_title = values.login_title.strip()
    row.description = values.description.strip() if values.description else None
    db.commit()
    return _system_response(row)


async def _upload_system_asset(kind: str, upload: UploadFile, db: Session, actor: Admin) -> SystemBrandingResponse:
    _require_owner(db, actor)
    payload = await upload.read(MAX_LOGO_BYTES + 1)
    detected = _logo_type(payload)
    if not payload or len(payload) > MAX_LOGO_BYTES or detected is None:
        raise HTTPException(status_code=422, detail={"code": "invalid_brand_asset", "message": "Use PNG, JPEG, or WebP up to 1 MiB"})
    extension, _ = detected
    row = _system(db)
    filename = f"system-{kind}.{extension}"
    directory = _directory()
    temporary = directory / f".{filename}.upload"
    temporary.write_bytes(payload)
    temporary.replace(directory / filename)
    previous = getattr(row, f"{kind}_filename")
    setattr(row, f"{kind}_filename", filename)
    db.commit()
    if previous and previous != filename:
        (directory / Path(previous).name).unlink(missing_ok=True)
    return _system_response(row)


@router.post("/branding/system/logo", response_model=SystemBrandingResponse)
async def upload_system_logo(
    logo: UploadFile = File(...),
    db: Session = Depends(get_db),
    actor: Admin = Depends(Admin.get_current),
):
    return await _upload_system_asset("logo", logo, db, actor)


@router.post("/branding/system/favicon", response_model=SystemBrandingResponse)
async def upload_system_favicon(
    favicon: UploadFile = File(...),
    db: Session = Depends(get_db),
    actor: Admin = Depends(Admin.get_current),
):
    return await _upload_system_asset("favicon", favicon, db, actor)


@router.get("/branding/system/{kind}", include_in_schema=False)
def system_brand_asset(kind: str, db: Session = Depends(get_db)):
    if kind not in {"logo", "favicon"}:
        raise HTTPException(status_code=404)
    row = _system(db)
    filename = getattr(row, f"{kind}_filename")
    target = _directory() / Path(filename).name if filename else None
    if target is None or not target.is_file():
        raise HTTPException(status_code=404)
    detected = _logo_type(target.read_bytes()[:16])
    if detected is None:
        raise HTTPException(status_code=404)
    return FileResponse(target, media_type=detected[1], headers={"Cache-Control": "public, max-age=300"})


@router.get("/branding", response_model=BrandingResponse)
def get_branding(
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    row = _db_admin(db, admin)
    return BrandingResponse(
        dashboard_theme=row.dashboard_theme or "heisenberg",
        logo_url=row.logo_url,
    )


@router.put("/branding", response_model=BrandingResponse)
def update_branding(
    values: BrandingUpdate,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    row = _db_admin(db, admin)
    row.dashboard_theme = values.dashboard_theme
    db.commit()
    db.refresh(row)
    return BrandingResponse(dashboard_theme=row.dashboard_theme, logo_url=row.logo_url)


@router.post("/branding/logo", response_model=BrandingResponse)
async def upload_logo(
    logo: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    payload = await logo.read(MAX_LOGO_BYTES + 1)
    if not payload or len(payload) > MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_logo_size", "message": "Logo must be at most 1 MiB"},
        )
    detected = _logo_type(payload)
    if detected is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_logo_type",
                "message": "Only PNG, JPEG, or WebP logos are accepted",
            },
        )
    extension, _ = detected
    row = _db_admin(db, admin)
    directory = _directory()
    filename = f"admin-{row.id}.{extension}"
    target = directory / filename
    temporary = directory / f".{filename}.upload"
    temporary.write_bytes(payload)
    temporary.replace(target)
    previous = row.logo_filename
    row.logo_filename = filename
    db.commit()
    if previous and previous != filename:
        previous_path = directory / Path(previous).name
        if previous_path.is_file():
            previous_path.unlink()
    return BrandingResponse(dashboard_theme=row.dashboard_theme, logo_url=row.logo_url)


@router.delete("/branding/logo", response_model=BrandingResponse)
def remove_logo(
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    row = _db_admin(db, admin)
    previous = row.logo_filename
    row.logo_filename = None
    db.commit()
    if previous:
        target = _directory() / Path(previous).name
        if target.is_file():
            target.unlink()
    return BrandingResponse(dashboard_theme=row.dashboard_theme, logo_url=None)


@router.get("/branding/logo/{admin_id}", include_in_schema=False)
def branding_logo(admin_id: int, db: Session = Depends(get_db)):
    row = db.get(DBAdmin, admin_id)
    if row is None or not row.logo_filename:
        raise HTTPException(status_code=404, detail="Logo not found")
    target = _directory() / Path(row.logo_filename).name
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Logo not found")
    detected = _logo_type(target.read_bytes()[:16])
    media_type = detected[1] if detected else "application/octet-stream"
    return FileResponse(target, media_type=media_type, headers={"Cache-Control": "public, max-age=300"})
