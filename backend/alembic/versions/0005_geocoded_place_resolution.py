"""Name place-resolution evidence accurately; retain legacy records without renewing them."""
from alembic import op
revision = "0005_geocoded_place_resolution"
down_revision = "0004_enrichment_fencing"
branch_labels = None
depends_on = None
RENAMES = {
    "boundary_verified_at": "place_resolved_at",
    "boundary_provider": "place_resolution_provider",
    "boundary_place_id": "resolved_place_id",
    "boundary_fingerprint": "place_resolution_fingerprint",
    "boundary_catalog_fingerprint": "place_resolution_catalog_fingerprint",
    "boundary_evidence": "place_resolution_evidence",
}


def upgrade():
    for old, new in RENAMES.items():
        op.alter_column("destinations", old, new_column_name=new)
    op.alter_column("destination_category_evidence", "boundary_fingerprint", new_column_name="place_resolution_fingerprint")


def downgrade():
    op.alter_column("destination_category_evidence", "place_resolution_fingerprint", new_column_name="boundary_fingerprint")
    for old, new in reversed(list(RENAMES.items())):
        op.alter_column("destinations", new, new_column_name=old)
