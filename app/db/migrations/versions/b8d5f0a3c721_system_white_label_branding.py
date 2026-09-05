"""add system white-label branding

Revision ID: b8d5f0a3c721
Revises: a7c4e9d2f610
"""
from alembic import op
import sqlalchemy as sa


revision = "b8d5f0a3c721"
down_revision = "a7c4e9d2f610"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "system_branding_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("panel_name", sa.String(80), nullable=False, server_default="Operations Console"),
        sa.Column("login_title", sa.String(120), nullable=False, server_default="Secure operator access"),
        sa.Column("description", sa.String(280), nullable=True),
        sa.Column("logo_filename", sa.String(255), nullable=True),
        sa.Column("favicon_filename", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.get_bind().execute(sa.text("INSERT INTO system_branding_settings (id) VALUES (1)"))


def downgrade():
    op.drop_table("system_branding_settings")
