from datetime import date, datetime, timezone
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.models import Base, Destination, DestinationEnrichment, DestinationFeature
from app.services.geoapify import CategoryEvidence
from app.domain.destination_features import FEATURES, MAPPING_VERSION, NORMALIZATION_VERSION, CATEGORY_KEYS, CATEGORY_RESULT_LIMIT, REQUEST_STRATEGY_VERSION, catalog_fingerprint, normalize_count


@pytest.fixture
def city_factory():
    def build(identifier=1, **changes):
        fields = dict(geoname_id=identifier, name=f"City {identifier}", ascii_name=f"City {identifier}", country_code="DE", country_name="Germany", continent_code="EU", latitude=52.5, longitude=13.4, population=500_000, timezone="Europe/Berlin", feature_code="PPL", source="GeoNames cities15000", source_modified=date(2026, 1, 1), source_sha256="a" * 64, country_info_sha256="b" * 64, imported_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        fields.update(changes)
        return Destination(**fields)
    return build


@pytest.fixture
def snapshot_factory():
    def build(city, counts=None, **changes):
        counts = counts or {}
        fetched_at = datetime(2026, 1, 3, tzinfo=timezone.utc)
        features_by_category = {category: [] for category in CATEGORY_KEYS}
        for name, count in counts.items():
            categories = FEATURES[name].categories
            if count > len(categories) * CATEGORY_RESULT_LIMIT:
                raise ValueError("Fixture count exceeds the per-category collection bound")
            for index in range(count):
                category = categories[index // CATEGORY_RESULT_LIMIT]
                features_by_category[category].append({"type": "Feature", "properties": {"place_id": f"{name}-{index}", "categories": [category]}})
        evidence = {category: CategoryEvidence(category, tuple(values), fetched_at) for category, values in features_by_category.items()}
        snapshot = DestinationEnrichment(destination_id=city.geoname_id, provider="geoapify", place_id=f"boundary-{city.geoname_id}", resolution_evidence={"boundary_verified_by": "boundaries/part-of"}, request_strategy=REQUEST_STRATEGY_VERSION, category_evidence={key: item.to_record() for key, item in evidence.items()}, mapping_version=MAPPING_VERSION, normalization_version=NORMALIZATION_VERSION, catalog_fingerprint=catalog_fingerprint(city), fetched_at=fetched_at)
        snapshot.features = []
        for name, definition in FEATURES.items():
            identifiers = {feature["properties"]["place_id"] for category in definition.categories for feature in features_by_category[category]}
            count = len(identifiers)
            snapshot.features.append(DestinationFeature(destination_id=city.geoname_id, feature=name, categories=list(definition.categories), raw_count=count, capped=any(len(features_by_category[category]) == CATEGORY_RESULT_LIMIT for category in definition.categories), normalized_value=normalize_count(count, city.population)))
        for key, value in changes.items():
            setattr(snapshot, key, value)
        return snapshot
    return build


@pytest.fixture
def database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    yield SimpleNamespace(engine=engine, factory=factory)
    engine.dispose()
