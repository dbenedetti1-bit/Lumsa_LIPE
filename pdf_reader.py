"""
Estrazione testo da PDF (trascrizioni già in formato PDF).
"""

from __future__ import annotations

from pathlib import Path


def extract_text_from_pdf(pdf_path: str | Path) -> str:
    """
    Estrae tutto il testo da un file PDF.
    Restituisce una stringa con il testo concatenato di tutte le pagine.
    Solleva RuntimeError se il file non esiste, non è un PDF valido o è vuoto.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise RuntimeError(f"File non trovato: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise RuntimeError(f"Estensione non .pdf: {pdf_path}")

    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("Libreria pypdf non installata. Esegui: uv sync")

    try:
        reader = PdfReader(str(pdf_path))
    except Exception as e:
        raise RuntimeError(f"Impossibile aprire il PDF: {e}") from e

    if len(reader.pages) == 0:
        raise RuntimeError("Il PDF non contiene pagine.")

    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text()
            if text and text.strip():
                parts.append(text.strip())
        except Exception:
            continue

    if not parts:
        raise RuntimeError("Dal PDF non è stato estratto alcun testo (file vuoto o solo immagini).")

    return "\n\n".join(parts)
