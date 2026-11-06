"""Reviewed Geoapify categories. Feature values are evidence proxies, not quality."""
from dataclasses import dataclass
from hashlib import sha256
import json
from math import log1p

MAPPING_VERSION = "geoapify-interests-v1"
NORMALIZATION_VERSION = "log-breadth-density-v1"
QUERY_LIMIT = 100  # Existing feature-count normalization bound; unchanged.
CATEGORY_RESULT_LIMIT = 20
REQUEST_STRATEGY_VERSION = "single-category-v2"
MAX_CATEGORY_REQUESTS = 22


@dataclass(frozen=True)
class FeatureDefinition:
    categories: tuple[str, ...]
    description: str


FEATURES = {
    "architecture": FeatureDefinition(("tourism.sights.building", "tourism.sights.bridge", "tourism.sights.castle", "tourism.sights.tower"), "Notable buildings, bridges, castles and towers; a built-heritage proxy, not architectural quality."),
    "history": FeatureDefinition(("heritage", "tourism.sights.archaeological_site", "tourism.sights.fort", "tourism.sights.memorial"), "Heritage, archaeological sites, forts and memorials; not an assessment of historical significance."),
    "photography": FeatureDefinition(("tourism.attraction.viewpoint", "heritage", "natural"), "Viewpoints, heritage and natural places are scenic-subject proxies, not photography conditions."),
    "food": FeatureDefinition(("catering.restaurant", "catering.cafe", "commercial.marketplace"), "Restaurants, cafes and marketplaces indicate food-place availability, not food quality."),
    "nature": FeatureDefinition(("natural", "leisure.park", "national_park"), "Natural places and parks within the city boundary, not regional outdoor access."),
    "art": FeatureDefinition(("entertainment.culture.gallery", "tourism.attraction.artwork"), "Galleries and public artworks, not artistic quality."),
    "museums": FeatureDefinition(("entertainment.museum",), "Mapped museum availability, not collection quality."),
    "beaches": FeatureDefinition(("beach",), "Mapped beaches, not beach quality or swimming conditions."),
    "shopping": FeatureDefinition(("commercial.shopping_mall", "commercial.marketplace"), "Malls and marketplaces are a limited shopping proxy, not the full retail scene."),
    "nightlife": FeatureDefinition(("catering.bar", "catering.pub"), "Bars and pubs are a limited nightlife proxy; events and late opening are not verified."),
}


CATEGORY_KEYS = tuple(dict.fromkeys(category for definition in FEATURES.values() for category in definition.categories))

def normalize_count(count: int, population: int) -> float:
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= QUERY_LIMIT:
        raise ValueError("Count must be an integer from 0 to 100")
    if population < 0:
        raise ValueError("Population cannot be negative")
    breadth = min(log1p(count) / log1p(50), 1.0)
    density = count * 100_000 / max(population, 100_000)
    density_signal = min(log1p(density) / log1p(20), 1.0)
    return round((breadth + density_signal) / 2, 6)


def catalog_fingerprint(city) -> str:
    values = [city.geoname_id, city.name, city.ascii_name, city.country_code, city.latitude, city.longitude, city.population]
    return sha256(json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
