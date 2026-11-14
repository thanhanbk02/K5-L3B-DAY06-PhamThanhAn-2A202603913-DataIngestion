"""Local demo: ask a question, show a grounded answer, highlight the passage in the PDF."""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
STARTER = ROOT / "starter"
sys.path.insert(0, str(STARTER))

from dotenv import load_dotenv

load_dotenv(STARTER / ".env")

from src.embeddings import MyEmbedder
from src.store import PgVectorStore
import os

PDF_PATH = STARTER / "data" / "Machine_learning_co_ban.pdf"
RESULTS = ROOT / "results"
STATIC = Path(__file__).resolve().parent / "static"
HOST = "127.0.0.1"
PORT = 8765

_lock = threading.Lock()
_embedder: MyEmbedder | None = None
_store: PgVectorStore | None = None


def get_services() -> tuple[MyEmbedder, PgVectorStore]:
    global _embedder, _store
    with _lock:
        if _embedder is None:
            _embedder = MyEmbedder()
            url = os.getenv("DATABASE_URL", "").strip()
            if not url:
                raise RuntimeError("Thiếu DATABASE_URL trong starter/.env")
            _store = PgVectorStore(url, _embedder.dimensions)
        return _embedder, _store


def extractive_answer(question: str, passage: str, page: int, section: str | None) -> str:
    text = re.sub(r"\s+", " ", passage).strip()
    sentences = [part.strip() for part in re.split(r"(?<=[\.!?])\s+", text) if part.strip()]
    complete = [sentence for sentence in sentences if sentence.endswith((".", "!", "?"))]
    pool = complete or sentences or [text]
    query_tokens = set(re.findall(r"\w+", question.casefold(), flags=re.UNICODE))
    best = max(pool, key=lambda sentence: (_overlap(sentence, query_tokens), -pool.index(sentence)))
    index = pool.index(best)
    body = " ".join(pool[index : index + 2])
    where = f"Trang {page}"
    if section:
        where += f", mục {section.replace('_', ' ')}"
    return f"{body}\n\nNguồn: {where}."


def _overlap(sentence: str, query_tokens: set[str]) -> int:
    tokens = set(re.findall(r"\w+", sentence.casefold(), flags=re.UNICODE))
    return len(tokens & query_tokens)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def benchmark_payload() -> dict:
    summary = _read_json(RESULTS / "summary.json")
    strategies = _read_json(RESULTS / "strategies.json")
    audit = _read_json(RESULTS / "manual_audit.json")
    traces = _read_json(RESULTS / "traces.json")
    questions: dict[str, dict] = {}
    for row in traces:
        item = questions.setdefault(
            row["id"],
            {
                "id": row["id"],
                "split": row["split"],
                "question": row["question"],
                "gold_page": row["gold_page"],
                "filter": row.get("filter"),
                "strategies": {},
            },
        )
        item["strategies"][row["strategy"]] = {
            "hit_at_1": bool(row["hit_at_1"]),
            "evidence_hit_at_3": bool(row["evidence_hit_at_3"]),
            "top_pages": [hit["page"] for hit in row["top3"]],
        }
    ordered = [questions[key] for key in sorted(questions, key=lambda key: int(key[1:]))]
    return {
        "model": summary["model"],
        "provider": summary["provider"],
        "dimensions": summary["dimensions"],
        "cost_usd": summary["cost_usd"],
        "embed_seconds": summary["embed_seconds"],
        "elapsed_seconds": summary["elapsed_seconds"],
        "search": summary["search"],
        "content_pages": summary["content_pages"],
        "reload": summary["reload"],
        "selection_rule": summary["selection_rule"],
        "selected_strategy": summary["selected_strategy"],
        "by_strategy": summary["summary"]["by_strategy"],
        "strategies": strategies["strategies"],
        "metrics": [
            {
                "name": "Hit@1",
                "definition": "Hạng 1 đúng tài liệu, đúng trang vàng, và mọi câu bằng chứng nằm trọn trong một chunk.",
            },
            {
                "name": "Evidence Hit@3",
                "definition": "Ít nhất một trong ba kết quả đầu thỏa điều kiện của Hit@1. Chỉ số này đo bằng chứng truy xuất, không đo câu trả lời sinh từ mô hình.",
            },
        ],
        "questions": ordered,
        "coherence": {
            "scale": audit["coherence"]["scale"],
            "mean_over_2": audit["coherence"]["mean_over_2"],
            "totals": audit["coherence"]["totals"],
            "labels": audit["coherence"]["labels"],
        },
        "metadata": {
            "checked": audit["metadata"]["checked"],
            "correct_and_sufficient": audit["metadata"]["correct_and_sufficient"],
            "top3_obeys_filter": audit["metadata"]["top3_obeys_filter"],
            "filter_detail": audit["metadata"]["filter_detail"],
            "useful_field": audit["metadata"]["useful_field"],
        },
        "grounding": {
            "scale": audit["grounding"]["scale"],
            "mean_over_2": audit["grounding"]["mean_over_2"],
            "rank1_mean_over_2": audit["grounding"]["rank1_mean_over_2"],
            "counts": audit["grounding"]["counts"],
            "labels": [
                {"id": row["id"], "A": row["A"], "B": row["B"], "C": row["C"], "reason": row["reason"]}
                for row in audit["grounding"]["labels"]
            ],
        },
        "test_note": audit["locked_test_retrieval"],
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._file(STATIC / "index.html", "text/html; charset=utf-8")
            return
        if path == "/api/pdf":
            self._file(PDF_PATH, "application/pdf")
            return
        if path == "/api/benchmark":
            try:
                self._json(200, benchmark_payload())
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            return
        if path.startswith("/static/"):
            target = (STATIC / path.removeprefix("/static/")).resolve()
            if STATIC.resolve() not in target.parents and target != STATIC.resolve():
                self._json(404, {"error": "Không tìm thấy file"})
                return
            content_type = "text/plain; charset=utf-8"
            if target.suffix == ".css":
                content_type = "text/css; charset=utf-8"
            elif target.suffix == ".js":
                content_type = "text/javascript; charset=utf-8"
            self._file(target, content_type)
            return
        self._json(404, {"error": "Không tìm thấy"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/ask":
            self._json(404, {"error": "Không tìm thấy"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            self._json(400, {"error": "JSON không hợp lệ"})
            return
        question = str(payload.get("question", "")).strip()
        strategy = str(payload.get("strategy", "A")).strip().upper()
        if strategy not in {"A", "B", "C"}:
            self._json(400, {"error": "Chiến lược phải là A, B hoặc C"})
            return
        if not question:
            self._json(400, {"error": "Nhập câu hỏi"})
            return
        try:
            result = self._ask(question, strategy)
        except Exception as exc:
            self._json(500, {"error": str(exc)})
            return
        self._json(200, result)

    def _ask(self, question: str, strategy: str) -> dict:
        embedder, store = get_services()
        with _lock:
            vector = embedder.embed_query(question)
            hits = store.search(strategy, vector, top_k=3)
        if not hits:
            return {
                "answer": "Không tìm thấy đoạn nào trong index.",
                "strategy": strategy,
                "page": 1,
                "evidence": "",
                "top3": [],
            }
        top = hits[0]
        section = (top.get("metadata") or {}).get("section")
        answer = extractive_answer(question, top["text"], top["page"], section)
        top3 = []
        for index, hit in enumerate(hits, start=1):
            meta = hit.get("metadata") or {}
            top3.append(
                {
                    "rank": index,
                    "page": hit["page"],
                    "section": meta.get("section"),
                    "score": round(float(hit["score"]), 4),
                    "text": hit["text"],
                    "chunk_id": hit["chunk_id"],
                }
            )
        return {
            "answer": answer,
            "strategy": strategy,
            "page": top["page"],
            "section": section,
            "score": round(float(top["score"]), 4),
            "evidence": top["text"],
            "source": top["source"],
            "top3": top3,
        }

    def _file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self._json(404, {"error": f"Thiếu file {path.name}"})
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[demo] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    if not PDF_PATH.is_file():
        raise SystemExit(f"Không thấy PDF: {PDF_PATH}")
    get_services()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Demo: http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
