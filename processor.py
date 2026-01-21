from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import ollama
try:
    # ollama python client
    from ollama import ResponseError  # type: ignore
except Exception:  # pragma: no cover
    ResponseError = None  # type: ignore


@dataclass(frozen=True)
class ChunkResult:
    chunk_index: int
    input_word_count: int
    output_text: str


def load_prompt_templates(prompts_md_path: str | Path) -> str:
    """
    Carica prompts.md e lo usa come template principale (ruolo/task/vincoli).
    """
    prompts_md_path = Path(prompts_md_path)
    text = prompts_md_path.read_text(encoding="utf-8")
    if not text.strip():
        raise RuntimeError("prompts.md è vuoto: impossibile costruire i template per Ollama.")
    return text.strip()


def chunk_text_by_words(text: str, *, max_words: int = 2500) -> list[str]:
    """
    Chunking semplice (2000-3000 parole consigliate) per evitare crash VRAM/contesto.
    Manteniamo i confini su newline quando possibile.
    """
    if max_words < 500:
        raise ValueError("max_words troppo basso.")

    lines = [ln.rstrip() for ln in text.splitlines()]
    chunks: list[list[str]] = []
    cur: list[str] = []
    cur_words = 0

    for ln in lines:
        ln_words = len(ln.split())
        if cur and cur_words + ln_words > max_words:
            chunks.append(cur)
            cur = []
            cur_words = 0
        cur.append(ln)
        cur_words += ln_words

    if cur:
        chunks.append(cur)

    out = ["\n".join(c).strip() for c in chunks]
    return [c for c in out if c]


def ollama_process_chunks(
    timestamped_transcript: str,
    *,
    prompts_md_path: str | Path,
    model: str = "llama3.1:8b",
    max_words_per_chunk: int = 2500,
    progress_cb=None,  # progress_cb(done:int, total:int)
    log_cb=None,  # log_cb(str)
) -> list[ChunkResult]:
    """
    Invia chunk a Ollama usando i template in prompts.md.
    Gestisce assenza servizio Ollama in modo esplicito.
    """
    templates = load_prompt_templates(prompts_md_path)
    chunks = chunk_text_by_words(timestamped_transcript, max_words=max_words_per_chunk)
    total = len(chunks)

    results: list[ChunkResult] = []
    for i, chunk in enumerate(chunks, start=1):
        if log_cb:
            log_cb(f"Ollama: elaboro chunk {i}/{total} ({len(chunk.split())} parole)...")

        user_prompt = (
            "Ecco il chunk di trascrizione (con timestamp). Produci l'output richiesto dai template.\n\n"
            f"{chunk}"
        )

        try:
            resp = ollama.chat(
                model=model,
                messages=[
                    {"role": "system", "content": templates},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as e:
            # Distinzione: modello mancante vs servizio non raggiungibile
            if ResponseError is not None and isinstance(e, ResponseError):
                # 404 tipicamente: model not found
                status = getattr(e, "status_code", None)
                msg = str(e)
                if status == 404 and "not found" in msg.lower():
                    installed = ""
                    try:
                        lst = ollama.list()
                        names = [m.get("name") for m in (lst.get("models") or []) if isinstance(m, dict)]
                        names = [n for n in names if n]
                        if names:
                            installed = "\nModelli installati: " + ", ".join(names)
                    except Exception:
                        installed = ""

                    raise RuntimeError(
                        f"Modello Ollama non trovato: '{model}'.\n"
                        f"Esegui `ollama pull {model}` oppure seleziona un modello installato."
                        f"{installed}"
                    ) from e

            # fallback generico: servizio non attivo / non raggiungibile
            raise RuntimeError(
                "Impossibile contattare Ollama. Avvia il servizio (es. `ollama serve`) e verifica il modello installato."
            ) from e

        content = ""
        try:
            content = (resp.get("message") or {}).get("content", "")  # type: ignore[assignment]
        except Exception:
            content = ""
        content = (content or "").strip()
        if not content:
            content = "(Output vuoto da Ollama)"

        results.append(ChunkResult(chunk_index=i, input_word_count=len(chunk.split()), output_text=content))

        if progress_cb:
            progress_cb(i, total)

    return results


def merge_chunk_results_to_markdown(results: list[ChunkResult]) -> str:
    """
    Unisce gli output chunk in un unico Markdown.
    """
    parts: list[str] = []
    for r in results:
        parts.append(f"## Chunk {r.chunk_index}\n\n{r.output_text}\n")
    return "\n".join(parts).strip() + "\n"

