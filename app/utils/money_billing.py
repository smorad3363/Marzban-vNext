"""Toman wallets, reseller prices, and usage settlement.

All mutations are intended to run inside the caller's database transaction.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import (
    Admin,
    AdminHierarchy,
    AdminMoneyTransaction,
    AdminUserPlan,
    AdminUserPlanPrice,
    AdminUserPlanVersion,
    MarzhelpAdminSettings,
)
from app.utils import admin_hierarchy
from app.utils.admin_billing import BillingMode


GIB = 1024 ** 3
PRICED_PLAN_MODES = {BillingMode.ALLOCATED_TRAFFIC, BillingMode.USER_CREDIT}


def _settings_for_update(db: Session, admin_ids: set[int]) -> dict[int, MarzhelpAdminSettings]:
    if not admin_ids:
        return {}
    rows = (
        db.query(MarzhelpAdminSettings)
        .filter(MarzhelpAdminSettings.admin_id.in_(sorted(admin_ids)))
        .order_by(MarzhelpAdminSettings.admin_id)
        .with_for_update()
        .populate_existing()
        .all()
    )
    result = {row.admin_id: row for row in rows}
    missing = admin_ids - set(result)
    if missing:
        raise admin_hierarchy.HierarchyError(
            "money_policy_missing", f"Monetary policy is missing for administrators: {sorted(missing)}"
        )
    return result


def _direct_parent(db: Session, admin: Admin) -> Admin | None:
    return db.get(Admin, admin.parent_admin_id) if admin.parent_admin_id else None


def _require_direct_child(parent: Admin, child: Admin) -> None:
    if child.parent_admin_id != parent.id:
        raise admin_hierarchy.HierarchyError(
            "money_direct_child_required", "Money and prices can only be assigned to a direct child"
        )


def effective_plan_price(
    db: Session,
    admin: Admin,
    plan: AdminUserPlan,
    version: AdminUserPlanVersion | None = None,
) -> int:
    version = version or db.get(AdminUserPlanVersion, plan.current_version_id)
    if version is None:
        raise admin_hierarchy.HierarchyError("plan_version_missing", "Plan current version is missing")
    if plan.is_trial:
        return 0
    override = db.get(AdminUserPlanPrice, (admin.id, plan.id))
    if override is not None:
        return int(override.price_toman)
    return int(version.price_toman or 0)


def replace_child_plan_prices(
    db: Session,
    *,
    parent: Admin,
    child: Admin,
    prices,
) -> None:
    _require_direct_child(parent, child)
    requested = {int(item.plan_id): int(item.price_toman) for item in prices}
    if len(requested) != len(prices):
        raise admin_hierarchy.HierarchyError("duplicate_plan_price", "A Plan price was submitted more than once")
    plans = {
        plan.id: plan
        for plan in db.query(AdminUserPlan).filter(AdminUserPlan.id.in_(requested)).all()
    } if requested else {}
    if set(plans) != set(requested):
        raise admin_hierarchy.HierarchyError("plan_not_found", "One or more priced Plans do not exist")
    for plan_id, child_price in requested.items():
        plan = plans[plan_id]
        parent_price = effective_plan_price(db, parent, plan)
        if child_price < parent_price:
            raise admin_hierarchy.HierarchyError(
                "plan_price_below_parent",
                f"Child price for Plan {plan_id} cannot be below parent price {parent_price}",
            )
        descendant_prices = (
            db.query(AdminUserPlanPrice.price_toman)
            .join(Admin, Admin.id == AdminUserPlanPrice.admin_id)
            .filter(
                Admin.parent_admin_id == child.id,
                AdminUserPlanPrice.plan_id == plan_id,
            )
            .all()
        )
        if any(int(row[0]) < child_price for row in descendant_prices):
            raise admin_hierarchy.HierarchyError(
                "plan_price_above_child_resale",
                "Raise direct-child resale prices before raising this Admin purchase price",
            )
    db.query(AdminUserPlanPrice).filter(AdminUserPlanPrice.admin_id == child.id).delete(
        synchronize_session=False
    )
    now = datetime.utcnow()
    db.add_all(
        AdminUserPlanPrice(
            admin_id=child.id,
            plan_id=plan_id,
            price_toman=price,
            assigned_by_admin_id=parent.id,
            created_at=now,
            updated_at=now,
        )
        for plan_id, price in sorted(requested.items())
    )


def validate_child_usage_price(
    db: Session,
    *,
    parent: Admin,
    child_price_per_gib_toman: int | None,
) -> None:
    if child_price_per_gib_toman is None or child_price_per_gib_toman < 0:
        raise admin_hierarchy.HierarchyError(
            "usage_price_required", "Actual-usage Admin requires a purchase price per GiB"
        )
    if admin_hierarchy.is_owner(db, parent):
        return
    parent_settings = db.get(MarzhelpAdminSettings, parent.id)
    parent_price = (
        parent_settings.used_traffic_price_per_gib_toman
        if parent_settings is not None
        else None
    )
    if parent_price is None:
        raise admin_hierarchy.HierarchyError(
            "parent_usage_price_missing", "Parent purchase price per GiB is not configured"
        )
    if child_price_per_gib_toman < int(parent_price):
        raise admin_hierarchy.HierarchyError(
            "usage_price_below_parent",
            f"Child price per GiB cannot be below parent price {parent_price}",
        )


def validate_existing_usage_resale_floor(
    db: Session,
    *,
    admin: Admin,
    new_price_per_gib_toman: int | None,
) -> None:
    if new_price_per_gib_toman is None:
        return
    child_prices = (
        db.query(MarzhelpAdminSettings.used_traffic_price_per_gib_toman)
        .join(Admin, Admin.id == MarzhelpAdminSettings.admin_id)
        .filter(
            Admin.parent_admin_id == admin.id,
            MarzhelpAdminSettings.billing_mode == BillingMode.USED_TRAFFIC.value,
            MarzhelpAdminSettings.money_billing_enabled.is_(True),
        )
        .all()
    )
    if any(row[0] is None or int(row[0]) < new_price_per_gib_toman for row in child_prices):
        raise admin_hierarchy.HierarchyError(
            "usage_price_above_child_resale",
            "Raise direct-child per-GiB prices before raising this Admin purchase price",
        )


def transfer_money(
    db: Session,
    *,
    actor: Admin,
    parent: Admin,
    child: Admin,
    amount_toman: int,
    operation_type: str,
    idempotency_key: str,
    note: str | None = None,
) -> tuple[dict, bool]:
    _require_direct_child(parent, child)
    if actor.id != parent.id and not admin_hierarchy.is_owner(db, actor):
        raise admin_hierarchy.HierarchyError("money_transfer_forbidden", "Only the parent or Owner can move this balance")
    if amount_toman <= 0 or operation_type not in {"grant", "reclaim"}:
        raise admin_hierarchy.HierarchyError("invalid_money_transfer", "A positive money transfer is required")
    operation_key = f"money:{operation_type}:{idempotency_key}"
    existing = (
        db.query(AdminMoneyTransaction)
        .filter(AdminMoneyTransaction.operation_key == operation_key)
        .order_by(AdminMoneyTransaction.admin_id)
        .all()
    )
    if existing:
        child_row = next((row for row in existing if row.admin_id == child.id), None)
        if (
            child_row is None
            or child_row.operation_type != operation_type
            or child_row.actor_admin_id != actor.id
            or child_row.counterparty_admin_id != parent.id
            or abs(int(child_row.delta_toman)) != amount_toman
        ):
            raise admin_hierarchy.HierarchyError("idempotency_conflict", "Money idempotency key is inconsistent")
        parent_row = next((row for row in existing if row.admin_id == parent.id), None)
        return {
            "operation_key": operation_key,
            "source_balance_toman": parent_row.balance_after if parent_row else None,
            "target_balance_toman": child_row.balance_after,
        }, False

    parent_is_owner = admin_hierarchy.is_owner(db, parent)
    settings = _settings_for_update(db, {child.id} | ({parent.id} if not parent_is_owner else set()))
    child_settings = settings[child.id]
    parent_settings = settings.get(parent.id)
    direction = 1 if operation_type == "grant" else -1
    child_delta = direction * amount_toman
    parent_delta = -child_delta
    if child_settings.money_balance_toman + child_delta < 0:
        raise admin_hierarchy.HierarchyError("money_balance_insufficient", "Child money balance is insufficient")
    if parent_settings is not None and parent_settings.money_balance_toman + parent_delta < 0:
        raise admin_hierarchy.HierarchyError("money_balance_insufficient", "Parent money balance is insufficient")

    rows = []
    mutations = [(child.id, child_settings, child_delta, parent.id)]
    if parent_settings is not None:
        mutations.append((parent.id, parent_settings, parent_delta, child.id))
    for admin_id, row_settings, delta, counterparty in mutations:
        before = int(row_settings.money_balance_toman or 0)
        row_settings.money_balance_toman = before + delta
        rows.append(
            AdminMoneyTransaction(
                operation_key=operation_key,
                operation_type=operation_type,
                admin_id=admin_id,
                actor_admin_id=actor.id,
                counterparty_admin_id=counterparty,
                delta_toman=delta,
                balance_before=before,
                balance_after=before + delta,
                details={"note": note} if note else None,
            )
        )
    db.add_all(rows)
    return {
        "operation_key": operation_key,
        "source_balance_toman": parent_settings.money_balance_toman if parent_settings is not None else None,
        "target_balance_toman": child_settings.money_balance_toman,
    }, True


def charge_plan_purchase(
    db: Session,
    *,
    buyer: Admin,
    actor: Admin,
    plan: AdminUserPlan,
    version: AdminUserPlanVersion,
    operation_type: str,
    idempotency_key: str,
    user_id: int | None = None,
) -> None:
    if plan.is_trial or admin_hierarchy.is_owner(db, buyer):
        return
    buyer_settings = db.get(MarzhelpAdminSettings, buyer.id)
    if buyer_settings is None or not buyer_settings.money_billing_enabled:
        return
    if BillingMode(buyer_settings.billing_mode) not in PRICED_PLAN_MODES:
        # USED_TRAFFIC may provision from Plans, but pays only for consumption.
        return
    operation_key = f"plan-money:{idempotency_key}"
    if db.query(AdminMoneyTransaction.id).filter(AdminMoneyTransaction.operation_key == operation_key).first():
        return

    chain: list[tuple[Admin, Admin]] = []
    current = buyer
    while not admin_hierarchy.is_owner(db, current):
        parent = _direct_parent(db, current)
        if parent is None:
            raise admin_hierarchy.HierarchyError("money_parent_missing", "Admin money chain has no parent")
        chain.append((current, parent))
        parent_settings = db.get(MarzhelpAdminSettings, parent.id)
        if admin_hierarchy.is_owner(db, parent) or parent_settings is None:
            break
        if BillingMode(parent_settings.billing_mode) not in PRICED_PLAN_MODES:
            break
        current = parent

    price_overrides = {
        row.admin_id: int(row.price_toman)
        for row in db.query(AdminUserPlanPrice)
        .filter(
            AdminUserPlanPrice.plan_id == plan.id,
            AdminUserPlanPrice.admin_id.in_([child.id for child, _ in chain]),
        )
        .all()
    } if chain else {}
    base_price = int(version.price_toman or 0)
    edges: list[tuple[Admin, Admin, int]] = []
    downstream_price: int | None = None
    for child, parent in chain:
        price = price_overrides.get(child.id, base_price)
        if downstream_price is not None and downstream_price < price:
            raise admin_hierarchy.HierarchyError(
                "plan_price_below_parent",
                "A reseller Plan price is below its current parent purchase price",
            )
        edges.append((child, parent, price))
        downstream_price = price

    non_owner_ids = {
        item.id for edge in edges for item in edge[:2] if not admin_hierarchy.is_owner(db, item)
    }
    settings = _settings_for_update(db, non_owner_ids)
    deltas: dict[int, int] = defaultdict(int)
    counterparties: dict[int, int] = {}
    for child, parent, price in edges:
        deltas[child.id] -= price
        counterparties[child.id] = parent.id
        if not admin_hierarchy.is_owner(db, parent):
            deltas[parent.id] += price
            counterparties[parent.id] = child.id
    for admin_id, delta in deltas.items():
        if int(settings[admin_id].money_balance_toman or 0) + delta < 0:
            raise admin_hierarchy.HierarchyError("money_balance_insufficient", "Money balance is insufficient for this Plan")
    for admin_id in sorted(deltas):
        row_settings = settings[admin_id]
        before = int(row_settings.money_balance_toman or 0)
        after = before + deltas[admin_id]
        row_settings.money_balance_toman = after
        db.add(
            AdminMoneyTransaction(
                operation_key=operation_key,
                operation_type=f"plan_{operation_type}",
                admin_id=admin_id,
                actor_admin_id=actor.id,
                counterparty_admin_id=counterparties.get(admin_id),
                delta_toman=deltas[admin_id],
                balance_before=before,
                balance_after=after,
                plan_id=plan.id,
                version_id=version.id,
                user_id=user_id,
            )
        )


def charge_form_purchase(
    db: Session,
    *,
    buyer: Admin,
    actor: Admin,
    user_id: int,
    amount_toman: int,
    operation_key: str | None = None,
) -> None:
    """Charge one deterministic ALLOCATED_TRAFFIC Form purchase."""
    if admin_hierarchy.is_owner(db, buyer) or amount_toman <= 0:
        return
    operation_key = operation_key or f"form-money:create:{user_id}"
    if db.query(AdminMoneyTransaction.id).filter(AdminMoneyTransaction.operation_key == operation_key).first():
        return
    settings = (
        db.query(MarzhelpAdminSettings)
        .filter(MarzhelpAdminSettings.admin_id == buyer.id)
        .with_for_update()
        .one()
    )
    if BillingMode(settings.billing_mode) != BillingMode.ALLOCATED_TRAFFIC:
        return
    before = int(settings.money_balance_toman or 0)
    if before < amount_toman:
        raise admin_hierarchy.HierarchyError("money_balance_insufficient", "Admin Toman wallet is insufficient")
    settings.money_balance_toman = before - amount_toman
    db.add(
        AdminMoneyTransaction(
            operation_key=operation_key,
            operation_type="form_create",
            admin_id=buyer.id,
            actor_admin_id=actor.id,
            counterparty_admin_id=buyer.parent_admin_id,
            delta_toman=-amount_toman,
            balance_before=before,
            balance_after=before - amount_toman,
            user_id=user_id,
            details={"formula": "GiB x PricePerGiB x DurationMultiplier"},
        )
    )


def settle_used_traffic(db: Session, usage_by_admin: dict[int, int]) -> set[int]:
    usage_by_admin = {int(k): int(v) for k, v in usage_by_admin.items() if int(v) > 0}
    if not usage_by_admin:
        return set()
    relevant_ids = set(usage_by_admin)
    relevant_ids.update(
        row[0]
        for row in db.query(AdminHierarchy.ancestor_id)
        .filter(AdminHierarchy.descendant_id.in_(sorted(usage_by_admin)))
        .distinct()
        .all()
    )
    admins = {
        row.id: row for row in db.query(Admin).filter(Admin.id.in_(sorted(relevant_ids))).all()
    }
    all_settings = {
        row.admin_id: row
        for row in db.query(MarzhelpAdminSettings)
        .filter(MarzhelpAdminSettings.admin_id.in_(sorted(relevant_ids)))
        .all()
    }
    billable: dict[int, int] = defaultdict(int)
    for owner_id, byte_count in usage_by_admin.items():
        current = admins.get(owner_id)
        while current is not None and not admin_hierarchy.is_owner(db, current):
            settings = all_settings.get(current.id)
            if (
                settings is None
                or not settings.money_billing_enabled
                or BillingMode(settings.billing_mode) != BillingMode.USED_TRAFFIC
            ):
                break
            billable[current.id] += byte_count
            current = admins.get(current.parent_admin_id)
    if not billable:
        return set()

    involved = set(billable)
    for admin_id in billable:
        parent_id = admins[admin_id].parent_admin_id
        if parent_id and parent_id in admins and not admin_hierarchy.is_owner(db, admins[parent_id]):
            involved.add(parent_id)
    locked = _settings_for_update(db, involved)
    deltas: dict[int, int] = defaultdict(int)
    charges: dict[int, int] = {}
    for admin_id, byte_count in billable.items():
        row = locked[admin_id]
        price = row.used_traffic_price_per_gib_toman
        if price is None:
            continue
        numerator = int(row.usage_billing_remainder or 0) + byte_count * int(price)
        charge, row.usage_billing_remainder = divmod(numerator, GIB)
        charges[admin_id] = charge
        deltas[admin_id] -= charge
        parent = admins.get(admins[admin_id].parent_admin_id)
        if parent is not None and not admin_hierarchy.is_owner(db, parent):
            deltas[parent.id] += charge
    # One mutable bucket per Admin/hour prevents the high-frequency usage job
    # from growing the ledger by thousands of rows per Admin/day. Closed hours
    # remain immutable, while wallet arithmetic stays exact on every sample.
    operation_key = datetime.utcnow().strftime("usage:%Y%m%d%H")
    existing_rows = {
        row.admin_id: row
        for row in db.query(AdminMoneyTransaction)
        .filter(
            AdminMoneyTransaction.operation_key == operation_key,
            AdminMoneyTransaction.admin_id.in_(sorted(deltas)),
        )
        .with_for_update()
        .populate_existing()
        .all()
    }
    for admin_id in sorted(deltas):
        row = locked[admin_id]
        before = int(row.money_balance_toman or 0)
        after = before + deltas[admin_id]
        row.money_balance_toman = after
        ledger = existing_rows.get(admin_id)
        if ledger is None:
            db.add(AdminMoneyTransaction(
                operation_key=operation_key,
                operation_type="usage_settlement",
                admin_id=admin_id,
                actor_admin_id=admin_id,
                counterparty_admin_id=admins[admin_id].parent_admin_id,
                delta_toman=deltas[admin_id],
                balance_before=before,
                balance_after=after,
                details={"usage_bytes": billable.get(admin_id, 0), "gross_charge_toman": charges.get(admin_id, 0)},
            ))
        else:
            details = dict(ledger.details or {})
            details["usage_bytes"] = int(details.get("usage_bytes", 0)) + billable.get(admin_id, 0)
            details["gross_charge_toman"] = int(details.get("gross_charge_toman", 0)) + charges.get(admin_id, 0)
            ledger.delta_toman = int(ledger.delta_toman) + deltas[admin_id]
            ledger.balance_after = after
            ledger.details = details
    crossed = {
        admin_id
        for admin_id in billable
        if int(locked[admin_id].money_balance_toman or 0) <= 0
        and locked[admin_id].account_status_id
        == admin_hierarchy.ACCOUNT_STATUS_IDS[admin_hierarchy.ACTIVE]
    }
    if not crossed:
        return set()
    covered = {
        descendant_id
        for ancestor_id, descendant_id in db.query(
            AdminHierarchy.ancestor_id, AdminHierarchy.descendant_id
        )
        .filter(
            AdminHierarchy.ancestor_id.in_(sorted(crossed)),
            AdminHierarchy.descendant_id.in_(sorted(crossed)),
            AdminHierarchy.depth > 0,
        )
        .all()
    }
    owner = admins.get(admin_hierarchy.owner_id(db))
    if owner is None:
        raise admin_hierarchy.HierarchyError(
            "owner_missing", "Owner is required for prepaid usage suspension"
        )
    suspended: set[int] = set()
    for admin_id in sorted(crossed - covered):
        admin_hierarchy.suspend_admin(
            db,
            actor=owner,
            target=admins[admin_id],
            reason_id=2,
            include_subtree=True,
            commit=False,
        )
        suspended.add(admin_id)
    return suspended
