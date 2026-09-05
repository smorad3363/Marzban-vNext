from typing import Literal, Optional

from pydantic import BaseModel, Field


BackupDestination = Literal["LOCAL", "TELEGRAM", "EMAIL", "TELEGRAM_EMAIL"]
BackupSchedule = Literal["15m", "30m", "1h", "3h", "6h", "12h", "24h"]


class BackupSettingsUpdate(BaseModel):
    enabled: bool = False
    destination: BackupDestination = "LOCAL"
    schedule: BackupSchedule = "24h"
    retention_count: int = Field(default=14, ge=1, le=365)
    telegram_bot_token: Optional[str] = Field(default=None, max_length=256)
    telegram_chat_id: Optional[str] = Field(default=None, max_length=64)
    smtp_host: Optional[str] = Field(default=None, max_length=256)
    smtp_port: Optional[int] = Field(default=None, ge=1, le=65535)
    smtp_username: Optional[str] = Field(default=None, max_length=256)
    smtp_password: Optional[str] = Field(default=None, max_length=256)
    smtp_use_tls: bool = True
    email_from: Optional[str] = Field(default=None, max_length=320)
    email_to: Optional[str] = Field(default=None, max_length=320)

class BackupSettingsResponse(BaseModel):
    enabled: bool
    destination: BackupDestination
    schedule: BackupSchedule
    retention_count: int
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True
    email_from: Optional[str] = None
    email_to: Optional[str] = None
    telegram_configured: bool = False
    smtp_configured: bool = False


class BackupArtifactResponse(BaseModel):
    id: int
    period_key: str
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    generation_status: str
    delivery_status: str
    error_code: Optional[str] = None


class BackupValidationResponse(BaseModel):
    valid: bool
    manifest: dict
    validation_token: str
