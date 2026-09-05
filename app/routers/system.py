from typing import Dict, List, Union

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from app import __version__, xray
from app.db import Session, crud, get_db
from app.db.models import AdminUserPlan, MarzhelpMetadata, ProxyHost as DBProxyHost
from app.models.admin import Admin
from app.models.proxy import HostUpdateAction, HostUpdateImpact, ProxyHost, ProxyInbound, ProxyTypes
from app.models.system import DashboardOverview, SystemStats
from app.models.user import UserStatus
from app.utils import access_groups, admin_hierarchy, admin_plans, dashboard_metrics, marzhelp_policy, responses
from app.utils.audit import AuditLogService
from app.utils.network_impact import analyze_host_update
from app.utils.system import cpu_usage, memory_usage, realtime_bandwidth

router = APIRouter(tags=["System"], prefix="/api", responses={401: responses._401})


@router.get("/dashboard/overview", response_model=DashboardOverview)
def get_dashboard_overview(
    timezone_offset_minutes: int = 0,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """Return bounded, authorization-scoped dashboard aggregates."""
    if timezone_offset_minutes < -840 or timezone_offset_minutes > 840:
        raise HTTPException(status_code=422, detail="Invalid timezone offset")
    dbadmin = crud.get_admin(db, admin.username)
    return dashboard_metrics.overview(
        db,
        dbadmin or admin,
        timezone_offset_minutes=timezone_offset_minutes,
    )


@router.get("/marzhelp/compatibility")
def get_marzhelp_compatibility(db: Session = Depends(get_db)):
    """Public installer preflight backed by the migrated database marker."""

    rows = db.query(MarzhelpMetadata).all()
    metadata = {row.key: row.value for row in rows}
    if metadata.get("source_id") != "smorad3363-marzban" or metadata.get("schema_version") != "1":
        raise HTTPException(status_code=409, detail="MarzHelp schema compatibility marker is missing")
    return {
        "compatible": True,
        "source_id": metadata["source_id"],
        "schema_version": int(metadata["schema_version"]),
        "minimum_marzhelp_version": metadata.get("minimum_marzhelp_version", "2"),
        "marzban_version": __version__,
    }


@router.get("/system", response_model=SystemStats)
def get_system_stats(
    db: Session = Depends(get_db), admin: Admin = Depends(Admin.get_current)
):
    """Fetch system stats including memory, CPU, and user metrics."""
    dbadmin: Union[Admin, None] = crud.get_admin(db, admin.username)
    effective_admin = dbadmin or admin
    hierarchy_on = dbadmin is not None and admin_hierarchy.hierarchy_enabled(db)
    owner_role = admin_hierarchy.is_owner(db, effective_admin)
    allowed_inbounds = marzhelp_policy.allowed_inbound_tags(db, effective_admin)
    count_scope = dbadmin.id if hierarchy_on and not owner_role else None
    legacy_admin = dbadmin if not hierarchy_on and not admin.is_sudo else None

    total_user = crud.get_users_count(
        db,
        admin=legacy_admin,
        scope_admin_id=count_scope,
        allowed_inbounds=allowed_inbounds,
    )
    users_active = crud.get_users_count(
        db, status=UserStatus.active, admin=legacy_admin, scope_admin_id=count_scope,
        allowed_inbounds=allowed_inbounds,
    )
    users_disabled = crud.get_users_count(
        db, status=UserStatus.disabled, admin=legacy_admin, scope_admin_id=count_scope,
        allowed_inbounds=allowed_inbounds,
    )
    users_on_hold = crud.get_users_count(
        db, status=UserStatus.on_hold, admin=legacy_admin, scope_admin_id=count_scope,
        allowed_inbounds=allowed_inbounds,
    )
    users_expired = crud.get_users_count(
        db, status=UserStatus.expired, admin=legacy_admin, scope_admin_id=count_scope,
        allowed_inbounds=allowed_inbounds,
    )
    users_limited = crud.get_users_count(
        db, status=UserStatus.limited, admin=legacy_admin, scope_admin_id=count_scope,
        allowed_inbounds=allowed_inbounds,
    )
    online_users = crud.count_online_users(
        db,
        24,
        admin=legacy_admin,
        scope_admin_id=count_scope,
        allowed_inbounds=allowed_inbounds,
    )
    if owner_role:
        mem = memory_usage()
        cpu = cpu_usage()
        system = crud.get_system_usage(db)
        realtime_bandwidth_stats = realtime_bandwidth()
    else:
        mem = type("Resource", (), {"total": 0, "used": 0})()
        cpu = type("CPU", (), {"cores": 0, "percent": 0.0})()
        system = type("Bandwidth", (), {"uplink": 0, "downlink": 0})()
        realtime_bandwidth_stats = type("Realtime", (), {"incoming_bytes": 0, "outgoing_bytes": 0})()

    return SystemStats(
        version=__version__,
        mem_total=mem.total,
        mem_used=mem.used,
        cpu_cores=cpu.cores,
        cpu_usage=cpu.percent,
        total_user=total_user,
        online_users=online_users,
        users_active=users_active,
        users_disabled=users_disabled,
        users_expired=users_expired,
        users_limited=users_limited,
        users_on_hold=users_on_hold,
        incoming_bandwidth=system.uplink if owner_role else 0,
        outgoing_bandwidth=system.downlink if owner_role else 0,
        incoming_bandwidth_speed=realtime_bandwidth_stats.incoming_bytes if owner_role else 0,
        outgoing_bandwidth_speed=realtime_bandwidth_stats.outgoing_bytes if owner_role else 0,
    )


@router.get("/inbounds", response_model=Dict[ProxyTypes, List[ProxyInbound]])
def get_inbounds(
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.get_current),
):
    """Retrieve inbound configurations grouped by protocol."""
    dbadmin = crud.get_admin(db, admin.username)
    allowed = marzhelp_policy.allowed_inbound_tags(db, dbadmin or admin)
    if allowed is None:
        return xray.config.inbounds_by_protocol
    return {
        protocol: [inbound for inbound in inbounds if inbound["tag"] in allowed]
        for protocol, inbounds in xray.config.inbounds_by_protocol.items()
        if any(inbound["tag"] in allowed for inbound in inbounds)
    }


@router.get(
    "/hosts", response_model=Dict[str, List[ProxyHost]], responses={403: responses._403}
)
def get_hosts(
    db: Session = Depends(get_db), admin: Admin = Depends(Admin.check_sudo_admin)
):
    """Get a list of proxy hosts grouped by inbound tag."""
    hosts = {tag: crud.get_hosts(db, tag, include_legacy=False) for tag in xray.config.inbounds_by_tag}
    return hosts


@router.put(
    "/hosts", response_model=Dict[str, List[ProxyHost]], responses={403: responses._403}
)
def modify_hosts(
    request: Request,
    bg: BackgroundTasks,
    modified_hosts: Dict[str, List[ProxyHost]],
    impact_action: HostUpdateAction | None = None,
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.check_sudo_admin),
):
    """Modify proxy hosts and update the configuration."""
    if impact_action is not None and not isinstance(impact_action, HostUpdateAction):
        impact_action = HostUpdateAction(impact_action)
    for inbound_tag in modified_hosts:
        if inbound_tag not in xray.config.inbounds_by_tag:
            raise HTTPException(
                status_code=400, detail=f"Inbound {inbound_tag} doesn't exist"
            )

    impact = analyze_host_update(db, modified_hosts)
    if impact.invalid_plan_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "host_change_would_invalidate_plan",
                "message": "این تغییر حداقل یک پلن را بدون اتصال معتبر باقی می‌گذارد. ابتدا برای پلن‌های نمایش‌داده‌شده Host جایگزین انتخاب کنید.",
                "impact": impact.model_dump(),
                "allowed_actions": ["cancel"],
            },
        )
    if impact.requires_confirmation and impact_action is None:
        allowed_actions = ["apply_current", "future_only", "cancel"]
        if impact.removed_host_ids:
            allowed_actions.insert(2, "detach")
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "host_change_confirmation_required",
                "message": f"این تغییر روی {impact.affected_plan_count} پلن و {impact.active_user_count} کاربر فعال اثر می‌گذارد. روش اعمال را انتخاب کنید.",
                "impact": impact.model_dump(),
                "allowed_actions": allowed_actions,
            },
        )
    replacement_hosts: dict[int, ProxyHost] = {}
    try:
        if impact_action == HostUpdateAction.future_only:
            existing = {
                host.id: host
                for host in db.query(DBProxyHost)
                .filter(DBProxyHost.id.in_(impact.changed_host_ids + impact.removed_host_ids))
                .with_for_update()
                .all()
            }
            rewritten: Dict[str, List[ProxyHost]] = {}
            for inbound_tag, hosts in modified_hosts.items():
                rewritten[inbound_tag] = []
                for host in hosts:
                    if host.id in impact.changed_host_ids:
                        existing[host.id].is_legacy = True
                        clone = host.model_copy(update={"id": None})
                        replacement_hosts[host.id] = clone
                        rewritten[inbound_tag].append(clone)
                    else:
                        rewritten[inbound_tag].append(host)
            for host_id in impact.removed_host_ids:
                existing[host_id].is_legacy = True
            modified_hosts = rewritten

        for inbound_tag, hosts in modified_hosts.items():
            crud.update_hosts(db, inbound_tag, hosts)

        replacement_ids = {
            old_id: host.id for old_id, host in replacement_hosts.items()
        }
        synced_user_ids: list[int] = []
        synced_user_ids.extend(access_groups.propagate_host_changes(
            db,
            replacement_ids=replacement_ids,
            removed_ids=set(impact.removed_host_ids),
        ))
        for plan_id in impact.affected_plan_ids:
            plan = (
                db.query(AdminUserPlan)
                .filter(AdminUserPlan.id == plan_id)
                .with_for_update()
                .one()
            )
            inbounds, hosts = admin_plans.version_network_scope(db, plan.current_version_id)
            revised_hosts = {
                tag: {
                    replacement_ids.get(host_id, host_id)
                    for host_id in host_ids
                    if host_id not in impact.removed_host_ids
                }
                for tag, host_ids in hosts.items()
            }
            if revised_hosts != hosts:
                previous, revision = admin_plans.add_network_revision(
                    db,
                    actor=admin,
                    plan=plan,
                    inbounds=inbounds,
                    hosts=revised_hosts,
                )
                if impact_action != HostUpdateAction.future_only:
                    synced_user_ids.extend(admin_plans.sync_active_users_to_network_revision(
                        db,
                        actor=admin,
                        plan=plan,
                        previous_version=previous,
                        revision=revision,
                    ))

        AuditLogService.log(
            db,
            admin,
            "settings.hosts_update",
            "proxy_hosts",
            f"Admin {admin.username} updated proxy hosts",
            details={
                "inbounds": {
                    inbound_tag: len(hosts)
                    for inbound_tag, hosts in modified_hosts.items()
                },
                "impact_action": impact_action.value if impact_action else None,
                "affected_plan_count": impact.affected_plan_count,
                "active_user_count": impact.active_user_count,
                "host_values_stored": False,
            },
            request=request,
            commit=False,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    xray.hosts.update()
    for user_id in sorted(set(synced_user_ids)):
        bg.add_task(xray.operations.update_user_by_id, user_id=user_id)

    return {
        tag: crud.get_hosts(db, tag, include_legacy=False)
        for tag in xray.config.inbounds_by_tag
    }


@router.post(
    "/hosts/impact", response_model=HostUpdateImpact, responses={403: responses._403}
)
def host_update_impact(
    modified_hosts: Dict[str, List[ProxyHost]],
    db: Session = Depends(get_db),
    admin: Admin = Depends(Admin.check_sudo_admin),
):
    """Preview Plan and active-User impact without mutating data."""
    for inbound_tag in modified_hosts:
        if inbound_tag not in xray.config.inbounds_by_tag:
            raise HTTPException(status_code=400, detail=f"Inbound {inbound_tag} doesn't exist")
    return analyze_host_update(db, modified_hosts)
