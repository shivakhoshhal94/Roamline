"""Durable resumable category work cache, separate from published snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0003_resumable_categories"
down_revision = "0002_category_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("destinations", sa.Column("category_cache_invalidated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table("destination_category_evidence",
        sa.Column("destination_id", sa.BigInteger(), sa.ForeignKey("destinations.geoname_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("category", sa.String(100), primary_key=True),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("place_id", sa.String(2048), nullable=False),
        sa.Column("resolution_evidence", sa.JSON(), nullable=False),
        sa.Column("catalog_fingerprint", sa.String(64), nullable=False),
        sa.Column("mapping_version", sa.String(50), nullable=False),
        sa.Column("request_strategy", sa.String(50), nullable=False),
        sa.Column("query_provenance", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("destination_category_evidence")
    op.drop_column("destinations", "category_cache_invalidated_at")
