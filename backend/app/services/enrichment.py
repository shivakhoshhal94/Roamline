"""Sequential category collection and atomic, deduplicated feature snapshots."""
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func
from dataclasses import dataclass
from uuid import uuid4
from hashlib import sha256
from sqlalchemy.orm import Session
from app.db.models import Destination, DestinationEnrichment, DestinationFeature, DestinationCategoryEvidence
from app.domain.destination_features import (
    FEATURES, CATEGORY_KEYS, MAX_CATEGORY_REQUESTS, REQUEST_STRATEGY_VERSION,
    MAPPING_VERSION, NORMALIZATION_VERSION, CATEGORY_RESULT_LIMIT, catalog_fingerprint, normalize_count,
)
from app.services.geoapify import CategoryEvidence, CityResolution, DestinationProvider, ProviderError


def aggregate_features(evidence: dict[str, CategoryEvidence]) -> dict[str, dict]:
    if set(evidence) != set(CATEGORY_KEYS):
        raise ProviderError("incomplete_category_evidence")
    aggregates = {}
    for name, definition in FEATURES.items():
        identifiers = set()
        capped = False
        for category in definition.categories:
            item = evidence[category]
            if item.category != category:
                raise ProviderError("invalid_response")
            identifiers.update(item.poi_ids)
            capped = capped or item.capped
        aggregates[name] = {"raw_count": len(identifiers), "capped": capped, "poi_ids": sorted(identifiers)}
    return aggregates


def usable_snapshot(city, snapshot, required_features) -> bool:
    if (snapshot is None or snapshot.provider != "geoapify" or not snapshot.place_id
            or snapshot.mapping_version != MAPPING_VERSION
            or snapshot.normalization_version != NORMALIZATION_VERSION
            or snapshot.request_strategy != REQUEST_STRATEGY_VERSION
            or snapshot.catalog_fingerprint != catalog_fingerprint(city)
            or not isinstance(snapshot.category_evidence, dict)
            or set(snapshot.category_evidence) != set(CATEGORY_KEYS)):
        return False
    try:
        evidence = {key: CategoryEvidence.from_record(key, value) for key, value in snapshot.category_evidence.items()}
        aggregates = aggregate_features(evidence)
        available = {feature.feature: feature for feature in snapshot.features}
        if set(available) != set(FEATURES) or not set(required_features).issubset(FEATURES):
            return False
        for name in FEATURES:
            feature = available.get(name)
            aggregate = aggregates[name]
            if (feature is None or feature.categories != list(FEATURES[name].categories)
                    or feature.raw_count != aggregate["raw_count"] or feature.capped != aggregate["capped"]
                    or feature.normalized_value != normalize_count(feature.raw_count, city.population)):
                return False
    except (ProviderError, ValueError, KeyError, TypeError):
        return False
    return True


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def query_provenance(place_id, category):
    return {"endpoint": "/v2/places", "category": category, "filter_type": "place",
            "place_id": place_id, "limit": CATEGORY_RESULT_LIMIT, "lang": "en", "pagination": False}


def place_resolution_identity(city, place_id):
    return sha256(("geoapify:geocoded-city-v1:" + place_id + ":" + catalog_fingerprint(city)).encode()).hexdigest()


def place_resolution_fresh(city, days=30, now=None):
    now = now or datetime.now(timezone.utc)
    return bool(city.place_resolved_at and city.resolved_place_id
        and city.place_resolution_provider == "geoapify"
        and isinstance(city.place_resolution_evidence, dict) and city.place_resolution_evidence
        and city.place_resolution_evidence.get("resolution_policy") == "geocoded-city-v1"
        and city.place_resolution_catalog_fingerprint == catalog_fingerprint(city)
        and city.place_resolution_fingerprint == place_resolution_identity(city, city.resolved_place_id)
        and now - timedelta(days=days) <= utc(city.place_resolved_at) <= now)


def cached_categories(session, city, stale_after_days=30, *, place_id=None, now=None,
                      place_resolution_stale_after_days=30):
    now = now or datetime.now(timezone.utc)
    if not place_resolution_fresh(city, place_resolution_stale_after_days, now):
        return {}
    cutoff = now - timedelta(days=stale_after_days)
    valid = {}
    rows = session.scalars(select(DestinationCategoryEvidence).where(
        DestinationCategoryEvidence.destination_id == city.geoname_id)).all()
    for row in rows:
        if (row.category not in CATEGORY_KEYS or row.provider != "geoapify"
                or row.generation != city.enrichment_generation
                or row.place_id != city.resolved_place_id
                or row.place_resolution_fingerprint != city.place_resolution_fingerprint
                or (place_id is not None and row.place_id != place_id)
                or row.catalog_fingerprint != catalog_fingerprint(city)
                or row.mapping_version != MAPPING_VERSION or row.request_strategy != REQUEST_STRATEGY_VERSION
                or row.query_provenance != query_provenance(row.place_id, row.category)):
            continue
        try:
            item = CategoryEvidence.from_record(row.category, row.evidence)
            if (not cutoff <= utc(row.fetched_at) == item.fetched_at <= now
                    or (city.category_cache_invalidated_at is not None
                        and item.fetched_at < utc(city.category_cache_invalidated_at))):
                continue
        except (ProviderError, ValueError, TypeError):
            continue
        valid[row.category] = (row, item)
    return valid


def enrichment_plan(session, city, refresh=False, stale_after_days=30, place_resolution_stale_after_days=30):
    fresh = place_resolution_fresh(city, place_resolution_stale_after_days)
    cached = {} if refresh else cached_categories(session, city, stale_after_days,
        place_resolution_stale_after_days=place_resolution_stale_after_days)
    published = False
    if (fresh and not refresh and city.category_cache_invalidated_at is None
            and usable_snapshot(city, city.enrichment, FEATURES)
            and city.enrichment.place_id == city.resolved_place_id
            and set(cached) == set(CATEGORY_KEYS)
            and all(city.enrichment.category_evidence[k] == item.to_record() for k, (_, item) in cached.items())):
        cutoff = datetime.now(timezone.utc) - timedelta(days=stale_after_days)
        published = all(cutoff <= CategoryEvidence.from_record(k, v).fetched_at <= datetime.now(timezone.utc)
                        for k, v in city.enrichment.category_evidence.items())
    cached_keys = list(CATEGORY_KEYS) if published else list(cached)
    missing = [key for key in CATEGORY_KEYS if key not in cached_keys]
    rows = session.scalars(select(DestinationCategoryEvidence).where(
        DestinationCategoryEvidence.destination_id == city.geoname_id)).all()
    identity = 0 if fresh else 1
    total = len(missing) + identity
    return {"total_categories": len(CATEGORY_KEYS), "cached_categories": cached_keys,
        "missing_categories": missing, "invalid_categories": [r.category for r in rows if r.category not in cached_keys],
        "place_resolution_status": "fresh" if fresh else ("expired_or_incompatible" if city.place_resolved_at else "missing"),
        "identity_lookup_requests": identity, "place_resolution_requests": identity,
        "boundary_api_requests": 0, "places_requests": len(missing),
        "estimated_additional_requests": total, "credit_consumption": {"geocoding": identity, "places": len(missing)},
        "minimum_credits": total, "estimated_credits": total,
        "credit_estimate_note": "One credit per geocoding request and per Places request at limit 20; no retries or pagination.",
        "published_snapshot_cached": published}


@dataclass(frozen=True)
class Worker:
    destination_id: int
    token: str
    generation: int
    fingerprint: str


def locked_city(session, identifier):
    city = session.scalar(select(Destination).where(Destination.geoname_id == identifier)
                          .with_for_update().execution_options(populate_existing=True))
    if city is None:
        raise ProviderError("destination_missing")
    return city


def database_now(session):
    # PostgreSQL owns lease time, avoiding clock skew between worker hosts.
    return session.scalar(select(func.clock_timestamp())) if session.get_bind().dialect.name == "postgresql" else datetime.now(timezone.utc)


def acquire_worker(bind, identifier, refresh=False, lease_seconds=180):
    with Session(bind, expire_on_commit=False) as work, work.begin():
        city = locked_city(work, identifier)
        now = database_now(work)
        if city.enrichment_lease_token and city.enrichment_lease_expires_at and utc(city.enrichment_lease_expires_at) > now:
            raise ProviderError("enrichment_conflict")
        if refresh:
            city.enrichment_generation += 1
            city.category_cache_invalidated_at = now
        city.enrichment_lease_token = str(uuid4())
        city.enrichment_lease_expires_at = now + timedelta(seconds=lease_seconds)
        worker = Worker(identifier, city.enrichment_lease_token, city.enrichment_generation, catalog_fingerprint(city))
    return worker


def check_worker(session, worker, lease_seconds=180):
    city = locked_city(session, worker.destination_id)
    now = database_now(session)
    if (city.enrichment_lease_token != worker.token or city.enrichment_generation != worker.generation
            or not city.enrichment_lease_expires_at or utc(city.enrichment_lease_expires_at) <= now
            or catalog_fingerprint(city) != worker.fingerprint):
        raise ProviderError("enrichment_conflict")
    city.enrichment_lease_expires_at = now + timedelta(seconds=lease_seconds)
    return city


def release_worker(bind, worker):
    with Session(bind) as work, work.begin():
        city = locked_city(work, worker.destination_id)
        if city.enrichment_lease_token == worker.token:
            city.enrichment_lease_token = None
            city.enrichment_lease_expires_at = None


def invalidate_destination(session, identifier):
    """Explicit cancellation/invalidation; caller owns this short transaction."""
    city = locked_city(session, identifier)
    city.enrichment_generation += 1
    city.category_cache_invalidated_at = database_now(session)
    city.enrichment_lease_token = None
    city.enrichment_lease_expires_at = None


def save_place_resolution(bind, worker, resolution, lease_seconds=180):
    with Session(bind) as work, work.begin():
        city = check_worker(work, worker, lease_seconds)
        fingerprint = place_resolution_identity(city, resolution.place_id)
        if city.place_resolution_fingerprint and city.place_resolution_fingerprint != fingerprint:
            city.enrichment_generation += 1
            worker = Worker(worker.destination_id, worker.token, city.enrichment_generation, worker.fingerprint)
        city.place_resolved_at = datetime.now(timezone.utc)
        city.place_resolution_provider = "geoapify"
        city.resolved_place_id = resolution.place_id
        city.place_resolution_fingerprint = fingerprint
        city.place_resolution_catalog_fingerprint = catalog_fingerprint(city)
        city.place_resolution_evidence = resolution.evidence
    return worker


def save_category(bind, worker, item, lease_seconds=180, place_resolution_stale_after_days=30):
    item = CategoryEvidence.from_record(item.category, item.to_record())
    with Session(bind) as work, work.begin():
        city = check_worker(work, worker, lease_seconds)
        if not place_resolution_fresh(city, place_resolution_stale_after_days):
            raise ProviderError("place_resolution_expired")
        # The destination row lock serializes category upserts and fences older
        # generations before touching their primary-key row.
        row = DestinationCategoryEvidence(destination_id=city.geoname_id, category=item.category,
            generation=worker.generation, place_resolution_fingerprint=city.place_resolution_fingerprint,
            provider="geoapify", place_id=city.resolved_place_id, resolution_evidence=city.place_resolution_evidence,
            catalog_fingerprint=worker.fingerprint, mapping_version=MAPPING_VERSION,
            request_strategy=REQUEST_STRATEGY_VERSION, query_provenance=query_provenance(city.resolved_place_id,item.category),
            evidence=item.to_record(), fetched_at=item.fetched_at)
        work.merge(row)


def publish_snapshot(bind, worker, stale_after_days=30, place_resolution_stale_after_days=30):
    with Session(bind) as session, session.begin():
        city = check_worker(session, worker)
        if not place_resolution_fresh(city, place_resolution_stale_after_days):
            raise ProviderError("place_resolution_expired")
        complete = cached_categories(session, city, stale_after_days, place_resolution_stale_after_days=place_resolution_stale_after_days)
        if set(complete) != set(CATEGORY_KEYS):
            raise ProviderError("incomplete_category_evidence")
        evidence = {key: item for key, (_, item) in complete.items()}
        resolution = CityResolution(city.resolved_place_id, city.place_resolution_evidence)
        aggregates = aggregate_features(evidence)
        # This locked transaction publishes the complete snapshot atomically;
        # an exception preserves the previous complete snapshot.
        if city.enrichment is not None:
            session.delete(city.enrichment)
            session.flush()
        snapshot = DestinationEnrichment(
            destination_id=city.geoname_id, provider="geoapify", place_id=resolution.place_id,
            resolution_evidence=resolution.evidence, mapping_version=MAPPING_VERSION,
            normalization_version=NORMALIZATION_VERSION, request_strategy=REQUEST_STRATEGY_VERSION,
            category_evidence={category: item.to_record() for category, item in evidence.items()},
            catalog_fingerprint=catalog_fingerprint(city), fetched_at=min(item.fetched_at for item in evidence.values()),
        )
        snapshot.features = [DestinationFeature(
            destination_id=city.geoname_id, feature=name, categories=list(definition.categories),
            raw_count=aggregates[name]["raw_count"], capped=aggregates[name]["capped"],
            normalized_value=normalize_count(aggregates[name]["raw_count"], city.population),
        ) for name, definition in FEATURES.items()]
        city.category_cache_invalidated_at = None
        city.enrichment = snapshot
        session.add(snapshot)
        return "refreshed"


async def enrich_city(session: Session, city: Destination, provider: DestinationProvider,
                      refresh=False, *, resolution=None, stale_after_days=30,
                      place_resolution_stale_after_days=30, lease_seconds=180):
    if session.new or session.dirty or session.deleted:
        raise ValueError("Enrichment requires a committed catalog and a read-only caller session")
    identifier, bind = city.geoname_id, session.get_bind()
    # Release caller read transaction before any external wait. All writes and
    # publication below own short transactions; the catalog city must be durable.
    session.rollback()
    worker = acquire_worker(bind, identifier, refresh, lease_seconds)
    try:
        with Session(bind, expire_on_commit=False) as work, work.begin():
            current = check_worker(work, worker, lease_seconds)
            plan = enrichment_plan(work, current, False, stale_after_days, place_resolution_stale_after_days)
            if plan["published_snapshot_cached"] and not refresh:
                return "cached"
            needs_resolution = not place_resolution_fresh(current, place_resolution_stale_after_days)
            work.expunge(current)
        if resolution is not None:
            worker = save_place_resolution(bind, worker, resolution, lease_seconds)
        elif needs_resolution:
            resolution = await provider.resolve_city(current)
            worker = save_place_resolution(bind, worker, resolution, lease_seconds)
        with Session(bind) as work, work.begin():
            current = check_worker(work, worker, lease_seconds)
            cached = cached_categories(work, current, stale_after_days, place_resolution_stale_after_days=place_resolution_stale_after_days)
            missing = [key for key in CATEGORY_KEYS if key not in cached]
            place_id = current.resolved_place_id
        for category in missing:
            with Session(bind) as work, work.begin():
                current = check_worker(work, worker, lease_seconds)
                if not place_resolution_fresh(current, place_resolution_stale_after_days):
                    raise ProviderError("place_resolution_expired")
            item = await provider.places(place_id, category)
            if item.category != category:
                raise ProviderError("invalid_response")
            save_category(bind, worker, item, lease_seconds, place_resolution_stale_after_days)
        return publish_snapshot(bind, worker, stale_after_days, place_resolution_stale_after_days)
    finally:
        release_worker(bind, worker)


async def enrich_candidates(session_factory, candidate_ids, provider, refresh=False, *,
                            stale_after_days=30, place_resolution_stale_after_days=30, lease_seconds=180):
    report = {"refreshed": [], "cached": [], "incomplete": [], "failures": []}
    for identifier in candidate_ids:
        try:
            with session_factory() as session:
                city = session.get(Destination, identifier)
                if city is None:
                    continue
                status = await enrich_city(session, city, provider, refresh, stale_after_days=stale_after_days,
                    place_resolution_stale_after_days=place_resolution_stale_after_days, lease_seconds=lease_seconds)
                report[status].append(identifier)
        except ProviderError as error:
            report["incomplete"].append(identifier)
            report["failures"].append({"geoname_id": identifier, "status": "conflict" if error.code == "enrichment_conflict" else "incomplete_enrichment",
                "code": error.code, "retry_after_seconds": error.retry_after})
            if error.code in {"rate_limited", "provider_authentication", "provider_unavailable", "missing_api_key", "request_budget_exceeded", "credit_budget_exceeded", "credit_cost_unbounded"}:
                break
    return report
