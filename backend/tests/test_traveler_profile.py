from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.domain.profile import TravelerProfile
from app.main import create_app


@pytest.fixture
def payload():
    return {
        "travel_context": {
            "origin_city": "  Rome  ", "origin_country": "Italy",
            "start_date": "2027-04-01", "end_date": "2027-04-07",
            "flexible_dates": True,
        },
        "interests": [{"name": "  Street   Photography ", "importance": "high"}],
        "travel_style": "relaxed", "activity_level": "moderate",
        "walking_preference": "low", "weather_preference": "mild",
        "weather_dislikes": ["strong_heat"], "crowd_preference": "quieter",
        "trip_pace": "fewer_places",
        "accessibility": {"requirements": ["avoid_many_stairs"], "additional_requirements": "  Nearby seating  "},
    }


@pytest.fixture
def client():
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://test:test@localhost:5432/test", cors_origins=["http://localhost:3000"])
    engine = MagicMock()
    with patch("app.main.create_db_engine", return_value=engine):
        with TestClient(create_app(settings)) as test_client:
            yield test_client
        engine.connect.assert_not_called()


def test_valid_profile_is_normalized(payload):
    profile = TravelerProfile.model_validate(payload)
    assert profile.travel_context.origin_city == "Rome"
    assert profile.travel_context.duration_days == 7
    assert profile.interests[0].name == "street photography"
    assert profile.accessibility.additional_requirements == "Nearby seating"


def test_duration_without_dates_and_optional_preferences():
    profile = TravelerProfile(travel_context={"duration_days": 3, "origin_city": " "})
    assert profile.travel_context.origin_city is None
    assert profile.weather_preference is None
    assert profile.interests == []


@pytest.mark.parametrize("changes", [
    {"start_date": "2027-04-08", "end_date": "2027-04-07"},
    {"start_date": "not-a-date"},
    {"duration_days": 0}, {"duration_days": -1}, {"duration_days": 366},
    {"duration_days": 3}, {"duration_days": 2.5}, {"duration_days": True},
    {"end_date": "2028-04-07"},
])
def test_invalid_context(payload, changes):
    payload["travel_context"].update(changes)
    with pytest.raises(ValidationError):
        TravelerProfile.model_validate(payload)


def test_duration_or_both_dates_required():
    with pytest.raises(ValidationError):
        TravelerProfile(travel_context={"start_date": "2027-04-01"})


def test_same_day_and_leap_day_duration():
    for start, end, expected in [("2028-02-29", "2028-02-29", 1), ("2028-02-28", "2028-03-01", 3)]:
        profile = TravelerProfile(travel_context={"start_date": start, "end_date": end})
        assert profile.travel_context.duration_days == expected


def test_duplicate_interests_after_normalization(payload):
    payload["interests"].append({"name": "STREET PHOTOGRAPHY", "importance": "low"})
    with pytest.raises(ValidationError, match="only once"):
        TravelerProfile.model_validate(payload)


@pytest.mark.parametrize("interest", [
    {"name": "nature", "importance": "essential"}, {"name": " "},
    {"name": "x" * 81}, {"name": "bad\x00name"}, {"name": 42},
])
def test_invalid_interest(payload, interest):
    payload["interests"] = [interest]
    with pytest.raises(ValidationError):
        TravelerProfile.model_validate(payload)


def test_extensible_interest_vocabulary(payload):
    payload["interests"] = [{"name": "Birdwatching", "importance": "high"}]
    assert TravelerProfile.model_validate(payload).interests[0].name == "birdwatching"


@pytest.mark.parametrize("accessibility", [
    {"additional_requirements": "x" * 501}, {"additional_requirements": ["stairs"]},
    {"additional_requirements": "bad\ninput"}, {"requirements": "avoid_many_stairs"},
    {"requirements": ["unknown"]}, {"requirements": ["frequent_rest", "frequent_rest"]},
    {"diagnosis": "not collected"},
])
def test_accessibility_validation(payload, accessibility):
    payload["accessibility"] = accessibility
    with pytest.raises(ValidationError):
        TravelerProfile.model_validate(payload)


@pytest.mark.parametrize("changes", [
    {"travel_style": "luxury"}, {"activity_level": "extreme"},
    {"walking_preference": "none"}, {"weather_preference": "snow"},
    {"weather_dislikes": ["cold", "cold"]}, {"crowd_preference": "crowded"},
    {"trip_pace": "fast"}, {"medical_history": "not collected"},
    {"interests": [{"name": str(i)} for i in range(31)]},
])
def test_invalid_preferences(payload, changes):
    payload.update(changes)
    with pytest.raises(ValidationError):
        TravelerProfile.model_validate(payload)


def test_long_origin_is_rejected(payload):
    payload["travel_context"]["origin_city"] = "x" * 81
    with pytest.raises(ValidationError):
        TravelerProfile.model_validate(payload)


def test_api_success_and_canonical_roundtrip(client, payload):
    response = client.post("/api/v1/traveler-profile/validate", json=payload)
    assert response.status_code == 200
    canonical = response.json()
    assert canonical["travel_context"]["duration_days"] == 7
    assert canonical["interests"][0]["name"] == "street photography"
    assert client.post("/api/v1/traveler-profile/validate", json=canonical).json() == canonical


def test_api_validation_failure_has_field_location(client, payload):
    broken = deepcopy(payload)
    broken["interests"][0]["importance"] = "essential"
    response = client.post("/api/v1/traveler-profile/validate", json=broken)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "interests", 0, "importance"]


def test_api_post_cors_preflight(client):
    response = client.options("/api/v1/traveler-profile/validate", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "POST" in response.headers["access-control-allow-methods"]
