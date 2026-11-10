# Worldwide destination retrieval

## Implemented scope

This internal retriever is separate from the existing Geoapify-backed recommendation score. It retrieves a worldwide shortlist from travel knowledge, without requiring deep Geoapify evidence. It never triggers Geoapify, OpenAI, weather, itinerary or text-generation calls. The product recommendation endpoint and frontend contract are unchanged; connecting retrieval to a final product ranking is a later milestone.

```text
GeoNames identity + current Wikivoyage dump
  -> conservative name/coordinate matching
  -> source pages + section-aware chunks + provenance
  -> local Sentence Transformers embeddings + PostgreSQL full-text search
  -> exact pgvector cosine / lexical destination pools
  -> destination-level reciprocal rank fusion
  -> optional bounded local CrossEncoder -> internal shortlist
```

There is no implicit Europe filter. Retrieval uses all available corpus destinations unless the caller supplies country/continent/population filters. English source coverage and the measured bootstrap index are not equivalent to complete worldwide coverage.

## Sources, ingestion and licensing

GeoNames remains the canonical city/country/continent/coordinate/population/timezone source. English Wikivoyage is the first travel-text source because its destination guides contain travel-oriented sections, rather than generic encyclopedia summaries. Wikipedia and Wikidata are planned fallback/supplementary sources, not implemented here.

Wikimedia publishes [current-article XML dumps](https://meta.wikimedia.org/wiki/Data_dumps/What%27s_available_for_download/en). Use `pages-articles.xml.bz2`, not revision history, individual-page API harvesting or rendered-HTML scraping. Downloads use an identifying User-Agent and verify the official SHA-1 manifest; ingestion records SHA-256. Pin an immutable dump date for reproduction. Raw archives and model files stay under ignored `.tmp/`, `data/` or `.cache/` directories.

`mwxml` streams pages/revisions; the importer retains only the latest revision per page, namespace zero, non-redirect articles with content. `mwparserfromhell` handles wikicode. City matching requires a canonical/ASCII title match (including a district guide's city prefix), a valid `geo` template, and exactly one matching GeoNames city within 25 km. Same-name candidates are checked worldwide; missing/ambiguous coordinates are skipped. Conservative matching will miss aliases, translated titles and some metropolitan districts. No guessed identity is inserted.

Page IDs, title, revision, revision timestamp, immutable revision URL, source/language, license, content hashes, dump hash, parser version, identity match method/distance and ingestion time are retained. Attribution: **Wikivoyage contributors, CC BY-SA 4.0**; each evidence snippet links to its source revision, from which page history/contributors are available. GeoNames attribution remains CC BY 4.0. Respect source attribution and share-alike obligations when exporting travel text or derived corpora.

Each page replacement is atomic and idempotent by source/page identity and content hash. A newer identical revision updates provenance without rebuilding identical chunks; older revisions are ignored. Changed text replaces page chunks and cascades deletion of outdated embeddings. A crash or malformed later page preserves earlier committed pages; rerunning the same dump resumes by skipping completed hashes. Run one ingestion operator at a time. Source deletions/renames and alias reconciliation are not automated.

## Chunking and metadata

Sections are parsed structurally, preserving the destination title and section heading. Understand, See, Do, Eat, Drink, Buy, Sleep, Get around and other meaningful sections are retained. Navigation templates, media/category links, reference/gallery/table noise are removed. Travel listing templates retain names/descriptions, rather than silently dropping POIs with the templates. This is a conservative cleaner, not MediaWiki template expansion.

Only oversized sections use `RecursiveCharacterTextSplitter`, with 1,400-character bodies, 120-character overlap and paragraph/newline/sentence/word boundaries. Bodies shorter than 60 characters are excluded. Prefixes preserve city/heading context; texts can therefore slightly exceed the body limit. Full-corpus length statistics and small/large counts are recorded in the evaluation JSON.

PostgreSQL stores source pages, chunks, and embeddings separately. Each chunk joins a GeoNames destination and preserves section/index/hash; embedding rows key chunk + model + immutable model revision. Both tested encoders produce normalized 384-dimensional vectors. Country/continent/population filters use reliable canonical metadata, not inferred costs, weather, safety, accessibility or crowd conditions.

## Local models

Focused optional dependencies are installed with `pip install -e "backend[dev,retrieval]"`. Sentence Transformers supplies transformer inference and CrossEncoder scoring directly. Model snapshots are pinned to immutable Hugging Face revisions, loaded from explicit local paths with `local_files_only=True` and `trust_remote_code=False`. Search never downloads weights or invokes inference APIs. `download-models` is an explicit separate operation from the legitimate public model repositories.

The two CPU candidates are `BAAI/bge-small-en-v1.5` and `intfloat/e5-small-v2`. BGE queries use its retrieval instruction; E5 queries/passages use the required prefixes. Both normalize vectors for cosine search. The benchmark compares retrieval quality, warm query latency, load/indexing time, model bytes and float-vector storage. Four CPU inference threads are used. Model choice and exact measurements are in [the report](../evaluation/retrieval/results.md).

CrossEncoder uses `cross-encoder/ms-marco-MiniLM-L6-v2`, pinned separately, on only 50 or 100 candidate destinations. It scores the query against up to three supporting snippets per candidate, bounded to 512 tokens. It never cross-encodes the full corpus. Logits/relevance are recorded separately from retrieval fusion scores and Roamline's existing 0–100 product score.

## Retrieval and aggregation

Dense retrieval starts with exact pgvector cosine distance. Exact mode explicitly disables index scans in its transaction so a later experimental ANN index cannot silently change the correctness baseline. Lexical retrieval uses PostgreSQL's English stemming/stopwords, OR-combined parsed lexemes, `ts_rank_cd` and a GIN-indexed tsvector. No second vector database or search server is used.

Each channel collapses chunk hits into destinations using the best-ranked chunk, retaining up to three supporting chunks. A destination receives one vote per channel regardless of its number of articles/districts. An alternative top-three-mean aggregation is evaluated, rather than averaging every chunk. Channel pools overfetch chunks (at least 500, up to 20 per requested candidate) before deduplication; this bounded heuristic can miss destinations dominated by other cities' numerous high-ranked chunks.

`ranx.fusion.rrf` implements standard reciprocal rank fusion with k=60. No hand-written IR metric or arbitrary semantic/lexical weighted score is used. The service preserves dense/lexical ranks and scores, fused score/rank, optional reranker score, final rank, evidence and source provenance. Geography defaults to worldwide available data. Ties use canonical GeoNames IDs where applicable.

HNSW is an experiment, not an unconditional production index. The evaluator builds a native partial HNSW index for the selected embedding revision, compares top-100 recall and latency against exact search, checks the PostgreSQL plan, and removes the experimental index. Exact search remains the default until representative scale and filtered recall justify ANN.

## Evaluation and measured corpus scope

[24 curated queries and graded qrels](../evaluation/retrieval/queries.json) were frozen before evaluation. They cover ancient history, art, markets, food, beaches/nightlife, mountains, winter sports, temples, music, urban design and quiet nature across continents. They are auditable project judgments, not objective tourism ground truth; unjudged alternatives may also be relevant.

Full-dump ingestion and transformer indexing are deliberately separate. A measured CPU probe took roughly nine seconds for 64 long chunks, making two-model full-corpus comparison an hours-long operation. The first benchmark therefore freezes a **query-independent bootstrap corpus**: up to 256 matched destinations selected by the existing population/continent/country round-robin policy, and up to four distinct travel sections per city. The manifest records source page ID, chunk index and text hash. No query or qrel affects selection. Every system uses the exact same manifest, including lexical retrieval. Missing expected destinations remain in metric denominators; qrels are not changed to improve scores.

The broader ingested knowledge remains in PostgreSQL. `embed` without a benchmark manifest incrementally indexes every remaining chunk for the chosen model/revision; no deep Geoapify evidence is required. Full-corpus retrieval evaluation remains pending. Bootstrap results do not prove retrieval quality for the full GeoNames universe or every ingested page. The internal search CLI sees available indexed dense evidence plus available lexical knowledge; benchmark scope is explicitly fixed for fair comparisons.

`ranx` computes Recall@10/20/50, MRR and nDCG@10. The evaluator compares lexical, both dense models, hybrid, hybrid plus CrossEncoder at 50/100 candidates, and aggregation alternatives. It reports p50/p95 wall-clock query latency (including query embedding), reranking latency, candidate counts, encoding/index time, model downloads, and HNSW recall. Model/JIT startup is separated from warm percentiles. This small development set guides engineering; a later held-out human-reviewed evaluation is needed before quality claims or tuning.

## Measured results and decision

The 2026-11-01 English dump processed **34,821 current content pages**. Name/coordinate matching accepted 10,157 pages; 10,142 contained usable sections, covering **9,026 canonical destinations in 219 countries** and **162,610 chunks**. Full-corpus character lengths: median 509, p95 1,353; 29,384 chunks below 200 characters and one above 1,600. A complete second ingestion pass wrote zero pages/chunks and skipped all 10,142 persisted hashes. [Ingestion measurements and checksums](../evaluation/retrieval/ingestion.json) distinguish matched pages from distinct cities.

The final benchmark contains **256 destinations / 1,024 chunks / 301 source pages**, selected without queries or qrels. Continental destination counts are AF 64, AN 1, AS 55, EU 56, NA 39, OC 21, SA 20. Only **29 of 49 unique judged destinations** are in this cohort; the other 20 are retained as misses. The broader corpus remains lexical-searchable, but most of its chunks are not yet embedded. The database also retains valid embeddings from a preliminary cohort; benchmark systems are restricted to the final frozen manifest.

**Default: BAAI/bge-small-en-v1.5**, revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`. Its hybrid nDCG@10 is 0.1774 versus E5's 0.1686, with similar small CPU models and 384 dimensions. This is a narrow development-set choice, not universal superiority: E5 has higher dense-only nDCG (0.1489 versus 0.1414). Model downloads are about 134.4 MB each; the final cohort's raw float32 vector payload is 1,572,864 bytes/model, excluding PostgreSQL row/index overhead. The final incremental encoding pass wrote 241 previously missing chunks/model in about 49 seconds; it is not a timing for all 162,610 chunks.

BGE dense Recall@50 is 0.4132 versus hybrid 0.3125. Hybrid improves MRR (0.3399 versus 0.2416) and nDCG@10, but does not dominate recall. CrossEncoder-50/100 nDCG@10 is 0.1575/0.1665, below hybrid; it remains optional/experimental, reduces measured nDCG while adding substantial latency, and is not production-ready or recommended as the default. Warm query p50/p95 seconds: lexical 0.066/0.103, dense 0.143/0.230, hybrid 0.255/0.544, rerank-50 6.888/7.616, rerank-100 13.210/14.567. These are CPU measurements on a shared development machine, not isolated service SLAs.

Best-chunk aggregation remains the simple baseline because one strong travel section can establish relevance and its MRR is higher (0.3399 versus top-three-mean 0.3269). Top-three-mean improves nDCG@10 to 0.1846 and Recall@50 to 0.3438; this trade-off merits held-out evaluation rather than claiming the baseline wins every metric.

Exact cosine remains the default. The forced native HNSW comparison achieved 0.9975 Recall@100 over eight queries, with exact/ANN p50 0.0440/0.0439 seconds and p95 0.0675/0.0564 seconds. This negligible p50 difference does not justify an index. At this bootstrap size PostgreSQL initially preferred primary-key scans plus sorting over the experimental HNSW index. The report includes a separately forced ANN-plan diagnostic, its actual plan and recall; planner hints are confined to the benchmark and the experimental index is removed afterward. No production ANN index is justified by this small cohort.

## Reproduction and internal CLI

Use the existing PostgreSQL named volume. Compose/CI now use `pgvector/pgvector:0.8.7-pg17-bookworm`, retaining PostgreSQL major version 17. Migration 0006 enables the vector extension and adds knowledge tables/GIN; downgrade drops only these tables and retains the shared extension. Never reset the volume to install pgvector.

From the repository root, activate the backend environment and configure the existing DATABASE_URL for host access (`127.0.0.1`, rather than the container hostname):

```powershell
python -m pip install -e "backend[dev,retrieval]"
# Apply Alembic from backend, then return to root.
python -m app.retrieval.cli download-wikivoyage --date 20261101
python -m app.retrieval.cli ingest-wikivoyage --dump .tmp/wikimedia/enwikivoyage-20261101-pages-articles.xml.bz2
python -m app.retrieval.cli stats
python -m app.retrieval.cli download-models --directory .tmp/models
python -m app.retrieval.evaluate --registry .tmp/models/registry.json
```

The model registry contains the explicit local snapshot paths/revisions. For a full incremental index or internal search, use the selected model's values:

```powershell
python -m app.retrieval.cli embed --model <model> --revision <revision> --model-path <local-snapshot>
python -m app.retrieval.cli search "historic streets, museums and local food" --model <model> --revision <revision> --model-path <local-snapshot> --top-k 10 --candidates 50
python -m app.retrieval.cli search "Roman architecture" --mode lexical --country IT
# Optional local reranking:
python -m app.retrieval.cli search "temples and traditional culture" --model <model> --revision <revision> --model-path <local-snapshot> --reranker-path <local-crossencoder-snapshot> --reranker-revision <revision> --candidates 100
```

No free text is interpreted by an LLM. No Geoapify enrichment is automatic. The existing product recommendation score is not blended with retrieval scores. Paid API calls, embedding/reranking API cost and local database service cost are all zero (excluding the user's existing hardware/electricity/network).

## Planned, not implemented

Wikipedia/Wikidata fallbacks, multilingual corpora, full-catalog embedding operations at production scale, independent identity review, held-out relevance judgments, product-level shortlist integration, automatic approved shortlist enrichment, LLM/RAG answer generation, LangGraph, weather and itinerary generation are not part of this milestone.

## Resumable corpus embeddings

Migration 0007 records the chunk content hash represented by each vector. `embedding-status --model <model> --revision <revision>` reports compatible cached coverage without loading weights. `embed` resumes by default, skips matching content/model revisions, commits bounded batches, and reports progress to stderr. `--force` replaces only the explicitly selected model/revision in atomic batches; it does not delete other model versions. `--batch-size`, `--inference-batch-size` and `--threads` bound/configure CPU execution. Changed content is checked again under row locks before storing a computed vector. Exceptions stop the run; completed batches survive.
