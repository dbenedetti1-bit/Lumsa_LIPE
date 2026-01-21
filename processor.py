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


@dataclass(frozen=True)
class TextChunk:
    """
    Un chunk è composto da:
    - context: coda del chunk precedente (overlap) usata solo come contesto
    - text: contenuto "nuovo" da elaborare in questo passo
    """

    context: str
    text: str


def load_prompt_templates(prompts_md_path: str | Path) -> str:
    """
    Carica prompts.md e lo usa come template principale (ruolo/task/vincoli).
    """
    prompts_md_path = Path(prompts_md_path)
    text = prompts_md_path.read_text(encoding="utf-8")
    if not text.strip():
        raise RuntimeError("prompts.md è vuoto: impossibile costruire i template per Ollama.")
    return text.strip()


def _safe_tail_for_overlap(text: str, overlap: int) -> str:
    """
    Estrae una coda di lunghezza ~overlap caratteri, evitando (quando possibile)
    di iniziare nel mezzo di una riga (per non spezzare timestamp o frasi).
    Garantisce sempre un overlap limitato, anche se non trova newline.
    """
    if overlap <= 0:
        return ""
    text = (text or "").strip()
    if not text:
        return ""

    # Calcola la posizione target per l'overlap
    target_start = max(0, len(text) - overlap)
    
    # Se siamo già all'inizio o siamo su un newline, usa direttamente
    if target_start == 0 or (target_start > 0 and text[target_start - 1] == "\n"):
        return text[target_start:].strip()
    
    # Cerca un newline dopo la posizione target (preferito)
    nl_after = text.find("\n", target_start)
    if nl_after != -1 and (nl_after + 1) < len(text):
        return text[nl_after + 1:].strip()
    
    # Se non trovato dopo, cerca un newline prima della posizione target
    nl_before = text.rfind("\n", 0, target_start)
    if nl_before != -1:
        # Usa il newline trovato, ma verifica che non superi troppo l'overlap
        candidate_start = nl_before + 1
        candidate_length = len(text) - candidate_start
        # Se il candidato è ragionevole (entro ~2x l'overlap), usalo
        if candidate_length <= overlap * 2:
            return text[candidate_start:].strip()
    
    # Fallback: usa la posizione target anche se nel mezzo di una riga
    # Questo garantisce sempre un overlap limitato
    return text[target_start:].strip()


def chunk_text_by_words(text: str, *, max_words: int = 2500, overlap: int = 100) -> list[TextChunk]:
    """
    Chunking semplice (2000-3000 parole consigliate) per evitare crash VRAM/contesto.
    Manteniamo i confini su newline quando possibile.

    Overlap:
    - i chunk successivi ricevono la coda del chunk precedente (in caratteri) come `context`
    - il `context` NON va "riscritto" in output: serve solo a mantenere continuità
    """
    if max_words < 500:
        raise ValueError("max_words troppo basso.")

    lines = [ln.rstrip() for ln in text.splitlines()]
    chunks_lines: list[list[str]] = []
    cur: list[str] = []
    cur_words = 0

    for ln in lines:
        ln_words = len(ln.split())
        if cur and cur_words + ln_words > max_words:
            chunks_lines.append(cur)
            cur = []
            cur_words = 0
        cur.append(ln)
        cur_words += ln_words

    if cur:
        chunks_lines.append(cur)

    base_chunks = ["\n".join(c).strip() for c in chunks_lines]
    base_chunks = [c for c in base_chunks if c]

    out: list[TextChunk] = []
    prev_text = ""
    for chunk_text in base_chunks:
        ctx = _safe_tail_for_overlap(prev_text, overlap) if prev_text else ""
        out.append(TextChunk(context=ctx, text=chunk_text))
        prev_text = chunk_text
    return out


def ollama_process_chunks(
    timestamped_transcript: str,
    *,
    prompts_md_path: str | Path,
    model: str = "qwen2.5:14b",
    max_words_per_chunk: int = 2500,
    overlap: int = 100,
    progress_cb=None,  # progress_cb(done:int, total:int)
    log_cb=None,  # log_cb(str)
) -> list[ChunkResult]:
    """
    Invia chunk a Ollama usando i template in prompts.md.
    Gestisce assenza servizio Ollama in modo esplicito.
    """
    templates = load_prompt_templates(prompts_md_path)
    chunks = chunk_text_by_words(timestamped_transcript, max_words=max_words_per_chunk, overlap=overlap)
    total = len(chunks)

    # CRUCIALE: modelli grandi (CPU offloading) possono essere molto lenti → no timeout
    client = ollama.Client(timeout=None)
    options = {"num_ctx": 4096}

    results: list[ChunkResult] = []
    for i, ch in enumerate(chunks, start=1):
        if log_cb:
            log_cb(f"Ollama: elaboro chunk {i}/{total} ({len(ch.text.split())} parole, overlap={overlap})...")

        if ch.context:
            user_prompt = (
                "Trasforma la trascrizione in una sezione di libro divulgativo (output finale pronto).\n"
                "Nota: il testo include un CONTEXTO ripetuto dal chunk precedente per continuità.\n"
                "Regola anti-ripetizione: NON riscrivere e NON parafrasare il CONTEXTO; usalo solo per capire dove riprendere.\n"
                "Scrivi invece SOLO il contenuto relativo al TESTO NUOVO, evitando di ripetere frasi già presenti nel CONTEXTO.\n\n"
                "### CONTEXTO (solo riferimento, non riscrivere)\n"
                f"{ch.context}\n\n"
                "### TESTO NUOVO (scrivi qui il contenuto finale)\n"
                f"{ch.text}"
            )
        else:
            user_prompt = (
                "Trasforma la trascrizione in una sezione di libro divulgativo (output finale pronto).\n"
                "Scrivi direttamente il contenuto finale, ben strutturato in Markdown.\n\n"
                f"{ch.text}"
            )

        try:
            resp = client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": templates},
                    {"role": "user", "content": user_prompt},
                ],
                options=options,
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
                        lst = client.list()
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

        results.append(ChunkResult(chunk_index=i, input_word_count=len(ch.text.split()), output_text=content))

        if progress_cb:
            progress_cb(i, total)

    return results


def merge_chunk_results_to_markdown(results: list[ChunkResult]) -> str:
    """
    Unisce gli output chunk in un unico Markdown.
    """
    parts: list[str] = []
    for r in results:
        txt = (r.output_text or "").strip()
        if txt:
            parts.append(txt)
    return "\n\n".join(parts).strip() + "\n"

