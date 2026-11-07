import asyncio
import copy
import httpx
import pytest
from app.services.geoapify import GeoapifyProvider, ProviderError
from app.core.config import Settings
from pydantic import ValidationError


def geo_feature(**changes):
    props = {"name": "City 1", "city": "City 1", "country_code": "de", "lat": 52.5, "lon": 13.4, "place_id": "city-boundary-1", "result_type": "city", "categories": ["administrative.city_level"]}
    props.update(changes)
    return {"type": "Feature", "properties": props}


def run_provider(handler, action, settings=None):
    settings = settings or Settings(_env_file=None, database_url="postgresql+psycopg://test:test@localhost/test")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await action(GeoapifyProvider(client, "unit-test-key", min_interval=0, timeout=settings.geoapify_timeout))
    return asyncio.run(run())


def test_identity_and_real_boundary_filter(city_factory):
    requests = []
    def handle(request):
        requests.append(request)
        if request.url.path == "/v2/places":
            assert request.url.params["filter"] == "place:city-boundary-1"
            assert request.url.params["categories"] == "entertainment.museum"
            assert request.url.params["limit"] == "20"
            assert request.url.params["lang"] == "en"
            assert "offset" not in request.url.params
            feature = {"properties": {"place_id": "museum-1", "categories": ["entertainment.museum"]}}
            return httpx.Response(200, json={"features": [feature, copy.deepcopy(feature)]})
        return httpx.Response(200, json={"features": [geo_feature()]})
    async def action(provider):
        resolution = await provider.resolve_city(city_factory())
        evidence = await provider.places(resolution.place_id, "entertainment.museum")
        assert resolution.evidence["resolved_by"] == "/v1/geocode/search"
        assert resolution.evidence["independent_polygon_verification"] is False
        assert evidence.count == 1
        assert not evidence.capped
    run_provider(handle, action)
    assert [item.url.path for item in requests] == ["/v1/geocode/search", "/v2/places"]
    assert "circle" not in str(requests[-1].url)


@pytest.mark.parametrize("changes", [{"country_code": "us"}, {"city": "Other", "name": "Other"}, {"lat": 0, "lon": 0}, {"result_type": "suburb"}, {"lat": "invalid"}])
def test_obvious_city_mismatches_are_rejected(city_factory, changes):
    with pytest.raises(ProviderError, match="city_identity_unresolved"):
        run_provider(lambda request: httpx.Response(200, json={"features": [geo_feature(**changes)]}), lambda provider: provider.resolve_city(city_factory()))


def test_ambiguous_geocoding_is_rejected(city_factory):
    with pytest.raises(ProviderError, match="city_identity_unresolved"):
        run_provider(lambda request: httpx.Response(200, json={"features": [geo_feature(), geo_feature(place_id="another-city-id")]}), lambda provider: provider.resolve_city(city_factory()))


@pytest.mark.parametrize("identifier", [None, "", "x", "  ", "bad id", "x" * 2049])
def test_missing_or_invalid_place_id_is_rejected(city_factory, identifier):
    with pytest.raises(ProviderError, match="city_identity_unresolved"):
        run_provider(lambda request: httpx.Response(200, json={"features": [geo_feature(place_id=identifier)]}),
                     lambda provider: provider.resolve_city(city_factory()))


@pytest.mark.parametrize("status,code", [(429,"rate_limited"),(401,"provider_authentication"),(503,"provider_unavailable"),(400,"provider_rejected_request")])
def test_provider_errors_are_sanitized_without_retries(status,code):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="sensitive provider details", headers={"Retry-After":"30"})
    with pytest.raises(ProviderError) as raised:
        run_provider(handle, lambda provider: provider.places("city-boundary-1", "entertainment.museum"))
    assert raised.value.code == code
    assert "unit-test-key" not in str(raised.value)
    assert "sensitive" not in str(raised.value)
    assert len(calls) == 1
    if status == 429:
        assert raised.value.retry_after == 30


def test_timeout_is_sanitized():
    def handle(request):
        raise httpx.ReadTimeout("key in URL", request=request)
    with pytest.raises(ProviderError, match="provider_unavailable"):
        run_provider(handle, lambda provider: provider.places("id", "beach"))


@pytest.mark.parametrize("body", [{}, {"features":[{}]}, {"features":[{"properties":{"place_id":"bad", "categories":["unrelated"]}}]}])
def test_malformed_evidence_is_not_a_zero_count(body):
    with pytest.raises(ProviderError, match="invalid_response"):
        run_provider(lambda request: httpx.Response(200, json=body), lambda provider: provider.places("city", "beach"))


def test_capped_evidence_is_explicit():
    features = [{"properties":{"place_id":f"place-{i}","categories":["beach"]}} for i in range(20)]
    result = run_provider(lambda request: httpx.Response(200, json={"features":features}), lambda provider: provider.places("city", "beach"))
    assert result.count == 20 and result.capped


def test_transport_logs_keep_exception_type_without_key_or_url(caplog):
    def handle(request):
        raise httpx.ConnectTimeout("", request=request)
    with pytest.raises(ProviderError, match="provider_unavailable"):
        run_provider(handle, lambda provider: provider.places("city", "beach"))
    assert "ConnectTimeout" in caplog.text
    assert "Connection establishment timed out." in caplog.text
    assert "unit-test-key" not in caplog.text
    assert "https://" not in caplog.text


def test_transport_log_redacts_raw_encoded_keys_and_urls(caplog):
    from urllib.parse import quote
    from app.services.geoapify import sanitized_transport_message
    key = "example/key with+symbols"
    error = httpx.ConnectError(
        "Cannot connect https://api.geoapify.com/v1/geocode/search?apiKey=" + quote(key, safe="") + " raw=" + key
    )
    message = sanitized_transport_message(error, key)
    assert key not in message
    assert quote(key, safe="") not in message
    assert "https://" not in message
    assert "[REDACTED]" in message
    assert "[URL redacted]" in message


def test_geoapify_timeout_defaults(monkeypatch):
    for part in ["CONNECT", "READ", "WRITE", "POOL"]:
        monkeypatch.delenv(f"GEOAPIFY_{part}_TIMEOUT_SECONDS", raising=False)
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://test:test@localhost/test")
    assert settings.geoapify_timeout.as_dict() == {"connect": 15.0, "read": 15.0, "write": 5.0, "pool": 5.0}


def test_environment_timeouts_reach_actual_httpx_request(monkeypatch):
    values = {"CONNECT": 19, "READ": 23, "WRITE": 7, "POOL": 9}
    for part, value in values.items():
        monkeypatch.setenv(f"GEOAPIFY_{part}_TIMEOUT_SECONDS", str(value))
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://test:test@localhost/test")
    def handle(request):
        assert request.extensions["timeout"] == {key.lower(): float(value) for key, value in values.items()}
        return httpx.Response(200, json={"features": []})
    result = run_provider(handle, lambda provider: provider.places("city", "beach"), settings)
    assert result.count == 0


@pytest.mark.parametrize("part", ["connect", "read", "write", "pool"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_geoapify_timeouts_require_positive_finite_bounds(part, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="postgresql+psycopg://test:test@localhost/test", **{f"geoapify_{part}_timeout_seconds": value})


def test_actual_transport_log_redacts_authenticated_request_url(caplog):
    def handle(request):
        raise httpx.ReadTimeout("Request timed out for " + str(request.url), request=request)
    with pytest.raises(ProviderError, match="provider_unavailable"):
        run_provider(handle, lambda provider: provider.places("city", "beach"))
    assert "ReadTimeout" in caplog.text
    assert "[URL redacted]" in caplog.text
    assert "unit-test-key" not in caplog.text
    assert "https://" not in caplog.text
    assert "apiKey=" not in caplog.text


def test_berlin_geocoded_place_is_used_without_boundary_calls(city_factory):
    calls = []
    def handle(request):
        calls.append(request)
        assert request.url.path == "/v1/geocode/search"
        assert request.url.params["type"] == "city"
        assert request.url.params["filter"] == "countrycode:de"
        assert request.url.params["limit"] == "5"
        assert request.url.params["lang"] == "en"
        return httpx.Response(200, json={"features": [geo_feature(name="Berlin", city="Berlin", state="Berlin",
            place_id="berlin-geocoded-place", categories=["administrative.country_part_level"])]})
    result = run_provider(handle, lambda provider: provider.resolve_city(city_factory(name="Berlin", ascii_name="Berlin")))
    assert result.place_id == "berlin-geocoded-place"
    assert result.evidence["resolution_policy"] == "geocoded-city-v1"
    assert len(calls) == 1


def test_same_name_candidates_require_country_and_proximity(city_factory):
    features = [geo_feature(country_code="us", place_id="other-country"),
                geo_feature(lat=48, lon=11, place_id="same-country-far-away"), geo_feature()]
    result = run_provider(lambda request: httpx.Response(200, json={"features": features}),
        lambda provider: provider.resolve_city(city_factory()))
    assert result.place_id == "city-boundary-1"


def test_multiple_or_unknown_categories_are_rejected_without_http():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"features": []})
    for category in ["beach,natural", ("beach", "natural"), "invented.category"]:
        with pytest.raises(ProviderError, match="unsupported_category"):
            run_provider(handle, lambda provider: provider.places("city", category))
    assert requests == []


def test_provider_enforces_budget_before_an_extra_request():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"features": []})
    async def action(provider):
        provider.max_requests = 1
        await provider.places("city", "beach")
        with pytest.raises(ProviderError, match="request_budget_exceeded"):
            await provider.places("city", "natural")
        assert provider.request_count == 1
    run_provider(handle, action)
    assert len(requests) == 1


def test_original_category_evidence_and_duplicate_identifiers_are_preserved():
    features = [{"type": "Feature", "properties": {"place_id": "poi-1", "name": "Original name", "categories": ["beach"], "datasource": {"sourcename": "openstreetmap"}}}] * 2
    evidence = run_provider(lambda request: httpx.Response(200, json={"features": features}), lambda provider: provider.places("city", "beach"))
    assert evidence.count == 1
    assert evidence.to_record()["returned_count"] == 2
    assert evidence.to_record()["features"] == features
    assert evidence.poi_ids == {"poi-1"}


def test_response_echoing_configured_key_is_not_persistable():
    feature = {"properties": {"place_id": "poi-1", "categories": ["beach"], "unexpected": "unit-test-key"}}
    with pytest.raises(ProviderError, match="invalid_response"):
        run_provider(lambda request: httpx.Response(200, json={"features": [feature]}), lambda provider: provider.places("city", "beach"))


def test_failed_http_attempt_counts_against_both_request_and_credit_budget():
    requests = []
    def handle(request):
        requests.append(request)
        raise httpx.ReadTimeout("", request=request)
    async def action(provider):
        provider.max_credits = 1
        with pytest.raises(ProviderError, match="provider_unavailable"):
            await provider.places("city", "beach")
        assert provider.request_count == provider.credits_reserved == 1
        with pytest.raises(ProviderError, match="credit_budget_exceeded"):
            await provider.places("city", "beach")
        assert provider.request_count == 1
    run_provider(handle, action)
    assert len(requests) == 1


def test_unbounded_boundary_credit_cost_blocks_before_http():
    requests = []
    async def action(provider):
        provider.max_credits = 23
        with pytest.raises(ProviderError, match="credit_cost_unbounded"):
            await provider._get("/v1/boundaries/part-of", {"geometry": "point"})
        with pytest.raises(ProviderError, match="credit_cost_unbounded"):
            await provider._get("/v1/boundaries/part-of", {"geometry": "geometry_1000"})
        assert provider.request_count == provider.credits_reserved == 0
    run_provider(lambda request: requests.append(request), action)
    assert requests == []


@pytest.mark.parametrize("offset,accepted", [(24.9, True), (25.1, False)])
def test_existing_25km_proximity_limit_is_preserved(city_factory, offset, accepted):
    feature = geo_feature(lat=52.5 + offset / 111.195)
    action = lambda provider: provider.resolve_city(city_factory())
    handler = lambda request: httpx.Response(200, json={"features": [feature]})
    if accepted:
        assert run_provider(handler, action).place_id == "city-boundary-1"
    else:
        with pytest.raises(ProviderError, match="city_identity_unresolved"):
            run_provider(handler, action)


def test_city_name_normalization_is_preserved(city_factory):
    result = run_provider(lambda request: httpx.Response(200, json={"features": [geo_feature(name="CITY-1", city="CITY-1")]}),
        lambda provider: provider.resolve_city(city_factory()))
    assert result.place_id == "city-boundary-1"
