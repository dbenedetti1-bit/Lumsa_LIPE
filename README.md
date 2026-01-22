# Trascrizioni — MP4 → Trascrizione → Capitolo di Libro

Workflow locale a **due fasi** per trasformare video-lezioni `.mp4` in capitoli di libro completi e ben strutturati.

## Architettura: Two-Pass Pipeline

Il workflow è diviso in due fasi distinte per superare i limiti di VRAM (6GB) e massimizzare la qualità:

### FASE 1: "The Cleaner" (Sanitizzazione)
- **Modello:** `llama3.1:8b` (veloce, tutto in GPU)
- **Scopo:** Trasforma la trascrizione grezza (rumore, ripetizioni, disfluenze) in elenchi puntati dettagliati di fatti puri
- **Output:** File intermedio `temp_cleaned_notes.md` (appunti puliti)

### FASE 2: "The Author" (Stesura Capitolo)
- **Modello:** `qwen2.5:14b` (alta qualità, CPU offloading)
- **Scopo:** Trasforma gli appunti puliti in un capitolo di libro completo, scorrevole e accademico
- **Output:** File finale `<nomefile>.capitolo.md` e `.docx`

## Requisiti

- **Python 3.12**
- GPU **NVIDIA** con **CUDA 12.x** (per Faster-Whisper `large-v3` in `float16`)
- **Ollama** installato e in esecuzione
  - Avvia il servizio con `ollama serve`
  - **Modelli richiesti:**
    - `llama3.1:8b` (Fase 1) - installa con `ollama pull llama3.1:8b`
    - `qwen2.5:14b` (Fase 2) - installa con `ollama pull qwen2.5:14b`

## Installazione (uv)

Nel folder del progetto:

```bash
uv sync
```

## Come si usa

### 1) Avvia Ollama

Avvia il servizio Ollama (deve essere attivo durante tutto il workflow):

```bash
ollama serve
```

### 2) Avvia la GUI

```bash
uv run python main.py
```

### 3) Seleziona l'MP4 e avvia

Nella finestra:

- **Carica MP4...**: scegli il file video
- **Chunk max parole (Fase 1)**: default `2500` (consigliato 2000–3000 per evitare problemi di VRAM)
- **Overlap (caratteri)**: default `100` (per mantenere continuità tra chunk)
- **Avvia workflow**: esegue tutto in **background** (thread) e la GUI resta reattiva

## Avanzamento e output

- **Progress bar**:
  - 0–40%: avanzamento trascrizione (in base ai secondi elaborati)
  - 40–70%: avanzamento FASE 1 (Cleaner) - pulizia trascrizione
  - 70–100%: avanzamento FASE 2 (Author) - scrittura capitolo (può richiedere tempo per CPU offloading)
- **Trascrizione parziale**: ogni ~5 minuti di audio viene appeso a `output/<video>/<video>.transcript.md` mentre la trascrizione è in corso

## Dove viene generato l'output

Per ogni video selezionato, l'output viene creato in:

- `output/<nome_video>/` (nella **stessa cartella** dell'MP4)

Esempio:

- Input: `D:\Lezioni\lezione1.mp4`
- Output:
  - `D:\Lezioni\output\lezione1\lezione1.wav` (audio estratto)
  - `D:\Lezioni\output\lezione1\lezione1.transcript.md` (trascrizione grezza con timestamp)
  - `D:\Lezioni\output\lezione1\temp_cleaned_notes.md` (appunti puliti - Fase 1)
  - `D:\Lezioni\output\lezione1\lezione1.capitolo.md` (capitolo finale - Fase 2)
  - `D:\Lezioni\output\lezione1\lezione1.docx` (Word document)

## Note importanti / Troubleshooting

- **CUDA non disponibile**: la trascrizione fallisce con un errore esplicito (verifica driver NVIDIA, CUDA 12.x, che la GPU sia visibile).
- **Ollama non attivo**: l'elaborazione fallisce con un errore esplicito (avvia `ollama serve`).
- **Modelli mancanti**: se un modello non è installato, il programma suggerirà di eseguire `ollama pull <modello>`.
- **FASE 2 lenta**: normale! `qwen2.5:14b` usa CPU offloading e può richiedere diversi minuti per generare il capitolo completo. Il timeout è illimitato per evitare interruzioni.
- **GUI bloccata**: non dovrebbe succedere; i passi pesanti girano in thread separato e gli aggiornamenti arrivano via coda eventi.

## Configurazione Tecnica

- **FASE 1 (Cleaner):**
  - Context window: `4096` token
  - Timeout: `30` secondi per chunk
  - Chunking: con overlap per continuità

- **FASE 2 (Author):**
  - Context window: `8192` token (fondamentale per capitoli lunghi)
  - Timeout: `None` (illimitato, per gestire CPU offloading lento)
  - Processamento: preferibilmente in un unico blocco se il testo pulito rientra nei limiti
