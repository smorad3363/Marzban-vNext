import os
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BINARY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship
from sqlalchemy.sql.expression import select, text

from app import xray
from app.db.base import Base
from app.models.node import NodeStatus
from app.models.proxy import (
    ProxyHostALPN,
    ProxyHostFingerprint,
    ProxyHostSecurity,
    ProxyTypes,
)
from app.models.user import ReminderType, UserDataLimitResetStrategy, UserStatus


def utc_now_naive() -> datetime:
    """Return UTC in the naive format used by existing database timestamps."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


class AdminRole(Base):
    __tablename__ = "admin_roles"

    id = Column(SmallInteger, primary_key=True, autoincrement=False)
    code = Column(String(32), nullable=False, unique=True)


class Admin(Base):
    __tablename__ = "admins"
    __table_args__ = (
        Index("ix_admins_parent_id", "parent_admin_id", "id"),
    )

    id = Column(Integer, primary_key=True)
    username = Column(String(34), unique=True, index=True)
    # Stable namespace for customer usernames. Nullable during the expand/rollback
    # window so the previous application can still create Admin rows.
    user_namespace_prefix = Column(String(16), nullable=True, unique=True)
    hashed_password = Column(String(128))
    users = relationship("User", back_populates="admin")
    created_at = Column(DateTime, default=datetime.utcnow)
    is_sudo = Column(Boolean, default=False)
    password_reset_at = Column(DateTime, nullable=True)
    telegram_id = Column(BigInteger, nullable=True, default=None)
    # Nullable for current-schema upgrade and old bootstrap compatibility. New
    # dashboard/API-created Admins require a non-empty value at validation time.
    phone = Column(String(32), nullable=True, default=None)
    dashboard_theme = Column(String(32), nullable=False, default="heisenberg")
    logo_filename = Column(String(255), nullable=True, default=None)
    discord_webhook = Column(String(1024), nullable=True, default=None)
    users_usage = Column(BigInteger, nullable=False, default=0)
    usage_logs = relationship("AdminUsageLogs", back_populates="admin")
    role_id = Column(
        SmallInteger,
        ForeignKey("admin_roles.id"),
        nullable=True,
    )
    parent_admin_id = Column(
        Integer,
        ForeignKey("admins.id", ondelete="RESTRICT"),
        nullable=True,
    )
    external_api_enabled = Column(Boolean, nullable=False, default=False)
    external_api_updated_by = Column(
        Integer,
        ForeignKey("admins.id", ondelete="SET NULL"),
        nullable=True,
    )
    external_api_updated_at = Column(DateTime, nullable=True)
    role = relationship("AdminRole")
    parent = relationship(
        "Admin",
        foreign_keys=[parent_admin_id],
        remote_side=[id],
        back_populates="children",
    )
    children = relationship(
        "Admin",
        foreign_keys=[parent_admin_id],
        back_populates="parent",
    )

    @property
    def logo_url(self) -> str | None:
        return f"/api/branding/logo/{self.id}" if self.logo_filename else None


class AdminHierarchySettings(Base):
    __tablename__ = "admin_hierarchy_settings"

    id = Column(SmallInteger, primary_key=True, autoincrement=False)
    enabled = Column(Boolean, nullable=False, default=False)
    max_depth = Column(Integer, nullable=False, default=64)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
    )


class SystemOwner(Base):
    __tablename__ = "system_owner"

    id = Column(SmallInteger, primary_key=True, autoincrement=False)
    admin_id = Column(
        Integer,
        ForeignKey("admins.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    assigned_at = Column(DateTime, nullable=False, default=utc_now_naive)
    admin = relationship("Admin", foreign_keys=[admin_id])


class AdminHierarchy(Base):
    __tablename__ = "admin_hierarchy"
    __table_args__ = (
        CheckConstraint("depth >= 0", name="ck_admin_hierarchy_depth_nonnegative"),
        Index(
            "ix_admin_hierarchy_descendant_ancestor_depth",
            "descendant_id",
            "ancestor_id",
            "depth",
        ),
    )

    ancestor_id = Column(
        Integer,
        ForeignKey("admins.id", ondelete="CASCADE"),
        primary_key=True,
    )
    descendant_id = Column(
        Integer,
        ForeignKey("admins.id", ondelete="CASCADE"),
        primary_key=True,
    )
    depth = Column(Integer, nullable=False)


class AdminUserCreationMode(Base):
    __tablename__ = "admin_user_creation_modes"

    id = Column(SmallInteger, primary_key=True, autoincrement=False)
    code = Column(String(32), nullable=False, unique=True)


class AdminAccountStatus(Base):
    __tablename__ = "admin_account_statuses"

    id = Column(SmallInteger, primary_key=True, autoincrement=False)
    code = Column(String(32), nullable=False, unique=True)


class AdminSuspensionReason(Base):
    __tablename__ = "admin_suspension_reasons"

    id = Column(SmallInteger, primary_key=True, autoincrement=False)
    code = Column(String(64), nullable=False, unique=True)
    description = Column(String(255), nullable=True)


class AdminCreditTransfer(Base):
    __tablename__ = "admin_credit_transfers"
    __table_args__ = (
        Index("ix_admin_credit_from_created", "from_admin_id", "created_at", "id"),
        Index("ix_admin_credit_to_created", "to_admin_id", "created_at", "id"),
        Index("ix_admin_credit_actor_created", "actor_admin_id", "created_at", "id"),
        Index("ix_admin_credit_adjusted_created", "adjusted_admin_id", "created_at", "id"),
        CheckConstraint("amount > 0", name="ck_admin_credit_transfer_amount_positive"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    from_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=True)
    to_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=True)
    actor_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    adjusted_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=True)
    resource = Column(String(32), nullable=True)
    amount = Column(BigInteger, nullable=False)
    delta = Column(BigInteger, nullable=True)
    balance_before = Column(BigInteger, nullable=True)
    balance_after = Column(BigInteger, nullable=True)
    source_delegated_before = Column(BigInteger, nullable=True)
    source_delegated_after = Column(BigInteger, nullable=True)
    operation_type = Column(String(32), nullable=False)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    note = Column(String(512), nullable=True)


class AllocatedTrafficRefundRequest(Base):
    __tablename__ = "allocated_traffic_refund_requests"
    __table_args__ = (
        Index(
            "ix_alloc_refund_reviewer_status_requested",
            "reviewer_admin_id",
            "status",
            "requested_at",
            "id",
        ),
        Index(
            "ix_alloc_refund_requester_requested",
            "requester_admin_id",
            "requested_at",
            "id",
        ),
        CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED','CANCELLED')",
            name="ck_alloc_refund_status",
        ),
        CheckConstraint(
            "requested_refund_amount > 0",
            name="ck_alloc_refund_amount_positive",
        ),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    requester_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    account_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    reviewer_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    target_user_id = Column(Integer, nullable=False)
    target_username = Column(String(34), nullable=False)
    snapshot_billing_mode = Column(String(32), nullable=False)
    snapshot_plan_id = Column(BigInteger, nullable=True)
    snapshot_plan_version_id = Column(BigInteger, nullable=True)
    snapshot_plan_name = Column(String(128), nullable=True)
    snapshot_allocated_quota = Column(BigInteger, nullable=False)
    snapshot_current_quota = Column(BigInteger, nullable=False)
    snapshot_used_traffic = Column(BigInteger, nullable=False)
    snapshot_remaining_traffic = Column(BigInteger, nullable=False)
    snapshot_user_created_at = Column(DateTime, nullable=True)
    snapshot_user_expire_at = Column(DateTime, nullable=True)
    snapshot_pre_delete_status = Column(String(32), nullable=False)
    requested_refund_amount = Column(BigInteger, nullable=False)
    request_reason = Column(String(512), nullable=False)
    request_note = Column(String(1024), nullable=True)
    correlation_id = Column(String(128), nullable=False, index=True)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    status = Column(String(16), nullable=False, default="PENDING")
    requested_at = Column(DateTime, nullable=False, default=utc_now_naive)
    decided_at = Column(DateTime, nullable=True)
    decided_by_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=True)
    decision_explanation = Column(String(1024), nullable=True)
    ledger_transfer_id = Column(
        BigInteger,
        ForeignKey("admin_credit_transfers.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )


class AllocatedTrafficRefundEvent(Base):
    __tablename__ = "allocated_traffic_refund_events"
    __table_args__ = (
        Index("ix_alloc_refund_event_request_created", "request_id", "created_at", "id"),
        CheckConstraint(
            "to_status IN ('PENDING','APPROVED','REJECTED','CANCELLED')",
            name="ck_alloc_refund_event_status",
        ),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    request_id = Column(
        BigInteger,
        ForeignKey("allocated_traffic_refund_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    from_status = Column(String(16), nullable=True)
    to_status = Column(String(16), nullable=False)
    explanation = Column(String(1024), nullable=True)
    operation_key = Column(String(128), nullable=False, unique=True)
    correlation_id = Column(String(128), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class AdminApiToken(Base):
    __tablename__ = "admin_api_tokens"
    __table_args__ = (
        Index("ix_admin_api_tokens_active", "admin_id", "revoked_at", "expires_at", "id"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    admin_id = Column(Integer, ForeignKey("admins.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(BINARY(32), nullable=False, unique=True)
    name = Column(String(96), nullable=False)
    scopes = Column(JSON, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    created_by_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class AdminSuspensionEvent(Base):
    __tablename__ = "admin_suspension_events"
    __table_args__ = (
        Index("ix_admin_suspension_target_started", "admin_id", "started_at", "id"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    actor_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    reason_id = Column(SmallInteger, ForeignKey("admin_suspension_reasons.id"), nullable=False)
    operation_type = Column(String(32), nullable=False, default="suspension")
    idempotency_key = Column(String(128), nullable=True, unique=True)
    payload_fingerprint = Column(String(64), nullable=True)
    limits_snapshot = Column(JSON, nullable=True)
    status = Column(String(24), nullable=False, default="processing")
    started_at = Column(DateTime, nullable=False, default=utc_now_naive)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by_admin_id = Column(
        Integer,
        ForeignKey("admins.id", ondelete="RESTRICT"),
        nullable=True,
    )
    resolved_idempotency_key = Column(String(128), nullable=True, unique=True)


class AdminSuspensionUser(Base):
    __tablename__ = "admin_suspension_users"
    __table_args__ = (
        Index("ix_admin_suspension_user_cursor", "event_id", "sync_status", "user_id"),
    )

    event_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("admin_suspension_events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    previous_status = Column(String(32), nullable=False)
    applied_status = Column(String(32), nullable=False)
    sync_status = Column(String(24), nullable=False, default="pending")


class AdminSuspensionAdmin(Base):
    """Exact pre-freeze admin state, scoped to one immutable freeze event."""

    __tablename__ = "admin_suspension_admins"
    __table_args__ = (
        Index("ix_admin_suspension_admin_cursor", "event_id", "restore_status", "admin_id"),
    )

    event_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("admin_suspension_events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), primary_key=True)
    previous_account_status_id = Column(SmallInteger, nullable=False)
    previous_suspended_reason_id = Column(SmallInteger, nullable=True)
    previous_suspended_at = Column(DateTime, nullable=True)
    previous_suspended_by_admin_id = Column(Integer, nullable=True)
    previous_suspension_event_id = Column(BigInteger, nullable=True)
    applied_account_status_id = Column(SmallInteger, nullable=False)
    restore_status = Column(String(24), nullable=False, default="applied")


class AdminReferralAttribution(Base):
    """Current referral attribution only; this table never grants resources."""

    __tablename__ = "admin_referral_attributions"
    __table_args__ = (
        CheckConstraint("referrer_admin_id <> referred_admin_id", name="ck_admin_referral_no_self"),
        CheckConstraint("rate_bps >= 0 AND rate_bps <= 10000", name="ck_admin_referral_rate"),
        Index("ix_admin_referral_referrer_referred", "referrer_admin_id", "referred_admin_id"),
    )

    referred_admin_id = Column(
        Integer,
        ForeignKey("admins.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    referrer_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    rate_bps = Column(Integer, nullable=False, default=0)
    created_by_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    updated_by_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)


class AdminReferralEvent(Base):
    """Immutable referral audit history with idempotent mutation keys."""

    __tablename__ = "admin_referral_events"
    __table_args__ = (
        Index("ix_admin_referral_event_referred_created", "referred_admin_id", "created_at", "id"),
        Index("ix_admin_referral_event_referrer_created", "new_referrer_admin_id", "created_at", "id"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    actor_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    referred_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    previous_referrer_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=True)
    new_referrer_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=True)
    previous_rate_bps = Column(Integer, nullable=True)
    new_rate_bps = Column(Integer, nullable=True)
    operation_type = Column(String(16), nullable=False)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    payload_fingerprint = Column(String(64), nullable=False)
    note = Column(String(512), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class AdminBulkJob(Base):
    __tablename__ = "admin_bulk_jobs"
    __table_args__ = (
        Index("ix_admin_bulk_jobs_actor_created", "actor_admin_id", "created_at", "id"),
        Index("ix_admin_bulk_jobs_status_cursor", "status", "last_user_id", "id"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    actor_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    target_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    # Stage 8 expands the original hierarchy-disable cursor into a durable
    # multi-target job while retaining the legacy columns for rollback safety.
    job_kind = Column(String(24), nullable=False, default="LEGACY_DISABLE")
    target_scope = Column(String(40), nullable=True)
    selected_admin_ids = Column(JSON, nullable=True)
    payload_fingerprint = Column(String(64), nullable=True)
    operation = Column(String(32), nullable=False)
    amount = Column(BigInteger, nullable=True)
    days_amount = Column(Integer, nullable=True)
    note = Column(String(512), nullable=True)
    include_subtree = Column(Boolean, nullable=False, default=False)
    status = Column(String(24), nullable=False, default="pending")
    total_count = Column(BigInteger, nullable=False, default=0)
    processed_count = Column(BigInteger, nullable=False, default=0)
    success_count = Column(BigInteger, nullable=False, default=0)
    failed_count = Column(BigInteger, nullable=False, default=0)
    skipped_count = Column(BigInteger, nullable=False, default=0)
    last_user_id = Column(Integer, nullable=True)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)
    completed_at = Column(DateTime, nullable=True)


class AdminBulkJobTarget(Base):
    """Immutable target snapshot plus per-target execution/audit result."""

    __tablename__ = "admin_bulk_job_targets"
    __table_args__ = (
        Index(
            "ix_admin_bulk_job_targets_pending",
            "job_id",
            "target_type",
            "status",
            "retryable",
            "sequence",
        ),
        Index("ix_admin_bulk_job_targets_report", "job_id", "sequence"),
        Index("ix_admin_bulk_job_targets_target", "target_type", "target_id", "job_id"),
        UniqueConstraint("idempotency_key", name="uq_admin_bulk_job_target_idempotency"),
    )

    job_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("admin_bulk_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    target_type = Column(String(16), primary_key=True)
    target_id = Column(Integer, primary_key=True)
    sequence = Column(Integer, nullable=False)
    target_username = Column(String(34), nullable=False)
    owner_admin_id = Column(Integer, nullable=True)
    idempotency_key = Column(String(128), nullable=False)
    payload_fingerprint = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="PENDING")
    attempts = Column(Integer, nullable=False, default=0)
    retryable = Column(Boolean, nullable=False, default=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(String(512), nullable=True)
    result_details = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)
    completed_at = Column(DateTime, nullable=True)


class AdminPlanCategory(Base):
    __tablename__ = "admin_plan_categories"
    __table_args__ = (
        Index("ix_admin_plan_categories_owner_active", "owner_admin_id", "archived_at", "id"),
        UniqueConstraint("owner_admin_id", "name", name="uq_admin_plan_categories_owner_name"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    owner_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(String(512), nullable=True)
    archived_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)


class AdminPlanCategoryAccess(Base):
    __tablename__ = "admin_plan_category_access"
    __table_args__ = (
        Index("ix_admin_plan_category_access_admin_category", "admin_id", "category_id"),
    )

    category_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("admin_plan_categories.id", ondelete="CASCADE"),
        primary_key=True,
    )
    admin_id = Column(Integer, ForeignKey("admins.id", ondelete="CASCADE"), primary_key=True)
    assigned_by_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class AdminUserPlan(Base):
    __tablename__ = "admin_user_plans"
    __table_args__ = (
        Index("ix_admin_user_plans_owner_active", "owner_admin_id", "archived_at", "id"),
        Index("ix_admin_user_plans_category_active", "category_id", "archived_at", "id"),
        UniqueConstraint("owner_admin_id", "name", name="uq_admin_user_plans_owner_name"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    owner_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    category_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("admin_plan_categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    name = Column(String(128), nullable=False)
    description = Column(String(512), nullable=True)
    # Trial is authoritative metadata. Names and notes must never classify users.
    is_trial = Column(Boolean, nullable=False, default=False)
    current_version_id = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=True)
    archived_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)
    category = relationship("AdminPlanCategory", lazy="joined")


class AdminUserPlanVersion(Base):
    __tablename__ = "admin_user_plan_versions"
    __table_args__ = (
        UniqueConstraint("plan_id", "version_number", name="uq_admin_plan_version_number"),
        CheckConstraint("version_number > 0", name="ck_admin_plan_version_positive"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    plan_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("admin_user_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number = Column(Integer, nullable=False)
    price_toman = Column(BigInteger, nullable=False, default=0)
    data_limit = Column(BigInteger, nullable=False)
    duration_days = Column(Integer, nullable=False)
    concurrent_user_limit = Column(Integer, nullable=True)
    reset_strategy = Column(String(32), nullable=False)
    renewal_volume_strategy = Column(String(32), nullable=False, default="replace")
    renewal_time_strategy = Column(String(32), nullable=False, default="extend_max")
    created_by_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class AdminUserPlanInbound(Base):
    __tablename__ = "admin_user_plan_inbounds"

    version_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("admin_user_plan_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    inbound_tag = Column(String(256), primary_key=True)


class AdminUserPlanHost(Base):
    __tablename__ = "admin_user_plan_hosts"

    version_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("admin_user_plan_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    inbound_tag = Column(String(256), primary_key=True)
    # No FK to mutable hosts: retain exact selection evidence after deletion and
    # let runtime validation fail closed instead of broadening scope.
    host_id = Column(Integer, primary_key=True)


class AdminUserPlanAccess(Base):
    __tablename__ = "admin_user_plan_access"
    __table_args__ = (
        Index("ix_admin_user_plan_access_plan_admin", "plan_id", "admin_id"),
    )

    admin_id = Column(Integer, ForeignKey("admins.id", ondelete="CASCADE"), primary_key=True)
    plan_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("admin_user_plans.id", ondelete="CASCADE"),
        primary_key=True,
    )
    include_subtree = Column(Boolean, nullable=False, default=False)


class AccessGroup(Base):
    __tablename__ = "access_groups"
    __table_args__ = (
        Index("ix_access_groups_owner_active", "owner_admin_id", "archived_at", "id"),
        UniqueConstraint("owner_admin_id", "name", name="uq_access_groups_owner_name"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    owner_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(String(512), nullable=True)
    archived_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)


class AccessGroupInbound(Base):
    __tablename__ = "access_group_inbounds"

    access_group_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("access_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    inbound_tag = Column(String(256), primary_key=True)


class AccessGroupHost(Base):
    __tablename__ = "access_group_hosts"

    access_group_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("access_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    inbound_tag = Column(String(256), primary_key=True)
    host_id = Column(Integer, primary_key=True)


class AccessGroupNode(Base):
    __tablename__ = "access_group_nodes"

    access_group_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("access_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    node_id = Column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True)


class UserPlanAssignment(Base):
    __tablename__ = "user_plan_assignments"
    __table_args__ = (
        Index("ix_user_plan_assignments_user_created", "user_id", "created_at", "id"),
        Index(
            "ix_user_plan_assignments_trial_operation_user",
            "is_trial",
            "operation_type",
            "user_id",
        ),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_id = Column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("admin_user_plans.id"), nullable=False)
    version_id = Column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("admin_user_plan_versions.id"), nullable=False)
    actor_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    operation_type = Column(String(24), nullable=False)
    # Immutable snapshot of the Plan kind at operation time.
    is_trial = Column(Boolean, nullable=False, default=False)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class AdminUserPlanPrice(Base):
    __tablename__ = "admin_user_plan_prices"
    __table_args__ = (
        Index("ix_admin_user_plan_prices_plan_admin", "plan_id", "admin_id"),
        CheckConstraint("price_toman >= 0", name="ck_admin_user_plan_price_nonnegative"),
    )

    admin_id = Column(Integer, ForeignKey("admins.id", ondelete="CASCADE"), primary_key=True)
    plan_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("admin_user_plans.id", ondelete="CASCADE"),
        primary_key=True,
    )
    price_toman = Column(BigInteger, nullable=False)
    assigned_by_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)


class TrialCleanupOperation(Base):
    __tablename__ = "trial_cleanup_operations"
    __table_args__ = (
        Index("ix_trial_cleanup_actor_created", "actor_admin_id", "created_at", "id"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    actor_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    expired_before = Column(DateTime, nullable=False)
    payload_fingerprint = Column(String(64), nullable=False)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    deleted_count = Column(Integer, nullable=False)
    deleted_usernames = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class AdminUsageLogs(Base):
    __tablename__ = "admin_usage_logs"

    id = Column(Integer, primary_key=True)
    admin_id = Column(Integer, ForeignKey("admins.id"))
    admin = relationship("Admin", back_populates="usage_logs")
    used_traffic_at_reset = Column(BigInteger, nullable=False)
    reset_at = Column(DateTime, default=datetime.utcnow)


class AdminAuditLog(Base):
    """Append-only record of sensitive administrative activity."""

    __tablename__ = "admin_audit_logs"
    __table_args__ = (
        Index("ix_admin_audit_logs_admin_created", "admin_id", "created_at"),
        Index("ix_admin_audit_logs_action_created", "action", "created_at"),
        Index(
            "ix_admin_audit_logs_target",
            "target_type",
            "target_id",
        ),
        Index(
            "ix_admin_audit_logs_target_name_created",
            "target_name",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_id = Column(
        Integer,
        ForeignKey("admins.id", ondelete="SET NULL"),
        nullable=True,
    )
    admin_username = Column(String(34), nullable=False, index=True)
    action = Column(String(64), nullable=False)
    target_type = Column(String(64), nullable=False)
    target_id = Column(String(128), nullable=True)
    target_name = Column(String(256), nullable=True)
    description = Column(Text, nullable=False)
    previous_value = Column(JSON, nullable=True)
    new_value = Column(JSON, nullable=True)
    details = Column(JSON, nullable=True)
    ip_address = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False, default="success")
    created_at = Column(
        DateTime,
        nullable=False,
        default=utc_now_naive,
        index=True,
    )


class MarzhelpMetadata(Base):
    """Compatibility metadata owned and migrated by Marzban."""

    __tablename__ = "marzhelp_metadata"

    key = Column(String(64), primary_key=True)
    value = Column(String(255), nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class MarzhelpAdminSettings(Base):
    """Canonical Marzhelp policy and admin-accounting settings."""

    __tablename__ = "marzhelp_admin_settings"
    __table_args__ = (
        Index("ix_marzhelp_admin_settings_billing_admin", "billing_mode", "admin_id"),
        Index("ix_marzhelp_admin_settings_status_admin", "account_status_id", "admin_id"),
    )

    admin_id = Column(Integer, ForeignKey("admins.id"), primary_key=True)
    # Existing rows are deliberately kept in compatibility mode. Only Owner may
    # assign one of the explicit commercial modes through the billing service.
    billing_mode = Column(String(32), nullable=True, default="LEGACY_COMPAT")
    # Monetary billing is opt-in for migrated accounts, and enabled for newly
    # configured commercial accounts. Values are stored as whole Toman.
    money_billing_enabled = Column(Boolean, nullable=False, default=False)
    money_balance_toman = Column(BigInteger, nullable=False, default=0)
    used_traffic_price_per_gib_toman = Column(BigInteger, nullable=True)
    # Numerator remainder modulo GiB for exact fractional usage settlement.
    usage_billing_remainder = Column(BigInteger, nullable=False, default=0)
    total_traffic = Column(BigInteger, nullable=True)
    delegated_traffic = Column(BigInteger, nullable=False, default=0)
    used_traffic = Column(BigInteger, nullable=False, default=0)
    expiry_date = Column(Date, nullable=True)
    status = Column(JSON, nullable=True)
    # Remaining successful create/renew/time-change operations. NULL is unrestricted.
    user_limit = Column(BigInteger, nullable=True)
    # Maximum owned user accounts. NULL means unrestricted.
    max_users = Column(BigInteger, nullable=True)
    user_count_used = Column(BigInteger, nullable=False, default=0)
    # Legacy weighted concurrent-device capacity is preserved independently.
    device_capacity_limit = Column(BigInteger, nullable=True)
    capacity_used = Column(BigInteger, nullable=False, default=0)
    provisioning_volume_limit = Column(BigInteger, nullable=True)
    provisioning_volume_used = Column(BigInteger, nullable=False, default=0)
    renewal_limit = Column(BigInteger, nullable=True)
    renewals_used = Column(BigInteger, nullable=False, default=0)
    renewal_enabled = Column(Boolean, nullable=False, default=True)
    renewal_remaining = Column(BigInteger, nullable=True)
    # Remaining independently granted Trial creations. Existing accounts start
    # fail-closed at zero; Owner adjustments are recorded in the resource ledger.
    trial_quota = Column(BigInteger, nullable=False, default=0)
    trial_quota_limit = Column(BigInteger, nullable=False, default=0)
    trials_used = Column(BigInteger, nullable=False, default=0)
    # Delegated child-admin creation is separate from the fixed role preset.
    # A finite limit is a budget: own creations and finite child allocations
    # both consume it. Owner bypasses these commercial counters.
    can_create_admins = Column(Boolean, nullable=False, default=False)
    can_delegate_admin_creation = Column(Boolean, nullable=False, default=False)
    can_create_allocated_children = Column(Boolean, nullable=False, default=True)
    admin_creation_limit = Column(BigInteger, nullable=True)
    admin_creations_used = Column(BigInteger, nullable=False, default=0)
    delegated_admin_creation_limit = Column(BigInteger, nullable=False, default=0)
    user_creation_mode_id = Column(
        SmallInteger,
        ForeignKey("admin_user_creation_modes.id"),
        nullable=False,
        default=1,
    )
    can_manage_plans = Column(Boolean, nullable=False, default=False)
    account_status_id = Column(
        SmallInteger,
        ForeignKey("admin_account_statuses.id"),
        nullable=False,
        default=1,
    )
    suspended_reason_id = Column(
        SmallInteger,
        ForeignKey("admin_suspension_reasons.id"),
        nullable=True,
    )
    suspended_at = Column(DateTime, nullable=True)
    suspended_by_admin_id = Column(
        Integer,
        ForeignKey("admins.id", ondelete="SET NULL"),
        nullable=True,
    )
    suspension_event_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("admin_suspension_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    admin_traffic_warning_percent = Column(Integer, nullable=False, default=80)
    sudo_traffic_warning_percent = Column(Integer, nullable=False, default=80)
    all_inbounds = Column(Boolean, nullable=False, default=True)
    all_user_limits = Column(Boolean, nullable=False, default=True)
    max_user_duration_days = Column(Integer, nullable=True)
    hashed_password_before = Column(String(255), nullable=True)
    last_expiry_notification = Column(DateTime, nullable=True)
    last_traffic_notification = Column(Integer, nullable=True)
    last_traffic_notify = Column(Integer, nullable=True)
    calculate_volume = Column(String(50), nullable=False, default="used_traffic")
    prevent_user_creation = Column(Boolean, nullable=False, default=False)
    prevent_user_deletion = Column(Boolean, nullable=False, default=False)
    prevent_user_reset = Column(Boolean, nullable=False, default=False)
    prevent_revoke_subscription = Column(Boolean, nullable=False, default=False)
    prevent_unlimited_traffic = Column(Boolean, nullable=False, default=False)
    # Full client addresses are sensitive. Non-sudo admins receive masked
    # addresses unless this capability is explicitly granted by sudo.
    # Compatibility column: policy writes now keep full client IP visibility enabled.
    view_full_client_ip = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    inbound_permissions = relationship(
        "MarzhelpAdminInboundPermission",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    user_limit_permissions = relationship(
        "MarzhelpAdminUserLimitPermission",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    subscription_mode_permissions = relationship(
        "MarzhelpAdminSubscriptionModePermission",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def allowed_inbounds(self):
        return sorted(item.inbound_tag for item in self.inbound_permissions)

    @property
    def allowed_user_limits(self):
        return sorted(item.concurrent_user_limit for item in self.user_limit_permissions)

    @property
    def allowed_subscription_modes(self):
        return sorted(item.mode for item in self.subscription_mode_permissions)


class MarzhelpAdminInboundPermission(Base):
    __tablename__ = "marzhelp_admin_allowed_inbounds"

    admin_id = Column(
        Integer,
        ForeignKey("marzhelp_admin_settings.admin_id", ondelete="CASCADE"),
        primary_key=True,
    )
    inbound_tag = Column(String(256), primary_key=True)


class MarzhelpAdminUserLimitPermission(Base):
    __tablename__ = "marzhelp_admin_allowed_user_limits"

    admin_id = Column(
        Integer,
        ForeignKey("marzhelp_admin_settings.admin_id", ondelete="CASCADE"),
        primary_key=True,
    )
    concurrent_user_limit = Column(Integer, primary_key=True)


class MarzhelpAdminSubscriptionModePermission(Base):
    __tablename__ = "marzhelp_admin_allowed_subscription_modes"

    admin_id = Column(
        Integer,
        ForeignKey("marzhelp_admin_settings.admin_id", ondelete="CASCADE"),
        primary_key=True,
    )
    mode = Column(String(48), primary_key=True)


class DeviceLimitSettings(Base):
    """Singleton runtime policy for native device/IP-limit enforcement."""

    __tablename__ = "device_limit_settings"

    id = Column(Integer, primary_key=True, autoincrement=False, default=1)
    enabled = Column(Boolean, nullable=False, default=False)
    # Kept for input/backward compatibility only. Runtime behavior uses the
    # independent capability flags below.
    enforcement_mode = Column(String(24), nullable=False, default="hybrid")
    device_slots_enabled = Column(Boolean, nullable=False, default=True)
    ip_detection_enabled = Column(Boolean, nullable=False, default=True)
    client_fingerprint_enabled = Column(Boolean, nullable=False, default=False)
    check_interval_seconds = Column(Integer, nullable=False, default=60)
    active_window_seconds = Column(Integer, nullable=False, default=300)
    hit_threshold = Column(Integer, nullable=False, default=3)
    min_successful_connections = Column(Integer, nullable=False, default=3)
    handoff_grace_seconds = Column(Integer, nullable=False, default=90)
    warning_auto_delete_seconds = Column(Integer, nullable=False, default=86400)
    strike_reset_seconds = Column(Integer, nullable=False, default=2592000)
    full_ip_retention_days = Column(Integer, nullable=False, default=7)
    incident_retention_days = Column(Integer, nullable=False, default=90)
    audit_retention_days = Column(Integer, nullable=False, default=180)
    auto_delete_enabled = Column(Boolean, nullable=False, default=False)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
    )


class DeviceLimitPenaltyStage(Base):
    __tablename__ = "device_limit_penalty_stages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    violation_count = Column(Integer, nullable=False, unique=True)
    action = Column(String(32), nullable=False)
    duration_seconds = Column(Integer, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)


class DeviceSlot(Base):
    """One independently revocable credential bundle owned by a user."""

    __tablename__ = "device_slots"
    __table_args__ = (
        UniqueConstraint("user_id", "slot_index", name="uq_device_slots_user_index"),
        Index("ix_device_slots_user_enabled", "user_id", "enabled"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    slot_index = Column(Integer, nullable=False)
    label = Column(String(64), nullable=True)
    credentials = Column(JSON, nullable=False)
    token_version = Column(String(36), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    last_seen_at = Column(DateTime, nullable=True)
    last_ip = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
    )

    user = relationship("User", back_populates="device_slots")
    client_observations = relationship(
        "DeviceClientObservation",
        back_populates="slot",
        lazy="selectin",
    )


class DeviceClientObservation(Base):
    """Aggregated, bounded subscription-client observation per slot/user."""

    __tablename__ = "device_client_observations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "slot_key",
            "normalized_identity",
            name="uq_device_client_observation_identity",
        ),
        Index(
            "ix_device_client_observation_user_slot_seen",
            "user_id",
            "slot_key",
            "last_seen_at",
        ),
        Index(
            "ix_device_client_observation_user_seen",
            "user_id",
            "last_seen_at",
        ),
        Index("ix_device_client_observation_slot", "slot_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    slot_id = Column(Integer, ForeignKey("device_slots.id", ondelete="SET NULL"), nullable=True)
    # 0 is the honest user-level fallback for legacy subscription tokens.
    slot_key = Column(Integer, nullable=False, default=0)
    normalized_identity = Column(String(64), nullable=False)
    client_name = Column(String(64), nullable=False, default="Unknown")
    client_version = Column(String(64), nullable=True)
    platform = Column(String(64), nullable=True)
    os_token = Column(String(128), nullable=True)
    network_stack = Column(String(128), nullable=True)
    raw_user_agent = Column(String(512), nullable=False)
    first_seen_at = Column(DateTime, nullable=False, default=utc_now_naive)
    last_seen_at = Column(DateTime, nullable=False, default=utc_now_naive)
    seen_count = Column(BigInteger, nullable=False, default=1)

    slot = relationship("DeviceSlot", back_populates="client_observations")
    user = relationship("User", back_populates="device_client_observations")


class DeviceLimitUserState(Base):
    __tablename__ = "device_limit_user_states"
    __table_args__ = (
        Index("ix_device_limit_state_penalty_until", "penalty_status", "blocked_until"),
    )

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    violation_count = Column(Integer, nullable=False, default=0)
    current_stage = Column(Integer, nullable=False, default=0)
    penalty_status = Column(String(32), nullable=False, default="clear")
    blocked_until = Column(DateTime, nullable=True)
    status_before_penalty = Column(String(24), nullable=True)
    last_violation_at = Column(DateTime, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    active_ip_count = Column(Integer, nullable=False, default=0)
    last_reason = Column(Text, nullable=True)
    pending_handoff_started_at = Column(DateTime, nullable=True)
    pending_ip_addresses = Column(JSON, nullable=True)
    pending_source_nodes = Column(JSON, nullable=True)
    pending_risk_score = Column(Integer, nullable=True)
    pending_last_fresh_at = Column(DateTime, nullable=True)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
    )

    user = relationship("User", back_populates="device_limit_state")


class DeviceLimitIncident(Base):
    __tablename__ = "device_limit_incidents"
    __table_args__ = (
        Index("ix_device_limit_incidents_user_created", "user_id", "created_at"),
        Index("ix_device_limit_incidents_admin_created", "admin_id", "created_at"),
        Index("ix_device_limit_incidents_created", "created_at"),
        Index(
            "ix_device_limit_incidents_warning_expiry",
            "event_state",
            "resolved_at",
            "expires_at",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    admin_id = Column(Integer, nullable=True)
    username = Column(String(34), nullable=False)
    stage = Column(Integer, nullable=False)
    action = Column(String(32), nullable=False)
    configured_limit = Column(Integer, nullable=False)
    observed_count = Column(Integer, nullable=False)
    ip_addresses = Column(JSON, nullable=True)
    source_nodes = Column(JSON, nullable=True)
    event_state = Column(String(32), nullable=False, default="confirmed_violation")
    risk_score = Column(Integer, nullable=True)
    signal_summary = Column(JSON, nullable=True)
    reason = Column(Text, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class MarzhelpUserState(Base):
    __tablename__ = "marzhelp_user_states"

    user_id = Column(BigInteger, primary_key=True)
    username = Column(String(50), nullable=True)
    lang = Column(String(10), nullable=True)
    state = Column(String(50), nullable=True)
    admin_id = Column(Integer, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    data = Column(Text, nullable=True)
    message_id = Column(Integer, nullable=True)
    template_index = Column(Integer, nullable=False, default=0)


class MarzhelpUserTemporary(Base):
    __tablename__ = "marzhelp_user_temporaries"

    user_id = Column(BigInteger, primary_key=True)
    user_key = Column(String(50), primary_key=True)
    value = Column(Text, nullable=True)


class MarzhelpAdminUsage(Base):
    __tablename__ = "marzhelp_admin_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_id = Column(Integer, ForeignKey("admins.id"), nullable=False, index=True)
    used_traffic_gb = Column(Numeric(18, 2), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class MarzhelpLimit(Base):
    __tablename__ = "marzhelp_limits"
    __table_args__ = (UniqueConstraint("type", "admin_id", "inbound_tag", name="uq_marzhelp_limit"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(16), nullable=False)
    admin_id = Column(Integer, ForeignKey("admins.id"), nullable=False, index=True)
    inbound_tag = Column(String(255), nullable=False)


class MarzhelpRuntimeSetting(Base):
    __tablename__ = "marzhelp_runtime_settings"

    setting_name = Column(String(64), primary_key=True)
    setting_value = Column(String(255), nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class OwnerCommercialPolicy(Base):
    __tablename__ = "owner_commercial_policy"

    id = Column(Integer, primary_key=True, default=1)
    price_per_gib_toman = Column(BigInteger, nullable=False, default=1000)
    allow_unlimited_duration = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class OwnerDurationPreset(Base):
    __tablename__ = "owner_duration_presets"

    duration_days = Column(Integer, primary_key=True, autoincrement=False)
    multiplier_basis_points = Column(Integer, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class MarzhelpDeletedUser(Base):
    __tablename__ = "marzhelp_deleted_users"

    user_id = Column(Integer, primary_key=True)
    admin_id = Column(Integer, nullable=False, index=True)
    username = Column(String(34), nullable=True)
    used_traffic_total = Column(BigInteger, nullable=False, default=0)
    allocated_traffic = Column(BigInteger, nullable=True)
    refunded_traffic = Column(BigInteger, nullable=False, default=0)
    deleted_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class MarzhelpAccountingTransaction(Base):
    __tablename__ = "marzhelp_accounting_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    operation_key = Column(String(128), nullable=False, unique=True)
    operation_type = Column(String(32), nullable=False)
    admin_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=True)
    username = Column(String(34), nullable=True)
    traffic_delta = Column(BigInteger, nullable=False, default=0)
    allowance_delta = Column(Integer, nullable=False, default=0)
    volume_delta = Column(BigInteger, nullable=False, default=0)
    renewal_delta = Column(Integer, nullable=False, default=0)
    result = Column(String(16), nullable=False, default="consumed")
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class AdminMoneyTransaction(Base):
    __tablename__ = "admin_money_transactions"
    __table_args__ = (
        UniqueConstraint("operation_key", "admin_id", name="uq_admin_money_operation_admin"),
        Index("ix_admin_money_admin_created", "admin_id", "created_at", "id"),
        Index("ix_admin_money_user_created", "user_id", "created_at", "id"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    operation_key = Column(String(160), nullable=False)
    operation_type = Column(String(32), nullable=False)
    admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    actor_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False)
    counterparty_admin_id = Column(Integer, ForeignKey("admins.id", ondelete="SET NULL"), nullable=True)
    delta_toman = Column(BigInteger, nullable=False)
    balance_before = Column(BigInteger, nullable=False)
    balance_after = Column(BigInteger, nullable=False)
    plan_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("admin_user_plans.id", ondelete="SET NULL"),
        nullable=True,
    )
    version_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("admin_user_plan_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_created_at_id", "created_at", "id"),
        Index("ix_users_admin_status", "admin_id", "status"),
        Index("ix_users_status_created_id", "status", "created_at", "id"),
        Index("ix_users_admin_created_id", "admin_id", "created_at", "id"),
    )

    id = Column(Integer, primary_key=True)
    username = Column(String(34, collation='NOCASE'), unique=True, index=True)
    proxies = relationship("Proxy", back_populates="user", cascade="all, delete-orphan")
    device_slots = relationship("DeviceSlot", back_populates="user", cascade="all, delete-orphan")
    device_client_observations = relationship(
        "DeviceClientObservation",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    device_limit_state = relationship(
        "DeviceLimitUserState",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    status = Column(Enum(UserStatus), nullable=False, default=UserStatus.active)
    used_traffic = Column(BigInteger, default=0)
    node_usages = relationship("NodeUserUsage", back_populates="user", cascade="all, delete-orphan")
    notification_reminders = relationship("NotificationReminder", back_populates="user", cascade="all, delete-orphan")
    data_limit = Column(BigInteger, nullable=True)
    # NULL keeps the historical unlimited-device behavior.
    concurrent_user_limit = Column(Integer, nullable=True)
    data_limit_reset_strategy = Column(
        Enum(UserDataLimitResetStrategy),
        nullable=False,
        default=UserDataLimitResetStrategy.no_reset,
    )
    usage_logs = relationship("UserUsageResetLogs", back_populates="user")  # maybe rename it to reset_usage_logs?
    expire = Column(Integer, nullable=True)
    admin_id = Column(Integer, ForeignKey("admins.id"), index=True)
    access_group_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("access_groups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    admin = relationship("Admin", back_populates="users")
    sub_revoked_at = Column(DateTime, nullable=True, default=None)
    sub_updated_at = Column(DateTime, nullable=True, default=None)
    sub_last_user_agent = Column(String(512), nullable=True, default=None)
    created_at = Column(DateTime, default=datetime.utcnow)
    note = Column(String(500), nullable=True, default=None)
    online_at = Column(DateTime, nullable=True, default=None)
    on_hold_expire_duration = Column(BigInteger, nullable=True, default=None)
    on_hold_timeout = Column(DateTime, nullable=True, default=None)

    # * Positive values: User will be deleted after the value of this field in days automatically.
    # * Negative values: User won't be deleted automatically at all.
    # * NULL: Uses global settings.
    auto_delete_in_days = Column(Integer, nullable=True, default=None)

    edit_at = Column(DateTime, nullable=True, default=None)
    last_status_change = Column(DateTime, default=datetime.utcnow, nullable=True)

    next_plan = relationship(
        "NextPlan",
        uselist=False,
        back_populates="user",
        cascade="all, delete-orphan"
    )

    @hybrid_property
    def reseted_usage(self) -> int:
        return int(sum([log.used_traffic_at_reset for log in self.usage_logs]))

    @reseted_usage.expression
    def reseted_usage(cls):
        return (
            select(func.sum(UserUsageResetLogs.used_traffic_at_reset)).
            where(UserUsageResetLogs.user_id == cls.id).
            label('reseted_usage')
        )

    @property
    def lifetime_used_traffic(self) -> int:
        return int(
            sum([log.used_traffic_at_reset for log in self.usage_logs])
            + self.used_traffic
        )

    @property
    def last_traffic_reset_time(self):
        return self.usage_logs[-1].reset_at if self.usage_logs else self.created_at

    @property
    def reset_history(self):
        return sorted(
            self.usage_logs,
            key=lambda log: log.reset_at or datetime.min,
            reverse=True,
        )

    @property
    def excluded_inbounds(self):
        _ = {}
        for proxy in self.proxies:
            _[proxy.type] = [i.tag for i in proxy.excluded_inbounds]
        return _

    @property
    def inbounds(self):
        _ = {}
        for proxy in self.proxies:
            _[proxy.type] = []
            excluded_tags = [i.tag for i in proxy.excluded_inbounds]
            for inbound in xray.config.inbounds_by_protocol.get(proxy.type, []):
                if inbound["tag"] not in excluded_tags:
                    _[proxy.type].append(inbound["tag"])

        return _


excluded_inbounds_association = Table(
    "exclude_inbounds_association",
    Base.metadata,
    Column("proxy_id", ForeignKey("proxies.id")),
    Column("inbound_tag", ForeignKey("inbounds.tag")),
)

template_inbounds_association = Table(
    "template_inbounds_association",
    Base.metadata,
    Column("user_template_id", ForeignKey("user_templates.id")),
    Column("inbound_tag", ForeignKey("inbounds.tag")),
)


class NextPlan(Base):
    __tablename__ = 'next_plans'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    data_limit = Column(BigInteger, nullable=False)
    expire = Column(Integer, nullable=True)
    add_remaining_traffic = Column(Boolean, nullable=False, default=False, server_default='0')
    fire_on_either = Column(Boolean, nullable=False, default=True, server_default='0')

    user = relationship("User", back_populates="next_plan")


class UserTemplate(Base):
    __tablename__ = "user_templates"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, unique=True)
    data_limit = Column(BigInteger, default=0)
    expire_duration = Column(BigInteger, default=0)  # in seconds
    username_prefix = Column(String(20), nullable=True)
    username_suffix = Column(String(20), nullable=True)

    inbounds = relationship(
        "ProxyInbound", secondary=template_inbounds_association
    )


class UserUsageResetLogs(Base):
    __tablename__ = "user_usage_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    user = relationship("User", back_populates="usage_logs")
    used_traffic_at_reset = Column(BigInteger, nullable=False)
    reset_at = Column(DateTime, default=datetime.utcnow)


class Proxy(Base):
    __tablename__ = "proxies"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    user = relationship("User", back_populates="proxies")
    type = Column(Enum(ProxyTypes), nullable=False)
    settings = Column(JSON, nullable=False)
    excluded_inbounds = relationship(
        "ProxyInbound", secondary=excluded_inbounds_association
    )


class ProxyInbound(Base):
    __tablename__ = "inbounds"

    id = Column(Integer, primary_key=True)
    tag = Column(String(256), unique=True, nullable=False, index=True)
    hosts = relationship(
        "ProxyHost", back_populates="inbound", cascade="all, delete-orphan"
    )


class ProxyHost(Base):
    __tablename__ = "hosts"
    __table_args__ = (
        Index("ix_hosts_inbound_legacy_id", "inbound_tag", "is_legacy", "id"),
    )

    id = Column(Integer, primary_key=True)
    remark = Column(String(256), unique=False, nullable=False)
    address = Column(String(256), unique=False, nullable=False)
    port = Column(Integer, nullable=True)
    path = Column(String(256), unique=False, nullable=True)
    sni = Column(String(1000), unique=False, nullable=True)
    host = Column(String(1000), unique=False, nullable=True)
    security = Column(
        Enum(ProxyHostSecurity),
        unique=False,
        nullable=False,
        default=ProxyHostSecurity.inbound_default,
    )
    alpn = Column(
        Enum(ProxyHostALPN),
        unique=False,
        nullable=False,
        default=ProxyHostSecurity.none,
        server_default=ProxyHostSecurity.none.name
    )
    fingerprint = Column(
        Enum(ProxyHostFingerprint),
        unique=False,
        nullable=False,
        default=ProxyHostSecurity.none,
        server_default=ProxyHostSecurity.none.name
    )

    inbound_tag = Column(String(256), ForeignKey("inbounds.tag"), nullable=False)
    inbound = relationship("ProxyInbound", back_populates="hosts")
    # Future-only network revisions keep historical User snapshots routable while
    # hiding retired Hosts from normal editing and new Plan selection.
    is_legacy = Column(Boolean, nullable=False, default=False, server_default="0")
    allowinsecure = Column(Boolean, nullable=True)
    is_disabled = Column(Boolean, nullable=True, default=False)
    mux_enable = Column(Boolean, nullable=False, default=False, server_default='0')
    fragment_setting = Column(String(100), nullable=True)
    noise_setting = Column(String(2000), nullable=True)
    random_user_agent = Column(Boolean, nullable=False, default=False, server_default='0')
    use_sni_as_host = Column(Boolean, nullable=False, default=False, server_default="0")


class System(Base):
    __tablename__ = "system"

    id = Column(Integer, primary_key=True)
    uplink = Column(BigInteger, default=0)
    downlink = Column(BigInteger, default=0)


class JWT(Base):
    __tablename__ = "jwt"

    id = Column(Integer, primary_key=True)
    secret_key = Column(
        String(64), nullable=False, default=lambda: os.urandom(32).hex()
    )


class TLS(Base):
    __tablename__ = "tls"

    id = Column(Integer, primary_key=True)
    key = Column(String(4096), nullable=False)
    certificate = Column(String(2048), nullable=False)


class Node(Base):
    __tablename__ = "nodes"

    id = Column(Integer, primary_key=True)
    name = Column(String(256, collation='NOCASE'), unique=True)
    address = Column(String(256), unique=False, nullable=False)
    port = Column(Integer, unique=False, nullable=False)
    api_port = Column(Integer, unique=False, nullable=False)
    xray_version = Column(String(32), nullable=True)
    status = Column(Enum(NodeStatus), nullable=False, default=NodeStatus.connecting)
    last_status_change = Column(DateTime, default=datetime.utcnow)
    message = Column(String(1024), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    uplink = Column(BigInteger, default=0)
    downlink = Column(BigInteger, default=0)
    user_usages = relationship("NodeUserUsage", back_populates="node", cascade="all, delete-orphan")
    usages = relationship("NodeUsage", back_populates="node", cascade="all, delete-orphan")
    usage_coefficient = Column(Float, nullable=False, server_default=text("1.0"), default=1)
    watchdog_enabled = Column(Boolean, nullable=False, server_default=text("1"), default=True)


class NodeWatchdogSettings(Base):
    __tablename__ = "node_watchdog_settings"

    id = Column(Integer, primary_key=True, default=1)
    enabled = Column(Boolean, nullable=False, default=False)
    telegram_bot_token = Column(String(256), nullable=True)
    telegram_chat_id = Column(String(64), nullable=True)
    check_interval = Column(Integer, nullable=False, default=15)
    backoff_cap = Column(Integer, nullable=False, default=600)
    remind_every = Column(Integer, nullable=False, default=1800)


class NodeUserUsage(Base):
    __tablename__ = "node_user_usages"
    __table_args__ = (
        UniqueConstraint('created_at', 'user_id', 'node_id'),
    )

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, unique=False, nullable=False)  # one hour per record
    user_id = Column(Integer, ForeignKey("users.id"))
    user = relationship("User", back_populates="node_usages")
    node_id = Column(Integer, ForeignKey("nodes.id"))
    node = relationship("Node", back_populates="user_usages")
    used_traffic = Column(BigInteger, default=0)


class NodeUsage(Base):
    __tablename__ = "node_usages"
    __table_args__ = (
        UniqueConstraint('created_at', 'node_id'),
    )

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, unique=False, nullable=False)  # one hour per record
    node_id = Column(Integer, ForeignKey("nodes.id"))
    node = relationship("Node", back_populates="usages")
    uplink = Column(BigInteger, default=0)
    downlink = Column(BigInteger, default=0)


class NotificationReminder(Base):
    __tablename__ = "notification_reminders"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    user = relationship("User", back_populates="notification_reminders")
    type = Column(Enum(ReminderType), nullable=False)
    threshold = Column(Integer, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class TelegramOutbox(Base):
    __tablename__ = "telegram_outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_telegram_outbox_idempotency"),
        Index("ix_telegram_outbox_dispatch", "status", "next_attempt_at", "id"),
        Index("ix_telegram_outbox_retention", "status", "completed_at", "id"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    idempotency_key = Column(String(191), nullable=False)
    event_type = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String(16), nullable=False, default="PENDING", server_default="PENDING")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at = Column(DateTime, nullable=False, default=utc_now_naive)
    last_error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    completed_at = Column(DateTime, nullable=True)


class BackupArtifact(Base):
    __tablename__ = "backup_artifacts"
    __table_args__ = (
        UniqueConstraint("period_key", name="uq_backup_artifacts_period"),
        Index("ix_backup_artifacts_delivery", "delivery_status", "created_at", "id"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    period_key = Column(String(32), nullable=False)
    database_name = Column(String(128), nullable=False)
    encrypted_path = Column(String(1024), nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    sha256 = Column(String(64), nullable=True)
    generation_status = Column(String(16), nullable=False, default="PENDING")
    delivery_status = Column(String(16), nullable=False, default="PENDING")
    error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    delivered_at = Column(DateTime, nullable=True)


class BackupSettings(Base):
    __tablename__ = "backup_settings"

    id = Column(Integer, primary_key=True, default=1)
    enabled = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    destination = Column(String(24), nullable=False, default="LOCAL", server_default="LOCAL")
    schedule = Column(String(8), nullable=False, default="24h", server_default="24h")
    retention_count = Column(Integer, nullable=False, default=14, server_default="14")
    telegram_bot_token = Column(String(256), nullable=True)
    telegram_chat_id = Column(String(64), nullable=True)
    smtp_host = Column(String(256), nullable=True)
    smtp_port = Column(Integer, nullable=True)
    smtp_username = Column(String(256), nullable=True)
    smtp_password = Column(String(256), nullable=True)
    smtp_use_tls = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    email_from = Column(String(320), nullable=True)
    email_to = Column(String(320), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)


class SystemBrandingSettings(Base):
    __tablename__ = "system_branding_settings"

    id = Column(Integer, primary_key=True, default=1)
    panel_name = Column(String(80), nullable=False, default="Operations Console", server_default="Operations Console")
    login_title = Column(String(120), nullable=False, default="Secure operator access", server_default="Secure operator access")
    description = Column(String(280), nullable=True)
    logo_filename = Column(String(255), nullable=True)
    favicon_filename = Column(String(255), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)
