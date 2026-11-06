"""Catalog and replaceable, provenance-bearing enrichment snapshots."""
from datetime import date, datetime
from sqlalchemy import text, BigInteger, CheckConstraint, Date, DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Destination(Base):
    __tablename__ = "destinations"
    __table_args__ = (
        CheckConstraint("population >= 0"),
        CheckConstraint("latitude >= -90 AND latitude <= 90"),
        CheckConstraint("longitude >= -180 AND longitude <= 180"),
    )
    geoname_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    ascii_name: Mapped[str] = mapped_column(String(200))
    country_code: Mapped[str] = mapped_column(String(2), index=True)
    country_name: Mapped[str] = mapped_column(String(200))
    continent_code: Mapped[str] = mapped_column(String(2))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    population: Mapped[int] = mapped_column(BigInteger)
    timezone: Mapped[str | None] = mapped_column(String(64))
    feature_code: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(100))
    source_modified: Mapped[date] = mapped_column(Date)
    source_sha256: Mapped[str] = mapped_column(String(64))
    country_info_sha256: Mapped[str] = mapped_column(String(64))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    category_cache_invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrichment_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    enrichment_lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    enrichment_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    boundary_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    boundary_provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    boundary_place_id: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    boundary_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    boundary_catalog_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    boundary_evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    enrichment: Mapped["DestinationEnrichment | None"] = relationship(back_populates="destination", cascade="all, delete-orphan", uselist=False)


class DestinationEnrichment(Base):
    __tablename__ = "destination_enrichments"
    destination_id: Mapped[int] = mapped_column(ForeignKey("destinations.geoname_id", ondelete="CASCADE"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(30))
    place_id: Mapped[str] = mapped_column(String(2048))
    resolution_evidence: Mapped[dict] = mapped_column(JSON)
    request_strategy: Mapped[str] = mapped_column(String(50), default="legacy-union-v1", server_default="legacy-union-v1")
    category_evidence: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    mapping_version: Mapped[str] = mapped_column(String(50))
    normalization_version: Mapped[str] = mapped_column(String(50))
    catalog_fingerprint: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    destination: Mapped[Destination] = relationship(back_populates="enrichment")
    features: Mapped[list["DestinationFeature"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class DestinationFeature(Base):
    __tablename__ = "destination_features"
    __table_args__ = (
        CheckConstraint("raw_count >= 0 AND raw_count <= 100"),
        CheckConstraint("normalized_value >= 0 AND normalized_value <= 1"),
    )
    destination_id: Mapped[int] = mapped_column(ForeignKey("destination_enrichments.destination_id", ondelete="CASCADE"), primary_key=True)
    feature: Mapped[str] = mapped_column(String(80), primary_key=True)
    categories: Mapped[list[str]] = mapped_column(JSON)
    raw_count: Mapped[int] = mapped_column(Integer)
    capped: Mapped[bool]
    normalized_value: Mapped[float] = mapped_column(Float)


class DestinationCategoryEvidence(Base):
    """Durable work cache; never queried by recommendation ranking."""
    __tablename__ = "destination_category_evidence"
    destination_id: Mapped[int] = mapped_column(ForeignKey("destinations.geoname_id", ondelete="CASCADE"), primary_key=True)
    category: Mapped[str] = mapped_column(String(100), primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    boundary_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str] = mapped_column(String(30))
    place_id: Mapped[str] = mapped_column(String(2048))
    resolution_evidence: Mapped[dict] = mapped_column(JSON)
    catalog_fingerprint: Mapped[str] = mapped_column(String(64))
    mapping_version: Mapped[str] = mapped_column(String(50))
    request_strategy: Mapped[str] = mapped_column(String(50))
    query_provenance: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
