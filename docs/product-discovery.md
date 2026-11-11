# Product discovery contracts

## Shared infrastructure

The existing canonical traveler profile is unchanged. Interests (including custom
hobbies) and travel style become an explicit deterministic text query. Importance
orders interest terms; it does not imply calibrated weights. Dates/duration remain
trip context, not evidence of seasonal suitability or an itinerary constraint.
Origin and accessibility free text are excluded from the search query. The API
returns `query`, `duration_days`, `preference_handling`, model identity and explicit
limitations. No profiles are persisted. Frontend validated preferences live only
in a React context until refresh and can be reused between both journeys.

BGE-small-en-v1.5 revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`
encodes a short query locally. PostgreSQL pgvector exact cosine and English full-text
search retrieve passages. Global discovery averages the two highest chunk scores
per destination and channel, using the single score when only one chunk is
available. Reciprocal rank fusion combines the channel rankings. This is the
current production trial based on 24 judged development queries. Human held-out
evaluation remains pending. CrossEncoder and HNSW are disabled for this path. Focused exploration
filters canonical destination ID in SQL
**before** retrieving each channel and fuses passages (k=60), not cities. Search
limits are bounded; database statements time out after 30 seconds. Process-local
locking serializes CPU inference/search to avoid concurrent CPU saturation. The
first request loads the model; it may be slower. This is a local product foundation,
not a capacity-tested public deployment. No CrossEncoder or external provider is used.

## APIs

- `POST /api/v1/discovery/global`: `{profile, countries?: string[], continents?: string[], limit?: 1..20}`.
  Codes are two-letter uppercase (case normalized); country and continent filters
  intersect. Default is worldwide. Returns `scope`, ordered `results` with GeoNames
  ID, city, country/code, continent, retrieval rank, raw RRF value, channel ranks,
  deterministic reasons and up to three source passages. No 0–100 fit score.
- `GET /api/v1/discovery/destinations?q=<city>&limit=10`: bounded canonical lookup
  of destinations with indexed guide knowledge, including country to disambiguate
  same-name cities. Literal substrings; `%`/`_` are escaped. Maximum limit 20.
- `POST /api/v1/discovery/destination`: `{destination_id, profile, limit?: 1..40}`.
  Number of days is `profile.travel_context.duration_days`, or derived inclusively
  from dates by the existing profile contract. Returns destination and eight topic
  groups with source passages. Groups use deterministic section/keyword rules;
  they are navigation aids, not verified POI categories or relevance labels. A
  passage appears once. Empty groups explicitly disclose missing returned evidence.

Every passage includes stored source/revision URL, license, title, section,
content hash and ingestion timestamp. Citations are source guide revisions, not
LLM-generated explanations. No synthetic destination results or injected API
responses are used. Unknown/no-guide destination: 404. Invalid input: 422. Missing
local model or unavailable storage: safe 503. Omitted filters search worldwide; filters that match no destinations return an empty list.
OpenAPI exposes input/output models for future separately tested interpretation or
query-expansion adapters; retrieval does not depend on one. Any future LLM may
receive only bounded selected evidence/shortlists, never the full corpus.

## Local runtime

Existing database and named volume must be retained, with migrations through 0007.
Use `pip install -e ".[dev,retrieval]"` in the backend environment. Set
`RETRIEVAL_MODEL_PATH` to the existing pinned local snapshot directory and
`RETRIEVAL_THREADS=2`. No downloads occur during request handling. Native Windows
uses the absolute host path; Docker uses:

```
RETRIEVAL_MODEL_PATH=/models/models--BAAI--bge-small-en-v1.5/snapshots/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a
```

Compose mounts `.tmp/models` at `/models` read-only and installs the existing
retrieval optional dependencies. Start with the documented Compose workflow.
Weights, corpus, credentials and caches remain ignored. No new schema migration is
needed by product integration; migration 0007 belongs to the preceding
content-aware embedding commit.

## Scope and evaluation

9,026 matched destinations / 162,610 embedded passages are coverage, not proven
worldwide recommendation quality. Human-review files and all existing evaluation
artifacts are unchanged by this integration. Full-corpus judged quality remains
pending. No held-out evaluation or tuning was performed. The historical bootstrap
benchmark has its original coverage limitations; no new metrics are inferred here.

No verified opening hours, real-time availability, accessibility, crowd/weather
suitability, route feasibility or complete itineraries are promised. A destination
may have semantically retrieved passages without literal interest matches; reasons
state the search channels and supporting sections rather than claiming every
preference is met. Existing Geoapify evidence, Berlin snapshot/features and
`POST /api/v1/recommendations/destinations` remain separate and unchanged. No
retrieved destination is automatically enriched.

Tests use isolated real PostgreSQL schemas, deterministic local fixture encoders,
real HTTP contracts and no paid/provider requests. Frontend tests cover API errors,
empty results, source-link safety and unchanged profile handoff contracts.
