# Full-corpus retrieval architecture assessment

Baseline: `6878909daa014f1b8423ddefa57202ef14f35855`. This is a development-only engineering comparison, not a held-out quality claim. No application defaults, frontend, scoring or labels were changed. No paid APIs were called.

## Judged coverage

24 of 48 prompts have existing judgments: 94 graded pairs across 49 destinations (42 represented in the indexed corpus). All existing qrels and denominator destinations were kept. The 24 new prompts (12 development, 12 held-out) and 1,036 ungraded candidate rows were excluded; no blanks became zero-grade judgments. Unjudged items receive no gain under the legacy qrels, which are incomplete.

## Candidate generation and aggregation

Full 162,610-chunk pinned BGE corpus. Each channel retrieves up to 3,000 chunks before destination aggregation. Dense uses 100 destination candidates; lexical uses 100; RRF uses 100 per channel; balanced union uses 50+50; high-recall union uses 100+50. Union interleaves ranks and deduplicates. Fixed channel hits were reused across ablations. The RRF algorithm is unchanged, but the evaluation pool is wider than the currently deployed API caps. Four directly measured baseline rankings matched replay outputs on all 24 queries.

| System | R@10 | R@20 | R@50 | R@100 | MRR | nDCG@5 | nDCG@10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| dense:best | 0.2188 | 0.2708 | 0.3750 | 0.4792 | 0.2939 | 0.1550 | 0.1849 |
| lexical:best | 0.0764 | 0.1389 | 0.2778 | 0.3854 | 0.0961 | 0.0352 | 0.0497 |
| rrf:best | 0.2153 | 0.3021 | 0.3958 | 0.4792 | 0.3914 | 0.1936 | 0.2025 |
| union:best | 0.1701 | 0.2500 | 0.3542 | 0.4583 | 0.2572 | 0.1228 | 0.1477 |
| high_recall_union:best | 0.1701 | 0.2500 | 0.3542 | 0.4688 | 0.2579 | 0.1228 | 0.1477 |
| dense:top2mean | 0.2153 | 0.2708 | 0.3750 | 0.5035 | 0.3884 | 0.1861 | 0.2156 |
| lexical:top2mean | 0.0764 | 0.1389 | 0.2986 | 0.4062 | 0.1244 | 0.0419 | 0.0543 |
| rrf:top2mean | 0.2778 | 0.3125 | 0.4062 | 0.4688 | 0.3945 | 0.2003 | 0.2279 |
| union:top2mean | 0.1840 | 0.2674 | 0.4062 | 0.4583 | 0.3833 | 0.1641 | 0.1926 |
| high_recall_union:top2mean | 0.1840 | 0.2674 | 0.4062 | 0.4722 | 0.3837 | 0.1641 | 0.1926 |
| dense:top3mean | 0.1944 | 0.2361 | 0.3854 | 0.4618 | 0.3188 | 0.1630 | 0.1896 |
| lexical:top3mean | 0.0625 | 0.1389 | 0.3090 | 0.3646 | 0.1410 | 0.0406 | 0.0582 |
| rrf:top3mean | 0.2674 | 0.3125 | 0.3958 | 0.4583 | 0.4249 | 0.2038 | 0.2318 |
| union:top3mean | 0.1736 | 0.2257 | 0.3854 | 0.4583 | 0.3283 | 0.1516 | 0.1748 |
| high_recall_union:top3mean | 0.1736 | 0.2257 | 0.3854 | 0.4722 | 0.3286 | 0.1516 | 0.1748 |

Dense mean top-two leads R@100 (0.5035) and avoids lexical search cost. RRF mean top-two leads R@50 (0.4062, tied with the two union variants at that aggregation) and improves top-20 recall from 0.2708 to 0.3125. RRF mean top-three leads MRR and nDCG@10. High-recall union does not dominate RRF. Lexical-only beats dense-only at Recall@20 on historic-photo, markets, coast-photo and history-asia (one legacy positive versus zero on each).

## Direct warm latency (seconds, p50 / p95)

| System | Query embedding | Vector database | Lexical database | Aggregation | End to end |
|---|---:|---:|---:|---:|---:|
| dense:best | 0.055 / 0.155 | 1.644 / 2.202 | 0.000 / 0.000 | 0.052 / 0.088 | 1.889 / 2.340 |
| lexical:best | 0.000 / 0.000 | 0.000 / 0.000 | 1.011 / 1.471 | 0.053 / 0.084 | 1.089 / 1.560 |
| rrf:best | 0.054 / 0.147 | 1.515 / 1.939 | 0.837 / 1.225 | 0.098 / 0.188 | 2.692 / 3.072 |
| union:best | 0.052 / 0.203 | 1.597 / 2.114 | 0.895 / 1.216 | 0.086 / 0.430 | 2.858 / 3.346 |

## Bounded reranking

Reranking uses the same dense mean-top-two shortlist. Replay total includes aggregation/ranking and model inference, but excludes database search and query encoding. It is not end-to-end service latency.

| Reranker | MRR | nDCG@5 | nDCG@10 | Reranking p50/p95 s | Replay total p50/p95 s |
|---|---:|---:|---:|---:|---:|
| none | 0.3884 | 0.1861 | 0.2156 | 0.000 / 0.000 | 0.058 / 0.130 |
| cross-encoder/ms-marco-MiniLM-L6-v2:20 | 0.2527 | 0.1289 | 0.1821 | 5.853 / 8.724 | 5.905 / 8.781 |
| cross-encoder/ms-marco-MiniLM-L6-v2:50 | 0.2159 | 0.1145 | 0.1620 | 14.157 / 18.506 | 14.218 / 18.615 |
| cross-encoder/ms-marco-TinyBERT-L2-v2:20 | 0.2428 | 0.1241 | 0.1556 | 0.692 / 1.279 | 0.799 / 1.381 |
| cross-encoder/ms-marco-TinyBERT-L2-v2:50 | 0.2316 | 0.1120 | 0.1432 | 1.459 / 2.249 | 1.521 / 2.310 |

## Exact cosine versus HNSW

Temporary index: 148.81 s build and analyze, 333,029,376 bytes. Plan used the HNSW index: True. Exact database p50/p95 1.735 / 3.421 s; HNSW 9.480 / 11.260 s. The experimental index was removed. HNSW was slower and missed the 0.99/0.98 destination-overlap targets at 20/100. This finding is specific to the tested SQL and plan, which showed a one-row hydration estimate and nested loops.

| Unit | Recall against exact @20 | @50 | @100 |
|---|---:|---:|---:|
| chunks | 0.9875 | 0.9842 | 0.9787 |
| destinations | 0.9854 | 0.9833 | 0.9717 |

Selected dense-top-two HNSW pipeline: R@100 0.4826, nDCG@10 0.2100, total p50/p95 10.070 / 11.948 s.

## E5

Pinned E5 used `passage:` for documents and uses `query:` for queries. The bounded probe added 128 vectors in 35.33 s (3.62 chunks/s). 1,392/162,610 are valid; 161,218 remain, approximately 12.4 hours if the measured rate holds. Raw stored payload: 2,138,112 bytes; full float32 payload would be 249,768,960 bytes excluding database overhead. BGE rows were preserved.

Fresh matched-cohort comparison: both models searched the identical 1,264 pre-probe chunks across 311 destinations; 30/49 legacy judged destinations were present. All 24 judged queries and all original qrels remain in the metrics. This is a coverage-limited model comparison.

| Model | R@20 | R@50 | R@100 | MRR | nDCG@10 | Total p50/p95 s |
|---|---:|---:|---:|---:|---:|---:|
| intfloat/e5-small-v2 | 0.2951 | 0.4201 | 0.5694 | 0.4157 | 0.2085 | 0.285 / 0.479 |
| BAAI/bge-small-en-v1.5 | 0.3264 | 0.4028 | 0.5590 | 0.2879 | 0.1691 | 0.286 / 0.697 |

E5 query encoding alone (24 legacy queries): 0.045 / 0.047 s p50/p95; local model snapshot footprint 134,410,906 bytes.

E5 full-corpus metrics and retrieval latency are unavailable. Do not compare partial-index results with full BGE. The older bootstrap comparison is separate: BGE led hybrid nDCG@10 (0.1774 vs 0.1686), while E5 led dense-only (0.1489 vs 0.1414). Keep BGE pending an apples-to-apples full-corpus comparison.

## Diversity and popularity

The selected dense policy averages 7.50 countries and 3.17 continents per top 10 (medians 8 and 3). Each shortlist is destination-deduplicated. Across all queries, the five most frequent destinations account for 20/240 slots (8.33%); Athens led with five appearances. This is not collapse into one small destination set. Exposure correlations are weak positive: population 0.0764, indexed text length 0.1177, chunk count 0.1276 (Spearman, all indexed cities including zero exposure). Indexed text length is a proxy for article size and includes chunk overlap. Correlations are descriptive, not causal.

| Query ID | Countries in top 10 | Continents in top 10 |
|---|---:|---:|
| ancient | 8 | 3 |
| renaissance | 6 | 2 |
| beach-nightlife | 9 | 4 |
| street-food | 8 | 4 |
| mountain-hiking | 8 | 4 |
| classical | 7 | 2 |
| historic-photo | 6 | 4 |
| modern | 7 | 4 |
| temples | 8 | 1 |
| winter | 8 | 3 |
| quiet-nature | 9 | 3 |
| shopping | 8 | 3 |
| jazz | 7 | 3 |
| roman | 6 | 2 |
| markets | 10 | 3 |
| art-museums | 6 | 2 |
| latin-food | 5 | 3 |
| coast-photo | 8 | 4 |
| history-asia | 6 | 3 |
| cafes | 8 | 5 |
| parks-city | 7 | 4 |
| waterfront | 7 | 4 |
| dance | 10 | 3 |
| food-modern | 8 | 3 |

## Examined weak cases

| Query | Legacy judged city | Observation |
|---|---|---|
| Mountain hiking | Banff | No indexed chunks; missing canonical catalog entry. |
| Mountain hiking | Queenstown | No indexed chunks; catalog gap and ambiguous/missing guide coordinates. |
| Mountain hiking | Cusco | No indexed chunks; Cuzco alias misses identity matching. |
| Winter | Zermatt | No indexed chunks despite guide evidence; catalog coverage gap. |
| Historic photo | Fes | No indexed chunks; Fez alias misses identity matching. |
| Temples | Nara-shi | No indexed chunks; Nara guide name differs. |
| Markets | Marrakesh | No indexed chunks; Marrakech alias misses identity matching. |
| Ancient history | Luxor | 90 chunks, outside the selected pool; all three compared channels missed it. Ranking/judgment cause unresolved. |
| Classical music | Salzburg | 44 chunks; rank 73 with top-two, 60 with best chunk. Aggregation demoted it, but did not cause the whole miss. |
| Historic photo | Prague | Rank 71 dense top-two, 14 lexical, 4 RRF top-two. Text evidence helps this case. |
| Markets | Bangkok | Rank 33 dense top-two, 19 lexical, 7 RRF top-two. Dense misses the top 20 despite abundant coverage. |
| Coast photo | Sydney | Rank 52 dense top-two, 7 lexical, 3 RRF top-two. Complementary lexical signal. |
| Street food | Mexico City | Rank 45 dense top-two and 75 lexical, but 9 with RRF top-two. Fusion rescues the case. |

## Recommendation and API smoke test

The [product-path A/B below](#product-discovery-policy-ab) supersedes the recommendation for the direct discovery shortlist. The earlier trial used a wider retrieval budget.

Candidate-generation recommendation from available evidence: pinned BGE, exact cosine, 3,000 chunk hits, dense top 100, lexical top 0, no union or RRF, mean of top two chunks, no reranker, return 20. This prioritizes Recall@100 and search cost; it is not a claim that dense produces the best top-20 ranking. The product policy remained unchanged during this wider-budget trial.

The proposed policy was injected temporarily at the retrieval boundary of the real discovery API; data and model were real and the frontend was unchanged. These results are smoke observations, not relevance grades.

| Interests | HTTP | Seconds | Top five |
|---|---:|---:|---|
| historic architecture, museums, food | 200 | 4.82 | Lüderitz, Namibia; Istanbul, Turkey; Thun, Switzerland; Havelian, Pakistan; Istaravshan, Tajikistan |
| beaches, nightlife | 200 | 2.55 | Netanya, Israel; Hurghada, Egypt; Phuket, Thailand; Vang Vieng, Laos; Tandil, Argentina |
| mountains, hiking, quiet travel | 200 | 2.53 | Requena, Spain; Chandigarh, India; Mianyang, China; Podujeva, Kosovo; Napa, United States |
| traditional culture, photography | 200 | 1.94 | Nikaho, Japan; Carei, Romania; Chandigarh, India; Takaoka, Japan; Ninohe, Japan |
| food markets, urban exploration | 200 | 2.05 | Kalugumalai, India; Delhi, India; Hoogeveen, The Netherlands; Freiburg, Germany; Kaesŏng, North Korea |

## Validation

- Compose configuration: passed
- Git whitespace and frozen evaluation inputs: passed
- Backend suite including PostgreSQL integration: 232 passed
- Frontend: 16 passed; lint and production build passed
- Alembic migration validation: upgrade/downgrade/re-upgrade and schema checks passed
- Application data regression: Berlin snapshot and score unchanged; all 162,610 BGE vectors valid; temporary ANN index removed

Raw benchmark files and local drivers are under ignored `.tmp/architecture-v1/` and `.tmp/architecture-*.py`. Existing qrels and human-review artifacts were not modified.

## Product discovery policy A/B

The global endpoint builds a deterministic query from the profile and retrieves up to 1,000 chunks per channel. At the time of the A/B comparison, the policy was exact BGE plus PostgreSQL lexical search, best-chunk aggregation and RRF with k=60. Each channel aggregates every returned chunk by GeoNames ID, with up to three evidence passages per destination. RRF ranks the union before the 50-destination internal cap. The API returns the requested prefix directly, eight by default and at most 20. The frontend requests eight. No second-stage product score, Geoapify enrichment or reranking participates in this route.

For this direct shortlist, prioritize Recall@20, nDCG@10, MRR and latency. Recall@50 and Recall@100 become primary candidate-coverage metrics if a later independent ranker consumes a larger pool.

Both policies used mean top-two aggregation, current product caps, the pinned local encoder and the real FastAPI route against read-only PostgreSQL. The experiment selected the policy in memory at the retrieval boundary. Eight profiles each ran twice in alternating order after one warm-up per policy. The smoke profiles have no relevance grades.

| Policy | Recall@20 | nDCG@10 | MRR |
|---|---:|---:|---:|
| Dense | 0.2604 | 0.1551 | 0.2603 |
| RRF | 0.2917 | 0.1900 | 0.2990 |

This judged check reuses the native channel hit lists for each of the 24 legacy development queries at the product caps. The 24 ungraded queries and human-review candidates are excluded. Earlier, wider-budget results remain above for comparison.

| Timing, seconds p50 / p95 | Dense | RRF |
|---|---:|---:|
| Dense search | 0.870 / 1.211 | 0.873 / 1.297 |
| Lexical search | 0.000 / 0.000 | 0.459 / 0.725 |
| Fusion | 0.000 / 0.000 | 0.028 / 0.035 |
| Aggregation | 0.018 / 0.024 | 0.034 / 0.043 |
| Retrieval total | 0.944 / 1.285 | 1.481 / 1.912 |
| API wall time | 0.967 / 1.307 | 1.515 / 1.945 |

Paired RRF minus dense API latency was 0.525 / 0.993 seconds p50/p95. Percentiles are descriptive for these 16 paired local observations.

| Interests | Shared / 20 | Dense countries / continents | RRF countries / continents | Dense top three | RRF top three |
|---|---:|---:|---:|---|---|
| history, architecture, museums, food | 9 | 16 / 3 | 16 / 4 | Akureyri, Siem Reap, Hōfu | Istanbul, London, Münster |
| beaches, nightlife | 7 | 18 / 5 | 16 / 6 | Netanya, Varna, Hurghada | Hurghada, Netanya, San Diego |
| mountains, hiking, quiet | 9 | 14 / 5 | 14 / 5 | Requena, Blanes, Chandigarh | Cape Town, Mianyang, Mount Hagen |
| traditional culture, photography | 8 | 8 / 4 | 12 / 5 | Takayama, Nikaho, Carei | Kumasi, Ibi, Ma’anshan |
| food markets, urban exploration | 9 | 14 / 4 | 16 / 5 | Kalugumalai, Delhi, Hoogeveen | London, Mexico City, Delhi |
| museums, classical music | 11 | 13 / 2 | 14 / 3 | Mumbai, Oxford, Vienna | Istanbul, Vienna, Berlin |
| nature, relaxed travel | 10 | 14 / 4 | 15 / 4 | Chandigarh, Hpa-An, Poplar Bluff | Grenoble, Ogbia, Tarragona |
| shopping, modern city life | 7 | 15 / 5 | 11 / 6 | Monopoli, Chanhassen, York | São Paulo, Ma’anshan, York |

Across the eight distinct profiles, dense returned 127 distinct destinations and RRF returned 130. The five most exposed destinations took 11.2% and 11.2% of the respective 160 slots. Duplicate slots across the measured lists were 0 and 0. Full ordered top-20 lists, per-channel candidate counts and timings are in the local ignored artifact `.tmp/architecture-v1/product-ab.json`.

Recommendation for the current direct product path: **RRF mean top-two**. The product-cap check gained 3.13 percentage points in Recall@20 and 0.0349 in nDCG@10. I recommend accepting the 0.525-second paired median API cost for those gains in the direct shortlist. The paired p95 cost was 0.993 seconds across this local sample. The results remain development evidence and the deployed latency budget has yet to be validated. A second-stage ranker is not required by the current flow. Evaluate one separately if a larger candidate pool is introduced and a validated reranker can improve the final shortlist.


## Adopted production trial

Global destination discovery uses BAAI/bge-small-en-v1.5, exact pgvector cosine,
PostgreSQL full-text search and reciprocal rank fusion. Each channel averages the
top two supporting chunk scores for each destination. A destination with one
supporting chunk uses that score. Internal results retain each channel's rank,
aggregate score and contributing chunk IDs and scores, together with the fused
rank, fused score and supporting citations. The API keeps the existing response
schema and maximum of 20 destinations.

The choice reflects the 24 judged development queries above. Human held-out
relevance evaluation remains pending. CrossEncoder and HNSW are disabled for
the trial. Global retrieval runs locally without Geoapify enrichment or an LLM.

The runtime smoke check used the real API and read-only PostgreSQL with outbound
HTTP blocked. After warm-up, all 16 requests for 20 results returned
20 unique destinations. Explicit and default eight-result requests returned
the same shortlist. Warm API latency measured 2.446 s p50 and
2.724 s p95, up from 1.515 s and 1.945 s in the assessment. The two
runs do not establish which part of the system caused the increase.
