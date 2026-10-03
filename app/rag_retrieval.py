"""Optional CPU embedding + FAISS experiment. Never used by the default Store path."""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from .models import KnowledgeDocument

ROOT = Path(__file__).resolve().parents[1]
MODEL = "BAAI/bge-small-zh-v1.5"
REVISION = "7999e1d3359715c523056ef9478215996d62a620"
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class RagConfig:
    model: str = MODEL
    revision: str = REVISION
    dimension: int = 512
    query_prefix: str = QUERY_PREFIX
    max_length: int = 512
    cache: Path = ROOT / ".rag-models"
    index_path: Path = ROOT / ".rag-index"

    @classmethod
    def from_env(cls):
        model = os.getenv("RAG_EMBEDDING_MODEL", MODEL)
        if model != MODEL:
            raise ValueError("Only the reviewed BAAI/bge-small-zh-v1.5 configuration is supported")
        return cls(model=model, cache=Path(os.getenv("RAG_MODEL_CACHE", str(ROOT / ".rag-models"))).resolve(),
                   index_path=Path(os.getenv("RAG_INDEX_PATH", str(ROOT / ".rag-index"))).resolve())

    def identity(self) -> dict:
        return {"model": self.model, "revision": self.revision, "dimension": self.dimension,
                "query_prefix": self.query_prefix, "max_length": self.max_length,
                "pooling": "CLS", "normalize": True, "device": "cpu", "threads": 2,
                "trust_remote_code": False, "license": "MIT", "text_format": "title-section-tags-content-v1"}


def document_text(doc: KnowledgeDocument) -> str:
    return f"标题：{doc.title}\n章节：{doc.section}\n标签：{'、'.join(doc.tags)}\n正文：{doc.content}"


def corpus_identity(documents: list[KnowledgeDocument]) -> dict:
    return {"corpus_sha256": sha256(ROOT / "knowledge/official_sources.jsonl"),
            "sources_sha256": sha256(ROOT / "knowledge/source_registry.json"),
            "documents_sha256": object_hash([doc.model_dump(mode="json") for doc in documents]),
            "document_ids": [doc.id for doc in documents]}


def validate_manifest(manifest: dict, config: RagConfig, documents: list[KnowledgeDocument], dimension: int) -> None:
    expected = {"model_config": config.identity(), **corpus_identity(documents), "dimension": dimension}
    if dimension != config.dimension or any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("Stale model/corpus/dimension index; rebuild with scripts/build_vector_index.py")
    threshold = manifest.get("threshold")
    if not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not -1 <= threshold <= 1:
        raise ValueError("Invalid calibrated threshold; rebuild index")


class LocalEncoder:
    def __init__(self, config: RagConfig, *, allow_download: bool = False):
        from huggingface_hub import snapshot_download
        from transformers import AutoModel, AutoTokenizer
        import torch

        self.config = config
        torch.set_num_threads(2)
        self.snapshot = Path(snapshot_download(
            repo_id=config.model, revision=config.revision, cache_dir=str(config.cache),
            local_files_only=not allow_download,
            allow_patterns=["config.json", "tokenizer*.json", "special_tokens_map.json", "vocab.txt", "model.safetensors", "README.md"],
        ))
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.snapshot), local_files_only=True, trust_remote_code=False)
        self.model = AutoModel.from_pretrained(str(self.snapshot), local_files_only=True,
                                             trust_remote_code=False, use_safetensors=True).eval().to("cpu")
        if self.model.config.hidden_size != config.dimension:
            raise ValueError("Model dimension differs from reviewed configuration")

    def encode(self, texts: list[str], *, queries: bool = False):
        import numpy as np
        import torch

        arrays = []
        for offset in range(0, len(texts), 16):
            batch = texts[offset:offset+16]
            if queries:
                batch = [self.config.query_prefix + text for text in batch]
            inputs = self.tokenizer(batch, padding=True, truncation=True,
                                    max_length=self.config.max_length, return_tensors="pt")
            with torch.inference_mode():
                embeddings = self.model(**inputs).last_hidden_state[:, 0]
                embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
            arrays.append(embeddings.cpu().numpy().astype("float32"))
        return np.ascontiguousarray(np.concatenate(arrays))


def select_hits(documents, scores, positions, threshold: float, limit: int):
    """Threshold and stable ordering, preserving original domain objects."""
    hits = [(documents[int(i)], float(score)) for score, i in zip(scores, positions)
            if 0 <= int(i) < len(documents) and math.isfinite(float(score)) and float(score) >= threshold]
    return sorted(hits, key=lambda item: (-item[1], item[0].id))[:max(0, limit)]


class VectorRetriever:
    def __init__(self, documents: list[KnowledgeDocument], config: RagConfig | None = None):
        import faiss

        self.config = config or RagConfig.from_env()
        self.documents = documents
        path = self.config.index_path
        self.manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        validate_manifest(self.manifest, self.config, documents, self.config.dimension)
        if sha256(path / "documents.faiss") != self.manifest["index_sha256"]:
            raise ValueError("Index checksum mismatch; rebuild index")
        self.index = faiss.read_index(str(path / "documents.faiss"))
        validate_manifest(self.manifest, self.config, documents, self.index.d)
        if self.index.ntotal != len(documents) or self.index.metric_type != faiss.METRIC_INNER_PRODUCT:
            raise ValueError("Index count/metric mismatch; rebuild index")
        self.encoder = LocalEncoder(self.config)  # Cache-only. Never downloads or rebuilds.
        if sha256(self.encoder.snapshot / "model.safetensors") != self.manifest["model_weights_sha256"]:
            raise ValueError("Model weights checksum mismatch; rebuild index")

    def search(self, query: str, limit: int = 3):
        if not query.strip() or limit <= 0:
            return []
        scores, positions = self.index.search(self.encoder.encode([query], queries=True), self.index.ntotal)
        return select_hits(self.documents, scores[0], positions[0], self.manifest["threshold"], limit)
