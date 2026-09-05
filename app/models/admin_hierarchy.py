from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.utils.admin_billing import BillingMode


AdminRoleCode = Literal["OWNER", "ADMIN"]


class HierarchyAdminNode(BaseModel):
    id: int
    username: str
    role: AdminRoleCode
    parent_admin_id: Optional[int] = None
    depth: int = 0
    external_api_enabled: bool = False
    account_status: Literal["ACTIVE", "SUSPENDED", "DISABLED"] = "ACTIVE"
    total_traffic: Optional[int] = None
    delegated_traffic: int = 0
    own_spend: int = 0
    available_traffic: Optional[int] = None
    renewal_enabled: bool = True
    renewal_remaining: Optional[int] = None
    trial_quota: int = 0
    trials_used: int = 0
    referral_referrer_admin_id: Optional[int] = None
    referral_rate_bps: Optional[int] = None
    active_owner_freeze_event_id: Optional[int] = None
    billing_mode: BillingMode = BillingMode.LEGACY_COMPAT
    can_create_admins: bool = False
    can_delegate_admin_creation: bool = False
    can_create_allocated_children: bool = True
    admin_creation_limit: Optional[int] = None
    admin_creations_used: int = 0
    delegated_admin_creation_limit: int = 0
    admin_creation_remaining: Optional[int] = None
    children: list["HierarchyAdminNode"] = Field(default_factory=list)


class HierarchyChildCreate(BaseModel):
    username: str = Field(min_length=3, max_length=34)
    password: str = Field(min_length=6, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=32)
    role: Literal["ADMIN"] = "ADMIN"
    billing_mode: BillingMode
    initial_credit: Optional[int] = Field(default=None, ge=1)
    user_creation_mode: Literal["FORM_ONLY", "PLAN_ONLY", "BOTH", "FREE_FORM"] = "PLAN_ONLY"
    can_manage_plans: bool = False
    can_create_admins: bool = False
    can_delegate_admin_creation: bool = False
    can_create_allocated_children: bool = True
    admin_creation_limit: Optional[int] = Field(default=0, ge=0)


class ReparentRequest(BaseModel):
    parent_username: str


class CreditTransferRequest(BaseModel):
    amount: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=128)
    note: Optional[str] = Field(default=None, max_length=512)


class CreditTransferResponse(BaseModel):
    id: int
    from_admin_id: Optional[int]
    to_admin_id: Optional[int]
    actor_admin_id: int
    adjusted_admin_id: Optional[int] = None
    resource: Optional[str] = None
    amount: int
    delta: Optional[int] = None
    balance_before: Optional[int] = None
    balance_after: Optional[int] = None
    source_delegated_before: Optional[int] = None
    source_delegated_after: Optional[int] = None
    operation_type: str
    idempotency_key: str
    created_at: datetime
    note: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class ExternalApiPolicy(BaseModel):
    enabled: bool


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=96)
    scopes: set[str] = Field(min_length=1)
    expires_at: datetime


class ApiTokenCreated(BaseModel):
    id: int
    name: str
    scopes: list[str]
    expires_at: datetime
    token: str


class ApiTokenSummary(BaseModel):
    id: int
    name: str
    scopes: list[str]
    expires_at: datetime
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class RenewalPolicyUpdate(BaseModel):
    enabled: bool = True
    remaining: Optional[int] = Field(default=None, ge=0)


class UserCreationModeUpdate(BaseModel):
    mode: Literal["FORM_ONLY", "PLAN_ONLY", "BOTH", "FREE_FORM"]
    can_manage_plans: bool = False


class BillingModeUpdate(BaseModel):
    mode: BillingMode
    idempotency_key: str = Field(min_length=8, max_length=128)
    reason: str = Field(min_length=1, max_length=512)


class AllocatedTrafficRefundCreate(BaseModel):
    requested_refund_amount: int = Field(gt=0)
    request_reason: str = Field(min_length=1, max_length=512)
    request_note: Optional[str] = Field(default=None, max_length=1024)
    correlation_id: str = Field(min_length=8, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=128)


class AllocatedTrafficRefundDecision(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    explanation: Optional[str] = Field(default=None, max_length=1024)


class AllocatedTrafficRefundResponse(BaseModel):
    id: int
    requester_admin_id: int
    account_admin_id: int
    reviewer_admin_id: int
    target_user_id: int
    target_username: str
    snapshot_billing_mode: str
    snapshot_plan_id: Optional[int] = None
    snapshot_plan_version_id: Optional[int] = None
    snapshot_plan_name: Optional[str] = None
    snapshot_allocated_quota: int
    snapshot_current_quota: int
    snapshot_used_traffic: int
    snapshot_remaining_traffic: int
    snapshot_user_created_at: Optional[datetime] = None
    snapshot_user_expire_at: Optional[datetime] = None
    snapshot_pre_delete_status: str
    requested_refund_amount: int
    request_reason: str
    request_note: Optional[str] = None
    correlation_id: str
    idempotency_key: str
    status: Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED"]
    requested_at: datetime
    decided_at: Optional[datetime] = None
    decided_by_admin_id: Optional[int] = None
    decision_explanation: Optional[str] = None
    ledger_transfer_id: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)


class AllocatedTrafficRefundEventResponse(BaseModel):
    id: int
    request_id: int
    actor_admin_id: int
    from_status: Optional[str] = None
    to_status: Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED"]
    explanation: Optional[str] = None
    operation_key: str
    correlation_id: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SuspendRequest(BaseModel):
    reason_id: int = Field(default=1, ge=1)
    include_subtree: bool = True


class OwnerFreezeRequest(BaseModel):
    reason_id: int = Field(default=1, ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128)
    note: str = Field(min_length=1, max_length=512)

    @field_validator("note")
    @classmethod
    def normalize_freeze_note(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Freeze reason is required")
        return value


class OwnerUnfreezeRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)


class ReferralAttributionUpdate(BaseModel):
    referrer_username: str = Field(min_length=3, max_length=34)
    rate_bps: int = Field(ge=0, le=10_000)
    idempotency_key: str = Field(min_length=8, max_length=128)
    note: Optional[str] = Field(default=None, max_length=512)


class ReferralAttributionRemove(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    note: Optional[str] = Field(default=None, max_length=512)


class ReferralAttributionResponse(BaseModel):
    referred_admin_id: int
    referred_username: str
    referrer_admin_id: Optional[int] = None
    referrer_username: Optional[str] = None
    rate_bps: Optional[int] = None
    last_event_id: Optional[int] = None
    replayed: bool = False


class BulkDisableRequest(BaseModel):
    include_subtree: bool = False
    idempotency_key: str = Field(min_length=8, max_length=128)
    batch_size: int = Field(default=500, ge=1, le=2000)


class AccountSummary(BaseModel):
    username: str
    user_namespace_prefix: str
    role: AdminRoleCode
    account_status: Literal["ACTIVE", "SUSPENDED", "DISABLED"]
    suspended_reason: Optional[str] = None
    suspended_at: Optional[datetime] = None
    own_users: int = 0
    subtree_users: int = 0
    total_traffic: Optional[int] = None
    delegated_traffic: int = 0
    own_spend: int = 0
    available_traffic: Optional[int] = None
    renewal_enabled: bool = True
    renewal_remaining: Optional[int] = None
    billing_mode: BillingMode = BillingMode.LEGACY_COMPAT
    money_billing_enabled: bool = False
    money_balance_toman: int = 0
    used_traffic_price_per_gib_toman: Optional[int] = None
    user_creation_mode: Literal["FORM_ONLY", "PLAN_ONLY", "BOTH", "FREE_FORM"] = "PLAN_ONLY"
    can_manage_plans: bool = False
    trial_quota: int = 0
    trials_used: int = 0
    can_create_admins: bool = False
    can_delegate_admin_creation: bool = False
    can_create_allocated_children: bool = True
    admin_creation_limit: Optional[int] = None
    admin_creations_used: int = 0
    delegated_admin_creation_limit: int = 0
    admin_creation_remaining: Optional[int] = None


class PlanCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)


class PlanCategoryUpdate(PlanCategoryCreate):
    pass


class PlanCategoryResponse(BaseModel):
    id: int
    owner_admin_id: int
    name: str
    description: Optional[str] = None
    archived_at: Optional[datetime] = None
    plan_count: int = 0


class PlanVersionInput(BaseModel):
    price_toman: int = Field(default=0, ge=0)
    data_limit: int = Field(ge=0)
    duration_days: int = Field(ge=1, le=3650)
    concurrent_user_limit: Optional[int] = Field(default=None, ge=1)
    reset_strategy: Literal["no_reset", "day", "week", "month", "year"] = "no_reset"
    renewal_volume_strategy: Literal["replace"] = "replace"
    renewal_time_strategy: Literal["extend_max"] = "extend_max"
    # Deprecated migration-only compatibility. New network access belongs to AccessGroup.
    inbounds: list[str] = Field(default_factory=list)
    hosts: dict[str, list[int]] = Field(default_factory=dict)

    @field_validator("inbounds")
    @classmethod
    def normalize_inbounds(cls, value: list[str]) -> list[str]:
        return sorted({item.strip() for item in value if item.strip()})

    @field_validator("hosts")
    @classmethod
    def normalize_hosts(cls, value: dict[str, list[int]]) -> dict[str, list[int]]:
        return {
            tag.strip(): sorted({int(host_id) for host_id in host_ids if int(host_id) > 0})
            for tag, host_ids in value.items()
            if tag.strip()
        }

    @model_validator(mode="after")
    def require_explicit_network_scope(self):
        if not self.inbounds and not self.hosts:
            return self
        if not self.inbounds:
            raise ValueError("Legacy Plan network scope requires at least one allowed inbound")
        if set(self.hosts) != set(self.inbounds):
            raise ValueError("Plan host scope must exactly match selected inbounds")
        if any(not self.hosts[tag] for tag in self.inbounds):
            raise ValueError("Every selected inbound requires at least one host")
        return self


class PlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    category_id: Optional[int] = None
    version: PlanVersionInput
    allowed_admin_ids: list[int] = Field(default_factory=list)
    include_subtree: bool = False
    is_trial: bool = False


class PlanUpdate(BaseModel):
    description: Optional[str] = Field(default=None, max_length=512)
    category_id: Optional[int] = None
    version: PlanVersionInput
    allowed_admin_ids: list[int] = Field(default_factory=list)
    include_subtree: bool = False


class PlanVersionResponse(BaseModel):
    price_toman: int
    data_limit: int
    duration_days: int
    concurrent_user_limit: Optional[int]
    reset_strategy: Literal["no_reset", "day", "week", "month", "year"]
    renewal_volume_strategy: Literal["replace"]
    renewal_time_strategy: Literal["extend_max"]
    inbounds: list[str]
    hosts: dict[str, list[int]]


class PlanResponse(BaseModel):
    id: int
    owner_admin_id: int
    name: str
    description: Optional[str]
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    current_version_id: int
    version_number: int
    archived_at: Optional[datetime]
    version: PlanVersionResponse
    allowed_admin_ids: list[int]
    include_subtree: bool
    is_trial: bool
    effective_price_toman: int = 0
    base_price_toman: Optional[int] = None


class PlanSummary(BaseModel):
    id: int
    name: str
    data_limit: int
    duration_days: int
    price_toman: int
    concurrent_user_limit: Optional[int]


class AdminPlanPriceInput(BaseModel):
    plan_id: int = Field(gt=0)
    price_toman: int = Field(ge=0)


class MoneyTransferRequest(BaseModel):
    amount_toman: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=128)
    note: Optional[str] = Field(default=None, max_length=512)


class MoneyTransferResponse(BaseModel):
    operation_key: str
    source_balance_toman: Optional[int] = None
    target_balance_toman: int
    replayed: bool = False


class DurationPresetInput(BaseModel):
    duration_days: int = Field(ge=1, le=3650)
    multiplier: float = Field(gt=0, le=100)
    enabled: bool = True


class OwnerPricingUpdate(BaseModel):
    price_per_gib_toman: int = Field(ge=0)
    allow_unlimited_duration: bool = False
    duration_presets: list[DurationPresetInput] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_durations(self):
        days = [item.duration_days for item in self.duration_presets]
        if len(days) != len(set(days)):
            raise ValueError("Duration presets must be unique")
        return self


class OwnerPricingResponse(OwnerPricingUpdate):
    pass


class PlanNetworkHostOption(BaseModel):
    id: int
    remark: str


class PlanNetworkOption(BaseModel):
    tag: str
    protocol: str
    network: str
    tls: str
    port: Optional[int] = None
    hosts: list[PlanNetworkHostOption]


class PlanUserCreate(BaseModel):
    username: str
    plan_id: int
    access_group_id: Optional[int] = Field(default=None, gt=0)
    status: Literal["active", "on_hold"] = "active"
    note: Optional[str] = Field(default=None, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=128)


class PlanRenewRequest(BaseModel):
    plan_id: int
    access_group_id: Optional[int] = Field(default=None, gt=0)
    idempotency_key: str = Field(min_length=8, max_length=128)


class AccessGroupInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    node_ids: list[int] = Field(default_factory=list)
    inbounds: list[str] = Field(min_length=1)
    hosts: dict[str, list[int]]

    @field_validator("node_ids", "inbounds")
    @classmethod
    def unique_sorted_values(cls, value):
        return sorted(set(value))

    @model_validator(mode="after")
    def require_explicit_hosts(self):
        if set(self.hosts) != set(self.inbounds) or any(not self.hosts[tag] for tag in self.inbounds):
            raise ValueError("Every Access Group inbound requires at least one explicit host")
        self.hosts = {tag: sorted(set(ids)) for tag, ids in self.hosts.items()}
        return self


class AccessGroupResponse(BaseModel):
    id: int
    owner_admin_id: int
    name: str
    description: Optional[str]
    node_ids: list[int]
    inbounds: list[str]
    hosts: dict[str, list[int]]
    archived_at: Optional[datetime]
    active_user_count: int = 0


class TrialQuotaAdjustmentRequest(BaseModel):
    amount: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=128)
    note: Optional[str] = Field(default=None, max_length=512)


class TrialQuotaResetRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    note: Optional[str] = Field(default=None, max_length=512)


class TrialCleanupRequest(BaseModel):
    expired_before: datetime
    idempotency_key: str = Field(min_length=8, max_length=128)


class TrialCleanupResponse(BaseModel):
    count: int
    usernames: list[str]
    replayed: bool = False
