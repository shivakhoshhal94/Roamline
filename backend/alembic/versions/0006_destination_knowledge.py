"""Worldwide travel-source chunks and pgvector embeddings."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR
from pgvector.sqlalchemy import Vector
revision = "0006_destination_knowledge"
down_revision = "0005_geocoded_place_resolution"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table("knowledge_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("destination_id", sa.BigInteger(), sa.ForeignKey("destinations.geoname_id", ondelete="CASCADE"), nullable=False),
        sa.Column("canonical_city", sa.String(200), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("continent_code", sa.String(2), nullable=False),
        sa.Column("source", sa.String(30), nullable=False), sa.Column("language", sa.String(5), nullable=False),
        sa.Column("source_page_id", sa.BigInteger(), nullable=False), sa.Column("title", sa.Text(), nullable=False),
        sa.Column("revision_id", sa.BigInteger(), nullable=False), sa.Column("revised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False), sa.Column("license", sa.String(50), nullable=False),
        sa.Column("dump_sha256", sa.String(64), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(50), nullable=False), sa.Column("match_method", sa.String(50), nullable=False),
        sa.Column("match_distance_km", sa.Float(), nullable=False), sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "language", "source_page_id"))
    op.create_index("ix_knowledge_pages_destination_id", "knowledge_pages", ["destination_id"])
    op.create_table("knowledge_chunks",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("page_id", sa.Integer(), sa.ForeignKey("knowledge_pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section", sa.Text(), nullable=False), sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("search_vector", TSVECTOR(), nullable=True), sa.UniqueConstraint("page_id", "chunk_index"))
    op.create_index("ix_knowledge_chunks_page_id", "knowledge_chunks", ["page_id"])
    op.create_index("ix_knowledge_chunks_search", "knowledge_chunks", ["search_vector"], postgresql_using="gin")
    op.create_table("knowledge_embeddings",
        sa.Column("chunk_id", sa.Integer(), sa.ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("model", sa.String(150), primary_key=True), sa.Column("revision", sa.String(64), primary_key=True),
        sa.Column("embedding", Vector(384), nullable=False), sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("knowledge_embeddings")
    op.drop_index("ix_knowledge_chunks_search", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_page_id", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_knowledge_pages_destination_id", table_name="knowledge_pages")
    op.drop_table("knowledge_pages")
    # Shared extension deliberately retained; other local tables may use it.
