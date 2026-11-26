"""add_vendor_cod_enabled

Revision ID: d0ff6adb10c6
Revises: a3599a8bab34
Create Date: 2026-11-18 18:57:01.527652
"""
from alembic import op
import sqlalchemy as sa


revision = 'd0ff6adb10c6'
down_revision = 'a3599a8bab34'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('vendors', sa.Column('cod_enabled', sa.Boolean(), nullable=True, server_default='true'))


def downgrade() -> None:
    op.drop_column('vendors', 'cod_enabled')
