from datetime import datetime, timezone
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from app.core.config import Settings
from app.domain.profile import TravelerProfile
from app.main import create_app
from app.services.ranking import rank_cached


def settings():
    return Settings(_env_file=None, database_url="postgresql+psycopg://test:test@localhost/test", candidate_max_count=40)


def profile(museums="high", food="low", extra=None):
    interests = [{"name":"museums", "importance":museums}, {"name":"food", "importance":food}]
    if extra:
        interests.append({"name":extra, "importance":"high"})
    return TravelerProfile(travel_context={"duration_days":7}, interests=interests)


def test_weights_change_influence_and_ranking(city_factory,snapshot_factory):
    a,b = city_factory(1),city_factory(2)
    snapshots={1:snapshot_factory(a,{"museums":20,"food":5}),2:snapshot_factory(b,{"museums":5,"food":20})}
    museum_results,_,_ = rank_cached([a,b],snapshots,profile(),5,settings())
    food_results,_,_ = rank_cached([a,b],snapshots,profile("low","high"),5,settings())
    assert museum_results[0].match_score == 57.4
    assert museum_results[0].destination.geoname_id == 1
    assert food_results[0].destination.geoname_id == 2
    museum_component = next(item for item in museum_results[0].components if item.feature=="museums")
    assert museum_component.weight == .75
    assert museum_component.contribution > next(item for item in museum_results[0].components if item.feature=="food").contribution


def test_unsupported_interests_do_not_change_denominator(city_factory,snapshot_factory):
    city=city_factory()
    snapshots={1:snapshot_factory(city,{"museums":20,"food":10})}
    base,_,_=rank_cached([city],snapshots,profile(),5,settings())
    extended,_,_=rank_cached([city],snapshots,profile(extra="football"),5,settings())
    assert base[0].match_score == extended[0].match_score
    assert [item.feature for item in extended[0].components]==["food","museums"]


def test_equal_scores_have_explicit_stable_id_order(city_factory,snapshot_factory):
    cities=[city_factory(3),city_factory(1),city_factory(2)]
    snapshots={city.geoname_id:snapshot_factory(city,{"food":10,"museums":10}) for city in cities}
    for ordered in [cities, list(reversed(cities))]:
        results, evaluated, excluded=rank_cached(ordered,snapshots,profile(),2,settings())
        assert [item.destination.geoname_id for item in results]==[1,2]
        assert evaluated==3 and excluded==0


def test_missing_incompatible_and_corrupt_features_are_excluded(city_factory,snapshot_factory):
    city=city_factory()
    snapshot=snapshot_factory(city,{"museums":10})
    snapshot.features=[item for item in snapshot.features if item.feature!="food"]
    assert rank_cached([city],{1:snapshot},profile(),5,settings()) == ([],0,1)
    snapshot=snapshot_factory(city,mapping_version="old")
    assert rank_cached([city],{1:snapshot},profile(),5,settings()) == ([],0,1)
    snapshot=snapshot_factory(city)
    snapshot.features[0].raw_count=-1
    custom=TravelerProfile(travel_context={"duration_days":1},interests=[{"name":"architecture"}])
    assert rank_cached([city],{1:snapshot},custom,5,settings()) == ([],0,1)


def test_changed_catalog_invalidates_snapshot(city_factory,snapshot_factory):
    city=city_factory()
    snapshot=snapshot_factory(city)
    city.population+=1
    assert rank_cached([city],{1:snapshot},profile(),5,settings()) == ([],0,1)


def test_stale_evidence_is_disclosed_not_silently_refreshed(city_factory,snapshot_factory):
    city=city_factory()
    results,_,_=rank_cached([city],{1:snapshot_factory(city)},profile(),5,settings(),datetime(2026,6,1,tzinfo=timezone.utc))
    assert results[0].data.stale
    assert results[0].top_reasons == ["No matching mapped places were found in the current cached evidence."]


@pytest.fixture
def client(database):
    with patch("app.main.create_db_engine", return_value=database.engine):
        with TestClient(create_app(settings())) as value:
            yield value


def request_payload(interests=None,limit=5):
    return {"profile":{"travel_context":{"duration_days":7},"interests":interests or [{"name":"museums","importance":"high"}]},"limit":limit}


def test_api_catalog_empty_is_an_honest_empty_state(client):
    response=client.post("/api/v1/recommendations/destinations",json=request_payload())
    assert response.status_code==200
    assert response.json()["metadata"]["status"]=="catalog_empty"
    assert response.json()["results"]==[]


def test_api_no_enriched_candidates(client,database,city_factory):
    with database.factory.begin() as session:
        session.add(city_factory())
    response=client.post("/api/v1/recommendations/destinations",json=request_payload())
    assert response.json()["metadata"]["status"]=="no_enriched_candidates"
    assert response.json()["metadata"]["excluded_incomplete_count"]==1


def test_api_returns_cached_components_countries_and_unsupported(client,database,city_factory,snapshot_factory):
    with database.factory.begin() as session:
        city=city_factory()
        session.add(city)
        session.add(snapshot_factory(city,{"museums":20}))
    payload=request_payload([{"name":"museums","importance":"high"},{"name":"football","importance":"high"}])
    payload["profile"].update({"weather_preference":"warm","accessibility":{"requirements":["wheelchair_access"]}})
    for _ in range(2):
        with patch("httpx.AsyncClient.get",side_effect=AssertionError("Traveler requests must never use the provider")):
            response=client.post("/api/v1/recommendations/destinations",json=payload)
        data=response.json()
        assert response.status_code==200
        assert data["metadata"]["status"]=="ready"
        assert data["results"][0]["components"][0]["raw_count"]==20
        assert data["results"][0]["components"][0]["weight"]==1
        assert {"football","Weather preference","Comfort and accessibility"} <= {item["preference"] for item in data["unsupported_preferences"]}
        assert data["countries"]==[{"country_code":"DE","country":"Germany","destination_ids":[1]}]
        assert "score" not in data["countries"][0]


def test_api_only_unsupported_interests_returns_no_fabricated_score(client,database,city_factory):
    with database.factory.begin() as session:
        session.add(city_factory())
    data=client.post("/api/v1/recommendations/destinations",json=request_payload([{"name":"football","importance":"high"}])).json()
    assert data["metadata"]["status"]=="no_supported_interests"
    assert data["results"]==[]


@pytest.mark.parametrize("limit",[0,21,-1,True,"5"])
def test_api_result_limit_bounds(client,limit):
    assert client.post("/api/v1/recommendations/destinations",json=request_payload(limit=limit)).status_code==422


def test_api_database_failure_is_sanitized(client):
    with patch("app.api.recommendations.recommend",side_effect=SQLAlchemyError("private connection details")):
        response=client.post("/api/v1/recommendations/destinations",json=request_payload())
    assert response.status_code==503
    assert "private" not in response.text


def test_original_evidence_corruption_or_legacy_collection_is_not_ranked(city_factory, snapshot_factory):
    city = city_factory()
    snapshot = snapshot_factory(city, {"museums": 10})
    snapshot.category_evidence["entertainment.museum"]["poi_ids"] = []
    assert rank_cached([city], {1: snapshot}, profile(), 5, settings()) == ([], 0, 1)
    legacy = snapshot_factory(city, request_strategy="legacy-union-v1")
    assert rank_cached([city], {1: legacy}, profile(), 5, settings()) == ([], 0, 1)
