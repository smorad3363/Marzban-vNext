"""separate Access Groups from commercial Plans

Revision ID: a7c4e9d2f610
Revises: d3a5c7e9f102
"""

from alembic import op
import sqlalchemy as sa


revision = "a7c4e9d2f610"
down_revision = "d3a5c7e9f102"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    mode = bind.execute(
        sa.text("SELECT code FROM admin_user_creation_modes WHERE id = 3")
    ).scalar()
    if mode is None:
        bind.execute(sa.text("INSERT INTO admin_user_creation_modes (id, code) VALUES (3, 'BOTH')"))
    elif mode != "BOTH":
        raise RuntimeError(f"admin_user_creation_modes id 3 already belongs to {mode!r}")
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "access_groups" not in tables:
        op.create_table(
            "access_groups",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
            sa.Column("owner_admin_id", sa.Integer(), sa.ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("description", sa.String(512), nullable=True),
            sa.Column("archived_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("owner_admin_id", "name", name="uq_access_groups_owner_name"),
        )
        op.create_index("ix_access_groups_owner_active", "access_groups", ["owner_admin_id", "archived_at", "id"])
        op.create_table(
            "access_group_inbounds",
            sa.Column("access_group_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("access_groups.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("inbound_tag", sa.String(256), primary_key=True),
        )
        op.create_table(
            "access_group_hosts",
            sa.Column("access_group_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("access_groups.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("inbound_tag", sa.String(256), primary_key=True),
            sa.Column("host_id", sa.Integer(), primary_key=True),
        )
        op.create_table(
            "access_group_nodes",
            sa.Column("access_group_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.ForeignKey("access_groups.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("node_id", sa.Integer(), sa.ForeignKey("nodes.id", ondelete="CASCADE"), primary_key=True),
        )
    tables = set(sa.inspect(bind).get_table_names())
    if "owner_commercial_policy" not in tables:
        op.create_table(
            "owner_commercial_policy",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("price_per_gib_toman", sa.BigInteger(), nullable=False),
            sa.Column("allow_unlimited_duration", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        bind.execute(sa.text(
            "INSERT INTO owner_commercial_policy (id, price_per_gib_toman, allow_unlimited_duration, updated_at) "
            "VALUES (1, 1000, 0, CURRENT_TIMESTAMP)"
        ))
    if "owner_duration_presets" not in tables:
        op.create_table(
            "owner_duration_presets",
            sa.Column("duration_days", sa.Integer(), primary_key=True),
            sa.Column("multiplier_basis_points", sa.Integer(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("duration_days > 0", name="ck_owner_duration_positive"),
            sa.CheckConstraint("multiplier_basis_points > 0", name="ck_owner_duration_multiplier_positive"),
        )
        for days, multiplier in ((1, 8000), (7, 9000), (30, 10000), (60, 11000)):
            bind.execute(
                sa.text("INSERT INTO owner_duration_presets (duration_days, multiplier_basis_points, enabled, updated_at) VALUES (:days, :multiplier, 1, CURRENT_TIMESTAMP)"),
                {"days": days, "multiplier": multiplier},
            )

    tables = set(sa.inspect(bind).get_table_names())
    if "backup_settings" not in tables:
        op.create_table(
            "backup_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("destination", sa.String(24), nullable=False, server_default="LOCAL"),
            sa.Column("schedule", sa.String(8), nullable=False, server_default="24h"),
            sa.Column("retention_count", sa.Integer(), nullable=False, server_default="14"),
            sa.Column("telegram_bot_token", sa.String(256), nullable=True),
            sa.Column("telegram_chat_id", sa.String(64), nullable=True),
            sa.Column("smtp_host", sa.String(256), nullable=True),
            sa.Column("smtp_port", sa.Integer(), nullable=True),
            sa.Column("smtp_username", sa.String(256), nullable=True),
            sa.Column("smtp_password", sa.String(256), nullable=True),
            sa.Column("smtp_use_tls", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("email_from", sa.String(320), nullable=True),
            sa.Column("email_to", sa.String(320), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        bind.execute(sa.text("INSERT INTO backup_settings (id) VALUES (1)"))

    columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "access_group_id" not in columns:
        op.add_column(
            "users",
            sa.Column("access_group_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        )
        op.create_foreign_key(
            "fk_users_access_group_id", "users", "access_groups", ["access_group_id"], ["id"], ondelete="SET NULL"
        )
        op.create_index("ix_users_access_group_id", "users", ["access_group_id"])

    # Compatibility backfill: each legacy Plan version becomes a network-only group.
    versions = bind.execute(sa.text(
        "SELECT v.id, v.plan_id, p.owner_admin_id, p.name "
        "FROM admin_user_plan_versions v JOIN admin_user_plans p ON p.id = v.plan_id "
        "WHERE EXISTS (SELECT 1 FROM admin_user_plan_inbounds i WHERE i.version_id = v.id)"
    )).mappings().all()
    version_groups = {}
    for row in versions:
        name = f"Migrated access: {row['name']} v{row['id']}"
        existing_group = bind.execute(
            sa.text("SELECT id FROM access_groups WHERE owner_admin_id = :owner AND name = :name"),
            {"owner": row["owner_admin_id"], "name": name[:128]},
        ).scalar()
        if existing_group is None:
            result = bind.execute(
                sa.text("INSERT INTO access_groups (owner_admin_id, name, description, created_at, updated_at) VALUES (:owner, :name, :description, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                {"owner": row["owner_admin_id"], "name": name[:128], "description": "Migrated from legacy Plan network scope"},
            )
            group_id = result.lastrowid
        else:
            group_id = existing_group
        version_groups[row["id"]] = group_id
        existing_inbounds = bind.execute(
            sa.text("SELECT COUNT(*) FROM access_group_inbounds WHERE access_group_id = :group_id"),
            {"group_id": group_id},
        ).scalar()
        if not existing_inbounds:
            bind.execute(sa.text(
                "INSERT INTO access_group_inbounds (access_group_id, inbound_tag) "
                "SELECT :group_id, inbound_tag FROM admin_user_plan_inbounds WHERE version_id = :version_id"
            ), {"group_id": group_id, "version_id": row["id"]})
            bind.execute(sa.text(
                "INSERT INTO access_group_hosts (access_group_id, inbound_tag, host_id) "
                "SELECT :group_id, inbound_tag, host_id FROM admin_user_plan_hosts WHERE version_id = :version_id"
            ), {"group_id": group_id, "version_id": row["id"]})

    latest_assignments = bind.execute(sa.text(
        "SELECT a.user_id, a.version_id FROM user_plan_assignments a "
        "JOIN (SELECT user_id, MAX(id) AS id FROM user_plan_assignments GROUP BY user_id) latest ON latest.id = a.id"
    )).mappings().all()
    for row in latest_assignments:
        group_id = version_groups.get(row["version_id"])
        if group_id is not None:
            bind.execute(
                sa.text("UPDATE users SET access_group_id = :group_id WHERE id = :user_id AND access_group_id IS NULL"),
                {"group_id": group_id, "user_id": row["user_id"]},
            )


def downgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "access_group_id" in columns:
        op.drop_index("ix_users_access_group_id", table_name="users")
        op.drop_constraint("fk_users_access_group_id", "users", type_="foreignkey")
        op.drop_column("users", "access_group_id")
    for table in (
        "backup_settings", "owner_duration_presets", "owner_commercial_policy", "access_group_nodes",
        "access_group_hosts", "access_group_inbounds", "access_groups",
    ):
        if table in set(sa.inspect(bind).get_table_names()):
            op.drop_table(table)
