"""Fixed-size windows and paragraph packing. Both keep page metadata."""
import re

from .models import Chunk, DocumentPage

_HEADINGS = {
    "lời tựa",
    "machine learning là gì?",
    "supervised learning",
    "các khái niệm cơ bản",
    "hai góc nhìn về supervised learning",
    "objective function",
    "overfitting",
    "regularized loss minimization",
    "tinh chỉnh các hyperparameter",
    "thuật toán supervised learning tổng quát",
    "loss function",
}


def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    """Split collapsed page text into overlapping character windows."""
    if size < 1:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be >= 0 and smaller than size")
    text = re.sub(r"\s+", " ", page.text).strip()
    if not text:
        return []
    windows: list[str] = []
    start = 0
    step = size - overlap
    while start < len(text):
        piece = text[start : start + size].strip()
        if piece:
            windows.append(piece)
        if start + size >= len(text):
            break
        start += step
    return [_make_chunk(page, index, piece, "fixed") for index, piece in enumerate(windows)]


def by_paragraph(page: DocumentPage, max_size: int) -> list[Chunk]:
    """Pack heading and paragraph blocks, and split any block past max_size."""
    if max_size < 1:
        raise ValueError("max_size must be positive")
    pieces = _pack(_paragraphs(page.text), max_size)
    return [_make_chunk(page, index, piece, "paragraph") for index, piece in enumerate(pieces)]


def _make_chunk(page: DocumentPage, index: int, text: str, family: str) -> Chunk:
    return Chunk(
        chunk_id=f"{page.document_id}:p{page.page:03d}:c{index:03d}",
        document_id=page.document_id,
        source=page.source,
        page=page.page,
        text=text,
        metadata={
            "section": page.metadata.get("section"),
            "chunk_family": family,
            "char_count": len(text),
            "index": index,
        },
    )


def _is_heading(line: str) -> bool:
    cleaned = re.sub(r"\s+", " ", line).strip().casefold()
    return cleaned in _HEADINGS or cleaned.startswith("chương ")


def _paragraphs(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            if current:
                blocks.append(" ".join(current))
                current = []
            continue
        if _is_heading(line) and current:
            blocks.append(" ".join(current))
            current = [line]
            continue
        current.append(line)
    if current:
        blocks.append(" ".join(current))
    return [block for block in blocks if block.strip()]


def _pack(blocks: list[str], max_size: int) -> list[str]:
    pieces: list[str] = []
    buffer = ""
    for block in blocks:
        parts = _split_oversized(block, max_size)
        for part in parts:
            if buffer and len(buffer) + 1 + len(part) > max_size:
                pieces.append(buffer)
                buffer = part
            elif buffer:
                buffer = f"{buffer} {part}"
            else:
                buffer = part
    if buffer:
        pieces.append(buffer)
    return pieces


def _split_oversized(block: str, max_size: int) -> list[str]:
    if len(block) <= max_size:
        return [block]
    sentences = [part.strip() for part in re.split(r"(?<=[\.!?])\s+", block) if part.strip()]
    if len(sentences) <= 1:
        return _hard_split(block, max_size)
    packed = _pack(sentences, max_size)
    flattened: list[str] = []
    for piece in packed:
        if len(piece) <= max_size:
            flattened.append(piece)
        else:
            flattened.extend(_hard_split(piece, max_size))
    return flattened


def _hard_split(text: str, max_size: int) -> list[str]:
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_size, len(text))
        if end < len(text):
            space = text.rfind(" ", start, end)
            if space > start + max_size // 2:
                end = space
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end <= start:
            end = start + max_size
        start = end
    return pieces
