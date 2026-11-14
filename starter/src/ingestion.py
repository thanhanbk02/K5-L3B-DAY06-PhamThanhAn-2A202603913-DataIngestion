"""Read a PDF and normalize page text. Empty or failed pages stay in the log."""
import re
import unicodedata
from pathlib import Path

from pypdf import PdfReader

from .models import DocumentPage


def normalize_text(text: str) -> str:
    """Normalize Unicode and whitespace, and keep line breaks for paragraph splits."""
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("\u00a0", " ").replace("\t", " ")
    lines = [re.sub(r"[ ]+", " ", line).strip() for line in text.splitlines()]
    normalized: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if normalized and not blank:
                normalized.append("")
            blank = True
            continue
        normalized.append(line)
        blank = False
    return "\n".join(normalized).strip()


def load_pdf(path: Path, document_id: str, source: str) -> list[DocumentPage]:
    """Extract every physical page and keep its page number and source.

    A page that yields no text, or whose extraction raises, is returned with
    empty text and metadata.extraction_status of "empty" or "error".
    """
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        raise RuntimeError(f"Cannot read encrypted PDF: {path}")
    pages: list[DocumentPage] = []
    for index, pdf_page in enumerate(reader.pages, start=1):
        try:
            raw = pdf_page.extract_text() or ""
            text = normalize_text(raw)
            status = "ok" if text else "empty"
            error = None
        except Exception as exc:
            text = ""
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
        metadata: dict[str, object] = {
            "extraction_status": status,
            "extracted_chars": len(text),
        }
        if error:
            metadata["extraction_error"] = error
        pages.append(
            DocumentPage(
                document_id=document_id,
                source=source,
                page=index,
                text=text,
                metadata=metadata,
            )
        )
    return pages
