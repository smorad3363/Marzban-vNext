from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.models.user import BulkUserOperation


class BulkTargetScope(str, Enum):
    ALL_USERS = "ALL_USERS"
    SELECTED_ADMINS_DIRECT = "SELECTED_ADMINS_DIRECT"
    SELECTED_ADMINS_SUBTREE = "SELECTED_ADMINS_SUBTREE"


class BulkAdminOperation(str, Enum):
    GRANT_CREDIT = "grant_credit"
    RECLAIM_CREDIT = "reclaim_credit"


class BulkUserScopeRequest(BaseModel):
    target_scope: BulkTargetScope
    selected_admin_ids: list[int] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_explicit_scope(self):
        self.selected_admin_ids = sorted(set(self.selected_admin_ids))
        if self.target_scope == BulkTargetScope.ALL_USERS:
            if self.selected_admin_ids:
                raise ValueError("ALL_USERS does not accept selected_admin_ids")
        elif not self.selected_admin_ids:
            raise ValueError("Selected-Admin scopes require selected_admin_ids")
        return self


class BulkUserPreviewRequest(BulkUserScopeRequest):
    pass


class BulkUserJobCreateRequest(BulkUserScopeRequest):
    operation_id: str = Field(min_length=8, max_length=96)
    operation: BulkUserOperation
    data_amount: Optional[int] = Field(default=None, ge=1)
    days_amount: Optional[int] = Field(default=None, ge=1, le=3650)

    @model_validator(mode="after")
    def validate_operation_amounts(self):
        data_operations = {
            BulkUserOperation.add_data,
            BulkUserOperation.subtract_data,
            BulkUserOperation.add_data_and_days,
        }
        day_operations = {
            BulkUserOperation.add_days,
            BulkUserOperation.subtract_days,
            BulkUserOperation.add_data_and_days,
        }
        if self.operation in data_operations and self.data_amount is None:
            raise ValueError("data_amount is required for this bulk operation")
        if self.operation in day_operations and self.days_amount is None:
            raise ValueError("days_amount is required for this bulk operation")
        if self.operation not in data_operations:
            self.data_amount = None
        if self.operation not in day_operations:
            self.days_amount = None
        return self


class BulkAdminPreviewRequest(BaseModel):
    selected_admin_ids: list[int] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def normalize_ids(self):
        self.selected_admin_ids = sorted(set(self.selected_admin_ids))
        return self


class BulkAdminJobCreateRequest(BulkAdminPreviewRequest):
    operation_id: str = Field(min_length=8, max_length=96)
    operation: BulkAdminOperation
    amount: int = Field(ge=1)
    note: Optional[str] = Field(default=None, max_length=512)


class BulkJobExecuteRequest(BaseModel):
    chunk_size: int = Field(default=100, ge=1, le=500)
    retry_failed: bool = False


class BulkPreviewResponse(BaseModel):
    target_scope: str
    selected_admin_ids: list[int]
    resolved_target_count: int
    sample_targets: list[str] = Field(default_factory=list)


class BulkTargetResultResponse(BaseModel):
    target_type: Literal["USER", "ADMIN"]
    target_id: int
    target_username: str
    owner_admin_id: Optional[int] = None
    status: Literal["PENDING", "SUCCESS", "FAILED", "SKIPPED"]
    attempts: int
    retryable: bool
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    result_details: Optional[dict] = None
    completed_at: Optional[datetime] = None


class BulkJobResponse(BaseModel):
    operation_id: str
    job_kind: str
    operation: str
    target_scope: Optional[str] = None
    selected_admin_ids: list[int] = Field(default_factory=list)
    status: str
    total: int
    success: int
    failed: int
    skipped: int
    pending: int
    has_more: bool
    report_has_more: bool = False
    next_target_cursor: Optional[int] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    targets: list[BulkTargetResultResponse] = Field(default_factory=list)


class BulkSelectedAction(BaseModel):
    operation: BulkUserOperation
    amount: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_amount(self):
        if self.operation in {
            BulkUserOperation.add_data,
            BulkUserOperation.subtract_data,
            BulkUserOperation.add_days,
            BulkUserOperation.subtract_days,
        } and self.amount is None:
            raise ValueError("Selected operation requires amount")
        return self


class BulkSelectionRequest(BaseModel):
    user_ids: list[int] = Field(min_length=1, max_length=500)
    actions: list[BulkSelectedAction] = Field(min_length=1, max_length=3)
    operation_id: str = Field(min_length=8, max_length=96)

    @model_validator(mode="after")
    def validate_compatibility(self):
        self.user_ids = sorted(set(self.user_ids))
        operations = [action.operation for action in self.actions]
        if len(operations) != len(set(operations)):
            raise ValueError("Duplicate bulk action")
        if BulkUserOperation.delete in operations and len(operations) > 1:
            raise ValueError("Delete cannot be combined with another action")
        if BulkUserOperation.activate in operations and BulkUserOperation.deactivate in operations:
            raise ValueError("Activate and deactivate are incompatible")
        if BulkUserOperation.add_data in operations and BulkUserOperation.subtract_data in operations:
            raise ValueError("Add and subtract traffic are incompatible")
        if BulkUserOperation.add_days in operations and BulkUserOperation.subtract_days in operations:
            raise ValueError("Add and subtract duration are incompatible")
        if BulkUserOperation.add_data_and_days in operations:
            raise ValueError("Use separate add_data and add_days actions")
        return self


class BulkSelectionPreview(BaseModel):
    user_count: int
    traffic_change: int = 0
    duration_change_days: int = 0
    status_change: Optional[str] = None
    cost_toman: int = 0
    usernames: list[str] = Field(default_factory=list)


class BulkSelectionResult(BaseModel):
    user_id: int
    username: str
    status: Literal["SUCCESS", "FAILED"]
    reason: Optional[str] = None


class BulkSelectionResponse(BaseModel):
    operation_id: str
    success: int
    failed: int
    results: list[BulkSelectionResult]
