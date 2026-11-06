"""Explicit catalog/enrichment operations. Never runs on application startup."""
import argparse
import asyncio
import json
from pathlib import Path
import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from app.core.config import Settings
from app.db.connection import create_db_engine
from app.db.models import Destination
from app.services.catalog import CITIES_URL, COUNTRIES_URL, import_geonames, select_candidates
from app.services.enrichment import enrich_candidates, usable_snapshot, enrichment_plan
from app.services.geoapify import GeoapifyProvider, ProviderError
from app.domain.destination_features import FEATURES


def download_sources(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    # Fixed official HTTPS URLs; raw files stay outside source control.
    with httpx.Client(timeout=httpx.Timeout(60, connect=5), follow_redirects=True) as client:
        for url, name in [(CITIES_URL, "cities15000.zip"), (COUNTRIES_URL, "countryInfo.txt")]:
            target = directory / name
            temporary = directory / (name + ".partial")
            try:
                total = 0
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as output:
                        for block in response.iter_bytes():
                            total += len(block)
                            if total > 100 * 1024 * 1024:
                                raise ValueError("Source exceeds download size limit")
                            output.write(block)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Roamline destination data operations")
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download-geonames")
    download.add_argument("--directory", type=Path, default=Path(".tmp/geonames"))
    importer = commands.add_parser("import-geonames")
    importer.add_argument("--cities", type=Path, required=True)
    importer.add_argument("--countries", type=Path, required=True)
    commands.add_parser("candidates")
    enrich = commands.add_parser("enrich")
    enrich.add_argument("--limit", type=int, default=1, help="Maximum cities to refresh this run (1–10)")
    enrich.add_argument("--geoname-id", type=int, help="Target one city already in the bounded candidate set")
    enrich.add_argument("--refresh", action="store_true", help="Replace compatible cached data only after a complete successful refresh")
    enrich.add_argument("--resume", action="store_true", help="Resume valid category evidence (the default)")
    enrich.add_argument("--dry-run", action="store_true", help="Plan only; no provider requests or database writes")
    enrich.add_argument("--max-requests", type=int, default=25, help="Hard total HTTP request budget, including identity resolution")
    enrich.add_argument("--max-credits", type=int, default=23, help="Authorized credit ceiling; unknown boundary costs block execution")
    args = parser.parse_args()
    if args.command == "enrich" and (args.max_requests < 1 or args.max_credits < 1 or (args.resume and args.refresh)):
        parser.error("--max-requests must be positive; --resume and --refresh are mutually exclusive")
    if args.command == "download-geonames":
        try:
            download_sources(args.directory)
        except (httpx.HTTPError, OSError, ValueError):
            parser.exit(1, "GeoNames download failed; check connectivity and the output directory.\n")
        print("Downloaded official cities15000.zip and countryInfo.txt; import records their SHA-256 hashes.")
        return
    settings = Settings()
    engine = create_db_engine(settings)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        if args.command == "import-geonames":
            with factory.begin() as session:
                count = import_geonames(session, args.cities, args.countries)
            print(json.dumps({"imported_or_updated": count}))
        else:
            with factory() as session:
                cities = session.scalars(select(Destination).where(Destination.population >= settings.candidate_min_population)).all()
                candidates = select_candidates(cities, settings.candidate_min_population, settings.candidate_max_count)
                if args.command == "candidates":
                    print(json.dumps([{"geoname_id": city.geoname_id, "city": city.name, "country": city.country_code, "continent": city.continent_code, "population": city.population, "cached": usable_snapshot(city, city.enrichment, FEATURES)} for city in candidates], ensure_ascii=True, indent=2))
                    return
                if not 1 <= args.limit <= 10:
                    parser.error("--limit must be between 1 and 10")
                selected = [city for city in candidates if args.geoname_id is None or city.geoname_id == args.geoname_id]
                if args.geoname_id is not None and not selected:
                    parser.error("--geoname-id must belong to the current bounded candidate set")
                selected = selected[:args.limit]
                plans = [{"geoname_id": city.geoname_id, **enrichment_plan(
                    session, city, args.refresh, settings.feature_stale_after_days, settings.boundary_stale_after_days)} for city in selected]
                print(json.dumps({"plans": plans, "max_requests": args.max_requests,
                                  "estimated_additional_requests": sum(p["estimated_additional_requests"] for p in plans),
                                  "estimated_credits": (sum(p["estimated_credits"] for p in plans) if all(p["estimated_credits"] is not None for p in plans) else None),
                                  "minimum_credits": sum(p["minimum_credits"] for p in plans),
                                  "max_credits": args.max_credits,
                                  "budget_sufficient": all(p["estimated_credits"] is not None for p in plans)
                                      and sum(p["estimated_credits"] for p in plans) <= args.max_credits
                                      and sum(p["estimated_additional_requests"] for p in plans) <= args.max_requests}), flush=True)
                if args.dry_run:
                    return
                if (any(p["estimated_credits"] is None for p in plans)
                        or sum(p["estimated_credits"] for p in plans) > args.max_credits
                        or sum(p["estimated_additional_requests"] for p in plans) > args.max_requests):
                    raise ProviderError("insufficient_or_unbounded_budget")
                identifiers = [city.geoname_id for city in selected]
                if not settings.geoapify_api_key or not settings.geoapify_api_key.get_secret_value().strip():
                    raise ProviderError("missing_api_key")
            async def run():
                async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(retries=0)) as client:
                    provider = GeoapifyProvider(client, settings.geoapify_api_key.get_secret_value(), settings.geoapify_min_interval_seconds, timeout=settings.geoapify_timeout, max_requests=args.max_requests, max_credits=args.max_credits)
                    return await enrich_candidates(factory, identifiers, provider, args.refresh, stale_after_days=settings.feature_stale_after_days, boundary_stale_after_days=settings.boundary_stale_after_days, lease_seconds=settings.enrichment_lease_seconds)
            report = asyncio.run(run())
            print(json.dumps(report))
            if report["failures"]:
                raise SystemExit(1)
    except ProviderError as error:
        parser.exit(1, f"Enrichment failed: {error.code}. Successful category evidence and prior complete snapshots are preserved.\n")
    except SQLAlchemyError:
        parser.exit(1, "Database operation failed; check configuration and run migrations.\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"Catalog input error: {error}.\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
