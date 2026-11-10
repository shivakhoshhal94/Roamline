# Full-corpus evaluation v1

Milestone 4A bootstrap artifacts in the parent directory are preserved.

## Human review required

`queries-review.json` contains 48 frozen prompts. The original 24 project qrels are development-only: they were previously exposed and cannot become a genuinely held-out test by relabeling them. The 24 new prompts have **no relevance grades**. The original `human-review.csv` was only a query template, not a usable destination-grading worksheet; reviewers are not expected to invent candidate destinations.

After full BGE embedding and integrity validation, `python -m app.retrieval.review_pool --model-path <pinned-local-BGE-snapshot>` constructs the actual worksheet. It retrieves a fixed top 20 destinations per query from lexical, BGE dense and current RRF/hybrid search, using best-chunk aggregation and the existing 3,000-chunk channel bound. There is no reranking, geographic restriction, evaluation, tuning or paid API. This is candidate retrieval, not a relevance judgment.

The union is deduplicated by query/GeoNames ID and ordered within each query by a fixed SHA-256 permutation. The reviewer CSV contains city, country and a stored Wikivoyage revision URL, with grade, reviewer and notes blank. Retrieval scores/ranks and system membership appear only in `human-review-pool.json`, separately from the grading worksheet. Review the CSV without consulting system scores before grading. `human-review-template.csv` preserves the original template; `human-review-pool-summary.json` records pool composition. Do not grade the template backup.

Allowed human grades are 3 strongly relevant, 2 relevant, 1 marginally relevant and 0 not relevant. Fill reviewer identity and any comments manually. Candidate inclusion is not an assertion of relevance. The generator refuses to overwrite grades, reviewer entries or manual notes; it does not modify qrels or split assignments. Stored evidence excerpts in the provenance file come directly from the knowledge database, not LLM generation.

Twelve new prompts are assigned to development, twelve to held-out by a frozen alternating split in `splits.json`. Use `python -m app.retrieval.full_evaluate --import-review evaluation/retrieval/full-corpus-v1/human-review.csv` to import completed rows. A nonempty reviewer is required, and frozen query text/splits cannot change. Human-reviewed grades must be imported into the query JSON and marked `judgment_status: human_reviewed` before quality evaluation. The evaluator refuses non-human held-out grades. Do not inspect held-out grades or results while tuning development parameters. New prompts are not factual relevance judgments or tourism ground truth.

Until review is complete, the expanded 48-query/held-out evaluation is **pending**. Legacy 24-query results are provisional engineering evidence only. No prospective held-out success is claimed.

## Execution

All usable knowledge chunks are embedded incrementally with pinned model revisions. Migration 0007 records each represented content hash. Completed batches survive interruption; changed content/model versions are not treated as cached. `.tmp/models` contains local weights and is ignored. No Geoapify or paid APIs are called.

The original BGE run was interrupted for a CPU configuration probe. It preserved 1,536 newly committed vectors and resume skipped them. A 64-chunk probe favored 2 CPU threads / inference batch 16 over 4 threads / batch 64 on this laptop. The full pass uses bounded 512-chunk database batches and 16-text inference microbatches. Query timings use 4 inference threads; the two settings are reported separately. No quantization, changed weights or shortened token limit is used.

`planned-configuration.json` freezes the experiment choices before full results. Full evaluation uses exact full-corpus retrieval, a 3,000-chunk channel pool, all qrels, three aggregation ablations, five candidate strategies and bounded 20/50 rerankers. Candidate chunks processed means returned hits consumed by destination aggregation, not PostgreSQL physical tuples scanned. Exact cosine still considers every compatible vector. Score meanings remain separate from product recommendation scores.

Download the additional small reranker explicitly with `python -m app.retrieval.cli download-models --directory .tmp/models --include-small-reranker`; this is a free public model download, not an inference API.

The quantitative run is launched explicitly from the repository root with configured host DATABASE_URL:

```powershell
python -m app.retrieval.full_evaluate --registry .tmp/models/registry.json
```

It refuses incomplete model indexes, fingerprints corpus/query/config/model revisions, checkpoints finished systems, freezes development choices before held-out evaluation, and writes metrics, latency, configuration and Markdown reports. Never run two benchmark writers against the same directory. Temporary HNSW indexes are removed after measurement. No frontend or recommendation endpoint integration is included.


## Completed BGE and candidate-pool step

BGE now covers all **162,610 chunks / 9,026 destinations**, using the pinned revision. This foreground resume added 137,794 vectors and preserved/skipped all 24,816 starting records. Final checks found zero missing vectors, hash mismatches, invalid dimensions/norms, nonfinite values, duplicate keys or orphans. `bge-foreground-resume.json` records the checks and wall time (including the overnight host/tool-connection gap); it is not an isolated encoding-throughput benchmark. The PostgreSQL named volume is preserved. No model or pooling process remains active after this step.

`human-review.csv` is now the actual, ungraded candidate worksheet: **24 queries**, **1,036 query-destination pairs**, **664 unique destinations**, **38 / 43 / 47** candidates per query (min/median/max), **118 country codes**, **six continents**, and **zero missing stored revision URLs**. Lexical, dense and hybrid each contributed 480 memberships; 366 pairs were shared across systems. Grade, reviewer and notes fields are blank. The CSV has no retrieval score/rank columns. `human-review-template.csv` preserves the original blank template byte-for-byte; do not grade that backup.

See [the complete pool report](human-review-pool-report.md), [machine-readable composition](human-review-pool-summary.json) and [separate provenance](human-review-pool.json). Query definitions, qrels and development/held-out assignments remain unchanged. E5 full encoding, CrossEncoder experiments, quality evaluation/tuning, OpenAI/Geoapify calls and paid APIs were not run. The old multi-model coordinator remains stopped/guarded. The next action is manual grading of the provided candidates, not automatic evaluation or product integration.

## Reproducibility and interpretation

These are historical corpus-completion, coverage and candidate-pooling records, not completed full-corpus ranking results. The bootstrap quality measurements remain in the parent evaluation directory and apply only to their documented cohort. Full-corpus development/held-out quality evaluation is pending; the new review prompts have no human judgments. ANN recall against exact retrieval measures search approximation overlap, not human relevance. Neither ANN nor a reranker becomes a product default through this tooling. The separate product integration was committed independently.

See [artifact reproduction](reproducibility.md) for the historical code fingerprint and byte-for-byte ungraded worksheet reconstruction. The code fingerprint predates the product query optimization; it must not be silently replaced with the current implementation hash. No original worksheet, template, provenance, query, split or legacy relevance value was changed while preparing this commit.
