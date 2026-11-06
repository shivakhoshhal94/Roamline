"""Destination catalog and versioned enrichment snapshots.

Revision ID: 0001_destination_catalog
Revises: None
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_destination_catalog"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "destinations",
        sa.Column("geoname_id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("ascii_name", sa.String(200), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("country_name", sa.String(200), nullable=False),
        sa.Column("continent_code", sa.String(2), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("population", sa.BigInteger(), nullable=False),
        sa.Column("timezone", sa.String(64)),
        sa.Column("feature_code", sa.String(10), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("source_modified", sa.Date(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("country_info_sha256", sa.String(64), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("population >= 0"),
        sa.CheckConstraint("latitude >= -90 AND latitude <= 90"),
        sa.CheckConstraint("longitude >= -180 AND longitude <= 180"),
    )
    op.create_index("ix_destinations_country_code", "destinations", ["country_code"])
    op.create_table(
        "destination_enrichments",
        sa.Column("destination_id", sa.BigInteger(), sa.ForeignKey("destinations.geoname_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("place_id", sa.String(2048), nullable=False),
        sa.Column("resolution_evidence", sa.JSON(), nullable=False),
        sa.Column("mapping_version", sa.String(50), nullable=False),
        sa.Column("normalization_version", sa.String(50), nullable=False),
        sa.Column("catalog_fingerprint", sa.String(64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "destination_features",
        sa.Column("destination_id", sa.BigInteger(), sa.ForeignKey("destination_enrichments.destination_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("feature", sa.String(80), primary_key=True),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("raw_count", sa.Integer(), nullable=False),
        sa.Column("capped", sa.Boolean(), nullable=False),
        sa.Column("normalized_value", sa.Float(), nullable=False),
        sa.CheckConstraint("raw_count >= 0 AND raw_count <= 100"),
        sa.CheckConstraint("normalized_value >= 0 AND normalized_value <= 1"),
    )


def downgrade() -> None:
    op.drop_table("destination_features")
    op.drop_table("destination_enrichments")
    op.drop_index("ix_destinations_country_code", table_name="destinations")
    op.drop_table("destinations")
