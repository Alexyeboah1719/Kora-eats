"""add_soft_delete_for_data_retention

Revision ID: a3599a8bab34
Revises: 62cb864a5afd
Create Date: 2026-11-11 12:15:13.336943
"""
from alembic import op
import sqlalchemy as sa


revision = 'a3599a8bab34'
down_revision = '62cb864a5afd'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('wallet_accounts', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('payout_accounts', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('withdrawals', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('payments', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_users_deleted_at', 'users', ['deleted_at'])
    op.create_index('ix_wallet_accounts_deleted_at', 'wallet_accounts', ['deleted_at'])
    op.create_index('ix_payout_accounts_deleted_at', 'payout_accounts', ['deleted_at'])
    op.create_index('ix_withdrawals_deleted_at', 'withdrawals', ['deleted_at'])
    op.create_index('ix_payments_deleted_at', 'payments', ['deleted_at'])


def downgrade() -> None:
    op.drop_index('ix_payments_deleted_at', 'payments')
    op.drop_index('ix_withdrawals_deleted_at', 'withdrawals')
    op.drop_index('ix_payout_accounts_deleted_at', 'payout_accounts')
    op.drop_index('ix_wallet_accounts_deleted_at', 'wallet_accounts')
    op.drop_index('ix_users_deleted_at', 'users')
    op.drop_column('payments', 'deleted_at')
    op.drop_column('withdrawals', 'deleted_at')
    op.drop_column('payout_accounts', 'deleted_at')
    op.drop_column('wallet_accounts', 'deleted_at')
    op.drop_column('users', 'deleted_at')
