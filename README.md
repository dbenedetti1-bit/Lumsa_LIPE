# Trascrizioni — MP4 → Trascrizione → Documento (Markdown/DOCX)

Workflow locale per trasformare video-lezioni `.mp4` in:

- **Trascrizione con timestamp** `[MM:SS]` (Faster-Whisper `large-v3` su **CUDA**)
- **Documento strutturato** (capitoli + sintesi + glossario) via **Ollama** (LLM locale)
- Export finale in **Markdown** e **Word (.docx)**

## Requisiti

- **Python 3.12**
- GPU **NVIDIA** con **CUDA 12.x** (per Faster-Whisper `large-v3` in `float16`)
- **Ollama** installato e in esecuzione
  - esempio: avvia il servizio con `ollama serve`
  - assicurati di avere un modello disponibile (es. `llama3.1:8b` o `llama3.2:3b`)

## Installazione (uv)

Nel folder del progetto:

```bash
uv sync
```

## Come si usa

### 1) Avvia Ollama

- Avvia il servizio Ollama (deve essere attivo mentre il programma elabora i chunk).

### 2) Avvia la GUI

```bash
uv run python main.py
```

### 3) Seleziona l’MP4 e avvia

Nella finestra:

- **Carica MP4...**: scegli il file video
- **Modello Ollama**: es. `llama3.1:8b` (default) oppure `llama3.2:3b`
- **Chunk max parole**: default `2500` (consigliato 2000–3000 per evitare problemi di contesto/VRAM)
- **Avvia workflow**: esegue tutto in **background** (thread) e la GUI resta reattiva

## Avanzamento e output parziali

- **Progress bar**:
  - 0–70%: avanzamento trascrizione (in base ai secondi elaborati)
  - 70–100%: avanzamento elaborazione Ollama sui chunk
- **Trascrizione parziale**: ogni ~5 minuti di audio viene appeso a
  `output/<video>/ <video>.transcript.md` mentre la trascrizione è in corso; al termine il file viene riscritto completo.

## Dove mettere i file

Puoi mettere gli `.mp4` dove vuoi (anche in cartelle diverse). Il programma lavora sul file selezionato.

## Dove viene generato l’output

Per ogni video selezionato, l’output viene creato in:

- `output/<nome_video>/` (nella **stessa cartella** dell’MP4)

Esempio:

- Input: `D:\Lezioni\lezione1.mp4`
- Output:
  - `D:\Lezioni\output\lezione1\lezione1.wav`
  - `D:\Lezioni\output\lezione1\lezione1.transcript.md`
  - `D:\Lezioni\output\lezione1\lezione1.structured.md`
  - `D:\Lezioni\output\lezione1\lezione1.docx`

## Note importanti / Troubleshooting

- **CUDA non disponibile**: la trascrizione fallisce con un errore esplicito (verifica driver NVIDIA, CUDA 12.x, che la GPU sia visibile).
- **Ollama non attivo**: l’elaborazione chunk fallisce con un errore esplicito (avvia `ollama serve` e verifica il modello).
- **GUI bloccata**: non dovrebbe succedere; i passi pesanti (estrazione, trascrizione, Ollama) girano in thread separato e gli aggiornamenti arrivano via coda eventi.
