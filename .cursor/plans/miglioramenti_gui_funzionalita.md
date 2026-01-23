# Piano: Miglioramenti GUI e Funzionalità Avanzate

## Obiettivi

1. **GUI Moderna**: Interfaccia più accattivante con layout organizzato e stili migliorati
2. **Parametri Fase 2**: Aggiungere controlli per chunk size e overlap nella Fase 2
3. **Dashboard Statistiche**: Mostrare durata audio, lunghezza trascrizione e notes
4. **Google AI Studio API**: Integrazione opzionale per usare Gemini invece di Ollama
5. **Rinominare temp_cleaned_notes.md**: Usare convenzione `<video>.notes.md`

## Implementazione Dettagliata

### 1. GUI Moderna e Accattivante

**File**: `main.py`

**Modifiche**:
- Usare `ttk.Style()` per temi moderni (es: 'clam', 'alt')
- Organizzare layout in sezioni con `ttk.LabelFrame`:
  - Sezione "File" (caricamento MP4)
  - Sezione "Parametri Fase 1" (chunk, overlap)
  - Sezione "Parametri Fase 2" (chunk, overlap, max_tokens)
  - Sezione "API Options" (Ollama locale vs Google AI Studio)
  - Sezione "Dashboard" (statistiche)
  - Sezione "Log" (output)
- Aggiungere colori e padding migliorati
- Migliorare progress bar con stile personalizzato

**Struttura Layout**:
```
┌─────────────────────────────────────┐
│ Header: Titolo                     │
├─────────────────────────────────────┤
│ File Selection                      │
├─────────────────────────────────────┤
│ Parametri Fase 1                    │
│   - Chunk max parole                │
│   - Overlap                         │
├─────────────────────────────────────┤
│ Parametri Fase 2                    │
│   - Chunk max parole                │
│   - Overlap                         │
│   - Max tokens per chunk            │
├─────────────────────────────────────┤
│ API Options                         │
│   - [ ] Usa Google AI Studio        │
│   - API Key: [________________]     │
│   - Start from: [Trascrizione/Notes]│
├─────────────────────────────────────┤
│ Dashboard Statistiche               │
│   - Durata audio: --:--            │
│   - Trascrizione: 0 parole, 0 char │
│   - Notes: 0 parole, 0 char         │
├─────────────────────────────────────┤
│ Progress Bar                        │
├─────────────────────────────────────┤
│ Log Area (ScrolledText)            │
└─────────────────────────────────────┘
```

### 2. Parametri Fase 2

**File**: `main.py`, `processor.py`

**Modifiche**:
- Aggiungere campi GUI per:
  - `phase2_chunk_max_words` (default: 2500)
  - `phase2_overlap` (default: 100)
  - `phase2_max_tokens` (default: 8000)
- Modificare `phase2_write_chapter()` per accettare questi parametri
- Implementare chunking con overlap per Fase 2 (se necessario)
- Passare i parametri da `main.py` a `phase2_write_chapter()`

### 3. Dashboard Statistiche

**File**: `main.py`

**Modifiche**:
- Creare sezione dashboard con `ttk.LabelFrame`
- Aggiungere label per statistiche:
  - Durata audio (da `VideoFileClip.duration` o `info.duration`)
  - Trascrizione: caratteri e parole
  - Notes: caratteri e parole
- Aggiornare statistiche durante il workflow
- Formattare numeri con separatori

### 4. Google AI Studio API

**File**: `processor.py`, `main.py`, `pyproject.toml`

**Modifiche**:
- Aggiungere dipendenza: `google-generativeai` in `pyproject.toml`
- Creare funzione `google_ai_write_chapter()` in `processor.py`
- Aggiungere GUI: checkbox, API key field, radio buttons per start point
- Modificare workflow per supportare Google AI Studio

### 5. Rinominare temp_cleaned_notes.md

**File**: `main.py`, `README.md`

**Modifiche**:
- Cambiare `temp_cleaned_path = out_dir / "temp_cleaned_notes.md"` 
- In: `notes_path = out_dir / f"{mp4_path.stem}.notes.md"`
- Aggiornare tutti i riferimenti

## File da Modificare

1. **`main.py`**: GUI, dashboard, workflow
2. **`processor.py`**: Parametri Fase 2, Google AI function
3. **`pyproject.toml`**: Dipendenza google-generativeai
4. **`README.md`**: Documentazione aggiornata

## Todo List

- [ ] Ristrutturare GUI con layout moderno
- [ ] Aggiungere parametri Fase 2 nella GUI
- [ ] Implementare dashboard statistiche
- [ ] Aggiungere sezione API Options
- [ ] Implementare funzione google_ai_write_chapter()
- [ ] Modificare phase2_write_chapter() per parametri
- [ ] Rinominare temp_cleaned_notes.md
- [ ] Aggiornare pyproject.toml
- [ ] Aggiornare README.md
