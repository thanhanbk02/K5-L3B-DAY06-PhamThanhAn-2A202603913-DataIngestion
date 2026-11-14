"""Run ingestion, three retrieval strategies, and the dev/test benchmark."""
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .chunking import by_paragraph, fixed_size
from .embeddings import MyEmbedder
from .evaluation import evaluate_query, summarize
from .ingestion import load_pdf
from .models import Chunk, DocumentPage
from .store import PgVectorStore

ROOT = Path(__file__).resolve().parents[2]
STARTER = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

FIXED_SIZE = 500
FIXED_OVERLAP = 50
PARAGRAPH_MAX = 800
SELECTION_RULE = (
    "Chọn cấu hình theo Evidence Hit@3 trên dev. Hòa thì lấy Hit@1 dev cao hơn, "
    "rồi tới cấu hình ít chunk hơn. Không dùng test để đổi chunk, filter hay câu hỏi."
)


def main() -> None:
    """Load the corpus, index strategies A/B/C, and save top-3 traces."""
    load_dotenv(STARTER / ".env")
    started = time.perf_counter()
    manifest = _read_json(ROOT / "data" / "manifest.json")
    benchmark = _read_json(ROOT / "benchmark" / "questions.json")
    questions = benchmark["questions"]
    _validate_benchmark(questions)
    content_pages = _content_page_count(manifest)
    if content_pages < 30:
        raise RuntimeError(f"Manifest has {content_pages} content pages; need at least 30")

    pdf_path = STARTER / "data" / "Machine_learning_co_ban.pdf"
    pages = load_pdf(pdf_path, manifest["source_id"], manifest["source_path"])
    _attach_manifest(pages, manifest)
    indexed = [page for page in pages if page.metadata.get("counted") and page.text]
    if len(indexed) < 30:
        raise RuntimeError(f"Only {len(indexed)} non-empty content pages were extracted")

    chunks_a = _chunks(indexed, "fixed")
    chunks_b = _chunks(indexed, "paragraph")
    strategies = _strategy_config(chunks_a, chunks_b)
    RESULTS.mkdir(parents=True, exist_ok=True)
    _write_json(RESULTS / "strategies.json", {"selection_rule": SELECTION_RULE, "strategies": strategies})
    _write_json(RESULTS / "ingestion_log.json", _ingestion_log(pages))

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL missing. Copy starter/.env.example to starter/.env")
    embedder = MyEmbedder()
    store = PgVectorStore(database_url, embedder.dimensions)
    try:
        store.create_schema()
        embed_started = time.perf_counter()
        vectors_a = _embed(embedder, "A", chunks_a)
        vectors_b = _embed(embedder, "B", chunks_b)
        embed_seconds = round(time.perf_counter() - embed_started, 2)
        store.upsert("A", chunks_a, vectors_a)
        store.upsert("B", chunks_b, vectors_b)
        store.upsert("C", chunks_b, vectors_b)
        reload_counts = _verify_reload(store, chunks_a, vectors_a, chunks_b, vectors_b)

        query_vectors = {
            question["id"]: embedder.embed_query(question["question"]) for question in questions
        }
        traces: list[dict[str, Any]] = []
        for strategy in ("A", "B", "C"):
            use_filter = strategy == "C"
            for question in questions:
                filters = question.get("filter") if use_filter else None
                hits = store.search(strategy, query_vectors[question["id"]], top_k=3, filters=filters)
                payload = dict(question)
                payload["strategy"] = strategy
                traces.append(evaluate_query(payload, hits))
        summary = summarize(traces)
        selected = _select(summary, strategies)
        report = {
            "model": embedder.model_name,
            "provider": "ollama",
            "dimensions": embedder.dimensions,
            "cost_usd": 0,
            "embed_seconds": embed_seconds,
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "search": "exact cosine similarity in pgvector (1 - cosine distance)",
            "content_pages": len(indexed),
            "reload": reload_counts,
            "selection_rule": SELECTION_RULE,
            "selected_strategy": selected,
            "summary": summary,
            "manual_labels": "Coherence, metadata audit, and grounding are not inferred from string match.",
        }
        _write_json(RESULTS / "summary.json", report)
        _write_json(RESULTS / "traces.json", traces)
        _write_json(RESULTS / "manual_sample.json", _manual_sample(traces))
    finally:
        store.close()
    _print_summary(report)


def _chunks(pages: list[DocumentPage], family: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in pages:
        if family == "fixed":
            chunks.extend(fixed_size(page, FIXED_SIZE, FIXED_OVERLAP))
        else:
            chunks.extend(by_paragraph(page, PARAGRAPH_MAX))
    ids = [chunk.chunk_id for chunk in chunks]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"Duplicate chunk ids in {family}")
    return chunks


def _strategy_config(chunks_a: list[Chunk], chunks_b: list[Chunk]) -> dict[str, Any]:
    return {
        "A": {
            "chunking": "fixed_size",
            "size": FIXED_SIZE,
            "overlap": FIXED_OVERLAP,
            "filter": False,
            "chunks": len(chunks_a),
            "char_stats": _char_stats(chunks_a),
        },
        "B": {
            "chunking": "paragraph",
            "max_size": PARAGRAPH_MAX,
            "filter": False,
            "chunks": len(chunks_b),
            "char_stats": _char_stats(chunks_b),
        },
        "C": {
            "chunking": "paragraph",
            "max_size": PARAGRAPH_MAX,
            "filter": "section, only when the question supplies one",
            "same_chunks_as": "B",
            "chunks": len(chunks_b),
            "char_stats": _char_stats(chunks_b),
        },
    }


def _embed(embedder: MyEmbedder, label: str, chunks: list[Chunk]) -> list[list[float]]:
    print(f"Embedding {label}: {len(chunks)} chunks with {embedder.model_name}", flush=True)
    return embedder.embed_documents([chunk.text for chunk in chunks])


def _verify_reload(
    store: PgVectorStore,
    chunks_a: list[Chunk],
    vectors_a: list[list[float]],
    chunks_b: list[Chunk],
    vectors_b: list[list[float]],
) -> dict[str, int]:
    before = {name: store.count(name) for name in ("A", "B", "C")}
    store.upsert("A", chunks_a, vectors_a)
    store.upsert("B", chunks_b, vectors_b)
    store.upsert("C", chunks_b, vectors_b)
    after = {name: store.count(name) for name in ("A", "B", "C")}
    expected = {"A": len(chunks_a), "B": len(chunks_b), "C": len(chunks_b)}
    if before != expected or after != expected:
        raise RuntimeError(f"Reload duplicated or dropped rows: before={before} after={after}")
    return after


def _select(summary: dict[str, Any], strategies: dict[str, Any]) -> str:
    def key(name: str) -> tuple[int, int, int]:
        dev = summary["by_strategy"][name]["dev"]
        return (dev["evidence_hit_at_3_count"], dev["hit_at_1_count"], -strategies[name]["chunks"])

    return max(("A", "B", "C"), key=key)


def _manual_sample(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sample = []
    for trace in traces:
        if trace["split"] != "test":
            continue
        sample.append(
            {
                "id": trace["id"],
                "strategy": trace["strategy"],
                "question": trace["question"],
                "gold_page": trace["gold_page"],
                "top3": [
                    {
                        "rank": item["rank"],
                        "page": item["page"],
                        "section": (item.get("metadata") or {}).get("section"),
                        "hit": item["hit"],
                        "text": item["text"],
                        "metadata_correct": None,
                        "coherence_0_to_2": None,
                        "grounding_0_to_2": None,
                    }
                    for item in trace["top3"]
                ],
            }
        )
    return sample


def _attach_manifest(pages: list[DocumentPage], manifest: dict[str, Any]) -> None:
    if len(pages) != manifest["physical_pages"]:
        raise RuntimeError(
            f"PDF has {len(pages)} pages; manifest records {manifest['physical_pages']}"
        )
    by_page = {item["page"]: item for item in manifest["pages"]}
    for page in pages:
        info = by_page[page.page]
        page.metadata["section"] = info["section"]
        page.metadata["counted"] = info["counted"]
        page.metadata["role"] = info["role"]


def _ingestion_log(pages: list[DocumentPage]) -> dict[str, Any]:
    rows = []
    for page in pages:
        rows.append(
            {
                "page": page.page,
                "section": page.metadata.get("section"),
                "counted": page.metadata.get("counted"),
                "extraction_status": page.metadata.get("extraction_status"),
                "extracted_chars": page.metadata.get("extracted_chars"),
                "extraction_error": page.metadata.get("extraction_error"),
            }
        )
    statuses = Counter(row["extraction_status"] for row in rows)
    return {
        "pages": rows,
        "status_counts": dict(statuses),
        "empty_or_error_pages": [
            row["page"] for row in rows if row["extraction_status"] in {"empty", "error"}
        ],
    }


def _content_page_count(manifest: dict[str, Any]) -> int:
    return sum(1 for page in manifest["pages"] if page.get("counted"))


def _validate_benchmark(questions: list[dict[str, Any]]) -> None:
    dev = sum(1 for question in questions if question.get("split") == "dev")
    test = sum(1 for question in questions if question.get("split") == "test")
    filtered = [question for question in questions if question.get("filter")]
    filtered_test = sum(1 for question in filtered if question.get("split") == "test")
    if len(questions) < 20 or dev < 15 or test < 5 or len(filtered) < 3 or filtered_test < 1:
        raise RuntimeError("Benchmark must have >=20 questions, 15 dev, 5 test, and 3 filters")


def _char_stats(chunks: list[Chunk]) -> dict[str, int]:
    lengths = [len(chunk.text) for chunk in chunks]
    return {"min": min(lengths), "max": max(lengths), "mean": round(sum(lengths) / len(lengths))}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _print_summary(report: dict[str, Any]) -> None:
    print(f"Model: {report['model']} | dimensions: {report['dimensions']} | cost_usd: 0")
    print(f"Embed seconds: {report['embed_seconds']} | selected on dev: {report['selected_strategy']}")
    for strategy, splits in report["summary"]["by_strategy"].items():
        for split, scores in splits.items():
            print(
                f"{strategy} {split}: Hit@1 {scores['hit_at_1']} | "
                f"Evidence Hit@3 {scores['evidence_hit_at_3']}"
            )
    print(f"Reload row counts: {report['reload']}")
    print(f"Wrote traces to {RESULTS}")


if __name__ == "__main__":
    main()
