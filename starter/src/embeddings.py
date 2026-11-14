"""Ollama EmbeddingGemma adapter. Fail if the local server is unavailable."""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# EmbeddingGemma retrieval prompts. The Ollama template passes text through unchanged.
DOCUMENT_PROMPT = "title: none | text: "
QUERY_PROMPT = "task: search result | query: "
DEFAULT_MODEL = "embeddinggemma:latest"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
BATCH_SIZE = 16


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        ...


class MyEmbedder:
    """Embed documents and queries with local Ollama EmbeddingGemma."""

    def __init__(self, model_name: str | None = None) -> None:
        configured = os.getenv("EMBEDDING_MODEL", "").strip()
        self.model_name = model_name or configured or DEFAULT_MODEL
        self.base_url = (os.getenv("OLLAMA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.dimensions = len(self._embed_raw(["dimension probe"])[0])
        if self.dimensions < 1:
            raise RuntimeError(f"Ollama returned an empty vector for {self.model_name}")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed_raw([f"{DOCUMENT_PROMPT}{text}" for text in texts])

    def embed_query(self, question: str) -> list[float]:
        return self._embed_raw([f"{QUERY_PROMPT}{question}"])[0]

    def _embed_raw(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            payload = json.dumps({"model": self.model_name, "input": batch}).encode()
            request = urllib.request.Request(
                f"{self.base_url}/api/embed",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    body = json.loads(response.read().decode())
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")
                raise RuntimeError(
                    f"Ollama rejected {self.model_name} ({exc.code}): {detail}"
                ) from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(
                    f"Cannot reach Ollama at {self.base_url}. "
                    f"Start it and pull {self.model_name}. Cause: {exc.reason}"
                ) from exc
            embeddings = body.get("embeddings")
            if not isinstance(embeddings, list) or len(embeddings) != len(batch):
                received = len(embeddings) if isinstance(embeddings, list) else 0
                raise RuntimeError(f"Ollama returned {received} vectors for {len(batch)} inputs")
            vectors.extend(embeddings)
        expected = getattr(self, "dimensions", None)
        if expected is not None:
            for vector in vectors:
                if len(vector) != expected:
                    raise RuntimeError(f"Expected {expected} dimensions, got {len(vector)}")
        return vectors
