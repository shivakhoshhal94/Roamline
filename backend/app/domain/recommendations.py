from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field
from app.domain.profile import TravelerProfile, PreferenceLevel


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: TravelerProfile
    limit: Annotated[int, Field(strict=True, ge=1, le=20)] = 5


class UnsupportedPreference(BaseModel):
    preference: str
    reason: str


class ScoreComponent(BaseModel):
    feature: str
    user_importance: PreferenceLevel
    weight: float
    destination_feature: float
    contribution: float
    raw_count: int
    capped: bool
    categories: list[str]
    proxy: str


class DestinationIdentity(BaseModel):
    geoname_id: int
    city: str
    country_code: str
    country: str
    latitude: float
    longitude: float
    population: int
    timezone: str | None


class EvidenceProvenance(BaseModel):
    provider: str
    place_id: str
    fetched_at: datetime
    stale: bool
    mapping_version: str
    normalization_version: str
    request_strategy: str
    category_query_limit: int
    unique_poi_count: int
    catalog_source: str
    catalog_source_sha256: str
    country_info_sha256: str
    catalog_imported_at: datetime


class DestinationMatch(BaseModel):
    destination: DestinationIdentity
    match_score: float
    components: list[ScoreComponent]
    top_reasons: list[str]
    data: EvidenceProvenance


class RecommendationMetadata(BaseModel):
    status: Literal["ready", "catalog_empty", "no_enriched_candidates", "no_supported_interests"]
    message: str
    candidate_policy: str
    candidate_min_population: int
    candidate_max_count: int
    candidate_count: int
    evaluated_count: int
    excluded_incomplete_count: int
    supported_interests: list[str]
    generated_at: datetime
    scoring_version: str
    score_scale: str = "0–100 evidence match; not a quality rating or probability"
    tie_break: str = "Displayed score descending, then GeoNames ID ascending"
    attribution: list[str] = ["GeoNames (CC BY 4.0)", "Geoapify; © OpenStreetMap contributors (ODbL)"]


class CountryGroup(BaseModel):
    country_code: str
    country: str
    destination_ids: list[int]


class RecommendationResponse(BaseModel):
    metadata: RecommendationMetadata
    results: list[DestinationMatch]
    unsupported_preferences: list[UnsupportedPreference]
    countries: list[CountryGroup]
