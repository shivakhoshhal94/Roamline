"""Retain original per-category evidence and collection strategy.

Revision ID: 0002_category_evidence
Revises: 0001_destination_catalog
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_category_evidence"
down_revision = "0001_destination_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("destination_enrichments", sa.Column("request_strategy", sa.String(50), nullable=False, server_default="legacy-union-v1"))
    op.add_column("destination_enrichments", sa.Column("category_evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade() -> None:
    op.drop_column("destination_enrichments", "category_evidence")
    op.drop_column("destination_enrichments", "request_strategy")
