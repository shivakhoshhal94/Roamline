"""Fence enrichment workers and independently date verified boundaries."""
from alembic import op
import sqlalchemy as sa
revision = "0004_enrichment_fencing"
down_revision = "0003_resumable_categories"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("destinations", sa.Column("enrichment_generation", sa.Integer(), nullable=False, server_default="0"))
    for name, kind in [
        ("enrichment_lease_token", sa.String(36)), ("enrichment_lease_expires_at", sa.DateTime(timezone=True)),
        ("boundary_verified_at", sa.DateTime(timezone=True)), ("boundary_provider", sa.String(30)),
        ("boundary_place_id", sa.String(2048)), ("boundary_fingerprint", sa.String(64)),
        ("boundary_catalog_fingerprint", sa.String(64)), ("boundary_evidence", sa.JSON())]:
        op.add_column("destinations", sa.Column(name, kind, nullable=True))
    op.add_column("destination_category_evidence", sa.Column("generation", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("destination_category_evidence", sa.Column("boundary_fingerprint", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("destination_category_evidence", "boundary_fingerprint")
    op.drop_column("destination_category_evidence", "generation")
    for name in ["boundary_evidence", "boundary_catalog_fingerprint", "boundary_fingerprint", "boundary_place_id", "boundary_provider", "boundary_verified_at", "enrichment_lease_expires_at", "enrichment_lease_token", "enrichment_generation"]:
        op.drop_column("destinations", name)
