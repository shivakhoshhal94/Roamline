"""CPU-local Sentence Transformers; model files are explicitly cached outside Git."""
from datetime import datetime, timezone
from pathlib import Path
import time
import numpy as np
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from app.knowledge.models import KnowledgeChunk, KnowledgeEmbedding

MODELS = ("BAAI/bge-small-en-v1.5", "intfloat/e5-small-v2")
RERANKER = "cross-encoder/ms-marco-MiniLM-L6-v2"
MODEL_REVISIONS = {
    "BAAI/bge-small-en-v1.5": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
    "intfloat/e5-small-v2": "ffb93f3bd4047442299a41ebb6fa998a38507c52",
    RERANKER: "233902d25c440f23af6f7d6e94d2946bac0bee0a",
}


def download_models(directory):
    from huggingface_hub import snapshot_download
    import json
    directory.mkdir(parents=True, exist_ok=True)
    registry = {}
    for name, revision in MODEL_REVISIONS.items():
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
    def __init__(self, name, revision, path, threads=4):
        from sentence_transformers import SentenceTransformer
        import torch
        if name not in MODELS:
            raise ValueError("Unsupported 384-dimensional benchmark model")
        if Path(path).resolve().name != revision or len(revision) != 40:
            raise ValueError("Use the pinned Hugging Face snapshot directory matching --revision")
        torch.set_num_threads(threads)
        self.name, self.revision = name, revision
        self.model = SentenceTransformer(str(path), device="cpu", local_files_only=True, trust_remote_code=False)

    def encode(self, texts, query=False):
        if self.name == "intfloat/e5-small-v2":
            texts = [("query: " if query else "passage: ") + text for text in texts]
        elif query:
            texts = ["Represent this sentence for searching relevant passages: " + text for text in texts]
        result = self.model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True)
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


def embed_chunks(factory, encoder, batch_size=64, chunk_ids=None):
    started, total, after = time.perf_counter(), 0, 0
    while True:
        with factory() as session:
            embedded = select(KnowledgeEmbedding.chunk_id).where(KnowledgeEmbedding.model == encoder.name, KnowledgeEmbedding.revision == encoder.revision)
            statement = select(KnowledgeChunk).where(KnowledgeChunk.id > after, ~KnowledgeChunk.id.in_(embedded))
            if chunk_ids is not None:
                statement = statement.where(KnowledgeChunk.id.in_(chunk_ids))
            rows = session.scalars(statement.order_by(KnowledgeChunk.id).limit(batch_size)).all()
        if not rows:
            break
        vectors = encoder.encode([row.text for row in rows])
        with factory.begin() as session:
            for row, vector in zip(rows, vectors, strict=True):
                statement = insert(KnowledgeEmbedding).values(chunk_id=row.id, model=encoder.name, revision=encoder.revision,
                    embedding=vector.tolist(), embedded_at=datetime.now(timezone.utc))
                session.execute(statement.on_conflict_do_nothing())
        after, total = rows[-1].id, total + len(rows)
    return {"embedded_chunks": total, "seconds": time.perf_counter() - started, "dimension": 384}
