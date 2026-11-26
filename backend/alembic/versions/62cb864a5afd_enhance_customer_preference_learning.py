"""enhance_customer_preference_learning

Revision ID: 62cb864a5afd
Revises: dd6b6ae568c7
Create Date: 2026-11-11 11:27:17.745213
"""
from alembic import op
import sqlalchemy as sa


revision = '62cb864a5afd'
down_revision = 'dd6b6ae568c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('customer_preferences', sa.Column('favorite_dishes', sa.JSON(), server_default='[]', nullable=False))
    op.add_column('customer_preferences', sa.Column('protein_preference', sa.String(length=20), nullable=True))
    op.add_column('customer_preferences', sa.Column('typical_order_hour', sa.Integer(), nullable=True))
    op.add_column('customer_preferences', sa.Column('prefers_fast_delivery', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('customer_preferences', sa.Column('typical_portion', sa.String(length=15), nullable=True))
    op.add_column('customer_preferences', sa.Column('last_order_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('customer_preferences', 'last_order_at')
    op.drop_column('customer_preferences', 'typical_portion')
    op.drop_column('customer_preferences', 'prefers_fast_delivery')
    op.drop_column('customer_preferences', 'typical_order_hour')
    op.drop_column('customer_preferences', 'protein_preference')
    op.drop_column('customer_preferences', 'favorite_dishes')
