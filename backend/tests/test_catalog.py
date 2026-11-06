from io import StringIO
import os
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from app.db.models import Base, Destination
from app.services.catalog import import_geonames, parse_cities, read_countries, select_candidates

COUNTRIES = "# comment\nDE\tDEU\t276\tGM\tGermany\tBerlin\t0\t0\tEU\n"


def city_row(identifier=1, population=500000, code="PPL", country="DE"):
    fields = [str(identifier), "Test City", "Test City", "", "52.5", "13.4", "P", code, country, "", "", "", "", "", str(population), "", "", "Europe/Berlin", "2026-01-01"]
    return "\t".join(fields)


def test_parse_official_19_field_format_and_country_mapping():
    rows = list(parse_cities(StringIO(city_row()), read_countries(COUNTRIES.splitlines())))
    assert rows[0]["geoname_id"] == 1
    assert rows[0]["country_name"] == "Germany"
    assert rows[0]["continent_code"] == "EU"
    assert rows[0]["population"] == 500000
    assert len(list(parse_cities([city_row(code="PPLX")], read_countries(COUNTRIES.splitlines())))) == 0


@pytest.mark.parametrize("row", ["broken", city_row(country="XX"), city_row(population=-1), city_row().replace("52.5", "NaN"), city_row().replace("52.5", "91")])
def test_malformed_catalog_rows_fail_clearly(row):
    with pytest.raises(ValueError):
        list(parse_cities([row], read_countries(COUNTRIES.splitlines())))


def test_candidate_policy_rounds_by_continent_then_country(city_factory):
    cities = [
        city_factory(1, population=900000, country_code="DE", continent_code="EU"),
        city_factory(2, population=800000, country_code="DE", continent_code="EU"),
        city_factory(3, population=700000, country_code="FR", continent_code="EU"),
        city_factory(4, population=600000, country_code="JP", continent_code="AS"),
        city_factory(5, population=500000, country_code="ZA", continent_code="AF"),
        city_factory(6, population=99999, country_code="IT", continent_code="EU"),
        city_factory(7, population=1000000, feature_code="PPLX"),
    ]
    assert [city.geoname_id for city in select_candidates(cities, max_candidates=5)] == [5, 4, 1, 3, 2]
    assert [city.geoname_id for city in select_candidates(reversed(cities), max_candidates=3)] == [5, 4, 1]


def test_candidate_tie_break_and_bounds(city_factory):
    cities = [city_factory(2), city_factory(1)]
    assert [city.geoname_id for city in select_candidates(cities)] == [1, 2]
    with pytest.raises(ValueError):
        select_candidates(cities, max_candidates=0)


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL integration URL not set")
def test_postgres_import_is_idempotent_and_transactional(tmp_path):
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    schema = "catalog_test_" + uuid4().hex
    countries = tmp_path / "countryInfo.txt"
    countries.write_text(COUNTRIES, encoding="utf-8")
    archive = tmp_path / "cities15000.zip"
    with ZipFile(archive, "w") as output:
        output.writestr("cities15000.txt", city_row())
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        scoped = engine.execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(scoped)
        for _ in range(2):
            with Session(scoped) as session, session.begin():
                assert import_geonames(session, archive, countries) == 1
        with Session(scoped) as session:
            rows = session.scalars(select(Destination)).all()
            assert len(rows) == 1
            assert rows[0].country_name == "Germany"
            assert len(rows[0].source_sha256) == 64
        with ZipFile(archive, "w") as output:
            output.writestr("cities15000.txt", "\n".join(city_row(identifier=i) for i in range(2, 503)) + "\ninvalid")
        with pytest.raises(ValueError):
            with Session(scoped) as session, session.begin():
                import_geonames(session, archive, countries)
        with Session(scoped) as session:
            assert session.get(Destination, 2) is None
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_geonames_tsv_does_not_interpret_city_quotes_as_csv_syntax():
    line = city_row().replace("Test City", '\"Quoted city\"')
    record = list(parse_cities([line], read_countries(COUNTRIES.splitlines())))[0]
    assert record["name"] == '\"Quoted city\"'
