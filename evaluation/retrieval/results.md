# Worldwide retrieval benchmark

Curated project qrels; incomplete judgments, not tourism ground truth

Queries: 24. Full ingestion: 10,142 pages, 9,026 destinations, 162,610 chunks.
**Measured bootstrap index:** 256 destinations, 1024 chunks, 219 countries. Only 29/49 judged destinations are present; missing destinations remain in metric denominators.
Selected embedding: `BAAI/bge-small-en-v1.5` (highest measured hybrid nDCG@10; latency breaks ties).

| System | Recall@10 | Recall@20 | Recall@50 | MRR | nDCG@10 | p50 s | p95 s |
|---|---:|---:|---:|---:|---:|---:|---:|
| lexical | 0.1111 | 0.1875 | 0.2708 | 0.2142 | 0.1055 | 0.0661 | 0.1025 |
| dense | 0.1840 | 0.2639 | 0.4132 | 0.2416 | 0.1414 | 0.1433 | 0.2305 |
| hybrid | 0.1771 | 0.2500 | 0.3125 | 0.3399 | 0.1774 | 0.2548 | 0.5443 |
| hybrid_reranker_50 | 0.1632 | 0.2500 | 0.3125 | 0.2884 | 0.1575 | 6.8878 | 7.6161 |
| hybrid_reranker_100 | 0.1701 | 0.2743 | 0.3993 | 0.3080 | 0.1665 | 13.2103 | 14.5673 |

## Embedding comparison

| Model | Dense nDCG@10 | Hybrid nDCG@10 | Hybrid p50/p95 s | Download bytes | Vector payload bytes |
|---|---:|---:|---|---:|---:|
| BAAI/bge-small-en-v1.5 | 0.1414 | 0.1774 | 0.2548/0.5443 | 134411157 | 1572864 |
| intfloat/e5-small-v2 | 0.1489 | 0.1686 | 0.2255/0.3035 | 134410906 | 1572864 |

BGE wins the hybrid comparison narrowly; E5 has higher dense-only nDCG. Hybrid improves MRR/nDCG over BGE dense retrieval but lowers Recall@50. Neither reranker setting beats hybrid nDCG@10, so reranking remains optional rather than a recommended default.

## Aggregation and reranking

Best-chunk aggregation is the baseline. Alternative top-three-mean hybrid metrics: `{"recall@10": 0.19791666666666666, "recall@20": 0.23958333333333334, "recall@50": 0.34375, "mrr": 0.3269015418694705, "ndcg@10": 0.18455417051978906}`.
hybrid_reranker_50: reranking-only seconds `{"p50": 6.659218850079924, "p95": 7.3874974099337125}`; candidate counts `{"p50": 50.0, "p95": 50.0}`.
hybrid_reranker_100: reranking-only seconds `{"p50": 12.911777500063181, "p95": 14.340689355076755}`; candidate counts `{"p50": 100.0, "p95": 100.0}`.

## Measurement limits

CPU-local, four inference threads on a shared development machine, not an isolated performance lab. Warm percentiles exclude model/JIT startup, reported separately in JSON. Indexing is incremental: embedded_chunks and seconds describe missing embeddings written by that run, not a fresh full-corpus index. Model revisions, startup, vector storage, package versions and all per-query rankings are in results.json.

The corpus manifest is query-independent and identical across systems. Qrels were frozen before evaluation and include missing-corpus destinations in the denominator. No geographic filter was applied. This is a development benchmark, not an untouched test set or comparative tourism-quality assessment. Full ingestion does not mean full transformer indexing. See ingestion.json for source checksums and idempotency measurements.

HNSW: `{"build_seconds": 0.27227710001170635, "exact_seconds": {"p50": 0.04401990002952516, "p95": 0.06750856495928018}, "hnsw_seconds": {"p50": 0.04385004995856434, "p95": 0.05641796005656942}, "recall_at_100_mean": 0.9975, "plan_nodes": [{"type": "Limit", "index": null}, {"type": "Nested Loop", "index": null}, {"type": "Nested Loop", "index": null}, {"type": "Nested Loop", "index": null}, {"type": "Index Scan", "index": "ix_knowledge_ann_experiment"}, {"type": "Materialize", "index": null}, {"type": "Index Scan", "index": "knowledge_chunks_pkey"}, {"type": "Index Scan", "index": "knowledge_pages_pkey"}, {"type": "Index Scan", "index": "destinations_pkey"}], "hnsw_index_used": true, "queries": 8, "ef_search": 200, "decision": "Experimental index removed; exact remains default pending representative scale/filter validation."}`

Paid API cost: 0. No OpenAI or Geoapify calls. No text generation.
