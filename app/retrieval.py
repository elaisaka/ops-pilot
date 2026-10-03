"""Small, deterministic lexical retriever. No evaluation labels or external services."""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

from .models import KnowledgeDocument

# Short business vocabulary, developed on the separate development set.
# These expand retrieval terms; they do not assert legal equivalence.
SYNONYMS = {
    "定做": "定作", "量身": "定作", "开箱": "拆封 查验", "试一下": "试用",
    "退钱": "退款", "邮费": "运费", "差评": "评价", "刷单": "虚构交易",
    "关店": "终止经营", "包裹": "快件", "送不到": "无法投递",
    "漏件": "内件短少", "验真": "查验", "电子票据": "数电发票",
    "电子票": "电子发票", "存档": "归档",
}
STOP_TERMS = set("可以 是否 应当 应该 不能 不得 规定 进行 有关 相关 提供 处理 用户 商品 服务 信息 消费者 经营者".split())
FIELD_WEIGHTS = {"title": 3.0, "tags": 3.0, "content": 2.0, "section": 1.0}


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def terms(text: str) -> set[str]:
    """Chinese adjacent bigrams and whole Latin/number tokens, never isolated characters."""
    result = set()
    for part in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", normalize(text)):
        if re.fullmatch(r"[a-z0-9]+", part):
            if len(part) >= 2:
                result.add(part)
        else:
            result.update(part[i:i+2] for i in range(len(part)-1))
    return result - STOP_TERMS


def rank_documents(query: str, documents: list[KnowledgeDocument], limit: int = 3) -> list[KnowledgeDocument]:
    if limit <= 0 or not documents:
        return []
    normalized = normalize(query)
    expanded = normalized + " " + " ".join(value for key, value in SYNONYMS.items() if key in normalized)
    query_terms = terms(expanded)
    fields = [{name: terms(" ".join(doc.tags) if name == "tags" else getattr(doc, name))
               for name in FIELD_WEIGHTS} for doc in documents]
    frequencies = Counter(term for doc_fields in fields for term in set().union(*doc_fields.values()))
    ranked = []
    for doc, doc_fields in zip(documents, fields):
        score = sum(weight * sum(math.log(1 + len(documents) / frequencies[term]) for term in query_terms & doc_fields[name])
                    for name, weight in FIELD_WEIGHTS.items())
        # No URL bonus or arbitrary fallback. Require lexical evidence above a small floor.
        if score >= 4.0:
            ranked.append((score, doc))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [doc for _, doc in ranked[:limit]]
