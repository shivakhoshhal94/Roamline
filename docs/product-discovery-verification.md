# Product discovery verification

Validated locally against the persisted full BGE corpus; no Geoapify calls or paid
API calls. `/health`, `/ready`, canonical destination lookup, global discovery and
Berlin-scoped discovery returned HTTP 200. The five-interest balanced five-day
profile surfaced London, Istanbul and Jerusalem as the first three global hits;
this is an integration observation, **not judged quality evidence**. Berlin focused
retrieval returned real revision-linked passages in the eight-category response.

The browser completed four-step profile intake, global search, source disclosures,
destination handoff, Berlin selection, reuse of the validated in-memory profile
and focused guide exploration. Mobile-width inspection had no horizontal overflow;
the attributed Berlin photograph loaded. Browser error/warning log was empty.
Loading state and unconfigured-client error state were also observed during setup.

The first full-corpus HTTP query hit the database statement timeout. Passage search
now ranks narrow chunk IDs/scores before hydrating full source/destination payloads.
This preserves the exact cosine/lexical/RRF definitions, filtering and fixed limits;
no qrels, retrieval tuning or relevance judgments were changed. A subsequent cold
HTTP global request succeeded in approximately 46 seconds including local model
and ranking-library initialization; Berlin-scoped retrieval took approximately
0.16 seconds. These are local observations, not a throughput benchmark or SLA.

The original Geoapify API still returned HTTP 200. A read-only regression check
confirmed Berlin's complete snapshot, 22 cached categories, 10 aggregates and
canonical 57.2 score remain unchanged. No application data was enriched or mutated
by the new retrieval endpoints.

See product-discovery.md for contracts and quality/availability limitations.

Initial pre-split working-tree validation: 230 backend tests passed (including PostgreSQL integration tests); 16 frontend tests passed; frontend lint and production build passed. Alembic upgrade/downgrade/re-upgrade, model-drift check, Compose configuration and `git diff --check` passed. Secret/artifact scan found no leaks or unintended generated files; `.env` remains ignored.

Commit-scope validation: the independently exported product tree, excluding Milestone 4B evaluation tooling, passed all 215 backend tests including PostgreSQL integration tests. All 16 frontend tests, lint and production build passed. Populated migration upgrade/downgrade/re-upgrade, schema checks, staged Compose configuration and staged whitespace validation passed.

The complete working-tree suite also passed all 232 backend tests, including preserved uncommitted evaluation tests; those evaluation changes are excluded from the product commit. Both staged-code discovery APIs were rechecked through the application HTTP interface with the actual pinned BGE encoder and a read-only connection to the persisted corpus. Global discovery returned nine database-verified source passages; Berlin exploration returned sixteen. All 162,610 BGE embeddings remained valid and the Berlin Geoapify baseline was unchanged. Browser observations above describe the earlier completed desktop/mobile verification of the same frontend implementation.
