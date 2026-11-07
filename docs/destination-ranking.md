# Destination data and deterministic ranking

This milestone ranks cities using prepared, structured place evidence. It does not use an LLM, retrieve live weather, create itineraries, or assess destination quality. One canonical `TravelerProfile` is reused without storing the traveler.

## Sources and attribution

- City identity: [GeoNames cities15000 export and field documentation](https://download.geonames.org/export/dump/readme.txt), with country names and continent codes from [countryInfo.txt](https://download.geonames.org/export/dump/countryInfo.txt). GeoNames is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); attribution: **GeoNames**. The upstream extract includes cities over 15,000 people or capitals; our importer further limits feature codes as described below.
- Place evidence: [Geoapify Places API and category taxonomy](https://apidocs.geoapify.com/docs/places/). Attribution: **Geoapify; © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright)**, OpenStreetMap data under ODbL. Geoapify documents persistent caching support. Retain these source/license notices when distributing derived evidence.
- City place resolution: [Geoapify geocoding](https://apidocs.geoapify.com/docs/geocoding/forward-geocoding/). [Geoapify documents using the geocoded place ID directly for POI scoping](https://apidocs.geoapify.com/how-to/place-discovery/places-in-city-district-postcode/).

Raw dumps stay outside Git. The catalog stores only identity, coordinates, population, timezone, feature code, country/continent mapping, source modification/import timestamps, and SHA-256 hashes of the city ZIP and country mapping. `ascii_name` supports identity comparison. Reimport upserts by GeoNames ID, in one transaction; malformed input rolls back. This MVP does not delete records absent from later extracts; source-deletion reconciliation remains future work.

## Exact candidate policy

Version: `population-continent-country-round-robin-v1`.

1. Import populated features (`P`) with code `PPL`, `PPLA`, `PPLA2`, `PPLA3`, `PPLA4`, `PPLC`, or `PPLG`. Exclude suburbs/sections, historical/abandoned places, and other feature types. Country and continent must resolve through countryInfo.
2. Eligible candidates have population **at least 100,000** by default (`CANDIDATE_MIN_POPULATION`).
3. Within each country, sort cities by population descending, then GeoNames ID ascending.
4. First consider each country's largest eligible city. Group these by continent and sort each group by population descending, then GeoNames ID ascending. Interleave the groups in fixed order **AF, AS, EU, NA, OC, SA, AN**, taking one city per nonempty group each turn.
5. Only after this country round is exhausted consider each country's second city, and repeat for subsequent rounds.
6. Stop at **40** candidates by default (`CANDIDATE_MAX_COUNT`, allowed 1–500). This same policy bounds explicit enrichment and request-time ranking. A CLI `--geoname-id` must belong to that selected set.

At very small limits the fixed continent order matters. Population thresholds and the limited set can omit small tourism destinations and entire countries. This is a transparent MVP budget/diversity rule, not a tourism-quality model.

## Explicit enrichment lifecycle

```text
GeoNames files -> PostgreSQL city catalog -> bounded candidates
    -> strict city geocoding (fresh cached resolution or one geocoding request)
    -> missing category requests -> independently committed category work cache
    -> all 22 valid categories -> atomic complete snapshot -> read-only ranking
```

No automatic import/enrichment happens on startup, and recommendations never instantiate a provider or call Geoapify. HTTPX is reused as a runtime dependency. Geoapify requests use centrally configured timeouts: connect/read 15 seconds and write/pool 5 seconds by default. Override them with `GEOAPIFY_CONNECT_TIMEOUT_SECONDS`, `GEOAPIFY_READ_TIMEOUT_SECONDS`, `GEOAPIFY_WRITE_TIMEOUT_SECONDS`, and `GEOAPIFY_POOL_TIMEOUT_SECONDS` (positive values up to 120 seconds). Enrichment is sequential, paced at one request/second by default, with **no automatic retries**. A 429 preserves Retry-After information and stops the batch; authentication and availability failures also stop it. Other city-specific errors are reported without erasing existing evidence. Responses, exception URLs, and API keys are not logged by this integration.

Resolution policy: `geocoded-city-v1`. One forward geocoding request uses canonical city/country, `type=city`, a country filter, `limit=5`, `lang=en`, and proximity bias. Acceptance independently checks `result_type == city`, exact country code, normalized canonical/ASCII city name, finite valid coordinates within **25 km** of the GeoNames point, and a nonempty 2–2048-character place ID without whitespace. Multiple distinct acceptable place IDs are rejected. The accepted geocoding ID is used directly in every Places request as `filter=place:<place_id>`.

Roamline uses **Geoapify's geocoded place boundary for POI scoping**. It does **not independently verify complete polygon coverage**, containing administrative hierarchy, or polygon extents. Normal enrichment makes **no point or polygon Boundaries API calls**. This is an explicitly simplified resolution policy, not a claim of equivalent independent geometry assurance. Berlin is accepted only as a matching city geocoding result; a geocoder result of type state/county is rejected even if named Berlin. The previous `country_part_level` hierarchy workaround is removed. No circular-radius or bounding-box fallback is used. Missing/incomplete provider polygons and translated-name mismatches remain limitations.

Collection strategy: `single-category-v2`. The existing mapping contains **22 unique category keys**. Deduplicate them in first-mapping-occurrence order, then query each missing/invalid category **at most once per run**, sequentially. Fresh compatible successful categories are skipped on resume. Every Places request contains one key, `limit=20`, `lang=en`, and `filter=place:<accepted geocoded place ID>`. There is no offset, pagination, concurrent collection or automatic retry. The mapping version and mathematical normalization are unchanged.

Original per-category GeoJSON POI features are persisted in `category_evidence`, together with returned/unique counts, query category, limit, language, capped flag and UTC fetch timestamp. Each provider place ID is counted once within an interest's union of mapped categories. An identical ID returned under building and bridge is one architecture POI; a heritage POI can still support both history and photography. Original provider categories and duplicate response observations remain available for audit. A successful empty response is recorded as zero availability, rather than a missing/error result.

Each validated successful response (including zero results) is committed immediately in a short, separate database transaction. Original provider features, deduplicated identifiers, returned/unique counts, caps and fetch timestamps use the existing `CategoryEvidence` record format. Only after **all 22 categories have valid completed evidence** are the ten interest unions normalized and published atomically. A feature is marked capped if any of its source category responses reached 20. Counts are lower-bound samples, not exhaustive city totals. Independent capped category samples can produce a different union from the former broad query; this is explicitly identified by the collection-strategy version. Legacy snapshots are retained by migration but are not silently reused as the new strategy.

A fresh city needs **one geocoding request + 22 single-category Places requests = 23 total HTTP requests / 23 base credits**. Geocoding limit 5 costs one request/credit; each Places request at limit 20 costs one credit. There is no pagination, retry, geometry surcharge or Boundary API request in this path. The CLI defaults to both a 23-request and 23-credit total run ceiling, shared across selected cities. Every attempted request, including failed attempts, reserves its budget before dispatch. A fresh compatible place resolution saves one request; valid categories are skipped on resume. The planner initializes no HTTP client and reports valid/missing/invalid categories, resolution freshness, one-or-zero geocoding calls, Places calls, zero Boundary API calls, exact base credits and budget sufficiency.

All category responses and aggregates exist in memory before any snapshot replacement. Transactions remain per city; a failed refresh leaves the previous snapshot and its raw evidence unchanged. The snapshot retains the accepted place reference, resolution evidence, category evidence, request strategy, mapping/normalization versions, source fingerprint, raw feature counts, cap flags, normalized values and timestamps. Cached records are verified against the original category evidence and deduplicated unions before ranking.

Category work-cache rows are reusable only within **30 days** (`FEATURE_STALE_AFTER_DAYS`, configurable). Stale, future-dated, malformed, mapping-incompatible or query-incompatible rows are requested again. Already published complete snapshots remain readable and are labelled stale by the existing recommendation policy; recommendation requests never refresh them. A new snapshot uses the oldest contributing category timestamp so a resumed publication does not falsely rejuvenate older observations. Changes to relevant canonical identity/population/coordinates invalidate the fingerprint; mapping/normalization changes or missing requested features also exclude a snapshot. Missing evidence is never silently treated as zero. A verified successful empty Places response is genuine zero mapped-place evidence.

## Interest mapping

Version: `geoapify-interests-v1`. These keys were checked against the official taxonomy. All are limited proxies for interest-related place availability.

| Interest | Category union | Interpretation |
| --- | --- | --- |
| architecture | `tourism.sights.building`, `tourism.sights.bridge`, `tourism.sights.castle`, `tourism.sights.tower` | Notable built heritage, not design quality |
| history | `heritage`, `tourism.sights.archaeological_site`, `tourism.sights.fort`, `tourism.sights.memorial` | Mapped heritage and historical sites |
| photography | `tourism.attraction.viewpoint`, `heritage`, `natural` | Scenic-subject proxy; no claim about light, access, or photography conditions |
| food | `catering.restaurant`, `catering.cafe`, `commercial.marketplace` | Food-place availability, not food quality |
| nature | `natural`, `leisure.park`, `national_park` | Natural places/parks inside the city; not regional access |
| art | `entertainment.culture.gallery`, `tourism.attraction.artwork` | Galleries and public artwork |
| museums | `entertainment.museum` | Museum availability |
| beaches | `beach` | Mapped beaches, not swimming suitability |
| shopping | `commercial.shopping_mall`, `commercial.marketplace` | Limited mall/market proxy, not all retail |
| nightlife | `catering.bar`, `catering.pub` | Limited bar/pub proxy; no events or opening-time verification |

No mapping is implemented for **hiking, sports, football, music, local culture**, or other custom interests. This does not claim that the provider has no related categories; it means this milestone has not adopted a sufficiently specific mapping for those traveler intentions. Unsupported interests are explicitly returned and receive no weight.

## Feature normalization

Version: `log-breadth-density-v1`.

Let `c` be the distinct returned-place count, from 0 to 100, and `p` the canonical population:

```text
breadth = min(log(1 + c) / log(1 + 50), 1)
density = c * 100000 / max(p, 100000)
density_signal = min(log(1 + density) / log(1 + 20), 1)
feature_value = round((breadth + density_signal) / 2, 6)
```

Zero yields zero. Log transformation/caps limit count dominance; a population-adjusted half prevents raw large-city counts from being the sole signal. A population floor avoids amplifying tiny population values. The fixed reference values (50 places, 20 per 100,000) are explicit MVP heuristics, not empirically calibrated travel-quality thresholds. A city's normalized value does not change just because another candidate is added.

The raw count, cap flag and normalized value are persisted. The service verifies the value against its raw evidence/version before ranking. Re-normalization is not silently written during a traveler request.

## Scoring and explanations

Version: `weighted-evidence-v1`.

```text
importance weight: low=1, moderate=2, high=3
S = selected interests with an implemented mapping
w_i = importance_i / sum(importance_j for j in S)
contribution_i = 100 * w_i * feature_value_i
match_score = round(sum(contribution_i), 1)
```

All candidates must have complete compatible evidence for **every requested supported interest**; incomplete candidates are excluded so denominators stay comparable. No supported interests yields an explicit empty response, never invented recommendations. Unsupported interests do not enter the denominator. Zero values are valid and can produce a zero-score tie.

Results sort by displayed score descending, then **GeoNames ID ascending**. Score components are returned in feature-name order. Reasons are deterministic templates drawn from the three highest positive contributions (feature name breaks contribution ties). No narrative generation is used. Contributions are rounded to one decimal for presentation and may not sum exactly to the displayed total; the unrounded values are summed first. Scores are neither probabilities nor experience-quality ratings. Increasing an interest's relative weight increases its influence; it need not increase a city's score when that city is weaker on that feature.

Dates, duration, origin, travel style, weather, crowds, activity/walking levels, trip pace, comfort/accessibility and costs do **not** contribute. Selected unsupported dimensions are disclosed. Accessibility suitability must be checked independently. Country groups only collect the returned ranked city IDs by country, preserving first appearance; there is no country score.

## API

`POST /api/v1/recommendations/destinations`

```json
{
  "profile": {
    "travel_context": { "duration_days": 7 },
    "interests": [{ "name": "museums", "importance": "high" }]
  },
  "limit": 5
}
```

`profile` is the existing canonical contract. `limit` is a strict integer from 1 to 20 (default 5). HTTP 200 returns:

- `metadata`: status/message, policy and bounds, selected/evaluated/excluded counts, supported interests, generation time, scoring version, attribution and scale/tie rules.
- `results`: canonical city/country identity, score, weighted components with raw evidence/proxy descriptions, deterministic reasons, and cache/source freshness/provenance.
- `unsupported_preferences`: named omitted interests/dimensions with reasons.
- `countries`: country labels and returned city IDs; no aggregated score.

Status is `ready`, `catalog_empty`, `no_enriched_candidates`, or `no_supported_interests`. Empty catalog/cache conditions are clear data-readiness states with no fabricated cards. Invalid input returns HTTP 422. Storage/schema unavailability returns a sanitized HTTP 503. Explicit provider failures occur in the CLI, not on recommendation requests.

## Operations

From the repository root with the backend environment activated and `DATABASE_URL` configured (native host normally `127.0.0.1`; Docker host `db`):

```sh
# Apply explicit migrations; no startup schema creation.
cd backend
alembic upgrade head
cd ..

# Store raw data only in this ignored folder.
python -m app.cli download-geonames --directory .tmp/geonames
python -m app.cli import-geonames --cities .tmp/geonames/cities15000.zip --countries .tmp/geonames/countryInfo.txt
python -m app.cli candidates

# GEOAPIFY_API_KEY must be configured on the backend only.
# Plan one selected candidate without provider calls or writes (no API key needed):
python -m app.cli enrich --limit 1 --dry-run --max-requests 23 --max-credits 23
# Execute only after reviewing/authorizing the request plan; resume is the default:
python -m app.cli enrich --limit 1 --resume --max-requests 23 --max-credits 23
# Controlled replacement of one chosen candidate's compatible cache:
python -m app.cli enrich --geoname-id <selected-geonames-id> --limit 1 --refresh --max-requests 23 --max-credits 23
```

`--limit` allows 1–10 cities per run. The limit applies to selected candidates in deterministic order; use `--geoname-id` to target a specific one. Fresh complete snapshots cost zero requests. Without `--refresh`, valid category work is resumed automatically (`--resume` makes this explicit). Changing candidate policy settings may select cities that have not been enriched; inspect `candidates` before spending provider quota. Destination-scoped enrichment leases and generation checks protect competing workers; catalog changes invalidate an active worker through fingerprint checks. Historical snapshot retention is not implemented.

For Compose, the equivalent backend commands are `docker compose exec backend alembic upgrade head` and `docker compose exec backend python -m app.cli ...`. Copy downloaded input files into the container (for example with `docker compose cp`) before importing there, or run the documented native CLI against the loopback PostgreSQL port. Keep the key in an ignored environment file or your shell; it is never a `NEXT_PUBLIC_*` value. Restart the backend container after environment changes. Do not print keyed request URLs.

## Validation and limitations

Automated tests mock Geoapify; they require no API key and make no provider calls. Tests cover parsing, idempotent PostgreSQL import/rollback, candidate ordering, strict city-resolution checks, exactly 22 single-category queries, sequential execution, limit/language/pagination constraints, shared-category reuse, within-interest ID unions, cross-interest attribution, zero evidence, request budgets, preserved raw evidence, unchanged normalization/scoring, deterministic ties, stale/legacy/corrupt caches, atomic failure safety and API responses. Optional PostgreSQL tests use `TEST_DATABASE_URL`; CI supplies a disposable PostgreSQL service.

Single-category requests succeeded where a broader architecture query timed out during controlled tests. This is evidence of request-dependent latency, not proof of a universal cause or a guarantee that every single-category call will succeed. OpenStreetMap/provider coverage differs across regions. Counts do not measure quality, opening status, access or proximity between experiences. Administrative city areas vary greatly. Capped counts are lower bounds and population data can be old or refer to different urban extents. Proxy overlap and fixed calibration choices can affect rank. Population/diversity selection omits many smaller destinations. Stale evidence and rejected/unenriched cities reduce the compared set. A failed refresh preserves prior data but does not make it current.

Live Geoapify enrichment must be verified separately with a configured key. An import, a passing mocked test, or a working empty state is not proof of live provider compatibility. Do not seed mock evidence into the application catalog or present test fixtures as real recommendations.

## Place-resolution freshness, schema and concurrency

Migration `0005_geocoded_place_resolution` renames operational boundary fields to accurate place-resolution terminology: `place_resolved_at`, `place_resolution_provider`, `resolved_place_id`, `place_resolution_fingerprint`, `place_resolution_catalog_fingerprint`, and `place_resolution_evidence`. Category rows carry `place_resolution_fingerprint`. Historical migrations remain unchanged. Existing records and published snapshots are retained, but the policy-versioned fingerprint and evidence policy prevent old independent-boundary records from being reused as fresh `geocoded-city-v1` resolutions. Downgrade reverses the column renames.

`PLACE_RESOLUTION_STALE_AFTER_DAYS` (default 30) replaces `BOUNDARY_STALE_AFTER_DAYS`. Resolution time changes only after successful strict city geocoding. POI responses have independent fetch times and never renew resolution freshness. An expired or incompatible resolution forces geocoding before collection continues. If the accepted place identity is unchanged, fresh compatible categories can resume. A changed place ID/fingerprint advances the enrichment generation; old category evidence is retained but excluded. Failed resolution preserves the previous complete snapshot.

A short PostgreSQL `SELECT ... FOR UPDATE` transaction acquires a durable destination lease. A live lease rejects a competing worker; an expired lease can be reclaimed with a new token. PostgreSQL server time governs lease checks. Every category write checks token, generation, expiry and destination fingerprint under the row lock. Invalidated/expired workers cannot overwrite newer evidence. Explicit refresh/invalidation advances the generation. No database connection or transaction is held during external HTTP waits.

Publication owns a short transaction that reloads and locks the destination and revalidates the active lease/generation, fresh accepted place resolution, fingerprint, and all 22 category records. Only then are the complete snapshot and ten feature aggregates replaced atomically. Failure preserves the previous snapshot and independent category progress. Normalization, interest mappings, scoring, tie-breaking and unsupported-preference handling remain unchanged. GeoNames imports update canonical source-owned fields only, preserving operational metadata.

## Live verification status

**Complete live Geoapify enrichment and live recommendation consumption remain pending.** The simplified policy was implemented and tested offline. A subsequent authorized Berlin run on 7 November 2026 used three requests (three conservatively estimated base credits): geocoding returned HTTP 200, `tourism.sights.building` returned HTTP 200 with 20 unique POIs, and `tourism.sights.bridge` stopped with a 15-second ReadTimeout before an HTTP response. The application preserved one valid category record, released its lease, and published no snapshot or feature aggregates. Twenty-one categories remain. There was no retry or second run, and no recommendation was attempted without a complete snapshot. Earlier experiments used the previous independent-boundary policy: five single-category Berlin requests succeeded, then an archaeological-site request timed out. Those observations predated durable caching and produced no complete snapshot. They do not establish live success for this new policy.

The new mocked full-path test proves the 23-request/23-credit dispatch ceiling and absence of Boundary API calls; it is not a live integration result. Another live run requires separate authorization. No synthetic provider evidence is inserted into the application catalog.
