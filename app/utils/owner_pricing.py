"""Owner pricing presets and deterministic Form pricing."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import Admin, MarzhelpAdminSettings, OwnerCommercialPolicy, OwnerDurationPreset
from app.models.admin_hierarchy import DurationPresetInput, OwnerPricingResponse, OwnerPricingUpdate
from app.utils import admin_hierarchy
from app.utils.admin_billing import BillingMode, billing_mode


GIB = 1024**3


def get_policy(db: Session) -> OwnerCommercialPolicy:
    policy = db.get(OwnerCommercialPolicy, 1)
    if policy is None:
        policy = OwnerCommercialPolicy(id=1, price_per_gib_toman=1000, allow_unlimited_duration=False)
        db.add(policy)
        db.flush()
    return policy


def response(db: Session) -> OwnerPricingResponse:
    policy = get_policy(db)
    presets = db.query(OwnerDurationPreset).order_by(OwnerDurationPreset.duration_days).all()
    return OwnerPricingResponse(
        price_per_gib_toman=policy.price_per_gib_toman,
        allow_unlimited_duration=policy.allow_unlimited_duration,
        duration_presets=[
            DurationPresetInput(
                duration_days=row.duration_days,
                multiplier=row.multiplier_basis_points / 10_000,
                enabled=row.enabled,
            )
            for row in presets
        ],
    )


def update(db: Session, actor: Admin, values: OwnerPricingUpdate) -> OwnerPricingResponse:
    if not admin_hierarchy.is_owner(db, actor):
        raise admin_hierarchy.HierarchyError("pricing_owner_only", "Only Owner can manage pricing")
    policy = get_policy(db)
    policy.price_per_gib_toman = values.price_per_gib_toman
    policy.allow_unlimited_duration = values.allow_unlimited_duration
    db.query(OwnerDurationPreset).delete(synchronize_session=False)
    db.add_all(
        OwnerDurationPreset(
            duration_days=item.duration_days,
            multiplier_basis_points=round(item.multiplier * 10_000),
            enabled=item.enabled,
        )
        for item in values.duration_presets
    )
    db.commit()
    return response(db)


def duration_preset(db: Session, expire: int | None) -> OwnerDurationPreset:
    policy = get_policy(db)
    if not expire:
        if not policy.allow_unlimited_duration:
            raise admin_hierarchy.HierarchyError("unlimited_duration_forbidden", "Unlimited duration requires Owner permission")
        raise admin_hierarchy.HierarchyError("unlimited_form_pricing_undefined", "Unlimited Form duration requires a priced preset")
    now = int(datetime.now(timezone.utc).timestamp())
    seconds = max(int(expire) - now, 0)
    days = max(round(seconds / 86400), 1)
    if abs(seconds - days * 86400) > 300:
        raise admin_hierarchy.HierarchyError("duration_preset_required", "Duration must match an Owner preset")
    preset = db.get(OwnerDurationPreset, days)
    if preset is None or not preset.enabled:
        raise admin_hierarchy.HierarchyError("duration_preset_required", "Duration must match an enabled Owner preset")
    return preset


def duration_days_preset(db: Session, duration_days: int) -> OwnerDurationPreset:
    preset = db.get(OwnerDurationPreset, int(duration_days))
    if preset is None or not preset.enabled:
        raise admin_hierarchy.HierarchyError("duration_preset_required", "Duration must match an enabled Owner preset")
    return preset


def form_price(db: Session, settings: MarzhelpAdminSettings, *, data_limit: int | None, expire: int | None) -> int:
    mode = billing_mode(settings)
    if mode == BillingMode.USER_CREDIT:
        raise admin_hierarchy.HierarchyError("user_credit_plan_only", "USER_CREDIT is always Plan Only")
    policy = get_policy(db)
    preset = duration_preset(db, expire)
    if data_limit is None or int(data_limit) <= 0:
        raise admin_hierarchy.HierarchyError("unlimited_form_traffic_forbidden", "Form cannot create unlimited traffic")
    numerator = int(data_limit) * int(policy.price_per_gib_toman) * int(preset.multiplier_basis_points)
    return (numerator + GIB * 10_000 - 1) // (GIB * 10_000)


def adjustment_price(db: Session, *, old_limit: int | None, new_limit: int | None,
                     old_expire: int | None, new_expire: int | None) -> int:
    """Quote added traffic or a purchased duration extension; never refund edits."""
    if not new_limit:
        raise admin_hierarchy.HierarchyError("unlimited_form_traffic_forbidden", "Form requires finite traffic")
    if old_limit is not None and new_limit < old_limit:
        raise admin_hierarchy.HierarchyError("allocated_traffic_reduction_forbidden", "Admin cannot reduce allocated user traffic")
    extension = max(int(new_expire or 0) - int(old_expire or 0), 0)
    added = max(new_limit - int(old_limit or 0), 0)
    if not extension and not added:
        return 0
    if extension:
        if not old_expire or extension % 86400:
            raise admin_hierarchy.HierarchyError("duration_preset_required", "Extension must match an Owner preset")
        preset = duration_days_preset(db, extension // 86400)
        volume = new_limit
    else:
        preset = duration_preset(db, new_expire)
        volume = added
    policy = get_policy(db)
    denominator = GIB * 10_000
    return (volume * int(policy.price_per_gib_toman) * int(preset.multiplier_basis_points)
            + denominator - 1) // denominator
