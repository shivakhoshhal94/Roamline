"""Travel-source provenance, section chunks and versioned local embeddings."""
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector
from app.db.models import Base


class KnowledgePage(Base):
    __tablename__ = "knowledge_pages"
    __table_args__ = (UniqueConstraint("source", "language", "source_page_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    destination_id: Mapped[int] = mapped_column(ForeignKey("destinations.geoname_id", ondelete="CASCADE"), index=True)
    canonical_city: Mapped[str] = mapped_column(String(200))
    country_code: Mapped[str] = mapped_column(String(2))
    continent_code: Mapped[str] = mapped_column(String(2))
    source: Mapped[str] = mapped_column(String(30))
    language: Mapped[str] = mapped_column(String(5))
    source_page_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(Text)
    revision_id: Mapped[int] = mapped_column(BigInteger)
    revised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str] = mapped_column(Text)
    license: Mapped[str] = mapped_column(String(50))
    dump_sha256: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(50))
    match_method: Mapped[str] = mapped_column(String(50))
    match_distance_km: Mapped[float] = mapped_column(Float)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (UniqueConstraint("page_id", "chunk_index"), Index("ix_knowledge_chunks_search", "search_vector", postgresql_using="gin"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("knowledge_pages.id", ondelete="CASCADE"), index=True)
    section: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR().with_variant(Text(), "sqlite"), nullable=True)


class KnowledgeEmbedding(Base):
    __tablename__ = "knowledge_embeddings"
    chunk_id: Mapped[int] = mapped_column(ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), primary_key=True)
    model: Mapped[str] = mapped_column(String(150), primary_key=True)
    revision: Mapped[str] = mapped_column(String(64), primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(384))
    embedded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
