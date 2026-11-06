"""Pure deterministic scoring plus a read-only catalog/cache query boundary."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import Settings
from app.db.models import Destination, DestinationEnrichment
from app.domain.destination_features import FEATURES, CATEGORY_RESULT_LIMIT
from app.domain.profile import TravelerProfile
from app.domain.recommendations import (
    CountryGroup, DestinationIdentity, DestinationMatch, EvidenceProvenance,
    RecommendationMetadata, RecommendationResponse, ScoreComponent, UnsupportedPreference,
)
from app.services.catalog import CANDIDATE_POLICY_VERSION, select_candidates
from app.services.enrichment import usable_snapshot

SCORING_VERSION = "weighted-evidence-v1"
IMPORTANCE_WEIGHTS = {"low": 1, "moderate": 2, "high": 3}


def unsupported_preferences(profile: TravelerProfile):
    unsupported = [UnsupportedPreference(preference=interest.name, reason="No supported destination feature mapping for this interest yet.") for interest in profile.interests if interest.name not in FEATURES]
    unsupported.append(UnsupportedPreference(preference="Travel context", reason="Origin, dates, duration and date flexibility are not used in this interest-only ranking."))
    labels = {"travel_style": "Travel style", "activity_level": "Activity level", "walking_preference": "Walking preference", "weather_preference": "Weather preference", "weather_dislikes": "Weather dislikes", "crowd_preference": "Crowd preference", "trip_pace": "Trip pace"}
    for key, label in labels.items():
        if getattr(profile, key):
            unsupported.append(UnsupportedPreference(preference=label, reason="This preference is not assessed by the current interest-only ranking."))
    if profile.accessibility.requirements or profile.accessibility.additional_requirements:
        unsupported.append(UnsupportedPreference(preference="Comfort and accessibility", reason="Accessibility and comfort suitability have not been assessed. Check these independently before choosing a destination."))
    return unsupported


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def rank_cached(cities, snapshots, profile: TravelerProfile, limit: int, settings: Settings, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    supported = [interest for interest in profile.interests if interest.name in FEATURES]
    total_weight = sum(IMPORTANCE_WEIGHTS[item.importance] for item in supported)
    matches = []
    excluded = 0
    if supported:
        for city in cities:
            snapshot = snapshots.get(city.geoname_id)
            if not usable_snapshot(city, snapshot, [item.name for item in supported]):
                excluded += 1
                continue
            evidence = {item.feature: item for item in snapshot.features}
            components = []
            raw_score = 0.0
            for interest in sorted(supported, key=lambda item: item.name):
                item = evidence[interest.name]
                weight = IMPORTANCE_WEIGHTS[interest.importance] / total_weight
                contribution = 100 * weight * item.normalized_value
                raw_score += contribution
                components.append(ScoreComponent(
                    feature=interest.name, user_importance=interest.importance,
                    weight=round(weight, 6), destination_feature=item.normalized_value,
                    contribution=round(contribution, 1), raw_count=item.raw_count,
                    capped=item.capped, categories=item.categories, proxy=FEATURES[interest.name].description,
                ))
            leading = sorted((item for item in components if item.contribution > 0), key=lambda item: (-item.contribution, item.feature))[:3]
            reasons = [f"Mapped places related to {item.feature} support your {item.user_importance} interest in this topic." for item in leading]
            if not reasons:
                reasons = ["No matching mapped places were found in the current cached evidence."]
            matches.append(DestinationMatch(
                destination=DestinationIdentity(
                    geoname_id=city.geoname_id, city=city.name, country_code=city.country_code,
                    country=city.country_name, latitude=city.latitude, longitude=city.longitude,
                    population=city.population, timezone=city.timezone,
                ), match_score=round(raw_score, 1), components=components, top_reasons=reasons,
                data=EvidenceProvenance(
                    provider=snapshot.provider, place_id=snapshot.place_id, fetched_at=snapshot.fetched_at,
                    stale=utc(snapshot.fetched_at) < now - timedelta(days=settings.feature_stale_after_days),
                    mapping_version=snapshot.mapping_version, normalization_version=snapshot.normalization_version,
                    request_strategy=snapshot.request_strategy, category_query_limit=CATEGORY_RESULT_LIMIT,
                    unique_poi_count=len({identifier for item in snapshot.category_evidence.values() for identifier in item["poi_ids"]}),
                    catalog_source=city.source, catalog_source_sha256=city.source_sha256,
                    country_info_sha256=city.country_info_sha256, catalog_imported_at=city.imported_at,
                ),
            ))
    matches.sort(key=lambda match: (-match.match_score, match.destination.geoname_id))
    return matches[:limit], len(matches), excluded


def recommend(session: Session, profile: TravelerProfile, limit: int, settings: Settings) -> RecommendationResponse:
    # No provider is instantiated here. Requests only read persisted catalog/evidence.
    has_catalog = session.scalar(select(Destination.geoname_id).limit(1)) is not None
    cities = session.scalars(select(Destination).where(Destination.population >= settings.candidate_min_population)).all() if has_catalog else []
    candidates = select_candidates(cities, settings.candidate_min_population, settings.candidate_max_count)
    supported = [item.name for item in profile.interests if item.name in FEATURES]
    snapshots = {item.destination_id: item for item in session.scalars(select(DestinationEnrichment).where(DestinationEnrichment.destination_id.in_([city.geoname_id for city in candidates]))).all()} if candidates and supported else {}
    results, evaluated, excluded = rank_cached(candidates, snapshots, profile, limit, settings)
    if not has_catalog:
        status, message = "catalog_empty", "Destination matches are not ready yet: the city catalog has not been loaded."
    elif not supported:
        status, message = "no_supported_interests", "Choose at least one currently supported interest to find destination matches."
    elif not results:
        status, message = "no_enriched_candidates", "No candidates have complete, compatible evidence for your interests yet. Please try again after destination data is prepared."
    else:
        status, message = "ready", "Matches use cached mapped-place evidence for your supported interests, not an assessment of destination quality."
    groups = {}
    for match in results:
        city = match.destination
        if city.country_code not in groups:
            groups[city.country_code] = CountryGroup(country_code=city.country_code, country=city.country, destination_ids=[])
        groups[city.country_code].destination_ids.append(city.geoname_id)
    return RecommendationResponse(
        metadata=RecommendationMetadata(
            status=status, message=message, candidate_policy=CANDIDATE_POLICY_VERSION,
            candidate_min_population=settings.candidate_min_population, candidate_max_count=settings.candidate_max_count,
            candidate_count=len(candidates), evaluated_count=evaluated, excluded_incomplete_count=excluded,
            supported_interests=supported, generated_at=datetime.now(timezone.utc), scoring_version=SCORING_VERSION,
        ), results=results, unsupported_preferences=unsupported_preferences(profile), countries=list(groups.values()),
    )
