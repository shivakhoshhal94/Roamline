"""Explicit offline knowledge operations and internal retrieval debugging."""
import argparse
from datetime import datetime
from pathlib import Path
import hashlib
import json
import sys
import httpx
from sqlalchemy import select, func
from sqlalchemy.orm import sessionmaker
from app.core.config import Settings
from app.db.connection import create_db_engine


def download_dump(directory, date):
    datetime.strptime(date, "%Y%m%d")
    directory.mkdir(parents=True, exist_ok=True)
    name = f"enwikivoyage-{date}-pages-articles.xml.bz2"
    base = f"https://dumps.wikimedia.org/enwikivoyage/{date}/"
    target = directory / name
    with httpx.Client(timeout=httpx.Timeout(120, connect=30), follow_redirects=True,
                      headers={"User-Agent": "Roamline/0.1 (https://github.com/shivakhoshhal94/Roamline; local research)"}) as client:
        response = client.get(base + f"enwikivoyage-{date}-sha1sums.txt")
        response.raise_for_status()
        matches = [line.split()[0] for line in response.text.splitlines() if line.split() and line.split()[-1] == name]
        if len(matches) != 1:
            raise ValueError("Official dump checksum unavailable")
        if not target.exists():
            partial = target.with_suffix(".partial")
            try:
                with client.stream("GET", base + name) as response:
                    response.raise_for_status()
                    size = 0
                    with partial.open("wb") as output:
                        for block in response.iter_bytes(1024*1024):
                            size += len(block)
                            if size > 1024*1024*1024:
                                raise ValueError("Dump exceeds 1 GiB download ceiling")
                            output.write(block)
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)
    with target.open("rb") as source:
        if hashlib.file_digest(source, "sha1").hexdigest() != matches[0]:
            raise ValueError("Official dump checksum mismatch")
    from app.knowledge.ingest import digest_file
    manifest = {"source_url": base+name, "sha1": matches[0], "sha256": digest_file(target), "bytes": target.stat().st_size}
    target.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Worldwide travel knowledge and retrieval (no Geoapify calls)")
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download-wikivoyage")
    download.add_argument("--date", required=True, help="Immutable official dump date YYYYMMDD")
    download.add_argument("--directory", type=Path, default=Path(".tmp/wikimedia"))
    models = commands.add_parser("download-models")
    models.add_argument("--include-small-reranker", action="store_true")
    models.add_argument("--directory", type=Path, default=Path(".tmp/models"))
    ingest = commands.add_parser("ingest-wikivoyage")
    ingest.add_argument("--dump", type=Path, required=True)
    ingest.add_argument("--max-pages", type=int)
    commands.add_parser("stats")
    embed = commands.add_parser("embed")
    embed.add_argument("--batch-size", type=int, default=64)
    embed.add_argument("--threads", type=int, default=4)
    embed.add_argument("--inference-batch-size", type=int, default=64)
    embed.add_argument("--force", action="store_true", help="Atomically replace batches for this explicit model revision")
    status = commands.add_parser("embedding-status")
    status.add_argument("--model", default="BAAI/bge-small-en-v1.5")
    status.add_argument("--revision", required=True)
    search = commands.add_parser("search")
    for command in [embed, search]:
        command.add_argument("--model", default="BAAI/bge-small-en-v1.5")
        command.add_argument("--revision")
        command.add_argument("--model-path", type=Path, help="Explicit local snapshot path; no automatic download")
    search.add_argument("query")
    search.add_argument("--country", action="append", default=[])
    search.add_argument("--continent", action="append", default=[])
    search.add_argument("--min-population", type=int, default=0)
    search.add_argument("--top-k", type=int, default=10)
    search.add_argument("--candidates", type=int, default=50)
    search.add_argument("--mode", choices=["lexical", "dense", "hybrid", "union", "high_recall_union"], default="hybrid")
    search.add_argument("--reranker-path", type=Path)
    search.add_argument("--reranker-revision")
    args = parser.parse_args()
    if args.command == "download-wikivoyage":
        print(json.dumps(download_dump(args.directory, args.date), indent=2))
        return
    if args.command == "download-models":
        from app.retrieval.embedding import download_models
        print(json.dumps(download_models(args.directory, include_small_reranker=args.include_small_reranker), indent=2))
        return
    if args.command in {"embed", "search"} and not (args.command == "search" and args.mode == "lexical"):
        if not args.revision or not args.model_path:
            parser.error("Dense/hybrid retrieval and embedding require --revision and --model-path")
    if args.command == "search" and args.reranker_path and not args.reranker_revision:
        parser.error("--reranker-path requires --reranker-revision")
    settings = Settings()
    engine = create_db_engine(settings)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        if args.command == "ingest-wikivoyage":
            from app.knowledge.ingest import ingest_dump
            if args.max_pages is not None and args.max_pages < 1:
                parser.error("--max-pages must be positive")
            result = ingest_dump(factory, args.dump, args.max_pages)
        elif args.command == "embedding-status":
            from app.retrieval.embedding import embedding_status
            result = embedding_status(factory, args.model, args.revision)
        elif args.command == "stats":
            from app.knowledge.models import KnowledgeChunk, KnowledgePage, KnowledgeEmbedding
            with factory() as session:
                result = {"pages": session.scalar(select(func.count()).select_from(KnowledgePage)),
                    "destinations": session.scalar(select(func.count(func.distinct(KnowledgePage.destination_id)))),
                    "chunks": session.scalar(select(func.count()).select_from(KnowledgeChunk)),
                    "embeddings": session.scalar(select(func.count()).select_from(KnowledgeEmbedding))}
        else:
            from app.retrieval.embedding import LocalEncoder, LocalReranker, embed_chunks
            encoder = None if args.command == "search" and args.mode == "lexical" else LocalEncoder(args.model, args.revision, args.model_path,
                **({"threads":args.threads,"inference_batch_size":args.inference_batch_size} if args.command=="embed" else {}))
            if args.command == "embed":
                result = embed_chunks(factory, encoder, batch_size=args.batch_size, force=args.force,
                    progress=lambda value: print(json.dumps(value), file=sys.stderr, flush=True))
            else:
                from app.retrieval.service import retrieve, RetrievalRequest
                reranker = LocalReranker(args.reranker_path, args.reranker_revision) if args.reranker_path else None
                request = RetrievalRequest(query=args.query, countries=args.country, continents=args.continent,
                    min_population=args.min_population, top_k=args.top_k, candidates=args.candidates, rerank=reranker is not None)
                with factory() as session:
                    result = retrieve(session, request, encoder, reranker, args.mode)
        print(json.dumps(result, indent=2, ensure_ascii=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
