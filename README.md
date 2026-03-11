# Trascrizioni — MP4 / PDF → Capitolo di Libro

Workflow locale a **due fasi** per trasformare video-lezioni `.mp4` **oppure** trascrizioni già in formato **PDF** in capitoli di libro completi e ben strutturati.

## Architettura: Two-Pass Pipeline

Il workflow è diviso in due fasi distinte per superare i limiti di VRAM (6GB) e massimizzare la qualità:

### FASE 1: "The Cleaner" (Sanitizzazione)
- **Modello:** `llama3.1:8b` (veloce, tutto in GPU)
- **Scopo:** Trasforma la trascrizione grezza (rumore, ripetizioni, disfluenze) in elenchi puntati dettagliati di fatti puri
- **Output:** File intermedio `<video>.notes.md` (appunti puliti)

### FASE 2: "The Author" (Stesura Capitolo)
- **Modello:** `qwen2.5:14b` (Ollama, alta qualità, CPU offloading) **oppure** modelli Gemini (Google AI Studio, più veloce)
- **Scopo:** Trasforma gli appunti puliti in un capitolo di libro completo, scorrevole e accademico
- **Output:** File finale `<nomefile>.capitolo.md` e `.docx`

## Requisiti

- **Python 3.12**
- GPU **NVIDIA** con **CUDA 12.x** (per Faster-Whisper `large-v3` in `float16`)

### Opzione A: Usa Ollama (locale, gratuito)
- **Ollama** installato e in esecuzione
  - Avvia il servizio con `ollama serve`
  - **Modelli richiesti:**
    - `llama3.1:8b` (Fase 1) - installa con `ollama pull llama3.1:8b`
    - `qwen2.5:14b` (Fase 2) - installa con `ollama pull qwen2.5:14b`

### Opzione B: Usa Google AI Studio (cloud, richiede API key)
- **Google AI Studio API Key** (obbligatorio se usi questa opzione)
  - Ottieni la chiave da: https://aistudio.google.com/apikey
  - **Modelli Gemini disponibili:**
    - `gemini-2.5-flash` (default, veloce ed economico)
    - `gemini-2.5-pro` (alta qualità)
    - `gemini-3-flash-preview` (ultima versione preview)

## Installazione (uv)

Nel folder del progetto:

```bash
uv sync
```

## Come si usa

### 1) Preparazione

**Se usi Ollama:**
- Avvia il servizio Ollama (deve essere attivo durante tutto il workflow):
  ```bash
  ollama serve
  ```

**Se usi Google AI Studio:**
- Ottieni la tua API key da https://aistudio.google.com/apikey
- Non serve avviare servizi locali

### 2) Avvia la GUI

```bash
uv run python main.py
```

### 3) Seleziona il file e avvia

Nella finestra:

**File Selection:**
- **Carica MP4...**: scegli un video; il programma estrarrà l'audio e trascriverà con Whisper.
- **Carica PDF (trascrizione)...**: scegli un PDF che contiene già il testo della trascrizione; il testo verrà estratto e usato direttamente per Fase 1 / Fase 2 (nessun audio né Whisper).

**Parametri Fase 1 (Cleaner):**
- **Chunk max parole**: default `2500` (consigliato 2000–3000 per evitare problemi di VRAM)
- **Overlap (caratteri)**: default `100` (per mantenere continuità tra chunk)

**Parametri Fase 2 (Author):**
- **Chunk max parole**: default `2500`
- **Overlap (caratteri)**: default `100`
- **Max tokens/chunk**: default `8000`

**API Options:**
- **Usa Google AI Studio**: checkbox per usare Gemini invece di Ollama
  - Se attivato, abilita i controlli sottostanti
- **API Key**: inserisci la tua Google AI Studio API Key (obbligatorio se usi Google AI Studio)
- **Modello**: dropdown per selezionare il modello Gemini da usare
  - `gemini-2.5-flash` (default, veloce)
  - `gemini-2.5-pro` (alta qualità)
  - `gemini-3-flash-preview` (preview)
- **Start from**: scegli se partire da "Notes" (dopo Fase 1) o "Trascrizione" (salta Fase 1 e usa direttamente la trascrizione grezza)

**Dashboard Statistiche:**
- Mostra in tempo reale: durata audio, statistiche trascrizione e notes

- **Avvia Workflow**: esegue tutto in **background** (thread) e la GUI resta reattiva

## Avanzamento e output

- **Progress bar**:
  - 0–40%: avanzamento trascrizione (in base ai secondi elaborati)
  - 40–70%: avanzamento FASE 1 (Cleaner) - pulizia trascrizione con Ollama (saltata se usi Google AI Studio con "Start from: Trascrizione")
  - 70–100%: avanzamento FASE 2 (Author) - scrittura capitolo
    - Con Ollama (`qwen2.5:14b`): può richiedere tempo per CPU offloading
    - Con Google AI Studio: generalmente più veloce
- **Trascrizione parziale**: ogni ~5 minuti di audio viene appeso a `output/<video>/<video>.transcript.md` mentre la trascrizione è in corso

## Dove viene generato l'output

Per ogni file selezionato (MP4 o PDF), l'output viene creato in:

- `output/<nome_file>/` (nella **stessa cartella** del file di input)

Esempio:

- Input MP4: `D:\Lezioni\lezione1.mp4`  
  Output: `D:\Lezioni\output\lezione1\` con `lezione1.wav`, `lezione1.transcript.md`, `lezione1.notes.md`, `lezione1.capitolo.md`, `lezione1.docx`
- Input PDF: `D:\Lezioni\trascrizione.pdf`  
  Output: `D:\Lezioni\output\trascrizione\` con `trascrizione.transcript.md` (testo estratto), `trascrizione.notes.md`, `trascrizione.capitolo.md`, `trascrizione.docx` (nessun file WAV)

## Note importanti / Troubleshooting

- **PDF senza testo**: se il PDF contiene solo immagini (scansioni) e non testo selezionabile, l'estrazione fallirà. Usa PDF con testo già presente o effettua OCR prima.
- **CUDA non disponibile**: la trascrizione da MP4 fallisce con un errore esplicito (verifica driver NVIDIA, CUDA 12.x, che la GPU sia visibile).
- **Ollama non attivo**: l'elaborazione fallisce con un errore esplicito (avvia `ollama serve`). **Non necessario se usi Google AI Studio.**
- **Modelli Ollama mancanti**: se un modello Ollama non è installato, il programma suggerirà di eseguire `ollama pull <modello>`.
- **Google AI API Key**: se usi Google AI Studio, assicurati di inserire una API key valida. Puoi ottenerla da https://aistudio.google.com/apikey
- **Modello Gemini non valido**: se selezioni un modello Gemini non disponibile, verrà mostrato un errore con l'elenco dei modelli supportati.
- **FASE 2 lenta con Ollama**: normale! `qwen2.5:14b` usa CPU offloading e può richiedere diversi minuti. Google AI Studio è generalmente più veloce.
- **FASE 1 saltata**: se usi Google AI Studio con "Start from: Trascrizione", la Fase 1 viene saltata e il modello Gemini lavora direttamente sulla trascrizione grezza.
- **Rate limit Google AI**: se raggiungi il rate limit, il programma attende automaticamente e riprova (fino a 5 tentativi con backoff esponenziale).
- **Dashboard statistiche**: le statistiche si aggiornano automaticamente durante il workflow.
- **GUI bloccata**: non dovrebbe succedere; i passi pesanti girano in thread separato e gli aggiornamenti arrivano via coda eventi.

## Configurazione Tecnica

- **FASE 1 (Cleaner) - Solo con Ollama:**
  - Modello: `llama3.1:8b`
  - Context window: `4096` token
  - Timeout: `90` secondi per chunk
  - Chunking: con overlap per continuità

- **FASE 2 (Author) - Ollama:**
  - Modello: `qwen2.5:14b`
  - Context window: `8192` token (fondamentale per capitoli lunghi)
  - Timeout: `None` (illimitato, per gestire CPU offloading lento)
  - Processamento: preferibilmente in un unico blocco se il testo pulito rientra nei limiti

- **FASE 2 (Author) - Google AI Studio:**
  - Modelli disponibili: `gemini-2.5-flash`, `gemini-2.5-pro`, `gemini-3-flash-preview`
  - Context window: ~1M token (molto grande, permette di processare testi molto lunghi)
  - Retry automatico: fino a 5 tentativi con backoff esponenziale in caso di rate limit
  - Processamento: preferibilmente in un unico blocco (sfrutta la grande context window)
