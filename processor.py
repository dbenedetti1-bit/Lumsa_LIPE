from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import ollama
try:
    # ollama python client
    from ollama import ResponseError  # type: ignore
except Exception:  # pragma: no cover
    ResponseError = None  # type: ignore

from constants import DEFAULT_GEMINI_MODEL


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


def load_phase2_prompt(prompts_md_path: str | Path) -> str:
    """
    Estrae solo il system prompt della FASE 2 da prompts.md.
    Cerca la sezione "## FASE 2" e estrae il contenuto tra i marker ```.
    """
    prompts_md_path = Path(prompts_md_path)
    text = prompts_md_path.read_text(encoding="utf-8")
    if not text.strip():
        raise RuntimeError("prompts.md è vuoto: impossibile caricare il prompt FASE 2.")
    
    # Trova la sezione FASE 2
    phase2_start = text.find("## FASE 2:")
    if phase2_start == -1:
        raise RuntimeError("Sezione FASE 2 non trovata in prompts.md")
    
    # Estrae tutto dalla sezione FASE 2 in poi
    phase2_section = text[phase2_start:]
    
    # Trova il blocco di codice con il System Prompt
    code_start = phase2_section.find("```")
    if code_start == -1:
        raise RuntimeError("Blocco di codice System Prompt non trovato nella sezione FASE 2")
    
    # Trova la fine del blocco di codice
    code_end = phase2_section.find("```", code_start + 3)
    if code_end == -1:
        raise RuntimeError("Fine del blocco di codice System Prompt non trovata nella sezione FASE 2")
    
    # Estrae il contenuto tra i marker ``` (escludendo i marker stessi)
    prompt_text = phase2_section[code_start + 3:code_end].strip()
    
    if not prompt_text:
        raise RuntimeError("System Prompt FASE 2 vuoto in prompts.md")
    
    return prompt_text


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


# ============================================================================
# TWO-PASS PIPELINE: FASE 1 - "The Cleaner"
# ============================================================================

def phase1_clean_transcript(
    timestamped_transcript: str,
    *,
    model: str = "llama3.1:8b",
    max_words_per_chunk: int = 2500,
    overlap: int = 100,
    progress_cb=None,  # progress_cb(done:int, total:int)
    log_cb=None,  # log_cb(str)
) -> str:
    """
    FASE 1: "The Cleaner"
    Trasforma la trascrizione grezza in elenchi puntati dettagliati di fatti puri.
    Usa un modello veloce (llama3.1:8b) per pulire e comprimere senza perdere informazioni.
    
    Returns:
        Testo pulito concatenato (elenchi puntati dettagliati).
    """
    system_prompt = (
        "Sei un analista dati rigoroso. Il tuo unico obiettivo è estrarre ogni singolo "
        "concetto tecnico, definizione ed esempio dal testo grezzo fornito.\n\n"
        "1. Ignora convenevoli, saluti e ripetizioni verbali.\n"
        "2. Restituisci il contenuto sotto forma di **elenco puntato dettagliato**.\n"
        "3. NON riassumere: mantieni la granulosità delle informazioni.\n"
        "4. NON aggiungere introduzioni o conclusioni.\n"
        "5. Mantieni i timestamp [MM:SS] se presenti nel testo originale."
    )
    
    chunks = chunk_text_by_words(timestamped_transcript, max_words=max_words_per_chunk, overlap=overlap)
    total = len(chunks)
    
    # Modello veloce: timeout aumentato a 90s per gestire chunk complessi
    # (alcuni chunk possono richiedere 30-60s, specialmente con contenuto denso)
    client = ollama.Client(timeout=90)
    options = {"num_ctx": 4096}
    
    results: list[ChunkResult] = []
    for i, ch in enumerate(chunks, start=1):
        if log_cb:
            log_cb(f"FASE 1 (Cleaner): chunk {i}/{total} ({len(ch.text.split())} parole)...")
        
        if ch.context:
            user_prompt = (
                "Estrai tutti i concetti tecnici e fatti dal seguente testo.\n"
                "Nota: il testo include un CONTEXTO dal chunk precedente per continuità.\n"
                "Regola: NON ripetere elementi già estratti dal CONTEXTO; estrai solo dal TESTO NUOVO.\n\n"
                "### CONTEXTO (già processato, non ripetere)\n"
                f"{ch.context}\n\n"
                "### TESTO NUOVO (estrai qui)\n"
                f"{ch.text}"
            )
        else:
            user_prompt = (
                "Estrai tutti i concetti tecnici e fatti dal seguente testo.\n"
                "Restituisci un elenco puntato dettagliato, mantenendo la granulosità delle informazioni.\n\n"
                f"{ch.text}"
            )
        
        try:
            resp = client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                options=options,
            )
        except Exception as e:
            # Distingue timeout da altri errori
            error_type = type(e).__name__
            error_msg = str(e)
            is_timeout = "timeout" in error_msg.lower() or "ReadTimeout" in error_type or "Timeout" in error_type
            
            if ResponseError is not None and isinstance(e, ResponseError):
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
            
            # Messaggio specifico per timeout
            if is_timeout:
                raise RuntimeError(
                    f"Timeout durante elaborazione chunk {i}/{total}.\n"
                    f"Ollama sta processando ma richiede più tempo del limite di 90s.\n"
                    f"Considera di aumentare ulteriormente il timeout o ridurre la dimensione dei chunk."
                ) from e
            
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
    
    # Concatena tutti i risultati
    cleaned_text = merge_chunk_results_to_markdown(results)
    return cleaned_text


# ============================================================================
# TWO-PASS PIPELINE: FASE 2 - "The Author"
# ============================================================================

def _estimate_tokens(text: str) -> int:
    """
    Stima approssimativa dei token: ~4 caratteri per token (conservativa).
    """
    return len(text) // 4


def phase2_write_chapter(
    cleaned_notes: str,
    *,
    prompts_md_path: str | Path,
    model: str = "qwen2.5:14b",
    max_words_per_chunk: int = 2500,
    overlap: int = 100,
    max_tokens_per_chunk: int = 8000,
    progress_cb=None,  # progress_cb(done:int, total:int)
    log_cb=None,  # log_cb(str)
) -> str:
    """
    FASE 2: "The Author"
    Trasforma gli appunti puliti in un capitolo di libro completo, scorrevole e ben formattato.
    Usa un modello grande (qwen2.5:14b) con CPU offloading per qualità massima.
    
    Args:
        cleaned_notes: Testo pulito dalla Fase 1 (elenchi puntati dettagliati).
        prompts_md_path: Percorso al file prompts.md da cui caricare il system prompt.
        max_words_per_chunk: Dimensione massima chunk in parole (default: 2500).
        overlap: Overlap tra chunk in caratteri (default: 100).
        max_tokens_per_chunk: Limite token per chunk (default: 8000).
    
    Returns:
        Capitolo di libro completo in Markdown.
    """
    system_prompt = load_phase2_prompt(prompts_md_path)
    
    # Stima se il testo può essere processato in un unico blocco
    estimated_tokens = _estimate_tokens(cleaned_notes)
    
    if estimated_tokens <= max_tokens_per_chunk:
        # Processa tutto in un unico blocco
        if log_cb:
            log_cb(f"FASE 2 (Author): testo completo (~{estimated_tokens} token stimati), processamento unico...")
        
        chunks_to_process = [cleaned_notes]
        total = 1
    else:
        # Divide in chunk con overlap (simile a Fase 1)
        if log_cb:
            log_cb(f"FASE 2 (Author): testo lungo (~{estimated_tokens} token), divisione in chunk con overlap...")
        
        # Usa chunking per parole con overlap (come Fase 1)
        text_chunks = chunk_text_by_words(cleaned_notes, max_words=max_words_per_chunk, overlap=overlap)
        chunks_to_process: list[str] = []
        for ch in text_chunks:
            # Per Fase 2, usiamo solo il testo (non il context separato)
            # L'overlap è già gestito nel chunking
            chunks_to_process.append(ch.text)
        total = len(chunks_to_process)
    
    # Modello grande: timeout illimitato (CPU offloading può essere molto lento)
    client = ollama.Client(timeout=None)
    options = {"num_ctx": 8192}  # CRUCIALE: context window grande
    
    results: list[str] = []
    for i, chunk_text in enumerate(chunks_to_process, start=1):
        if log_cb:
            log_cb(f"FASE 2 (Author): elaboro blocco {i}/{total}...")
        
        user_prompt = (
            "Scrivi un capitolo di libro completo basato sui seguenti appunti strutturati.\n\n"
            "REGOLA CRITICA - COMPLETEZZA:\n"
            "- Includi TUTTI i concetti tecnici, definizioni, esempi e dettagli presenti negli appunti.\n"
            "- NON omettere dettagli tecnici, classificazioni, tipologie o meccanismi descritti.\n"
            "- Se gli appunti menzionano più elementi (es: 'nuvole cumuliformi, stratiformi, cirriformi'), includi TUTTI questi elementi nel capitolo.\n"
            "- Il capitolo finale deve contenere almeno l'80-90% delle informazioni presenti negli appunti.\n\n"
            "Organizza il contenuto:\n"
            "- Trasforma gli elenchi in prosa fluida quando opportuno per la leggibilità.\n"
            "- Organizza con titoli Markdown (##, ###) logici.\n"
            "- Collega i concetti in modo fluido (senza salti logici).\n\n"
            f"{chunk_text}"
        )
        
        try:
            resp = client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                options=options,
            )
        except Exception as e:
            if ResponseError is not None and isinstance(e, ResponseError):
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
        
        results.append(content)
        
        if progress_cb:
            progress_cb(i, total)
    
    # Concatena i risultati (se divisi in chunk)
    chapter_text = "\n\n".join(results).strip() + "\n"
    return chapter_text


# ============================================================================
# GOOGLE AI STUDIO INTEGRATION
# ============================================================================

def google_ai_write_chapter(
    input_text: str,
    *,
    api_key: str,
    prompts_md_path: str | Path,
    model: str = DEFAULT_GEMINI_MODEL,
    max_words_per_chunk: int = 2500,
    overlap: int = 100,
    progress_cb=None,  # progress_cb(done:int, total:int)
    log_cb=None,  # log_cb(str)
) -> str:
    """
    Usa Google AI Studio (Gemini) per generare il capitolo.
    Supporta start da trascrizione grezza o da notes puliti.
    
    Args:
        input_text: Testo di input (trascrizione o notes).
        api_key: Google AI Studio API key.
        prompts_md_path: Percorso al file prompts.md da cui caricare il system prompt.
        model: Modello Gemini da usare (default: DEFAULT_GEMINI_MODEL).
        max_words_per_chunk: Dimensione massima chunk in parole (default: 2500).
        overlap: Overlap tra chunk in caratteri (default: 100).
    
    Returns:
        Capitolo di libro completo in Markdown.
    """
    try:
        import google.generativeai as genai
        from google.api_core import exceptions as google_exceptions
    except ImportError:
        raise RuntimeError(
            "google-generativeai non installato. Esegui: uv sync"
        )
    
    system_prompt = load_phase2_prompt(prompts_md_path)
    
    # Configura API
    genai.configure(api_key=api_key)
    
    # Google AI ha context window di ~1M token, quindi processiamo tutto insieme
    # senza chunking (a meno che non sia veramente enorme, >800k token per sicurezza)
    estimated_tokens = _estimate_tokens(input_text)
    max_safe_tokens = 800000  # Limite di sicurezza (800k token, ben sotto 1M)
    
    if estimated_tokens <= max_safe_tokens:
        # Processa tutto in un unico blocco (sfrutta la grande context window)
        if log_cb:
            log_cb(f"Google AI: testo completo (~{estimated_tokens:,} token stimati), processamento unico (context window: 1M token)...")
        
        chunks_to_process = [input_text]
        total = 1
    else:
        # Solo se veramente enorme, divide in chunk (caso raro)
        if log_cb:
            log_cb(f"Google AI: testo molto lungo (~{estimated_tokens:,} token), divisione in chunk con overlap...")
        
        text_chunks = chunk_text_by_words(input_text, max_words=max_words_per_chunk, overlap=overlap)
        chunks_to_process: list[str] = []
        for ch in text_chunks:
            chunks_to_process.append(ch.text)
        total = len(chunks_to_process)
    
    # Crea il modello una volta (riutilizzato per tutti i chunk)
    gemini_model = genai.GenerativeModel(
        model_name=model,
        system_instruction=system_prompt,
    )
    
    results: list[str] = []
    for i, chunk_text in enumerate(chunks_to_process, start=1):
        if log_cb:
            log_cb(f"Google AI: elaboro blocco {i}/{total}...")
        
        user_prompt = (
            "Scrivi un capitolo di libro completo basato sui seguenti appunti strutturati.\n\n"
            "REGOLA CRITICA - COMPLETEZZA:\n"
            "- Includi TUTTI i concetti tecnici, definizioni, esempi e dettagli presenti negli appunti.\n"
            "- NON omettere dettagli tecnici, classificazioni, tipologie o meccanismi descritti.\n"
            "- Se gli appunti menzionano più elementi (es: 'nuvole cumuliformi, stratiformi, cirriformi'), includi TUTTI questi elementi nel capitolo.\n"
            "- Il capitolo finale deve contenere almeno l'80-90% delle informazioni presenti negli appunti.\n\n"
            "Organizza il contenuto:\n"
            "- Trasforma gli elenchi in prosa fluida quando opportuno per la leggibilità.\n"
            "- Organizza con titoli Markdown (##, ###) logici.\n"
            "- Collega i concetti in modo fluido (senza salti logici).\n\n"
            f"{chunk_text}"
        )
        
        # Retry logic per gestire rate limit (429)
        max_retries = 5
        retry_delay = 20  # secondi (reset per ogni chunk)
        content = ""
        
        for attempt in range(1, max_retries + 1):
            try:
                # Genera risposta
                response = gemini_model.generate_content(user_prompt)
                
                if response.text:
                    content = response.text.strip()
                else:
                    content = "(Output vuoto da Google AI)"
                
                # Successo, esci dal loop
                break
                
            except Exception as e:
                error_msg = str(e)
                error_lower = error_msg.lower()
                
                # Controlla se è ResourceExhausted (429) o rate limit
                # Verifica il tipo di eccezione e il codice di stato
                is_rate_limit = False
                try:
                    # Prova a importare e verificare ResourceExhausted
                    from google.api_core.exceptions import ResourceExhausted
                    if isinstance(e, ResourceExhausted):
                        is_rate_limit = True
                except (ImportError, AttributeError):
                    # Fallback: verifica stringhe nell'errore
                    is_rate_limit = (
                        "ResourceExhausted" in str(type(e).__name__) or
                        "429" in error_msg or
                        getattr(e, "status_code", None) == 429 or
                        "rate limit" in error_lower or
                        "quota" in error_lower
                    )
                
                if is_rate_limit and attempt < max_retries:
                    # Rate limit raggiunto, aspetta e riprova
                    if log_cb:
                        log_cb(f"Rate limit raggiunto (tentativo {attempt}/{max_retries}). Attendo {retry_delay} secondi...")
                    time.sleep(retry_delay)
                    # Backoff esponenziale: aumenta il delay per i tentativi successivi
                    retry_delay = min(retry_delay * 1.5, 60)  # max 60 secondi
                    continue
                elif "API_KEY" in error_msg or "api key" in error_lower:
                    raise RuntimeError(
                        f"API Key Google AI non valida o mancante. Verifica la chiave inserita."
                    ) from e
                elif "not found" in error_lower or "not available" in error_lower or "invalid model" in error_lower:
                    from constants import GEMINI_MODELS
                    raise RuntimeError(
                        f"Modello '{model}' non disponibile o non valido.\n"
                        f"Modelli disponibili: {', '.join(GEMINI_MODELS)}"
                    ) from e
                else:
                    # Altri errori o rate limit dopo tutti i tentativi
                    if is_rate_limit:
                        raise RuntimeError(
                            f"Rate limit Google AI raggiunto dopo {max_retries} tentativi. Riprova più tardi."
                        ) from e
                    else:
                        raise RuntimeError(
                            f"Errore durante chiamata Google AI: {error_msg}"
                        ) from e
        
        results.append(content)
        
        if progress_cb:
            progress_cb(i, total)
    
    # Concatena i risultati
    chapter_text = "\n\n".join(results).strip() + "\n"
    return chapter_text

