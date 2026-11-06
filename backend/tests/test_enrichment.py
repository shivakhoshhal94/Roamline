import asyncio
from datetime import datetime, timezone
import httpx
import pytest
from app.db.models import Destination
from app.domain.destination_features import FEATURES, CATEGORY_KEYS, CATEGORY_RESULT_LIMIT
from app.services.enrichment import aggregate_features, enrich_candidates, enrich_city
from app.services.geoapify import CategoryEvidence, CityResolution, GeoapifyProvider, ProviderError


def original_feature(identifier, category):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [13.4, 52.5]}, "properties": {"place_id": identifier, "categories": [category], "name": "Original POI"}}


class FakeProvider:
    def __init__(self, fail_on=None, code="provider_unavailable"):
        self.calls = 0
        self.categories = []
        self.fail_on = fail_on
        self.code = code
        self.active = 0
        self.peak = 0

    async def resolve_city(self, city):
        self.calls += 1
        if self.fail_on == self.calls:
            raise ProviderError(self.code)
        return CityResolution("mock-boundary", {"boundary_verified_by": "mock test provider"})

    async def places(self, place_id, category):
        self.calls += 1
        self.categories.append(category)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0)
            if self.fail_on == self.calls:
                raise ProviderError(self.code)
            # All category responses deliberately share seven identifiers.
            return CategoryEvidence(category, tuple(original_feature(f"poi-{i}", category) for i in range(7)), datetime.now(timezone.utc))
        finally:
            self.active -= 1


def test_exactly_22_unique_categories_sequential_and_cache_reused(database, city_factory):
    with database.factory.begin() as session:
        session.add(city_factory())
    provider = FakeProvider()
    report = asyncio.run(enrich_candidates(database.factory, [1], provider))
    assert report["refreshed"] == [1]
    assert provider.calls == 23
    assert len(provider.categories) == len(set(provider.categories)) == len(CATEGORY_KEYS) == 22
    assert set(provider.categories) == {category for definition in FEATURES.values() for category in definition.categories}
    assert provider.categories.count("heritage") == 1
    assert provider.categories.count("natural") == 1
    assert provider.categories.count("commercial.marketplace") == 1
    assert provider.peak == 1
    with database.factory() as session:
        snapshot = session.get(Destination, 1).enrichment
        assert len(snapshot.features) == 10
        assert len(snapshot.category_evidence) == 22
        assert all(feature.raw_count == 7 for feature in snapshot.features)
        assert snapshot.category_evidence["heritage"]["features"][0]["properties"]["name"] == "Original POI"
    second = FakeProvider(fail_on=1)
    assert asyncio.run(enrich_candidates(database.factory, [1], second))["cached"] == [1]
    assert second.calls == 0


def test_deduplicate_within_interest_and_allow_cross_interest_attribution():
    now = datetime.now(timezone.utc)
    evidence = {category: CategoryEvidence(category, (), now) for category in CATEGORY_KEYS}
    evidence["heritage"] = CategoryEvidence("heritage", (original_feature("shared", "heritage"),) * 2, now)
    evidence["tourism.sights.archaeological_site"] = CategoryEvidence("tourism.sights.archaeological_site", (original_feature("shared", "tourism.sights.archaeological_site"), original_feature("history-only", "tourism.sights.archaeological_site")), now)
    evidence["tourism.attraction.viewpoint"] = CategoryEvidence("tourism.attraction.viewpoint", (original_feature("photo-only", "tourism.attraction.viewpoint"),), now)
    aggregates = aggregate_features(evidence)
    assert aggregates["history"]["poi_ids"] == ["history-only", "shared"]
    assert aggregates["photography"]["poi_ids"] == ["photo-only", "shared"]
    assert aggregates["history"]["raw_count"] == aggregates["photography"]["raw_count"] == 2
    assert aggregates["beaches"]["raw_count"] == 0
    assert evidence["heritage"].to_record()["returned_count"] == 2


@pytest.mark.parametrize("code", ["provider_unavailable", "rate_limited", "provider_rejected_request"])
def test_failed_last_category_never_writes_partial_snapshot(database, city_factory, code):
    with database.factory.begin() as session:
        session.add(city_factory())
    provider = FakeProvider(fail_on=23, code=code)
    report = asyncio.run(enrich_candidates(database.factory, [1], provider))
    assert report["failures"][0]["code"] == code
    assert len(provider.categories) == 22
    with database.factory() as session:
        assert session.get(Destination, 1).enrichment is None


def test_failed_refresh_keeps_prior_evidence(database, city_factory, snapshot_factory):
    with database.factory.begin() as session:
        city = city_factory()
        session.add(city)
        session.add(snapshot_factory(city, {"museums": 15}))
    provider = FakeProvider(fail_on=3)
    report = asyncio.run(enrich_candidates(database.factory, [1], provider, refresh=True))
    assert report["failures"][0]["code"] == "provider_unavailable"
    with database.factory() as session:
        snapshot = session.get(Destination, 1).enrichment
        assert snapshot.place_id == "boundary-1"
        assert next(item.raw_count for item in snapshot.features if item.feature == "museums") == 15


def test_rate_limit_stops_batch(database, city_factory):
    with database.factory.begin() as session:
        session.add_all([city_factory(1), city_factory(2)])
    provider = FakeProvider(fail_on=1, code="rate_limited")
    report = asyncio.run(enrich_candidates(database.factory, [1, 2], provider))
    assert provider.calls == 1 and len(report["failures"]) == 1


def test_explicit_successful_refresh_replaces_snapshot(database, city_factory, snapshot_factory):
    with database.factory.begin() as session:
        city = city_factory()
        session.add(city)
        session.add(snapshot_factory(city, {"museums": 15}))
    assert asyncio.run(enrich_candidates(database.factory, [1], FakeProvider(), refresh=True))["refreshed"] == [1]
    with database.factory() as session:
        snapshot = session.get(Destination, 1).enrichment
        assert snapshot.place_id == "mock-boundary"
        assert all(feature.raw_count == 7 for feature in snapshot.features)


def test_zero_result_categories_are_complete_evidence(database, city_factory):
    class ZeroProvider(FakeProvider):
        async def places(self, place_id, category):
            self.categories.append(category)
            return CategoryEvidence(category, (), datetime.now(timezone.utc))
    with database.factory.begin() as session:
        session.add(city_factory())
    assert asyncio.run(enrich_candidates(database.factory, [1], ZeroProvider()))["refreshed"] == [1]
    with database.factory() as session:
        snapshot = session.get(Destination, 1).enrichment
        assert len(snapshot.category_evidence) == 22
        assert all(feature.raw_count == 0 and feature.normalized_value == 0 for feature in snapshot.features)


def test_http_contract_and_experiment_budget(database, city_factory):
    requests = []
    with database.factory.begin() as session:
        session.add(city_factory())
    async def run():
        async def handler(request):
            requests.append(request)
            if request.url.path == "/v1/boundaries/part-of":
                return httpx.Response(200, json={"features": []})
            assert request.url.path == "/v2/places"
            assert "," not in request.url.params["categories"]
            assert request.url.params["limit"] == "20"
            assert request.url.params["lang"] == "en"
            assert "offset" not in request.url.params
            return httpx.Response(200, json={"features": []})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            from app.core.config import Settings
            settings = Settings(_env_file=None, database_url="postgresql+psycopg://test:test@localhost/test")
            provider = GeoapifyProvider(client, "unit-test-key", min_interval=0, timeout=settings.geoapify_timeout, max_requests=23)
            await provider._get("/v1/boundaries/part-of", {"geometry": "point"})
            with database.factory.begin() as session:
                city = session.get(Destination, 1)
                await enrich_city(session, city, provider, resolution=CityResolution("verified-boundary", {"verified": True}))
            assert provider.request_count == 23
            with pytest.raises(ProviderError, match="request_budget_exceeded"):
                await provider.places("verified-boundary", "beach")
    asyncio.run(run())
    assert len(requests) == 23


def test_budget_exhaustion_preserves_database(database, city_factory):
    from sqlalchemy import select
    from app.db.models import DestinationCategoryEvidence
    with database.factory.begin() as session:
        session.add(city_factory())
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"features": []}))) as client:
            from app.core.config import Settings
            settings = Settings(_env_file=None, database_url="postgresql+psycopg://test:test@localhost/test")
            provider = GeoapifyProvider(client, "unit-test-key", min_interval=0, timeout=settings.geoapify_timeout, max_requests=21)
            with pytest.raises(ProviderError, match="request_budget_exceeded"):
                with database.factory.begin() as session:
                    await enrich_city(session, session.get(Destination, 1), provider,
                                      resolution=CityResolution("verified", {"verified": True}))
    asyncio.run(run())
    with database.factory() as session:
        assert session.get(Destination, 1).enrichment is None
        assert len(session.scalars(select(DestinationCategoryEvidence)).all()) == 21


def cache_rows(database):
    from sqlalchemy import select
    from app.db.models import DestinationCategoryEvidence
    with database.factory() as session:
        return session.scalars(select(DestinationCategoryEvidence)).all()


def test_commit_immediately_timeout_resume_only_missing_and_no_incomplete_ranking(database, city_factory):
    from app.core.config import Settings
    from app.domain.profile import TravelerProfile
    from app.services.ranking import recommend
    with database.factory.begin() as session:
        session.add(city_factory())
    class InspectProvider(FakeProvider):
        async def places(self, place_id, category):
            # The previous success is visible to a different DB session before
            # this request starts, not merely buffered in the outer transaction.
            assert len(cache_rows(database)) == len(self.categories)
            return await super().places(place_id, category)
    first = InspectProvider(fail_on=4)
    report = asyncio.run(enrich_candidates(database.factory, [1], first))
    assert report["incomplete"] == [1]
    assert report["failures"][0]["status"] == "incomplete_enrichment"
    assert len(cache_rows(database)) == 2
    with database.factory() as session:
        result = recommend(session, TravelerProfile(travel_context={"duration_days": 2},
            interests=[{"name": "museums"}]), 5,
            Settings(_env_file=None, database_url="postgresql+psycopg://test:test@localhost/test"))
        assert result.results == []
        assert result.metadata.status == "no_enriched_candidates"
    second = FakeProvider()
    assert asyncio.run(enrich_candidates(database.factory, [1], second))["refreshed"] == [1]
    assert second.categories == list(CATEGORY_KEYS[2:])
    assert second.calls == 20  # no new boundary lookup
    from sqlalchemy import select, func
    from app.db.models import DestinationEnrichment
    with database.factory() as session:
        assert session.scalar(select(func.count()).select_from(DestinationEnrichment)) == 1
        assert len(session.get(Destination, 1).enrichment.features) == 10
    assert len(cache_rows(database)) == 22
    assert asyncio.run(enrich_candidates(database.factory, [1], FakeProvider(fail_on=1)))["cached"] == [1]


@pytest.mark.parametrize("change", ["stale", "mapping", "query", "boundary", "catalog", "invalid", "future"])
def test_resume_invalidates_only_incompatible_category(database, city_factory, change):
    from datetime import timedelta
    from app.db.models import DestinationCategoryEvidence
    with database.factory.begin() as session:
        session.add(city_factory())
    # Three successes then failure; no complete snapshot to bypass planning.
    asyncio.run(enrich_candidates(database.factory, [1], FakeProvider(fail_on=5)))
    with database.factory.begin() as session:
        row = session.get(DestinationCategoryEvidence, (1, CATEGORY_KEYS[0]))
        if change in {"stale", "future"}:
            timestamp = datetime.now(timezone.utc) + timedelta(days=-31 if change == "stale" else 1)
            record = dict(row.evidence, fetched_at=timestamp.isoformat())
            row.fetched_at, row.evidence = timestamp, record
        elif change == "mapping":
            row.mapping_version = "obsolete"
        elif change == "query":
            row.query_provenance = dict(row.query_provenance, limit=5)
        elif change == "boundary":
            row.place_id = "different-boundary"
        elif change == "catalog":
            row.catalog_fingerprint = "outdated"
        else:
            row.evidence = dict(row.evidence, unique_count=999)
    second = FakeProvider()
    asyncio.run(enrich_candidates(database.factory, [1], second))
    assert CATEGORY_KEYS[0] in second.categories
    assert CATEGORY_KEYS[1] not in second.categories
    assert CATEGORY_KEYS[2] not in second.categories
    assert len(second.categories) == 20


def test_zero_results_are_reused_on_resume(database, city_factory):
    with database.factory.begin() as session:
        session.add(city_factory())
    class ZeroThenFailure(FakeProvider):
        async def places(self, place_id, category):
            self.categories.append(category)
            if len(self.categories) == 2:
                raise ProviderError("provider_unavailable")
            return CategoryEvidence(category, (), datetime.now(timezone.utc))
    asyncio.run(enrich_candidates(database.factory, [1], ZeroThenFailure()))
    assert cache_rows(database)[0].evidence["unique_count"] == 0
    second = FakeProvider()
    asyncio.run(enrich_candidates(database.factory, [1], second))
    assert CATEGORY_KEYS[0] not in second.categories


def test_refresh_invalidates_work_cache_but_failed_refresh_preserves_published(database, city_factory):
    with database.factory.begin() as session:
        session.add(city_factory())
    asyncio.run(enrich_candidates(database.factory, [1], FakeProvider()))
    with database.factory() as session:
        original = session.get(Destination, 1).enrichment.category_evidence
    report = asyncio.run(enrich_candidates(database.factory, [1], FakeProvider(fail_on=3), refresh=True))
    assert report["incomplete"] == [1]
    assert len(cache_rows(database)) == 22  # old observations are retained, logically invalidated
    from app.services.enrichment import cached_categories
    with database.factory() as session:
        assert len(cached_categories(session, session.get(Destination, 1))) == 2
        assert session.get(Destination, 1).enrichment.category_evidence == original
    # Resume must continue the interrupted explicit refresh, not skip it merely
    # because the old complete snapshot remains usable for recommendations.
    second = FakeProvider()
    asyncio.run(enrich_candidates(database.factory, [1], second))
    assert len(second.categories) == 20


def test_dry_plan_has_no_requests_or_writes_and_counts_identity_budget(database, city_factory):
    from app.services.enrichment import enrichment_plan
    with database.factory.begin() as session:
        session.add(city_factory())
    with database.factory() as session:
        plan = enrichment_plan(session, session.get(Destination, 1))
        assert plan["estimated_additional_requests"] == 25
        assert plan["estimated_credits"] is None
        assert plan["minimum_credits"] == 24
        assert plan["cached_categories"] == []
    assert cache_rows(database) == []
    asyncio.run(enrich_candidates(database.factory, [1], FakeProvider(fail_on=4)))
    with database.factory() as session:
        plan = enrichment_plan(session, session.get(Destination, 1))
        assert len(plan["cached_categories"]) == 2
        assert len(plan["missing_categories"]) == plan["estimated_additional_requests"] == 20
        refresh = enrichment_plan(session, session.get(Destination, 1), refresh=True)
        assert refresh["estimated_additional_requests"] == 22
    assert len(cache_rows(database)) == 2


def test_failed_refresh_before_first_category_still_resumes(database, city_factory):
    with database.factory.begin() as session:
        session.add(city_factory())
    asyncio.run(enrich_candidates(database.factory, [1], FakeProvider()))
    asyncio.run(enrich_candidates(database.factory, [1], FakeProvider(fail_on=1), refresh=True))
    assert len(cache_rows(database)) == 22
    from app.services.enrichment import cached_categories
    with database.factory() as session:
        assert cached_categories(session, session.get(Destination, 1)) == {}
    second = FakeProvider()
    asyncio.run(enrich_candidates(database.factory, [1], second))
    assert len(second.categories) == 22
    with database.factory() as session:
        assert session.get(Destination, 1).category_cache_invalidated_at is None


def test_publication_rollback_preserves_categories_and_old_snapshot(database, city_factory, snapshot_factory):
    with database.factory.begin() as session:
        city = city_factory()
        session.add(city)
        session.add(snapshot_factory(city, {"museums": 15}))
    from unittest.mock import patch
    with pytest.raises(RuntimeError, match="simulated publication rollback"):
        with patch("app.services.enrichment.normalize_count", side_effect=RuntimeError("simulated publication rollback")):
            with database.factory() as session:
                asyncio.run(enrich_city(session, session.get(Destination, 1), FakeProvider(), refresh=True))
    assert len(cache_rows(database)) == 22
    with database.factory() as session:
        city = session.get(Destination, 1)
        assert city.enrichment.place_id == "boundary-1"
        assert city.category_cache_invalidated_at is not None
    second = FakeProvider(fail_on=1)
    assert asyncio.run(enrich_candidates(database.factory, [1], second))["refreshed"] == [1]
    assert second.calls == 0


def test_postgres_category_transactions_resume_and_atomic_publication(city_factory, snapshot_factory):
    import os
    from uuid import uuid4
    from types import SimpleNamespace
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Base
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("PostgreSQL integration URL not set")
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    schema = "category_test_" + uuid4().hex
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        scoped = engine.execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(scoped)
        database = SimpleNamespace(engine=scoped, factory=sessionmaker(scoped, expire_on_commit=False))
        test_commit_immediately_timeout_resume_only_missing_and_no_incomplete_ranking(database, city_factory)
        with database.factory.begin() as session:
            session.delete(session.get(Destination, 1))
        test_publication_rollback_preserves_categories_and_old_snapshot(database, city_factory, snapshot_factory)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
