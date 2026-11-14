"""Score retrieved chunks against gold pages and evidence quotes."""
import re
import unicodedata
from typing import Any


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Mark a hit only when source, page, and every recorded quote match.

    String match is retrieval evidence, not a human coherence or grounding label.
    """
    quotes = _quotes(question)
    ranked: list[dict[str, Any]] = []
    for index, hit in enumerate(hits, start=1):
        source_match = hit.get("document_id") == question.get("gold_source_id")
        page_match = hit.get("page") == question.get("gold_page")
        evidence_match = bool(quotes) and all(_contains(hit.get("text", ""), quote) for quote in quotes)
        ranked.append(
            {
                "rank": index,
                "chunk_id": hit.get("chunk_id"),
                "score": hit.get("score"),
                "document_id": hit.get("document_id"),
                "source": hit.get("source"),
                "page": hit.get("page"),
                "metadata": hit.get("metadata") or {},
                "text": hit.get("text", ""),
                "source_match": source_match,
                "page_match": page_match,
                "evidence_match": evidence_match,
                "hit": source_match and page_match and evidence_match,
            }
        )
    top3 = ranked[:3]
    return {
        "id": question.get("id"),
        "split": question.get("split"),
        "strategy": question.get("strategy"),
        "question": question.get("question"),
        "expected_answer": question.get("expected_answer"),
        "gold_source_id": question.get("gold_source_id"),
        "gold_page": question.get("gold_page"),
        "filter": question.get("filter"),
        "evidence_quotes": quotes,
        "hit_at_1": bool(top3 and top3[0]["hit"]),
        "evidence_hit_at_3": any(item["hit"] for item in top3),
        "top3": top3,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Count Hit@1 and Evidence Hit@3 for each strategy and dev/test split."""
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for trace in traces:
        strategy = str(trace.get("strategy"))
        split = str(trace.get("split"))
        grouped.setdefault(strategy, {}).setdefault(split, []).append(trace)
    by_strategy: dict[str, dict[str, Any]] = {}
    for strategy, splits in grouped.items():
        by_strategy[strategy] = {split: _rates(items) for split, items in splits.items()}
    return {"by_strategy": by_strategy}


def _quotes(question: dict[str, Any]) -> list[str]:
    quotes = []
    for key in ("evidence_quote", "evidence_quote_extra"):
        value = question.get(key)
        if isinstance(value, str) and value.strip():
            quotes.append(value.strip())
    return quotes


def _contains(text: str, quote: str) -> bool:
    return _flat(quote) in _flat(text)


def _flat(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").replace("\t", " ")
    return re.sub(r"\s+", " ", text).strip()


def _rates(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(items)
    hit_at_1 = sum(1 for item in items if item.get("hit_at_1"))
    hit_at_3 = sum(1 for item in items if item.get("evidence_hit_at_3"))
    return {
        "n": total,
        "hit_at_1": f"{hit_at_1}/{total}",
        "hit_at_1_count": hit_at_1,
        "evidence_hit_at_3": f"{hit_at_3}/{total}",
        "evidence_hit_at_3_count": hit_at_3,
    }
