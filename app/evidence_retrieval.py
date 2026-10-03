"""Optional retrieval adapter, imported without loading any embedding dependency."""
from time import perf_counter

from .models import RetrievalTrace


class RetrievalUnavailableError(RuntimeError):
    """Sanitized configuration failure suitable for HTTP and audit output."""


RAG_UNAVAILABLE = "RAG 检索不可用：请检查可选依赖、本地模型缓存和索引配置，或由管理员显式切回 candidate；本次未执行业务动作。"
RAG_NO_EVIDENCE = "当前知识库没有足够依据。请补充信息或转人工处理；本次未创建工单或退款审批。"


def vector_evidence(store, query):
    # Only reached AFTER guardrail. No implicit download, rebuild, fallback or
    # shared mutable per-request trace. Fresh initialization also validates each
    # request against current corpus/index/model files (including after failure).
    from .rag_retrieval import RagConfig, VectorRetriever, sha256

    start = perf_counter()
    config = RagConfig.from_env()
    manifest_path = config.index_path / "manifest.json"
    digest = sha256(manifest_path)
    retriever = VectorRetriever(store.list_knowledge(), config)
    hits = retriever.search(query, limit=3)
    if sha256(manifest_path) != digest:
        raise ValueError("Index manifest changed during retrieval")
    documents = [doc for doc, _ in hits]
    return documents, RetrievalTrace(
        retriever="vector_rag", document_ids=[doc.id for doc in documents],
        scores=[score for _, score in hits], threshold=retriever.manifest["threshold"],
        model=config.model, revision=config.revision, index_manifest_sha256=digest,
        elapsed_ms=(perf_counter() - start) * 1000, empty=not documents,
    )
