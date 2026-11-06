import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from app.db.models import Base, Destination, DestinationCategoryEvidence, DestinationEnrichment
from app.services.enrichment import (acquire_worker, release_worker, check_worker, invalidate_destination,
    save_boundary, save_category, publish_snapshot, enrich_candidates, cached_categories, enrichment_plan)
from app.services.geoapify import CategoryEvidence, CityResolution, ProviderError
from app.domain.destination_features import CATEGORY_KEYS
from test_enrichment import FakeProvider, original_feature


def evidence(category=CATEGORY_KEYS[0], identifier="new"):
    return CategoryEvidence(category, (original_feature(identifier, category),), datetime.now(timezone.utc))


def initialize(database, city_factory):
    with database.factory.begin() as session:
        session.add(city_factory())


def test_boundary_expiry_reverification_does_not_renew_from_places(database, city_factory):
    initialize(database, city_factory)
    asyncio.run(enrich_candidates(database.factory, [1], FakeProvider(fail_on=4)))
    with database.factory() as session:
        original = session.get(Destination, 1).boundary_verified_at
    asyncio.run(enrich_candidates(database.factory, [1], FakeProvider()))
    with database.factory() as session:
        assert session.get(Destination, 1).boundary_verified_at == original
    with database.factory.begin() as session:
        session.get(Destination, 1).boundary_verified_at = datetime.now(timezone.utc) - timedelta(days=31)
    provider = FakeProvider()
    assert asyncio.run(enrich_candidates(database.factory, [1], provider))["refreshed"] == [1]
    assert provider.calls == 1  # successful boundary revalidation; categories still fresh
    with database.factory() as session:
        assert session.get(Destination, 1).boundary_verified_at != original


def test_boundary_change_invalidates_categories_and_failed_refresh_keeps_snapshot(database, city_factory):
    initialize(database, city_factory)
    asyncio.run(enrich_candidates(database.factory, [1], FakeProvider()))
    with database.factory.begin() as session:
        city = session.get(Destination, 1)
        original = city.enrichment.category_evidence
        city.boundary_verified_at = datetime.now(timezone.utc) - timedelta(days=31)
    failed = FakeProvider(fail_on=1)
    assert asyncio.run(enrich_candidates(database.factory, [1], failed))["incomplete"] == [1]
    with database.factory() as session:
        assert session.get(Destination, 1).enrichment.category_evidence == original
    class Changed(FakeProvider):
        async def resolve_city(self, city):
            return CityResolution("new-boundary", {"boundary_verified_by": "mock"})
    provider = Changed()
    asyncio.run(enrich_candidates(database.factory, [1], provider))
    assert len(provider.categories) == 22
    with database.factory() as session:
        city = session.get(Destination, 1)
        assert city.enrichment_generation == 1
        assert city.enrichment.place_id == "new-boundary"
        assert len(cached_categories(session, city)) == 22


def test_lease_expiry_recovery_and_stale_writes_rejected(database, city_factory):
    initialize(database, city_factory)
    first = acquire_worker(database.engine, 1)
    first = save_boundary(database.engine, first, CityResolution("boundary", {"verified": True}))
    save_category(database.engine, first, evidence(identifier="old"))
    with pytest.raises(ProviderError, match="enrichment_conflict"):
        acquire_worker(database.engine, 1)
    with database.factory.begin() as session:
        session.get(Destination, 1).enrichment_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    second = acquire_worker(database.engine, 1)
    save_category(database.engine, second, evidence(identifier="new"))
    with pytest.raises(ProviderError, match="enrichment_conflict"):
        save_category(database.engine, first, evidence(identifier="stale"))
    with pytest.raises(ProviderError, match="enrichment_conflict"):
        publish_snapshot(database.engine, first)
    release_worker(database.engine, first)
    with database.factory() as session:
        assert session.get(Destination, 1).enrichment_lease_token == second.token
        assert session.get(DestinationCategoryEvidence, (1, CATEGORY_KEYS[0])).evidence["poi_ids"] == ["new"]
    release_worker(database.engine, second)


def test_invalidation_during_collection_blocks_old_worker_and_preserves_snapshot(database, city_factory):
    initialize(database, city_factory)
    asyncio.run(enrich_candidates(database.factory, [1], FakeProvider()))
    with database.factory() as session:
        original = session.get(Destination, 1).enrichment.category_evidence
    class Invalidating(FakeProvider):
        async def places(self, place_id, category):
            if len(self.categories) == 2:
                with database.factory.begin() as other:
                    invalidate_destination(other, 1)
            return await super().places(place_id, category)
    report = asyncio.run(enrich_candidates(database.factory, [1], Invalidating(), refresh=True))
    assert report["failures"][0]["status"] == "conflict"
    with database.factory() as session:
        city = session.get(Destination, 1)
        assert city.enrichment.category_evidence == original
        assert city.category_cache_invalidated_at is not None
        assert cached_categories(session, city) == {}
        rows = session.scalars(select(DestinationCategoryEvidence)).all()
        assert len(rows) == 22  # retained; stale response never overwrites next category
        assert sum(row.generation == 1 for row in rows) == 2


@pytest.fixture
def postgres_database():
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("PostgreSQL integration URL not set")
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    schema = "fencing_test_" + uuid4().hex
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        scoped = engine.execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(scoped)
        yield SimpleNamespace(engine=scoped, factory=sessionmaker(scoped, expire_on_commit=False))
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_postgres_stale_generation_and_lease_recovery(postgres_database, city_factory):
    test_lease_expiry_recovery_and_stale_writes_rejected(postgres_database, city_factory)


def test_postgres_concurrent_invalidation_preserves_published(postgres_database, city_factory):
    test_invalidation_during_collection_blocks_old_worker_and_preserves_snapshot(postgres_database, city_factory)


def test_postgres_two_workers_same_category_and_publication_row_lock(postgres_database, city_factory):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    database = postgres_database
    initialize(database, city_factory)
    gate = Barrier(2)
    def acquire():
        gate.wait(timeout=5)
        try:
            return acquire_worker(database.engine, 1)
        except ProviderError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: acquire(), range(2)))
    assert results.count("enrichment_conflict") == 1
    worker = next(result for result in results if result != "enrichment_conflict")
    worker = save_boundary(database.engine, worker, CityResolution("boundary", {"verified": True}))
    with database.factory.begin() as session:
        invalidate_destination(session, 1)
    second = acquire_worker(database.engine, 1)
    # Old/new workers actually race on the same category primary key.
    gate = Barrier(2)
    def write(token, identifier):
        gate.wait(timeout=5)
        try:
            save_category(database.engine, token, evidence(identifier=identifier))
            return "saved"
        except ProviderError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(write, worker, "stale")
        b = pool.submit(write, second, "current")
        assert a.result(timeout=10) == "enrichment_conflict"
        assert b.result(timeout=10) == "saved"
    with database.factory() as session:
        assert session.get(DestinationCategoryEvidence, (1, CATEGORY_KEYS[0])).evidence["poi_ids"] == ["current"]
    # Publication must acquire the destination row lock, even before checking completeness.
    from sqlalchemy.exc import OperationalError
    with database.factory.begin() as blocker:
        blocker.execute(select(Destination).where(Destination.geoname_id == 1).with_for_update())
        with database.engine.connect() as connection:
            connection.execute(text("SET lock_timeout = '150ms'"))
            connection.commit()
            with pytest.raises(OperationalError):
                publish_snapshot(connection, second)
    with database.factory() as session:
        assert session.get(Destination, 1).enrichment is None
    release_worker(database.engine, second)


def test_postgres_import_preserves_all_operational_fields(postgres_database, tmp_path):
    from zipfile import ZipFile
    from test_catalog import COUNTRIES, city_row
    from app.services.catalog import import_geonames
    database = postgres_database
    countries = tmp_path / "countryInfo.txt"
    countries.write_text(COUNTRIES, encoding="utf-8")
    cities = tmp_path / "cities15000.zip"
    def archive(population):
        with ZipFile(cities, "w") as output:
            output.writestr("cities15000.txt", city_row(population=population))
    archive(100000)
    with database.factory.begin() as session:
        import_geonames(session, cities, countries)
        city = session.scalar(select(Destination))
        identifier = city.geoname_id
        city.enrichment_generation = 7
        city.category_cache_invalidated_at = datetime.now(timezone.utc)
        city.boundary_verified_at = datetime.now(timezone.utc)
        city.boundary_provider = "geoapify"
        city.boundary_place_id = "verified"
        city.boundary_evidence = {"verified": True}
        city.boundary_fingerprint = "f" * 64
        city.boundary_catalog_fingerprint = "c" * 64
        city.enrichment_lease_token = "lease-token"
        city.enrichment_lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    with database.factory() as session:
        city = session.get(Destination, identifier)
        columns = [c.name for c in Destination.__table__.columns if c.name.startswith(("boundary_", "enrichment_", "category_cache_"))]
        original = {key: getattr(city, key) for key in columns}
    for population in [100000, 200000, 200000]:
        archive(population)
        with database.factory.begin() as session:
            assert import_geonames(session, cities, countries) == 1
        with database.factory() as session:
            city = session.get(Destination, identifier)
            assert {key: getattr(city, key) for key in columns} == original
            assert city.population == population
            assert len(session.scalars(select(Destination)).all()) == 1


def test_postgres_no_open_transaction_during_provider_wait(postgres_database, city_factory):
    database = postgres_database
    initialize(database, city_factory)
    class CheckingProvider(FakeProvider):
        async def resolve_city(self, city):
            assert database.engine.pool.checkedout() == 0
            return await super().resolve_city(city)
        async def places(self, place_id, category):
            assert database.engine.pool.checkedout() == 0
            return await super().places(place_id, category)
    assert asyncio.run(enrich_candidates(database.factory, [1], CheckingProvider()))["refreshed"] == [1]


def test_expired_boundary_during_places_response_is_not_persisted(database, city_factory):
    initialize(database, city_factory)
    worker = acquire_worker(database.engine, 1)
    worker = save_boundary(database.engine, worker, CityResolution("boundary", {"verified": True}))
    with database.factory.begin() as session:
        session.get(Destination, 1).boundary_verified_at = datetime.now(timezone.utc) - timedelta(days=31)
    with pytest.raises(ProviderError, match="boundary_expired"):
        save_category(database.engine, worker, evidence())
    with database.factory() as session:
        assert session.get(DestinationCategoryEvidence, (1, CATEGORY_KEYS[0])) is None
    release_worker(database.engine, worker)


def test_enrichment_refuses_to_discard_uncommitted_caller_changes(database, city_factory):
    from app.services.enrichment import enrich_city
    initialize(database, city_factory)
    provider = FakeProvider()
    with database.factory() as session:
        city = session.get(Destination, 1)
        city.name = "Pending edit"
        with pytest.raises(ValueError, match="read-only caller"):
            asyncio.run(enrich_city(session, city, provider))
        assert city.name == "Pending edit"
        assert session.dirty
    assert provider.calls == 0
