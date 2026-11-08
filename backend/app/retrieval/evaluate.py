"""Measured local retrieval benchmark; curated judgments are never rewritten."""
import argparse
from collections import defaultdict
from importlib.metadata import version
from app.db.models import Destination
from app.services.catalog import select_candidates
from hashlib import sha256
import json
import platform
from pathlib import Path
from time import perf_counter
import numpy as np
from ranx import Qrels, Run, evaluate
from sqlalchemy import create_engine, select, func, text, event
from sqlalchemy.orm import sessionmaker
from app.core.config import Settings
from app.knowledge.models import KnowledgePage, KnowledgeChunk, KnowledgeEmbedding
from app.retrieval.embedding import MODELS, RERANKER, LocalEncoder, LocalReranker, embed_chunks
from app.retrieval.service import RetrievalRequest, retrieve, chunk_ranks

METRICS = ["recall@10", "recall@20", "recall@50", "mrr", "ndcg@10"]


def percentiles(values):
    return {"p50": float(np.percentile(values, 50)), "p95": float(np.percentile(values, 95))}


def score_runs(qrels, runs):
    return {key: float(value) for key, value in evaluate(qrels, Run(runs), METRICS, make_comparable=True).items()}


def run_system(factory, queries, qrels, encoder=None, reranker=None, mode="hybrid", candidates=50, aggregation="best", corpus_ids=None):
    runs, latencies, rerank_latencies, counts = {}, [], [], []
    for query in queries:
        request = RetrievalRequest(query=query["query"], top_k=50, candidates=candidates, rerank=reranker is not None)
        before = perf_counter()
        with factory() as session:
            result = retrieve(session, request, encoder, reranker, mode, aggregation=aggregation, corpus_ids=corpus_ids)
        latencies.append(perf_counter()-before)
        rerank_latencies.append(result["reranking_seconds"])
        counts.append(result["candidate_count"])
        runs[query["id"]] = {str(item["geoname_id"]): float(len(result["results"])-rank) for rank, item in enumerate(result["results"])}
    return {"metrics": score_runs(qrels, runs), "query_seconds": percentiles(latencies),
        "reranking_seconds": percentiles(rerank_latencies), "candidate_counts": percentiles(counts), "runs": runs}


def freeze_corpus(factory, path):
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        hashes = [item["hash"] for item in manifest["chunks"]]
        with factory() as session:
            rows = session.execute(select(KnowledgeChunk, KnowledgePage.source_page_id).join(KnowledgePage).where(KnowledgeChunk.content_hash.in_(hashes))).all()
        keyed = {(page_id, chunk.chunk_index, chunk.content_hash): chunk.id for chunk,page_id in rows}
        try:
            return [keyed[(item["page_id"],item["chunk_index"],item["hash"])] for item in manifest["chunks"]], manifest
        except KeyError:
            raise ValueError("Frozen corpus no longer matches the ingested source snapshot") from None
    with factory() as session:
        cities = session.scalars(select(Destination).where(Destination.geoname_id.in_(select(KnowledgePage.destination_id).distinct()))).all()
        selected = select_candidates(cities, min_population=1, max_candidates=256)
        rows = session.execute(select(KnowledgeChunk, KnowledgePage).join(KnowledgePage).where(KnowledgePage.destination_id.in_([city.geoname_id for city in selected]))).all()
    grouped = defaultdict(list)
    for chunk,page in rows:
        grouped[page.destination_id].append((chunk,page))
    priority = {name:index for index,name in enumerate(["See","Do","Eat","Understand","Overview","Drink","Buy","Get around","Stay safe"])}
    chunks = []
    for city in selected:
        values = sorted(grouped[city.geoname_id], key=lambda pair:(priority.get(pair[0].section,99), "/" in pair[1].title, pair[1].title, pair[0].chunk_index))
        chosen, headings = [], set()
        for chunk,page in values:
            if chunk.section not in headings:
                chosen.append((chunk,page));headings.add(chunk.section)
            if len(chosen)==4: break
        for chunk,page in chosen:
            chunks.append({"geoname_id":city.geoname_id,"city":city.name,"country":city.country_code,"continent":city.continent_code,
                "page_id":page.source_page_id,"chunk_index":chunk.chunk_index,"section":chunk.section,"hash":chunk.content_hash})
    manifest={"selection":"Frozen after complete dump ingestion and before final model comparison: existing population/continent/country-round-robin policy over matched cities, max 256 destinations, up to four distinct travel sections each. No qrels or query text used.","chunks":chunks}
    path.write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    keyed = {(page.source_page_id,chunk.chunk_index,chunk.content_hash):chunk.id for chunk,page in rows}
    return [keyed[(item["page_id"],item["chunk_index"],item["hash"])] for item in chunks],manifest


def benchmark(factory, registry, dataset, output):
    corpus_ids, manifest = freeze_corpus(factory, output.parent / "corpus.json")
    raw = dataset.read_bytes()
    queries = json.loads(raw)["queries"]
    qrels = Qrels({query["id"]: {str(item["geoname_id"]): item["grade"] for item in query["relevance"]} for query in queries})
    report = {"dataset_sha256": sha256(raw).hexdigest(), "queries": len(queries), "platform": platform.platform(),
        "cpu_threads": 4, "paid_api_calls": 0, "embedding_api_cost": 0, "reranking_api_cost": 0, "database_cost_local": 0,
        "models": {}, "systems": {}, "bootstrap_selection": manifest["selection"],
        "indexing_note": "Incremental encoding: embedded_chunks counts missing vectors written this run, not a fresh full-corpus index.",
        "packages": {name: version(name) for name in ["sentence-transformers","torch","pgvector","mwxml","mwparserfromhell","ranx"]}, "metric_scope": "Curated project qrels; incomplete judgments, not tourism ground truth"}
    with factory() as session:
        all_lengths = list(session.scalars(select(func.length(KnowledgeChunk.text))))
        report["knowledge_corpus"] = {"pages": session.scalar(select(func.count()).select_from(KnowledgePage)),
            "destinations": session.scalar(select(func.count(func.distinct(KnowledgePage.destination_id)))),
            "chunks": len(all_lengths), "text_length": percentiles(all_lengths),
            "small_under_200": sum(v<200 for v in all_lengths), "large_over_1600": sum(v>1600 for v in all_lengths)}
        lengths = list(session.scalars(select(func.length(KnowledgeChunk.text)).where(KnowledgeChunk.id.in_(corpus_ids))))
        indexed = set(session.scalars(select(KnowledgePage.destination_id).join(KnowledgeChunk).where(KnowledgeChunk.id.in_(corpus_ids))))
        expected = {item["geoname_id"] for query in queries for item in query["relevance"]}
        report["corpus"] = {"pages": session.scalar(select(func.count(func.distinct(KnowledgeChunk.page_id))).where(KnowledgeChunk.id.in_(corpus_ids))),
            "destinations": len(indexed), "chunks": len(lengths), "text_length": percentiles(lengths),
            "small_under_200": sum(value < 200 for value in lengths), "large_over_1600": sum(value > 1600 for value in lengths),
            "countries": session.scalar(select(func.count(func.distinct(KnowledgePage.country_code))).where(KnowledgePage.destination_id.in_(indexed))),
            "continents": {code: count for code, count in session.execute(select(KnowledgePage.continent_code,func.count(func.distinct(KnowledgePage.destination_id))).where(KnowledgePage.destination_id.in_(indexed)).group_by(KnowledgePage.continent_code))},
            "qrel_destinations": len(expected), "qrel_destinations_indexed": len(expected & indexed),
            "unmatched_qrel_ids": sorted(expected-indexed)}
    # First-query startup is measured separately; steady-state percentiles exclude JIT/model load.
    start = perf_counter()
    with factory() as session:
        retrieve(session, RetrievalRequest(query=queries[0]["query"], rerank=False, top_k=50), mode="lexical", corpus_ids=corpus_ids)
    report["lexical_cold_seconds"] = perf_counter()-start
    report["systems"]["lexical"] = run_system(factory, queries, qrels, mode="lexical", corpus_ids=corpus_ids)
    for name in MODELS:
        data = registry[name]
        start = perf_counter()
        encoder = LocalEncoder(name, data["revision"], data["path"])
        load_seconds = perf_counter()-start
        indexing = embed_chunks(factory, encoder, chunk_ids=corpus_ids)
        encoder.encode([queries[0]["query"]], query=True)
        start = perf_counter()
        with factory() as session:
            retrieve(session, RetrievalRequest(query=queries[0]["query"], rerank=False, top_k=50), encoder, corpus_ids=corpus_ids)
        cold = perf_counter()-start
        dense = run_system(factory, queries, qrels, encoder, mode="dense", corpus_ids=corpus_ids)
        hybrid = run_system(factory, queries, qrels, encoder, mode="hybrid", corpus_ids=corpus_ids)
        report["models"][name] = {"revision": data["revision"], "dimension": 384, "download_bytes": data["bytes"],
            "model_load_seconds": load_seconds, "indexing": indexing, "cold_hybrid_seconds": cold,
            "vector_payload_bytes": len(lengths)*384*4, "dense": dense, "hybrid": hybrid}
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(name, "dense", dense["metrics"], "hybrid", hybrid["metrics"], flush=True)
        del encoder
    selected = max(MODELS, key=lambda name: (report["models"][name]["hybrid"]["metrics"]["ndcg@10"], -report["models"][name]["hybrid"]["query_seconds"]["p50"]))
    report["selected_model"] = selected
    data = registry[selected]
    encoder = LocalEncoder(selected, data["revision"], data["path"])
    report["systems"]["dense"] = report["models"][selected]["dense"]
    report["systems"]["hybrid"] = report["models"][selected]["hybrid"]
    reranker_data = registry[RERANKER]
    reranker = LocalReranker(reranker_data["path"], reranker_data["revision"])
    reranker.score(queries[0]["query"], ["A historic city with museums and architecture."])
    report["reranker"] = {"model": RERANKER, "revision": reranker_data["revision"], "download_bytes": reranker_data["bytes"], "max_tokens": 512}
    for size in [50, 100]:
        result = run_system(factory, queries, qrels, encoder, reranker, candidates=size, corpus_ids=corpus_ids)
        report["systems"][f"hybrid_reranker_{size}"] = result
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("rerank", size, result["metrics"], flush=True)
    report["top3mean_aggregation"] = run_system(factory, queries, qrels, encoder, mode="hybrid", aggregation="top3mean", corpus_ids=corpus_ids)
    report["ann"] = benchmark_ann(factory, queries[:8], encoder, corpus_ids)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_report(report, output.with_suffix(".md"))
    return report


def benchmark_ann(factory, queries, encoder, corpus_ids):
    # An experimental partial index avoids mixing embedding model spaces. It is
    # removed after measurement; exact search remains the production baseline.
    from sqlalchemy import Index
    index = Index("ix_knowledge_ann_experiment", KnowledgeEmbedding.embedding,
        postgresql_using="hnsw", postgresql_ops={"embedding":"vector_cosine_ops"},
        postgresql_where=(KnowledgeEmbedding.model == encoder.name) & (KnowledgeEmbedding.revision == encoder.revision))
    bind = factory.kw["bind"]
    before = perf_counter()
    with bind.begin() as connection:
        index.create(connection)
        connection.execute(text("ANALYZE knowledge_embeddings"))
    build = perf_counter()-before
    exact_times, ann_times, plan_nodes = [], [], []
    ann_qrels, ann_runs = {}, {}
    captured = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT") and "knowledge_embeddings" in statement:
            captured.append((statement, parameters))
    event.listen(bind, "before_cursor_execute", capture)
    try:
        for query in queries:
            vector = encoder.encode([query["query"]], query=True)[0].tolist()
            request = RetrievalRequest(query=query["query"], rerank=False)
            with factory() as session:
                before = perf_counter()
                exact = chunk_ranks(session, request, encoder, vector, exact=True, limit=100, corpus_ids=corpus_ids)
                exact_times.append(perf_counter()-before)
            with factory() as session:
                # This small filtered cohort otherwise favors primary-key scans.
                # Force the experimental ANN plan, then confirm it with EXPLAIN.
                session.execute(text("SET LOCAL enable_seqscan = off"))
                session.execute(text("SET LOCAL enable_bitmapscan = off"))
                session.execute(text("SET LOCAL enable_sort = off"))
                session.execute(text("SET LOCAL hnsw.ef_search = 200"))
                before = perf_counter()
                approximate = chunk_ranks(session, request, encoder, vector, exact=False, limit=100, corpus_ids=corpus_ids)
                ann_times.append(perf_counter()-before)
                if not plan_nodes:
                    sql, parameters = captured[-1]
                    plan = session.connection().exec_driver_sql("EXPLAIN (FORMAT JSON) " + sql, parameters).scalar()[0]["Plan"]
                    def walk(node):
                        plan_nodes.append({"type":node["Node Type"],"index":node.get("Index Name")})
                        for child in node.get("Plans", []): walk(child)
                    walk(plan)
            ann_qrels[query["id"]] = {str(row[0].id): 1 for row in exact}
            ann_runs[query["id"]] = {str(row[0].id): float(len(approximate)-rank) for rank,row in enumerate(approximate)}
    finally:
        event.remove(bind, "before_cursor_execute", capture)
        with bind.begin() as connection:
            index.drop(connection)
        KnowledgeEmbedding.__table__.indexes.discard(index)
    return {"build_seconds": build, "exact_seconds": percentiles(exact_times), "hnsw_seconds": percentiles(ann_times),
        "recall_at_100_mean": float(evaluate(Qrels(ann_qrels), Run(ann_runs), "recall@100", make_comparable=True)), "plan_nodes": plan_nodes,
        "hnsw_index_used": any(node["index"] == "ix_knowledge_ann_experiment" for node in plan_nodes), "queries": len(queries), "ef_search": 200,
        "decision": "Experimental index removed; exact remains default pending representative scale/filter validation."}


def write_report(report, path):
    corpus = report["corpus"]
    knowledge = report["knowledge_corpus"]
    lines = ["# Worldwide retrieval benchmark", "", report["metric_scope"], "",
        f"Queries: {report['queries']}. Full ingestion: {knowledge['pages']:,} pages, {knowledge['destinations']:,} destinations, {knowledge['chunks']:,} chunks.",
        f"**Measured bootstrap index:** {corpus['destinations']} destinations, {corpus['chunks']} chunks, {corpus['countries']} countries. Only {corpus['qrel_destinations_indexed']}/{corpus['qrel_destinations']} judged destinations are present; missing destinations remain in metric denominators.",
        f"Selected embedding: `{report['selected_model']}` (highest measured hybrid nDCG@10; latency breaks ties).", "",
        "| System | Recall@10 | Recall@20 | Recall@50 | MRR | nDCG@10 | p50 s | p95 s |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, result in report["systems"].items():
        m = result["metrics"]
        t = result["query_seconds"]
        lines.append(f"| {name} | {m['recall@10']:.4f} | {m['recall@20']:.4f} | {m['recall@50']:.4f} | {m['mrr']:.4f} | {m['ndcg@10']:.4f} | {t['p50']:.4f} | {t['p95']:.4f} |")
    lines += ["", "## Embedding comparison", "",
        "| Model | Dense nDCG@10 | Hybrid nDCG@10 | Hybrid p50/p95 s | Download bytes | Vector payload bytes |",
        "|---|---:|---:|---|---:|---:|"]
    for name, result in report["models"].items():
        t = result["hybrid"]["query_seconds"]
        lines.append(f"| {name} | {result['dense']['metrics']['ndcg@10']:.4f} | {result['hybrid']['metrics']['ndcg@10']:.4f} | {t['p50']:.4f}/{t['p95']:.4f} | {result['download_bytes']} | {result['vector_payload_bytes']} |")
    lines += ["", "BGE wins the hybrid comparison narrowly; E5 has higher dense-only nDCG. Hybrid improves MRR/nDCG over BGE dense retrieval but lowers Recall@50. Neither reranker setting beats hybrid nDCG@10, so reranking remains optional rather than a recommended default.",
        "", "## Aggregation and reranking", "",
        "Best-chunk aggregation is the baseline. Alternative top-three-mean hybrid metrics: `" + json.dumps(report["top3mean_aggregation"]["metrics"]) + "`."]
    for name, result in report["systems"].items():
        if "reranker" in name:
            lines.append(f"{name}: reranking-only seconds `{json.dumps(result['reranking_seconds'])}`; candidate counts `{json.dumps(result['candidate_counts'])}`.")
    lines += ["", "## Measurement limits", "",
        "CPU-local, four inference threads on a shared development machine, not an isolated performance lab. Warm percentiles exclude model/JIT startup, reported separately in JSON. Indexing is incremental: embedded_chunks and seconds describe missing embeddings written by that run, not a fresh full-corpus index. Model revisions, startup, vector storage, package versions and all per-query rankings are in results.json.",
        "", "The corpus manifest is query-independent and identical across systems. Qrels were frozen before evaluation and include missing-corpus destinations in the denominator. No geographic filter was applied. This is a development benchmark, not an untouched test set or comparative tourism-quality assessment. Full ingestion does not mean full transformer indexing. See ingestion.json for source checksums and idempotency measurements.",
        "", "HNSW: `" + json.dumps(report["ann"]) + "`", "",
        "Paid API cost: 0. No OpenAI or Geoapify calls. No text generation."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--registry",type=Path,default=Path(".tmp/models/registry.json"))
    parser.add_argument("--dataset",type=Path,default=Path("evaluation/retrieval/queries.json"))
    parser.add_argument("--output",type=Path,default=Path("evaluation/retrieval/results.json"))
    args=parser.parse_args()
    import numba
    numba.set_num_threads(4)
    engine=create_engine(str(Settings().database_url))
    try:
        benchmark(sessionmaker(engine,expire_on_commit=False),json.loads(args.registry.read_text()),args.dataset,args.output)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
