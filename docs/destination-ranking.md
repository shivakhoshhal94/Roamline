# Destination data and deterministic ranking

This milestone ranks cities using prepared, structured place evidence. It does not use an LLM, retrieve live weather, create itineraries, or assess destination quality. One canonical `TravelerProfile` is reused without storing the traveler.

## Sources and attribution

- City identity: [GeoNames cities15000 export and field documentation](https://download.geonames.org/export/dump/readme.txt), with country names and continent codes from [countryInfo.txt](https://download.geonames.org/export/dump/countryInfo.txt). GeoNames is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); attribution: **GeoNames**. The upstream extract includes cities over 15,000 people or capitals; our importer further limits feature codes as described below.
- Place evidence: [Geoapify Places API and category taxonomy](https://apidocs.geoapify.com/docs/places/). Attribution: **Geoapify; © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright)**, OpenStreetMap data under ODbL. Geoapify documents persistent caching support. Retain these source/license notices when distributing derived evidence.
- Identity and extent: [Geoapify geocoding](https://apidocs.geoapify.com/docs/geocoding/forward-geocoding/) and [containing administrative boundaries](https://apidocs.geoapify.com/docs/boundaries/).

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
    -> verified boundary (fresh cached verification or provider resolution)
    -> missing category requests -> independently committed category work cache
    -> all 22 valid categories -> atomic complete snapshot -> read-only ranking
```

No automatic import/enrichment happens on startup, and recommendations never instantiate a provider or call Geoapify. HTTPX is reused as a runtime dependency. Geoapify requests use centrally configured timeouts: connect/read 15 seconds and write/pool 5 seconds by default. Override them with `GEOAPIFY_CONNECT_TIMEOUT_SECONDS`, `GEOAPIFY_READ_TIMEOUT_SECONDS`, `GEOAPIFY_WRITE_TIMEOUT_SECONDS`, and `GEOAPIFY_POOL_TIMEOUT_SECONDS` (positive values up to 120 seconds). Enrichment is sequential, paced at one request/second by default, with **no automatic retries**. A 429 preserves Retry-After information and stops the batch; authentication and availability failures also stop it. Other city-specific errors are reported without erasing existing evidence. Responses, exception URLs, and API keys are not logged by this integration.

A geocoding match must have city result type, exact country code, a matching canonical/ASCII city name after case/accent/punctuation normalization, and a coordinate within **25 km** of the GeoNames point. Multiple distinct matches are rejected. Then the containing-boundaries endpoint must identify one matching administrative city boundary. That boundary's `place_id` is saved and used as `filter=place:...` for every feature query. City-state boundaries classified as country parts are accepted only when city/state names and country match, and the containing polygon extent matches the geocoded city extent within the documented 1 km geometry accuracy. That extra extent evidence is persisted; a same-named wider region is rejected. No arbitrary-radius fallback is used. Conservative matching can reject translated names and valid cities whose centers differ substantially; this is preferable to guessing.

Collection strategy: `single-category-v2`. The existing mapping contains **22 unique category keys**. Deduplicate them in first-mapping-occurrence order, then query each missing/invalid category **at most once per run**, sequentially. Fresh compatible successful categories are skipped on resume. Every Places request contains one key, `limit=20`, `lang=en`, and `filter=place:<verified boundary ID>`. There is no offset, pagination, concurrent collection or automatic retry. The mapping version and mathematical normalization are unchanged.

Original per-category GeoJSON POI features are persisted in `category_evidence`, together with returned/unique counts, query category, limit, language, capped flag and UTC fetch timestamp. Each provider place ID is counted once within an interest's union of mapped categories. An identical ID returned under building and bridge is one architecture POI; a heritage POI can still support both history and photography. Original provider categories and duplicate response observations remain available for audit. A successful empty response is recorded as zero availability, rather than a missing/error result.

Each validated successful response (including zero results) is committed immediately in a short, separate database transaction. Original provider features, deduplicated identifiers, returned/unique counts, caps and fetch timestamps use the existing `CategoryEvidence` record format. Only after **all 22 categories have valid completed evidence** are the ten interest unions normalized and published atomically. A feature is marked capped if any of its source category responses reached 20. Counts are lower-bound samples, not exhaustive city totals. Independent capped category samples can produce a different union from the former broad query; this is explicitly identified by the collection-strategy version. Legacy snapshots are retained by migration but are not silently reused as the new strategy.

The enrichment service accepts an already verified `CityResolution`, so the controlled Berlin experiment can recover the previously verified boundary in **one point lookup** and use the remaining **22 requests** for categories. The provider enforces a request budget before every HTTP call. With `max_requests=23`, a 24th call is blocked locally. Places requests at limit 20 cost one credit each. The earlier 23-credit experiment assumed already-verified identity/extent and a point-boundary recovery returning at most 20 boundaries; it is not a fresh-verification budget. Point boundaries cost one credit per 20 returned results; polygon boundaries add one credit per returned geometry. Normal CLI identity resolution can require two requests (three for city-state extent verification), so a fresh city can need up to **25 HTTP requests**, with credits calculated separately. The CLI defaults to a **25-request total run budget**, shared across selected cities; `--max-requests` changes that hard ceiling. Inspect the plan before using that general workflow under a tighter budget. The experiment runs Berlin only and stops on any failed category; quota, 5xx, timeout and budget errors cannot cause automatic retries or incomplete snapshot publication. Successful category observations now survive interrupted runs; no partial snapshot is published.

All category responses and aggregates exist in memory before any snapshot replacement. Transactions remain per city; a failed refresh leaves the previous snapshot and its raw evidence unchanged. The snapshot retains the boundary reference, resolution evidence, category evidence, request strategy, mapping/normalization versions, source fingerprint, raw feature counts, cap flags, normalized values and timestamps. Cached records are verified against the original category evidence and deduplicated unions before ranking.

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
python -m app.cli enrich --limit 1 --dry-run --max-requests 25
# Execute only after reviewing/authorizing the request plan; resume is the default:
python -m app.cli enrich --limit 1 --resume --max-requests 25
# Controlled replacement of one chosen candidate's compatible cache:
python -m app.cli enrich --geoname-id <selected-geonames-id> --limit 1 --refresh --max-requests 25
```

`--limit` allows 1–10 cities per run. The limit applies to selected candidates in deterministic order; use `--geoname-id` to target a specific one. Fresh complete snapshots cost zero requests. Without `--refresh`, valid category work is resumed automatically (`--resume` makes this explicit). Changing candidate policy settings may select cities that have not been enriched; inspect `candidates` before spending provider quota. Destination-scoped enrichment leases and generation checks protect competing workers; catalog changes invalidate an active worker through fingerprint checks. Historical snapshot retention is not implemented.

For Compose, the equivalent backend commands are `docker compose exec backend alembic upgrade head` and `docker compose exec backend python -m app.cli ...`. Copy downloaded input files into the container (for example with `docker compose cp`) before importing there, or run the documented native CLI against the loopback PostgreSQL port. Keep the key in an ignored environment file or your shell; it is never a `NEXT_PUBLIC_*` value. Restart the backend container after environment changes. Do not print keyed request URLs.

## Validation and limitations

Automated tests mock Geoapify; they require no API key and make no provider calls. Tests cover parsing, idempotent PostgreSQL import/rollback, candidate ordering, identity/boundary checks, exactly 22 single-category queries, sequential execution, limit/language/pagination constraints, shared-category reuse, within-interest ID unions, cross-interest attribution, zero evidence, request budgets, preserved raw evidence, unchanged normalization/scoring, deterministic ties, stale/legacy/corrupt caches, atomic failure safety and API responses. Optional PostgreSQL tests use `TEST_DATABASE_URL`; CI supplies a disposable PostgreSQL service.

Single-category requests succeeded where a broader architecture query timed out during controlled tests. This is evidence of request-dependent latency, not proof of a universal cause or a guarantee that every single-category call will succeed. OpenStreetMap/provider coverage differs across regions. Counts do not measure quality, opening status, access or proximity between experiences. Administrative city areas vary greatly. Capped counts are lower bounds and population data can be old or refer to different urban extents. Proxy overlap and fixed calibration choices can affect rank. Population/diversity selection omits many smaller destinations. Stale evidence and rejected/unenriched cities reduce the compared set. A failed refresh preserves prior data but does not make it current.

Live Geoapify enrichment must be verified separately with a configured key. An import, a passing mocked test, or a working empty state is not proof of live provider compatibility. Do not seed mock evidence into the application catalog or present test fixtures as real recommendations.

## Controlled Berlin verification (6 November 2026)

The single-category implementation and offline tests passed. One boundary point lookup recovered the previously verified Berlin reference. The live experiment then attempted these category requests, sequentially at limit 20 and language English:

| Category | HTTP result | Returned POIs |
| --- | --- | --- |
| `tourism.sights.building` | 200 | 20 |
| `tourism.sights.bridge` | 200 | 20 |
| `tourism.sights.castle` | 200 | 19 |
| `tourism.sights.tower` | 200 | 20 |
| `heritage` | 200 | 20 |
| `tourism.sights.archaeological_site` | ReadTimeout before response | Not available |

The run stopped immediately: **6 Places attempts + 1 boundary lookup = 7 requests / 7 conservatively estimated credits**. There were no retries or other-city requests. No snapshot or feature rows were persisted; successful partial observations were not published as complete evidence. The complete city-wide unique sampled POI count and real recommendation consumption therefore remain unverified. This shows that one-category queries help the previously failing architecture case but do not guarantee low provider latency. It does not establish whether the archaeological-site timeout was query-specific or intermittent.

The 23-credit experiment budget applies to one point-boundary recovery plus 22 category calls. General identity verification may request polygons, which Geoapify can charge additionally; the generic CLI HTTP request cap must not be mistaken for a universal credit-cost estimate.


## Resumable category storage and publication

Migration `0003_resumable_categories` adds one work-cache table, `destination_category_evidence`, keyed by destination and category. The existing snapshot JSON in migration 0002 remains the immutable publication copy, not a second independently scored cache. Separate rows are necessary for short category transactions that survive a later timeout or publication rollback. Rows record provider, verified boundary place ID and resolution proof, catalog fingerprint, mapping/strategy versions, safe query provenance (endpoint, category, place filter, English language, limit 20, no pagination), original evidence, counts/caps and fetch time. No authenticated URLs or API keys are stored.

Resume validates all metadata and the canonical evidence record before reuse. All contributing rows must share one verified boundary. Fresh cached resolution proof avoids repeated identity requests; otherwise identity is resolved normally. Query, mapping, destination fingerprint, boundary or freshness changes invalidate incompatible category rows. A successful HTTP 200 with zero places is complete evidence, never a failed/missing category.

`--refresh` explicitly invalidates the work cache using a durable per-destination timestamp. Older observations are retained until a successful category response replaces them, but cannot contribute to the refresh. The marker survives failure even before the first new category, so a subsequent `--resume` continues the refresh rather than accepting the older published snapshot as finished. `--refresh` and `--resume` cannot be combined. `--dry-run --refresh` plans invalidation but performs no writes.

Every successful category commits before the next request starts. Errors return `incomplete_enrichment` with a safe provider code; the batch report lists incomplete destinations. No failed category is marked complete, no automatic retry occurs, and no incomplete recommendation snapshot is published. Once all categories pass revalidation, publication replaces the snapshot and its ten feature rows, clearing the invalidation marker in the same caller transaction. Failure/rollback preserves the previous snapshot and all independently committed category work. Ranking only reads published snapshots; mappings, normalization, scoring, weights, unsupported-interest handling and tie-breaking are unchanged.

Before execution the CLI prints total/cached/missing categories, estimated additional requests and base estimated credits, plus the hard request ceiling. A new identity is conservatively budgeted at three HTTP calls; cached verification needs none. Each actual HTTP attempt (including failed attempts and identity requests) consumes the shared request budget before dispatch. Exhaustion stops locally and returns incomplete status, preserving progress. Unused budget never triggers extra requests. Credit estimates are now operation-specific. Unknown boundary result costs are reported as unknown, and a finite CLI credit ceiling blocks unbounded plans before HTTP client initialization. No billing settings are changed. Same-destination enrichment is now guarded by a durable lease and generation checks (see corrective review below). Historical snapshots remain outside this milestone.

No Geoapify calls were made during resumable-cache implementation. The previous Berlin run still has no complete live snapshot or verified live recommendation consumption. Its successful partial responses predate durable caching and were not recovered or inserted as synthetic evidence. Another tightly budgeted live resume test requires separate authorization.


## Corrective review: fencing, independent boundary freshness and credit limits

Migration `0004_enrichment_fencing` adds a durable destination generation, lease token/expiry, independent boundary verification timestamp/provider/place reference, catalog and boundary fingerprints, and original verification proof. Category rows record their generation and boundary fingerprint. Existing unverified cache rows are retained but cannot be reused until verified compatibility exists. Published snapshots remain untouched by this migration.

A short `SELECT ... FOR UPDATE` transaction acquires the destination lease. A live lease rejects a second worker with explicit `conflict`; an expired lease can be reclaimed with a new random token. PostgreSQL server time governs lease checks. Worker failure leaves independently committed categories intact; after expiry, another worker resumes the same generation. Every write renews/checks the token, generation, lease expiry and destination fingerprint under the destination row lock. An expired or invalidated worker is fenced before its category upsert, so it cannot overwrite newer evidence. Explicit invalidation advances the generation and cancels the lease; `--refresh` advances the generation after acquiring exclusive ownership. Concurrent refresh attempts cannot take over an unexpired lease. No database transaction or connection is held during external HTTP awaits.

Publication owns its own short transaction. It reloads and locks the destination, checks lease/generation/fingerprint, verifies the current independently fresh boundary, then revalidates all 22 category rows against the active generation, boundary identity, mapping/query configuration, counts and timestamps. Only then are the snapshot and ten features replaced and the invalidation marker cleared. A conflict or any incomplete evidence aborts publication. The old snapshot and committed work-cache rows remain available. Recommendation access/scoring and the frontend response contract are unchanged.

`boundary_verified_at` changes only after successful identity/boundary resolution, never after a Places response. `BOUNDARY_STALE_AFTER_DAYS` defaults to 30 independently of `FEATURE_STALE_AFTER_DAYS`. An expired boundary forces verification before categories continue. If the same boundary/fingerprint is verified, still-fresh compatible categories can be reused. A changed boundary advances the generation and excludes previous category evidence. Failed verification preserves the previous published snapshot. GeoNames upserts explicitly update source-owned fields and preserve all operational fields, including leases, generations, invalidation and boundary metadata.

The planner reports identity lookups, point/optional polygon boundary verification, Places calls, valid/missing/invalid categories, boundary state, request totals, and operation-specific credit accounting. It never creates an HTTP client. `--max-credits` defaults to **23** independently of the HTTP ceiling. A missing/expired boundary has unknown result-dependent credits: `estimated_credits` is null, not a made-up fixed total. Insufficient/unknown plans are refused before execution; the provider also reserves known credits before dispatch and counts failed attempts. Under a finite ceiling, unbounded boundary calls are blocked even if the database changes between planning and execution. No retry policy was changed.

Official pricing ([pricing details](https://www.geoapify.com/pricing-details/), [Boundaries API](https://apidocs.geoapify.com/docs/boundaries/)):

- Geocoding: 1 credit per request.
- Places with limit <=20: 1 credit per request (22 for the unchanged mapping).
- Point boundaries: `ceil(P / 20)` credits for P returned boundaries.
- Polygon boundaries: `ceil(G / 20) + G` credits for G returned geometries.

A fresh ordinary-city workflow therefore needs at least **24 credits** (22 Places + geocoding + point boundaries). The existing Berlin city-state extent path requires three identity/boundary HTTP calls plus 22 Places calls: **25 requests**, with total credits `23 + ceil(P/20) + ceil(G/20) + G`. With P=4 and G=4 as in the prior Berlin observations, this is **29 credits**, conditional on those result counts remaining the same. The theoretical minimum for this polygon path with at least one result per boundary call is **26 credits**. Exact future cost cannot be established offline or bounded by an undocumented result-limit parameter. A single point-boundary lookup would omit the existing geocoding/extent proof; it is not adopted as a shortcut. No strict identity/country/extent checks, categories or scoring were weakened.

The authorized 23 credits are insufficient for fresh Berlin verification. No service calls were made for this correction and no live command should run until a sufficient, accurately bounded workflow/budget is separately reviewed and authorized. Public documentation was consulted only; no authenticated API endpoints were contacted. Earlier Berlin successes do not constitute persisted, fresh verification for the current database.

A possible two-call identity strategy (geocoding followed directly by polygon boundaries, without the preliminary point lookup) could retain the same extent checks, but still needs at least 25 total credits for 22 categories and at least one polygon. With four polygons it would need 28 credits. It has not been implemented or live-tested; it cannot solve the 23-credit constraint and is not a reason to bypass existing validation.
