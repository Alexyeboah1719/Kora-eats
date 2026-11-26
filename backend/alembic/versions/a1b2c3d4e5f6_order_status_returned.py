"""order status RETURNED (rider brings food back to vendor)

Revision ID: a1b2c3d4e5f6
Revises: dd6b6ae568c7
Create Date: 2026-11-19 00:00:00.000000
"""
from alembic import op

revision = 'a1b2c3d4e5f6'
down_revision = 'd0ff6adb10c6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE must run outside the migration transaction.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'RETURNED'")


def downgrade() -> None:
    pass  # PostgreSQL cannot remove a value from an enum.
