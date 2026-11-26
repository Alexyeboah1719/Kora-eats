"""customer refund requests on returned prepaid orders

Revision ID: f6e5d4c3b2a1
Revises: 8f23a16b5877
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa


revision = "f6e5d4c3b2a1"
down_revision = "8f23a16b5877"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("refund_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("refund_status", sa.String(length=20), nullable=True))
    op.add_column("orders", sa.Column("refund_review_note", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "refund_review_note")
    op.drop_column("orders", "refund_status")
    op.drop_column("orders", "refund_requested_at")
