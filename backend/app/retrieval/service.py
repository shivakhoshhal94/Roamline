"""Worldwide chunk retrieval, destination-level RRF and bounded cross-encoding."""
from time import perf_counter
from itertools import zip_longest
import math
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, func, text, cast, Text
from app.db.models import Destination
from app.knowledge.models import KnowledgePage, KnowledgeChunk, KnowledgeEmbedding


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination_id: int | None = Field(default=None, gt=0)
    query: str = Field(min_length=3, max_length=1500)
    countries: list[str] = []
    continents: list[str] = []
    min_population: int = Field(default=0, ge=0)
    top_k: int = Field(default=10, ge=1, le=150)
    candidates: int = Field(default=50, ge=1, le=150)
    rerank: bool = True

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Query must not be blank")
        return value.strip()

    @field_validator("countries", "continents")
    @classmethod
    def geographic_codes(cls, values, info):
        values = sorted(set(value.upper() for value in values))
        if any(len(value) != 2 or not value.isalpha() for value in values):
            raise ValueError("Use two-letter geographic codes")
        if info.field_name == "continents" and not set(values) <= {"AF", "AS", "EU", "NA", "OC", "SA", "AN"}:
            raise ValueError("Unknown continent")
        return values


def filtered(statement, request):
    statement = statement.where(Destination.population >= request.min_population)
    if request.destination_id is not None:
        statement = statement.where(Destination.geoname_id == request.destination_id)
    if request.countries:
        statement = statement.where(Destination.country_code.in_(request.countries))
    if request.continents:
        statement = statement.where(Destination.continent_code.in_(request.continents))
    return statement


def chunk_ranks(session, request, encoder=None, query_vector=None, mode="dense", exact=True, limit=500, corpus_ids=None):
    # Rank narrow IDs first; sorting full guide and operational destination rows
    # across the corpus needlessly spills large payloads to disk. Hydrate only hits.
    statement = select(KnowledgeChunk.id.label("hit_id")).join(KnowledgePage, KnowledgePage.id == KnowledgeChunk.page_id).join(Destination, Destination.geoname_id == KnowledgePage.destination_id)
    if mode == "dense":
        if encoder is None:
            raise ValueError("Dense retrieval requires a versioned encoder")
        if query_vector is None:
            query_vector = encoder.encode([request.query], query=True)[0].tolist()
        distance = KnowledgeEmbedding.embedding.cosine_distance(query_vector)
        statement = statement.join(KnowledgeEmbedding, KnowledgeEmbedding.chunk_id == KnowledgeChunk.id).where(
            KnowledgeEmbedding.model == encoder.name, KnowledgeEmbedding.revision == encoder.revision,
            KnowledgeEmbedding.content_hash == KnowledgeChunk.content_hash)
        statement = statement.add_columns((1 - distance).label("score"))
        # Exact remains correct even when an experimental ANN index exists.
        if exact:
            session.execute(text("SET LOCAL enable_indexscan = off"))
        statement = statement.order_by(distance, KnowledgeChunk.id) if exact else statement.order_by(distance)
    elif mode == "lexical":
        session.execute(text("SET LOCAL enable_indexscan = on"))
        # PostgreSQL handles stemming/stopwords. OR the parsed lexemes so a
        # multi-interest query can match a relevant section about one interest.
        parsed = func.plainto_tsquery("english", request.query)
        terms = func.to_tsquery("english", func.replace(cast(parsed, Text), " & ", " | "))
        score = func.ts_rank_cd(KnowledgeChunk.search_vector, terms)
        statement = statement.add_columns(score.label("score")).where(KnowledgeChunk.search_vector.op("@@")(terms)).order_by(score.desc(), KnowledgeChunk.id)
    else:
        raise ValueError("Unknown retrieval mode")
    if corpus_ids is not None:
        statement = statement.where(KnowledgeChunk.id.in_(corpus_ids))
    hits = filtered(statement, request).limit(limit).subquery()
    hydrated = select(KnowledgeChunk, KnowledgePage, Destination, hits.c.score).join(
        hits, hits.c.hit_id == KnowledgeChunk.id).join(
        KnowledgePage, KnowledgePage.id == KnowledgeChunk.page_id).join(
        Destination, Destination.geoname_id == KnowledgePage.destination_id).order_by(
        hits.c.score.desc(), KnowledgeChunk.id)
    return session.execute(hydrated).all()


def destination_ranks(rows, policy="best"):
    if policy not in {"best", "top2mean", "top3mean"}:
        raise ValueError("Unknown aggregation policy")
    # Best-ranked chunk establishes each city's rank; at most three supporting
    # chunks are retained. Long district-rich cities do not gain votes per chunk.
    cities = {}
    for chunk, page, city, score in rows:
        identifier = str(city.geoname_id)
        if identifier not in cities:
            cities[identifier] = {"geoname_id": city.geoname_id, "city": city.name, "country": city.country_name,
                "country_code": city.country_code, "continent": city.continent_code, "score": float(score), "evidence": []}
        if len(cities[identifier]["evidence"]) < 3:
            cities[identifier]["evidence"].append({"chunk_id": chunk.id, "section": chunk.section, "snippet": chunk.text[:600],
                "text": chunk.text, "chunk_score": float(score), "source": page.source, "title": page.title, "page_id": page.source_page_id,
                "revision_id": page.revision_id, "url": page.source_url, "license": page.license,
                "content_hash": chunk.content_hash, "ingested_at": page.ingested_at.isoformat()})
    if policy in {"top2mean", "top3mean"}:
        for item in cities.values():
            item["score"] = sum(e["chunk_score"] for e in item["evidence"][:int(policy[3])])/len(item["evidence"][:int(policy[3])])
        cities = dict(sorted(cities.items(), key=lambda pair: (-pair[1]["score"], int(pair[0]))))
    return cities


def fuse_destinations(dense, lexical):
    from ranx import Run
    from ranx.fusion import rrf
    inputs = []
    for ranking in [dense, lexical]:
        if ranking:
            # Ordinal scores make exact tie handling and input ranks explicit.
            inputs.append(Run({"q": {key: float(len(ranking)-rank) for rank, key in enumerate(ranking)}}))
    if not inputs:
        return {}
    return dict(rrf(inputs, k=60).to_dict()["q"])


def retrieve(session, request, encoder=None, reranker=None, mode="hybrid", query_vector=None, exact=True, aggregation="best", corpus_ids=None, dense_k=None, lexical_k=None, rerank_limit=None, chunk_limit=None):
    if request.candidates < request.top_k:
        raise ValueError("candidates must be at least top_k")
    if mode not in {"lexical", "dense", "hybrid", "union", "high_recall_union"}:
        raise ValueError("Unknown retrieval mode")
    started = perf_counter()
    timings = {key: 0.0 for key in ["query_embedding", "dense_database", "lexical_database", "aggregation", "fusion", "reranker"]}
    dense_rows, lexical_rows = [], []
    chunk_limit = max(500, request.candidates*20) if chunk_limit is None else chunk_limit
    if not 1 <= chunk_limit <= 10000:
        raise ValueError("Chunk candidate limit must be 1..10000")
    if mode != "lexical":
        before = perf_counter()
        if encoder is None:
            raise ValueError("Dense retrieval requires a versioned encoder")
        if query_vector is None:
            query_vector = encoder.encode([request.query], query=True)[0].tolist()
        timings["query_embedding"] = perf_counter()-before
        before = perf_counter()
        dense_rows = chunk_ranks(session, request, encoder, query_vector, "dense", exact, chunk_limit, corpus_ids)
        timings["dense_database"] = perf_counter()-before
    if mode != "dense":
        before = perf_counter()
        lexical_rows = chunk_ranks(session, request, mode="lexical", limit=chunk_limit, corpus_ids=corpus_ids)
        timings["lexical_database"] = perf_counter()-before
    before = perf_counter()
    dense = destination_ranks(dense_rows, aggregation)
    lexical = destination_ranks(lexical_rows, aggregation)
    if mode == "high_recall_union":
        dense_k, lexical_k = 100, 50
    if dense_k is not None:
        dense = dict(list(dense.items())[:dense_k])
    if lexical_k is not None:
        lexical = dict(list(lexical.items())[:lexical_k])
    timings["aggregation"] = perf_counter()-before
    before = perf_counter()
    if mode == "hybrid":
        scores = fuse_destinations(dense, lexical)
    elif mode in {"union", "high_recall_union"}:
        # Rank-interleaved set union: no incomparable dense/FTS score weighting.
        order = dict.fromkeys(key for pair in zip_longest(dense, lexical) for key in pair if key is not None)
        scores = {key: float(len(order)-rank) for rank, key in enumerate(order)}
    else:
        channel = dense if mode == "dense" else lexical
        scores = {key: item["score"] for key, item in channel.items()}
    timings["fusion"] = perf_counter()-before
    dense_order, lexical_order = list(dense), list(lexical)
    keys = sorted(scores, key=lambda key: (-scores[key], int(key)))[:request.candidates]
    results = []
    for key in keys:
        base = dict(dense.get(key) or lexical[key])
        evidence = {}
        for pair in zip_longest(dense.get(key, {}).get("evidence", []), lexical.get(key, {}).get("evidence", [])):
            for item in pair:
                if item is not None:
                    evidence.setdefault(item["chunk_id"], item)
        base.update(retrieval_score=float(scores[key]), dense_rank=dense_order.index(key)+1 if key in dense else None,
            dense_score=dense[key]["score"] if key in dense else None,
            lexical_rank=lexical_order.index(key)+1 if key in lexical else None,
            lexical_score=lexical[key]["score"] if key in lexical else None,
            fused_rank=len(results)+1, evidence=list(evidence.values())[:3], reranker_score=None)
        if aggregation == "top2mean":
            # Keep each channel's score inputs even when the API merges its citations.
            base["aggregation_evidence"] = {
                channel: [
                    {"chunk_id": item["chunk_id"], "chunk_score": item["chunk_score"]}
                    for item in ranking[key]["evidence"][:2]
                ]
                for channel, ranking in (("dense", dense), ("lexical", lexical))
                if key in ranking
            }
        results.append(base)
    retrieval_seconds = perf_counter()-started
    reranking_seconds = 0.0
    if request.rerank:
        if reranker is None:
            raise ValueError("Reranking requested without a local reranker")
        before = perf_counter()
        limit = max(1, len(results)) if rerank_limit is None else rerank_limit
        if not 1 <= limit <= 100:
            raise ValueError("Reranker candidate limit must be 1..100")
        selected, tail = results[:limit], results[limit:]
        pairs = ["\n".join(item["text"][:600] for item in result["evidence"]) for result in selected]
        values = reranker.score(request.query, pairs) if pairs else []
        if len(values) != len(selected) or any(not math.isfinite(float(value)) for value in values):
            raise ValueError("Invalid reranker output")
        for result, value in zip(selected, values, strict=True):
            result["reranker_score"] = float(value)
        selected.sort(key=lambda item: (-item["reranker_score"], -item["retrieval_score"], item["geoname_id"]))
        results = selected + tail
        reranking_seconds = perf_counter()-before
        timings["reranker"] = reranking_seconds
    for rank, result in enumerate(results, 1):
        result["rank"] = rank
        result.pop("score", None)
        for evidence in result["evidence"]:
            evidence.pop("text", None)
    timings["total"] = perf_counter()-started
    timings["other"] = max(0.0, timings["total"]-sum(value for key,value in timings.items() if key!="total"))
    return {"timings": timings, "chunk_hits": {"dense": len(dense_rows), "lexical": len(lexical_rows)},
            "channel_destination_counts": {"dense": len(dense), "lexical": len(lexical)},
            "union_destination_count": len(set(dense) | set(lexical)), "results": results[:request.top_k], "candidate_count": len(results), "retrieval_seconds": retrieval_seconds,
            "reranking_seconds": reranking_seconds, "total_seconds": perf_counter()-started,
            "scope": "worldwide" if not request.countries and not request.continents else "explicit_geographic_filter",
            "score_type": "retrieval relevance; separate from product recommendation score",
            "embedding_model": {"name": encoder.name, "revision": encoder.revision} if encoder is not None else None,
            "reranker_revision": getattr(reranker, "revision", None),
            "corpus_note": "Available knowledge/index coverage only; no automatic Geoapify enrichment."}
