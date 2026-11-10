"""CPU-local Sentence Transformers; model files are explicitly cached outside Git."""
from datetime import datetime, timezone
from pathlib import Path
import time
import numpy as np
from sqlalchemy import select, func, exists
from sqlalchemy.dialects.postgresql import insert
from app.knowledge.models import KnowledgeChunk, KnowledgeEmbedding

MODELS = ("BAAI/bge-small-en-v1.5", "intfloat/e5-small-v2")
RERANKER = "cross-encoder/ms-marco-MiniLM-L6-v2"
SMALL_RERANKER = "cross-encoder/ms-marco-TinyBERT-L2-v2"
MODEL_REVISIONS = {
    "BAAI/bge-small-en-v1.5": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
    "intfloat/e5-small-v2": "ffb93f3bd4047442299a41ebb6fa998a38507c52",
    RERANKER: "233902d25c440f23af6f7d6e94d2946bac0bee0a",
    SMALL_RERANKER: "81d1926f67cb8eee2c2be17ca9f793c7c3bd20cc",
}


def download_models(directory, include_small_reranker=False):
    from huggingface_hub import snapshot_download
    import json
    directory.mkdir(parents=True, exist_ok=True)
    registry = {}
    for name, revision in MODEL_REVISIONS.items():
        if name == SMALL_RERANKER and not include_small_reranker:
            continue
        started = time.perf_counter()
        path = snapshot_download(name, revision=revision, cache_dir=str(directory), token=False,
            allow_patterns=["*.json", "*.txt", "*.safetensors", "1_Pooling/*", "README.md", "LICENSE*"],
            ignore_patterns=["onnx/*", "openvino/*"], max_workers=2)
        registry[name] = {"revision": revision, "path": path,
            "bytes": sum(file.stat().st_size for file in Path(path).rglob("*") if file.is_file()),
            "download_seconds": time.perf_counter()-started}
    (directory / "registry.json").write_text(json.dumps(registry, indent=2), encoding="utf-8")
    return registry


class LocalEncoder:
    def __init__(self, name, revision, path, threads=4, inference_batch_size=64):
        from sentence_transformers import SentenceTransformer
        import torch
        if name not in MODELS:
            raise ValueError("Unsupported 384-dimensional benchmark model")
        if Path(path).resolve().name != revision or len(revision) != 40:
            raise ValueError("Use the pinned Hugging Face snapshot directory matching --revision")
        if not 1 <= threads <= 128:
            raise ValueError("threads must be 1..128")
        torch.set_num_threads(threads)
        if not 1 <= inference_batch_size <= 128:
            raise ValueError("inference_batch_size must be 1..128")
        self.inference_batch_size = inference_batch_size
        self.name, self.revision = name, revision
        self.model = SentenceTransformer(str(path), device="cpu", local_files_only=True, trust_remote_code=False)

    def encode(self, texts, query=False):
        if self.name == "intfloat/e5-small-v2":
            texts = [("query: " if query else "passage: ") + text for text in texts]
        elif query:
            texts = ["Represent this sentence for searching relevant passages: " + text for text in texts]
        result = self.model.encode(texts, batch_size=getattr(self, "inference_batch_size", 64), normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True)
        if result.shape != (len(texts), 384) or not np.isfinite(result).all():
            raise ValueError("Invalid embedding output")
        return result


class LocalReranker:
    def __init__(self, path, revision):
        from sentence_transformers import CrossEncoder
        if Path(path).resolve().name != revision or len(revision) != 40:
            raise ValueError("Use the pinned reranker snapshot directory matching its revision")
        self.revision = revision
        self.model = CrossEncoder(str(path), device="cpu", local_files_only=True, trust_remote_code=False, max_length=512)

    def score(self, query, documents):
        values = np.asarray(self.model.predict([(query, text) for text in documents], batch_size=32, show_progress_bar=False)).reshape(-1)
        if len(values) != len(documents) or not np.isfinite(values).all():
            raise ValueError("Invalid reranker output")
        return values.tolist()


def embedding_status(factory, model, revision, chunk_ids=None):
    with factory() as session:
        valid = exists().where(KnowledgeEmbedding.chunk_id == KnowledgeChunk.id,
            KnowledgeEmbedding.model == model, KnowledgeEmbedding.revision == revision,
            KnowledgeEmbedding.content_hash == KnowledgeChunk.content_hash)
        scope = select(KnowledgeChunk.id)
        if chunk_ids is not None:
            scope = scope.where(KnowledgeChunk.id.in_(chunk_ids))
        total = session.scalar(select(func.count()).select_from(scope.subquery()))
        done = session.scalar(select(func.count()).select_from(scope.where(valid).subquery()))
    return {"total_chunks": total, "already_embedded": done, "remaining": total-done,
        "vector_payload_bytes": done*384*4, "model": model, "revision": revision}


def embed_chunks(factory, encoder, batch_size=64, chunk_ids=None, force=False, progress=None):
    if not 1 <= batch_size <= 512:
        raise ValueError("batch_size must be between 1 and 512")
    started, total, after = time.perf_counter(), 0, 0
    initial = embedding_status(factory, encoder.name, encoder.revision, chunk_ids)
    if progress:
        progress(dict(initial, newly_embedded=0, seconds=0))
    while True:
        with factory() as session:
            valid = exists().where(KnowledgeEmbedding.chunk_id == KnowledgeChunk.id,
                KnowledgeEmbedding.model == encoder.name, KnowledgeEmbedding.revision == encoder.revision,
                KnowledgeEmbedding.content_hash == KnowledgeChunk.content_hash)
            statement = select(KnowledgeChunk).where(KnowledgeChunk.id > after)
            if not force:
                statement = statement.where(~valid)
            if chunk_ids is not None:
                statement = statement.where(KnowledgeChunk.id.in_(chunk_ids))
            rows = session.scalars(statement.order_by(KnowledgeChunk.id).limit(batch_size)).all()
        if not rows:
            break
        # An inference/DB failure propagates; earlier committed batches survive.
        try:
            vectors = encoder.encode([row.text for row in rows])
        except Exception as error:
            if progress:
                progress(dict(initial, newly_embedded=total, failed_batch_chunks=len(rows),
                    exception_type=type(error).__name__, seconds=time.perf_counter()-started))
            raise
        with factory.begin() as session:
            current = {row.id: row.content_hash for row in session.scalars(
                select(KnowledgeChunk).where(KnowledgeChunk.id.in_([row.id for row in rows])).with_for_update())}
            values = [dict(chunk_id=row.id, model=encoder.name, revision=encoder.revision,
                content_hash=row.content_hash, embedding=vector.tolist(), embedded_at=datetime.now(timezone.utc))
                for row, vector in zip(rows, vectors, strict=True) if current.get(row.id) == row.content_hash]
            if values:
                statement = insert(KnowledgeEmbedding).values(values)
                session.execute(statement.on_conflict_do_update(
                    index_elements=["chunk_id", "model", "revision"],
                    set_={key: getattr(statement.excluded, key) for key in ["content_hash", "embedding", "embedded_at"]}))
        after, total = rows[-1].id, total + len(values)
        if progress:
            progress(dict(initial, newly_embedded=total, estimated_remaining=max(0,initial["remaining"]-total), seconds=time.perf_counter()-started))
    seconds = time.perf_counter()-started
    return dict(initial, embedded_chunks=total, newly_embedded=total,
        skipped_unchanged=0 if force else initial["already_embedded"], failed=0, seconds=seconds,
        chunks_per_second=total/seconds if seconds else 0, dimension=384,
        final_status=embedding_status(factory, encoder.name, encoder.revision, chunk_ids))
