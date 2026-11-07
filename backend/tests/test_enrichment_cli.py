import json
from unittest.mock import patch
import pytest
from app import cli
from app.core.config import Settings
from app.db.models import Destination


def test_cli_dry_run_without_key_is_read_only(database, city_factory, capsys, monkeypatch):
    with database.factory.begin() as session:
        session.add(city_factory())
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://test:test@localhost/test")
    monkeypatch.setattr("sys.argv", ["roamline", "enrich", "--geoname-id", "1", "--dry-run", "--refresh", "--max-requests", "3"])
    with patch.object(cli, "Settings", return_value=settings), patch.object(cli, "create_db_engine", return_value=database.engine), patch.object(database.engine, "dispose"), patch.object(cli, "GeoapifyProvider", side_effect=AssertionError("dry-run must not instantiate provider")):
        cli.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan["max_requests"] == 3
    assert plan["estimated_additional_requests"] == 23
    assert plan["plans"][0]["total_categories"] == 22
    with database.factory() as session:
        assert session.get(Destination, 1).category_cache_invalidated_at is None


@pytest.mark.parametrize("args", [["--max-requests", "0"], ["--refresh", "--resume"]])
def test_cli_invalid_budget_or_conflicting_modes_rejected(args, monkeypatch):
    monkeypatch.setattr("sys.argv", ["roamline", "enrich", *args])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


def test_cli_unknown_or_insufficient_credit_plan_blocks_before_http(database, city_factory, monkeypatch, capsys):
    with database.factory.begin() as session:
        session.add(city_factory())
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://test:test@localhost/test", geoapify_api_key="mock-key")
    monkeypatch.setattr("sys.argv", ["roamline", "enrich", "--geoname-id", "1", "--resume", "--max-requests", "22"])
    with patch.object(cli, "Settings", return_value=settings), patch.object(cli, "create_db_engine", return_value=database.engine), patch.object(database.engine, "dispose"), patch("httpx.AsyncClient", side_effect=AssertionError("HTTP forbidden")):
        with pytest.raises(SystemExit) as error:
            cli.main()
    assert error.value.code == 1
    plan = json.loads(capsys.readouterr().out)
    assert plan["minimum_credits"] == 23
    assert plan["estimated_credits"] == 23
    assert not plan["budget_sufficient"]
    with database.factory() as session:
        assert session.get(Destination, 1).enrichment_lease_token is None


@pytest.mark.parametrize("requests,sufficient", [(23, True), (22, False)])
def test_fresh_berlin_plan_is_exactly_23_without_http(database, city_factory, capsys, monkeypatch, requests, sufficient):
    with database.factory.begin() as session:
        session.add(city_factory(2950159, name="Berlin", ascii_name="Berlin"))
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://test:test@localhost/test")
    monkeypatch.setattr("sys.argv", ["roamline", "enrich", "--geoname-id", "2950159", "--dry-run", "--resume", "--max-requests", str(requests), "--max-credits", "23"])
    with patch.object(cli, "Settings", return_value=settings), patch.object(cli, "create_db_engine", return_value=database.engine), patch.object(database.engine, "dispose"), patch("httpx.Client", side_effect=AssertionError("No HTTP client")), patch("httpx.AsyncClient", side_effect=AssertionError("No HTTP client")):
        cli.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan["estimated_additional_requests"] == plan["estimated_credits"] == 23
    assert plan["budget_sufficient"] is sufficient
    assert plan["plans"][0]["identity_lookup_requests"] == 1
    assert plan["plans"][0]["places_requests"] == 22
    assert plan["plans"][0]["boundary_api_requests"] == 0
