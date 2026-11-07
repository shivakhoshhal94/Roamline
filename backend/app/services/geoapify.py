"""Async provider boundary. No provider exceptions, response bodies or keyed URLs escape."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import logging
import re
import time
from typing import Protocol
import unicodedata
from urllib.parse import quote, quote_plus

import httpx
from app.db.models import Destination
from app.domain.destination_features import CATEGORY_KEYS, CATEGORY_RESULT_LIMIT


logger = logging.getLogger(__name__)


def sanitized_transport_message(error: httpx.RequestError, api_key: str) -> str:
    """Keep actionable transport detail, never keyed URLs or the configured secret."""
    message = str(error)
    for secret in {api_key, quote(api_key, safe=""), quote_plus(api_key)}:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    message = re.sub(r"https?://[^\s\"'<>]+", "[URL redacted]", message)
    if not message.strip():
        if isinstance(error, httpx.ConnectTimeout):
            message = "Connection establishment timed out."
        elif isinstance(error, httpx.ReadTimeout):
            message = "Response read timed out."
        elif isinstance(error, httpx.ProxyError):
            message = "Proxy negotiation failed."
        else:
            message = "Transport failure without an exception message."
    return message[:500]


class ProviderError(Exception):
    def __init__(self, code: str, retry_after: int | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


@dataclass(frozen=True)
class CityResolution:
    place_id: str
    evidence: dict


@dataclass(frozen=True)
class CategoryEvidence:
    category: str
    features: tuple[dict, ...]
    fetched_at: datetime

    def __post_init__(self):
        if (self.category not in CATEGORY_KEYS or len(self.features) > CATEGORY_RESULT_LIMIT
                or self.fetched_at.tzinfo is None):
            raise ProviderError("invalid_response")
        for feature in self.features:
            if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
                raise ProviderError("invalid_response")
            properties = feature["properties"]
            identifier = properties.get("place_id")
            categories = properties.get("categories")
            if (not isinstance(identifier, str) or not identifier or len(identifier) > 2048
                    or not isinstance(categories, list)
                    or not any(isinstance(value, str) and (value == self.category or value.startswith(self.category + ".")) for value in categories)):
                raise ProviderError("invalid_response")

    @property
    def poi_ids(self) -> frozenset[str]:
        return frozenset(feature["properties"]["place_id"] for feature in self.features)

    @property
    def count(self) -> int:
        return len(self.poi_ids)

    @property
    def capped(self) -> bool:
        return len(self.features) == CATEGORY_RESULT_LIMIT

    def to_record(self) -> dict:
        return {"category": self.category, "features": list(self.features), "poi_ids": sorted(self.poi_ids),
                "returned_count": len(self.features), "unique_count": self.count, "capped": self.capped,
                "fetched_at": self.fetched_at.isoformat(), "limit": CATEGORY_RESULT_LIMIT, "lang": "en"}

    @classmethod
    def from_record(cls, category: str, record: dict) -> "CategoryEvidence":
        try:
            if not isinstance(record, dict) or not isinstance(record.get("features"), list):
                raise ValueError()
            evidence = cls(category, tuple(record["features"]), datetime.fromisoformat(record["fetched_at"]))
            canonical = evidence.to_record()
            if any(record.get(key) != value for key, value in canonical.items()):
                raise ValueError()
            return evidence
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ProviderError("invalid_cached_evidence") from None


class DestinationProvider(Protocol):
    async def resolve_city(self, city: Destination) -> CityResolution: ...
    async def places(self, place_id: str, category: str) -> CategoryEvidence: ...


def normalized_name(value: str) -> str:
    return re.sub(r"[^\w]", "", "".join(char for char in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(char)))


def distance_km(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    dlat = b - a
    dlon = math.radians(lon2 - lon1)
    value = math.sin(dlat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(value)))


def features_from(data: dict) -> list[dict]:
    features = data.get("features")
    if not isinstance(features, list) or any(not isinstance(item, dict) or not isinstance(item.get("properties"), dict) for item in features):
        raise ProviderError("invalid_response")
    return features


def identity_match(city: Destination, properties: dict) -> bool:
    if str(properties.get("country_code", "")).upper() != city.country_code:
        return False
    names = {normalized_name(city.name), normalized_name(city.ascii_name)} - {""}
    provider_names = {normalized_name(str(properties.get(key, ""))) for key in ("city", "name")}
    if not names.intersection(provider_names):
        return False
    try:
        lat, lon = float(properties["lat"]), float(properties["lon"])
        return (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180
                and distance_km(city.latitude, city.longitude, lat, lon) <= 25)
    except (TypeError, ValueError, KeyError):
        return False


class GeoapifyProvider:
    def __init__(self, client: httpx.AsyncClient, api_key: str, min_interval: float = 1.0, *, timeout: httpx.Timeout, places_timeout: httpx.Timeout | None = None, max_requests: int = 23, max_credits: int | None = None):
        if not api_key.strip():
            raise ProviderError("missing_api_key")
        if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 1:
            raise ValueError("Request budget must be a positive integer")
        if max_credits is not None and (isinstance(max_credits, bool) or not isinstance(max_credits, int) or max_credits < 1):
            raise ValueError("Credit budget must be a positive integer")
        self.max_credits = max_credits
        self.credits_reserved = 0
        self.max_requests = max_requests
        self.request_count = 0
        self.timeout = timeout
        self.places_timeout = places_timeout if places_timeout is not None else timeout
        self.client = client
        self._api_key = api_key
        self.min_interval = min_interval
        self._last_request = 0.0

    async def _get(self, path: str, parameters: dict) -> dict:
        if self.request_count >= self.max_requests:
            raise ProviderError("request_budget_exceeded")
        # Only geocoding and <=20 Places have a documented fixed cost. Boundary
        # result counts/geometry cost are unbounded offline: never dispatch them
        # under a finite authorized credit ceiling without a proven cost bound.
        if self.max_credits is not None:
            if (path not in {"/v1/geocode/search", "/v2/places"}
                    or (path == "/v2/places" and parameters.get("limit") != CATEGORY_RESULT_LIMIT)):
                raise ProviderError("credit_cost_unbounded")
            if self.credits_reserved + 1 > self.max_credits:
                raise ProviderError("credit_budget_exceeded")
            self.credits_reserved += 1
        self.request_count += 1
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = time.monotonic()
        try:
            response = await self.client.get("https://api.geoapify.com" + path, params={**parameters, "apiKey": self._api_key}, timeout=self.places_timeout if path == "/v2/places" else self.timeout)
        except httpx.RequestError as error:
            logger.warning(
                "Geoapify transport failure (%s): %s",
                type(error).__name__, sanitized_transport_message(error, self._api_key),
            )
            raise ProviderError("provider_unavailable") from None
        if response.status_code == 429:
            retry = response.headers.get("Retry-After", "")
            raise ProviderError("rate_limited", int(retry) if retry.isdigit() else None)
        if response.status_code in (401, 403):
            raise ProviderError("provider_authentication")
        if response.status_code >= 500:
            raise ProviderError("provider_unavailable")
        if not response.is_success:
            raise ProviderError("provider_rejected_request")
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except ValueError:
            raise ProviderError("invalid_response") from None

    async def resolve_city(self, city: Destination) -> CityResolution:
        data = await self._get("/v1/geocode/search", {
            "text": f"{city.name}, {city.country_code}", "type": "city",
            "filter": f"countrycode:{city.country_code.lower()}",
            "bias": f"proximity:{city.longitude},{city.latitude}", "limit": 5, "lang": "en", "format": "geojson",
        })
        matches = {}
        for feature in features_from(data):
            props = feature["properties"]
            place_id = props.get("place_id")
            if (props.get("result_type") == "city" and isinstance(place_id, str) and 2 <= len(place_id) <= 2048 and place_id.strip() == place_id
                    and not any(char.isspace() for char in place_id) and identity_match(city, props)):
                matches[place_id] = feature
        if len(matches) != 1:
            raise ProviderError("city_identity_unresolved")
        geocoding_id, matched_feature = next(iter(matches.items()))
        matched = matched_feature["properties"]
        return CityResolution(geocoding_id, {
            "resolution_policy": "geocoded-city-v1", "resolved_by": "/v1/geocode/search",
            "geocoding_place_id": geocoding_id, "city": matched.get("city", matched.get("name")),
            "country_code": city.country_code, "result_type": "city",
            "latitude": matched["lat"], "longitude": matched["lon"],
            "distance_km": round(distance_km(city.latitude, city.longitude, float(matched["lat"]), float(matched["lon"])), 3),
            "filter": "place", "independent_polygon_verification": False,
        })

    async def places(self, place_id: str, category: str) -> CategoryEvidence:
        if not isinstance(category, str) or category not in CATEGORY_KEYS:
            raise ProviderError("unsupported_category")
        data = await self._get("/v2/places", {
            "categories": category, "filter": f"place:{place_id}",
            "limit": CATEGORY_RESULT_LIMIT, "lang": "en",
        })
        features = features_from(data)
        # Preserve original POI evidence, but never persist a response that
        # unexpectedly echoes this request's configured credential.
        if self._api_key in json.dumps(features, ensure_ascii=True):
            raise ProviderError("invalid_response")
        return CategoryEvidence(category, tuple(features), datetime.now(timezone.utc))
