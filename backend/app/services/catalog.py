"""GeoNames import and a deterministic, bounded, geographically mixed policy."""
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from hashlib import sha256
from io import TextIOWrapper
from pathlib import Path
import csv
import math
from zipfile import ZipFile

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from app.db.models import Destination

SOURCE = "GeoNames cities15000"
CITIES_URL = "https://download.geonames.org/export/dump/cities15000.zip"
COUNTRIES_URL = "https://download.geonames.org/export/dump/countryInfo.txt"
CITY_CODES = {"PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLC", "PPLG"}
CONTINENTS = ("AF", "AS", "EU", "NA", "OC", "SA", "AN")
CANDIDATE_POLICY_VERSION = "population-continent-country-round-robin-v1"


def read_countries(lines):
    result = {}
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.rstrip("\r\n").split("\t")
        if len(fields) < 9 or len(fields[0]) != 2 or fields[8] not in CONTINENTS or not fields[4]:
            raise ValueError(f"Invalid countryInfo row {number}")
        result[fields[0]] = {"country_name": fields[4], "continent_code": fields[8]}
    if not result:
        raise ValueError("countryInfo contains no countries")
    return result


def parse_cities(lines, countries):
    for number, fields in enumerate(csv.reader(lines, delimiter="\t", quoting=csv.QUOTE_NONE), 1):
        if not fields:
            continue
        if len(fields) != 19:
            raise ValueError(f"Expected 19 GeoNames fields at city row {number}")
        if fields[6] != "P" or fields[7] not in CITY_CODES:
            continue
        try:
            country = countries[fields[8]]
            record = {
                "geoname_id": int(fields[0]), "name": fields[1], "ascii_name": fields[2],
                "latitude": float(fields[4]), "longitude": float(fields[5]),
                "feature_code": fields[7], "country_code": fields[8], **country,
                "population": int(fields[14]), "timezone": fields[17] or None,
                "source_modified": date.fromisoformat(fields[18]),
            }
            if (record["geoname_id"] < 1 or not record["name"] or len(record["name"]) > 200
                    or len(record["ascii_name"]) > 200 or record["population"] < 0
                    or not math.isfinite(record["latitude"]) or not math.isfinite(record["longitude"])
                    or not -90 <= record["latitude"] <= 90 or not -180 <= record["longitude"] <= 180):
                raise ValueError()
        except (KeyError, ValueError, OverflowError):
            raise ValueError(f"Invalid city identity, coordinates, population or country at row {number}") from None
        yield record


def import_geonames(session: Session, cities_path: Path, countries_path: Path) -> int:
    """One caller-owned transaction; malformed imports roll back, IDs upsert idempotently."""
    cities_hash = sha256(cities_path.read_bytes()).hexdigest()
    countries_hash = sha256(countries_path.read_bytes()).hexdigest()
    countries = read_countries(countries_path.read_text(encoding="utf-8").splitlines())
    imported_at = datetime.now(timezone.utc)
    count = 0
    seen = set()

    def write_batch(batch):
        statement = insert(Destination).values(batch)
        source_fields = set(batch[0]) - {"geoname_id"}
        update = {name: getattr(statement.excluded, name) for name in source_fields}
        session.execute(statement.on_conflict_do_update(index_elements=[Destination.geoname_id], set_=update))

    with ZipFile(cities_path) as archive:
        if "cities15000.txt" not in archive.namelist():
            raise ValueError("Archive must contain the official cities15000.txt member")
        with archive.open("cities15000.txt") as raw:
            batch = []
            for record in parse_cities(TextIOWrapper(raw, encoding="utf-8"), countries):
                if record["geoname_id"] in seen:
                    raise ValueError("Duplicate GeoNames ID in input")
                seen.add(record["geoname_id"])
                batch.append({**record, "source": SOURCE, "source_sha256": cities_hash, "country_info_sha256": countries_hash, "imported_at": imported_at})
                count += 1
                if len(batch) == 500:
                    write_batch(batch)
                    batch = []
            if batch:
                write_batch(batch)
    if not count:
        raise ValueError("No eligible populated cities found")
    return count


def select_candidates(cities, min_population: int = 100_000, max_candidates: int = 40):
    if min_population < 1 or not 1 <= max_candidates <= 500:
        raise ValueError("Invalid candidate policy bounds")
    countries = defaultdict(list)
    for city in cities:
        if city.population >= min_population and city.feature_code in CITY_CODES and city.continent_code in CONTINENTS:
            countries[city.country_code].append(city)
    for values in countries.values():
        values.sort(key=lambda city: (-city.population, city.geoname_id))
    selected = []
    round_number = 0
    while len(selected) < max_candidates:
        by_continent = defaultdict(list)
        for values in countries.values():
            if round_number < len(values):
                city = values[round_number]
                by_continent[city.continent_code].append(city)
        if not by_continent:
            break
        queues = {continent: deque(sorted(values, key=lambda city: (-city.population, city.geoname_id))) for continent, values in by_continent.items()}
        while any(queues.values()) and len(selected) < max_candidates:
            for continent in CONTINENTS:
                if queues.get(continent) and len(selected) < max_candidates:
                    selected.append(queues[continent].popleft())
        round_number += 1
    return selected
