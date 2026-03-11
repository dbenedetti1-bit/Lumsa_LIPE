from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from tkinter import END, BOTH, DISABLED, NORMAL, Tk, filedialog, messagebox, StringVar, IntVar
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from audio import extract_audio_to_wav
from constants import DEFAULT_GEMINI_MODEL, GEMINI_MODELS
from exporter import save_docx, save_markdown
from pdf_reader import extract_text_from_pdf
from processor import phase1_clean_transcript, phase2_write_chapter, google_ai_write_chapter
from transcriber import load_transcript_from_markdown, segments_to_timestamped_text, transcribe_wav
from moviepy import VideoFileClip


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _fmt_mmss(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    s = int(seconds)
    return f"{s//60:02d}:{s%60:02d}"


def _fmt_progress_time(label: str, done_s: float, total_s: float | None) -> str:
    left = _fmt_mmss(done_s)
    right = _fmt_mmss(total_s)
    return f"{label}: {left} / {right}"


def _fmt_progress_chunks(label: str, done: float, total: float | None) -> str:
    if total and total > 0:
        return f"{label}: chunk {int(done)}/{int(total)}"
    return f"{label}: in corso..."


class App(Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Trascrizioni - MP4 / PDF → Capitolo di Libro")
        self.minsize(900, 700)

        self._event_q: Queue[tuple[str, object]] = Queue()
        self._worker: threading.Thread | None = None
        self._phase = "idle"
        
        # Statistiche
        self._stats = {
            "audio_duration": None,
            "transcript_chars": 0,
            "transcript_words": 0,
            "notes_chars": 0,
            "notes_words": 0,
        }

        self._build_ui()
        self.after(100, self._drain_events)

    def _build_ui(self) -> None:
        # Configura stile moderno
        style = ttk.Style()
        style.theme_use("clam")
        
        # Container principale con padding
        main_container = ttk.Frame(self, padding=10)
        main_container.pack(fill=BOTH, expand=True)
        
        # ========================================================================
        # Sezione: File Selection
        # ========================================================================
        file_frame = ttk.LabelFrame(main_container, text="File", padding=8)
        file_frame.pack(fill="x", pady=(0, 8))
        
        file_inner = ttk.Frame(file_frame)
        file_inner.pack(fill="x")
        
        self.btn_pick_mp4 = ttk.Button(file_inner, text="Carica MP4...", command=self._pick_mp4)
        self.btn_pick_mp4.pack(side="left", padx=(0, 10))
        self.btn_pick_pdf = ttk.Button(file_inner, text="Carica PDF (trascrizione)...", command=self._pick_pdf)
        self.btn_pick_pdf.pack(side="left", padx=(0, 10))
        
        self.lbl_file = ttk.Label(file_inner, text="Nessun file selezionato", foreground="gray")
        self.lbl_file.pack(side="left")
        
        # ========================================================================
        # Sezione: Parametri Fase 1
        # ========================================================================
        phase1_frame = ttk.LabelFrame(main_container, text="Parametri Fase 1 (Cleaner)", padding=8)
        phase1_frame.pack(fill="x", pady=(0, 8))
        
        phase1_inner = ttk.Frame(phase1_frame)
        phase1_inner.pack(fill="x")
        
        ttk.Label(phase1_inner, text="Chunk max parole:").grid(row=0, column=0, padx=(0, 5), sticky="w")
        self.chunk_var = ttk.Entry(phase1_inner, width=10)
        self.chunk_var.insert(0, "2500")
        self.chunk_var.grid(row=0, column=1, padx=(0, 15))
        
        ttk.Label(phase1_inner, text="Overlap (caratteri):").grid(row=0, column=2, padx=(0, 5), sticky="w")
        self.overlap_var = ttk.Entry(phase1_inner, width=10)
        self.overlap_var.insert(0, "100")
        self.overlap_var.grid(row=0, column=3)
        
        # ========================================================================
        # Sezione: Parametri Fase 2
        # ========================================================================
        phase2_frame = ttk.LabelFrame(main_container, text="Parametri Fase 2 (Author)", padding=8)
        phase2_frame.pack(fill="x", pady=(0, 8))
        
        phase2_inner = ttk.Frame(phase2_frame)
        phase2_inner.pack(fill="x")
        
        ttk.Label(phase2_inner, text="Chunk max parole:").grid(row=0, column=0, padx=(0, 5), sticky="w")
        self.phase2_chunk_var = ttk.Entry(phase2_inner, width=10)
        self.phase2_chunk_var.insert(0, "2500")
        self.phase2_chunk_var.grid(row=0, column=1, padx=(0, 15))
        
        ttk.Label(phase2_inner, text="Overlap (caratteri):").grid(row=0, column=2, padx=(0, 5), sticky="w")
        self.phase2_overlap_var = ttk.Entry(phase2_inner, width=10)
        self.phase2_overlap_var.insert(0, "100")
        self.phase2_overlap_var.grid(row=0, column=3, padx=(0, 15))
        
        ttk.Label(phase2_inner, text="Max tokens/chunk:").grid(row=0, column=4, padx=(0, 5), sticky="w")
        self.phase2_max_tokens_var = ttk.Entry(phase2_inner, width=10)
        self.phase2_max_tokens_var.insert(0, "8000")
        self.phase2_max_tokens_var.grid(row=0, column=5)
        
        # ========================================================================
        # Sezione: API Options
        # ========================================================================
        api_frame = ttk.LabelFrame(main_container, text="API Options", padding=8)
        api_frame.pack(fill="x", pady=(0, 8))
        
        api_inner = ttk.Frame(api_frame)
        api_inner.pack(fill="x")
        
        self.use_google_ai_var = IntVar(value=0)
        ttk.Checkbutton(api_inner, text="Usa Google AI Studio", variable=self.use_google_ai_var, 
                       command=self._toggle_google_ai).grid(row=0, column=0, sticky="w", padx=(0, 15))
        
        ttk.Label(api_inner, text="API Key:").grid(row=0, column=1, padx=(0, 5), sticky="w")
        self.google_api_key_var = StringVar()
        self.google_api_key_entry = ttk.Entry(api_inner, textvariable=self.google_api_key_var, width=40, show="*")
        self.google_api_key_entry.grid(row=0, column=2, padx=(0, 15))
        self.google_api_key_entry.configure(state=DISABLED)
        
        ttk.Label(api_inner, text="Modello:").grid(row=0, column=3, padx=(0, 5), sticky="w")
        self.google_model_var = StringVar(value=DEFAULT_GEMINI_MODEL)
        google_model_combo = ttk.Combobox(api_inner, textvariable=self.google_model_var, width=20, state="readonly")
        google_model_combo["values"] = GEMINI_MODELS
        google_model_combo.grid(row=0, column=4, padx=(0, 15))
        google_model_combo.configure(state=DISABLED)
        self.google_model_combo = google_model_combo
        
        ttk.Label(api_inner, text="Start from:").grid(row=0, column=5, padx=(0, 5), sticky="w")
        self.google_start_from_var = StringVar(value="notes")
        self.radio_notes = ttk.Radiobutton(api_inner, text="Notes", variable=self.google_start_from_var, 
                       value="notes", state=DISABLED)
        self.radio_notes.grid(row=0, column=6, padx=(0, 10))
        self.radio_transcript = ttk.Radiobutton(api_inner, text="Trascrizione", variable=self.google_start_from_var, 
                       value="transcript", state=DISABLED)
        self.radio_transcript.grid(row=0, column=7)
        
        # ========================================================================
        # Sezione: Dashboard Statistiche
        # ========================================================================
        dashboard_frame = ttk.LabelFrame(main_container, text="Dashboard Statistiche", padding=8)
        dashboard_frame.pack(fill="x", pady=(0, 8))
        
        dashboard_inner = ttk.Frame(dashboard_frame)
        dashboard_inner.pack(fill="x")
        
        self.lbl_audio_duration = ttk.Label(dashboard_inner, text="Durata audio: --:--")
        self.lbl_audio_duration.grid(row=0, column=0, padx=(0, 20), sticky="w")
        
        self.lbl_transcript_stats = ttk.Label(dashboard_inner, text="Trascrizione: 0 parole, 0 caratteri")
        self.lbl_transcript_stats.grid(row=0, column=1, padx=(0, 20), sticky="w")
        
        self.lbl_notes_stats = ttk.Label(dashboard_inner, text="Notes: 0 parole, 0 caratteri")
        self.lbl_notes_stats.grid(row=0, column=2, sticky="w")
        
        # ========================================================================
        # Progress Bar
        # ========================================================================
        self.progress = ttk.Progressbar(main_container, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 5))
        
        self.progress_label = ttk.Label(main_container, text="Pronto.")
        self.progress_label.pack(fill="x", pady=(0, 8))
        
        # ========================================================================
        # Sezione: Log
        # ========================================================================
        log_frame = ttk.LabelFrame(main_container, text="Log", padding=8)
        log_frame.pack(fill=BOTH, expand=True)
        
        self.log = ScrolledText(log_frame, height=15, wrap="word")
        self.log.pack(fill=BOTH, expand=True)
        self._log("Pronto. Seleziona un file MP4 o un PDF con trascrizione.")
        
        # ========================================================================
        # Bottone Start
        # ========================================================================
        self.btn_start = ttk.Button(main_container, text="Avvia Workflow", command=self._start, state=DISABLED)
        self.btn_start.pack(pady=(8, 0))
        
        self.input_path: Path | None = None
        self.input_type: str | None = None  # "mp4" | "pdf"
    
    def _toggle_google_ai(self) -> None:
        """Abilita/disabilita i controlli Google AI in base al checkbox"""
        state = NORMAL if self.use_google_ai_var.get() else DISABLED
        self.google_api_key_entry.configure(state=state)
        self.google_model_combo.configure(state=state)
        self.radio_notes.configure(state=state)
        self.radio_transcript.configure(state=state)
    
    def _update_stats(self) -> None:
        """Aggiorna le statistiche nella dashboard"""
        # Durata audio
        if self._stats["audio_duration"] is not None:
            self.lbl_audio_duration.configure(text=f"Durata audio: {_fmt_mmss(self._stats['audio_duration'])}")
        else:
            self.lbl_audio_duration.configure(text="Durata audio: --:--")
        
        # Trascrizione
        words = self._stats["transcript_words"]
        chars = self._stats["transcript_chars"]
        self.lbl_transcript_stats.configure(text=f"Trascrizione: {words:,} parole, {chars:,} caratteri")
        
        # Notes
        words = self._stats["notes_words"]
        chars = self._stats["notes_chars"]
        self.lbl_notes_stats.configure(text=f"Notes: {words:,} parole, {chars:,} caratteri")

    def _log(self, msg: str) -> None:
        self.log.insert(END, f"[{_ts()}] {msg}\n")
        self.log.see(END)

    def _pick_mp4(self) -> None:
        path = filedialog.askopenfilename(
            title="Seleziona video MP4",
            filetypes=[("MP4 Video", "*.mp4"), ("All files", "*.*")],
        )
        if not path:
            return
        self.input_path = Path(path)
        self.input_type = "mp4"
        self.lbl_file.configure(text=str(self.input_path))
        self.btn_start.configure(state=NORMAL)
        self._log(f"Selezionato MP4: {self.input_path.name}")

    def _pick_pdf(self) -> None:
        path = filedialog.askopenfilename(
            title="Seleziona PDF con trascrizione",
            filetypes=[("PDF", "*.pdf"), ("All files", "*.*")],
        )
        if not path:
            return
        self.input_path = Path(path)
        self.input_type = "pdf"
        self.lbl_file.configure(text=str(self.input_path))
        self.btn_start.configure(state=NORMAL)
        self._log(f"Selezionato PDF: {self.input_path.name}")

    def _start(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("In esecuzione", "Il workflow è già in esecuzione.")
            return
        if not self.input_path or not self.input_type:
            messagebox.showwarning("File mancante", "Seleziona un file MP4 o un PDF con trascrizione.")
            return

        # Valida parametri Fase 1
        try:
            max_words = int(self.chunk_var.get().strip())
        except Exception:
            messagebox.showerror("Valore non valido", "Chunk max parole (Fase 1) deve essere un numero intero.")
            return

        try:
            overlap = int(self.overlap_var.get().strip())
            if overlap < 0:
                raise ValueError("Overlap non può essere negativo")
        except Exception:
            messagebox.showerror("Valore non valido", "Overlap (Fase 1) deve essere un numero intero non negativo.")
            return

        # Valida parametri Fase 2
        try:
            phase2_max_words = int(self.phase2_chunk_var.get().strip())
        except Exception:
            messagebox.showerror("Valore non valido", "Chunk max parole (Fase 2) deve essere un numero intero.")
            return

        try:
            phase2_overlap = int(self.phase2_overlap_var.get().strip())
            if phase2_overlap < 0:
                raise ValueError("Overlap non può essere negativo")
        except Exception:
            messagebox.showerror("Valore non valido", "Overlap (Fase 2) deve essere un numero intero non negativo.")
            return

        try:
            phase2_max_tokens = int(self.phase2_max_tokens_var.get().strip())
            if phase2_max_tokens < 1000:
                raise ValueError("Max tokens troppo basso")
        except Exception:
            messagebox.showerror("Valore non valido", "Max tokens (Fase 2) deve essere un numero intero >= 1000.")
            return

        # Valida Google AI se selezionato
        use_google_ai = self.use_google_ai_var.get() == 1
        google_api_key = ""
        google_model = DEFAULT_GEMINI_MODEL
        google_start_from = "notes"
        if use_google_ai:
            google_api_key = self.google_api_key_var.get().strip()
            if not google_api_key:
                messagebox.showerror("API Key mancante", "Inserisci la Google AI Studio API Key.")
                return
            google_model = self.google_model_var.get().strip()
            if not google_model or google_model not in GEMINI_MODELS:
                messagebox.showerror("Modello non valido", f"Seleziona un modello valido tra: {', '.join(GEMINI_MODELS)}")
                return
            google_start_from = self.google_start_from_var.get()

        # Reset statistiche
        self._stats = {
            "audio_duration": None,
            "transcript_chars": 0,
            "transcript_words": 0,
            "notes_chars": 0,
            "notes_words": 0,
        }
        self._update_stats()

        self.progress.configure(value=0, maximum=100)
        self.btn_start.configure(state=DISABLED)
        self.btn_pick_mp4.configure(state=DISABLED)
        self.btn_pick_pdf.configure(state=DISABLED)
        
        api_type = "Google AI Studio" if use_google_ai else "Ollama"
        self._log(f"Avvio workflow in background... ({self.input_type.upper()} + {api_type})")

        self._worker = threading.Thread(
            target=self._run_workflow,
            kwargs={
                "input_path": self.input_path,
                "input_type": self.input_type,
                "max_words": max_words,
                "overlap": overlap,
                "phase2_max_words": phase2_max_words,
                "phase2_overlap": phase2_overlap,
                "phase2_max_tokens": phase2_max_tokens,
                "use_google_ai": use_google_ai,
                "google_api_key": google_api_key,
                "google_model": google_model,
                "google_start_from": google_start_from,
            },
            daemon=True,
        )
        self._worker.start()

    def _emit(self, kind: str, payload: object) -> None:
        self._event_q.put((kind, payload))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self._event_q.get_nowait()
                if kind == "log":
                    self._log(str(payload))
                elif kind == "progress":
                    phase, done, total = payload  # type: ignore[misc]
                    label_txt = ""
                    if phase == "transcription":
                        val = (done / total) * 40.0 if total and total > 0 else 0
                        label_txt = _fmt_progress_time("Trascrizione", done, total)
                    elif phase == "phase1":
                        val = 40.0 + ((done / total) * 30.0 if total and total > 0 else 0)
                        label_txt = _fmt_progress_chunks("Fase 1 (Cleaner)", done, total)
                    elif phase == "phase2":
                        val = 70.0 + ((done / total) * 30.0 if total and total > 0 else 0)
                        label_txt = _fmt_progress_chunks("Fase 2 (Author)", done, total)
                    else:
                        val = 0
                    self.progress.configure(value=min(val, 100))
                    if label_txt:
                        self.progress_label.configure(text=label_txt)
                elif kind == "stats":
                    # Aggiorna statistiche
                    self._stats.update(payload)  # type: ignore[arg-type]
                    self._update_stats()
                elif kind == "done":
                    self._log("Completato.")
                    self.btn_start.configure(state=NORMAL)
                    self.btn_pick_mp4.configure(state=NORMAL)
                    self.btn_pick_pdf.configure(state=NORMAL)
                    self.progress.configure(value=100)
                    self.progress_label.configure(text="Completato.")
                elif kind == "error":
                    self.btn_start.configure(state=NORMAL)
                    self.btn_pick_mp4.configure(state=NORMAL)
                    self.btn_pick_pdf.configure(state=NORMAL)
                    self.progress.configure(value=0)
                    self.progress_label.configure(text="Errore.")
                    messagebox.showerror("Errore", str(payload))
                else:
                    # ignora
                    pass
        except Empty:
            pass
        finally:
            self.after(100, self._drain_events)

    def _run_workflow(
        self,
        *,
        input_path: Path,
        input_type: str,
        max_words: int,
        overlap: int,
        phase2_max_words: int,
        phase2_overlap: int,
        phase2_max_tokens: int,
        use_google_ai: bool,
        google_api_key: str,
        google_model: str,
        google_start_from: str,
    ) -> None:
        """
        Pipeline: da MP4 (audio→trascrizione) o da PDF (testo già estratto),
        poi FASE 1 (opzionale), FASE 2 (Author), export MD + DOCX.
        """
        try:
            t0 = time.time()
            stem = input_path.stem
            out_dir = input_path.parent / "output" / stem
            out_dir.mkdir(parents=True, exist_ok=True)

            transcript_text: str | None = None

            if input_type == "pdf":
                # Input da PDF: estrazione testo (trascrizione già in PDF)
                self._emit("log", f"Lettura PDF: {input_path.name}")
                transcript_text = extract_text_from_pdf(input_path)
                # Salva trascrizione estratta in .transcript.md per coerenza con il workflow
                transcript_md_path = out_dir / f"{stem}.transcript.md"
                save_markdown(transcript_md_path, "# Trascrizione (da PDF)\n\n" + transcript_text)
                self._emit("log", f"Testo estratto e salvato in {transcript_md_path.name}")
                self._emit("progress", ("transcription", 100.0, 100.0))
            else:
                # Input da MP4: audio + Whisper
                try:
                    clip = VideoFileClip(str(input_path))
                    audio_duration = clip.duration
                    clip.close()
                    self._emit("stats", {"audio_duration": audio_duration})
                except Exception:
                    pass

                transcript_md_path = out_dir / f"{stem}.transcript.md"
                transcript_text = load_transcript_from_markdown(transcript_md_path)
                if transcript_text:
                    n_words = len(transcript_text.split())
                    self._emit("log", f"Trascrizione già presente ({n_words:,} parole), salto trascrizione → {transcript_md_path.name}")
                    self._emit("progress", ("transcription", 100.0, 100.0))
                else:
                    wav_path = out_dir / f"{stem}.wav"
                    if wav_path.exists() and wav_path.stat().st_size > 0:
                        self._emit("log", f"WAV già presente, salto estrazione → {wav_path.name}")
                    else:
                        self._emit("log", f"Estrazione audio → {wav_path.name}")
                        extract_audio_to_wav(input_path, wav_path)

                    self._emit("log", "Trascrizione Faster-Whisper (large-v3, cuda float16)...")
                    transcript_md_path.write_text("# Trascrizione (timestamp)\n\n", encoding="utf-8")
                    segments = transcribe_wav(
                        wav_path,
                        progress_cb=lambda done, total: self._emit("progress", ("transcription", done, total)),
                        partial_path=transcript_md_path,
                        partial_interval_s=300,
                    )
                    transcript_text = segments_to_timestamped_text(segments)
                    save_markdown(transcript_md_path, "# Trascrizione (timestamp)\n\n" + transcript_text)
                    self._emit("log", f"Salvata trascrizione: {transcript_md_path.name}")

            if transcript_text:
                self._emit("stats", {
                    "transcript_chars": len(transcript_text),
                    "transcript_words": len(transcript_text.split()),
                })

            if not transcript_text or len(transcript_text.strip()) < 10:
                raise RuntimeError(
                    "Trascrizione vuota o troppo corta. Verifica il file (audio/PDF) o riprova."
                )

            # ========================================================================
            # FASE 1: "The Cleaner" - Pulizia (Ollama). Saltata se: Google AI + (trascrizione oppure PDF)
            # ========================================================================
            skip_phase1 = use_google_ai and (
                google_start_from == "transcript" or input_type == "pdf"
            )
            cleaned_notes = ""
            if not skip_phase1:
                self._emit("log", "FASE 1: Pulizia trascrizione con llama3.1:8b...")
                try:
                    cleaned_notes = phase1_clean_transcript(
                        transcript_text,
                        model="llama3.1:8b",
                        max_words_per_chunk=max_words,
                        overlap=overlap,
                        progress_cb=lambda d, t: self._emit("progress", ("phase1", d, t)),
                        log_cb=lambda m: self._emit("log", m),
                    )
                except Exception as e:
                    self._emit("log", f"ERRORE durante FASE 1 (Cleaner): {e}")
                    raise

                if not cleaned_notes or len(cleaned_notes.strip()) < 10:
                    raise RuntimeError("FASE 1: Output vuoto o troppo corto. Verifica il modello llama3.1:8b.")

                # Salva file intermedio
                notes_path = out_dir / f"{stem}.notes.md"
                save_markdown(notes_path, cleaned_notes)
                self._emit("log", f"FASE 1 completata: appunti puliti salvati in {notes_path.name}")
                
                # Aggiorna statistiche notes
                self._emit("stats", {
                    "notes_chars": len(cleaned_notes),
                    "notes_words": len(cleaned_notes.split()),
                })
            else:
                # Google AI da trascrizione o da PDF: nessuna Fase 1, nessun Ollama
                self._emit("log", "FASE 1 saltata: uso API direttamente dal testo (nessun Ollama).")
                cleaned_notes = transcript_text  # Usa trascrizione come input per Google AI
                n_words = len(cleaned_notes.split())
                self._emit("log", f"Input per FASE 2: {n_words:,} parole dalla trascrizione.")

            # ========================================================================
            # FASE 2: "The Author" - Scrittura capitolo
            # ========================================================================
            if use_google_ai:
                self._emit("log", f"FASE 2: Scrittura capitolo con Google AI Studio (modello: {google_model})...")
                try:
                    chapter_text = google_ai_write_chapter(
                        cleaned_notes,
                        api_key=google_api_key,
                        prompts_md_path=Path(__file__).with_name("prompts.md"),
                        model=google_model,
                        max_words_per_chunk=phase2_max_words,
                        overlap=phase2_overlap,
                        progress_cb=lambda d, t: self._emit("progress", ("phase2", d, t)),
                        log_cb=lambda m: self._emit("log", m),
                    )
                except Exception as e:
                    self._emit("log", f"ERRORE durante FASE 2 (Google AI): {e}")
                    raise
            else:
                self._emit("log", "FASE 2: Scrittura capitolo con qwen2.5:14b (può richiedere tempo)...")
                try:
                    chapter_text = phase2_write_chapter(
                        cleaned_notes,
                        prompts_md_path=Path(__file__).with_name("prompts.md"),
                        model="qwen2.5:14b",
                        max_words_per_chunk=phase2_max_words,
                        overlap=phase2_overlap,
                        max_tokens_per_chunk=phase2_max_tokens,
                        progress_cb=lambda d, t: self._emit("progress", ("phase2", d, t)),
                        log_cb=lambda m: self._emit("log", m),
                    )
                except Exception as e:
                    self._emit("log", f"ERRORE durante FASE 2 (Author): {e}")
                    raise

            if not chapter_text or len(chapter_text.strip()) < 10:
                api_name = "Google AI" if use_google_ai else "qwen2.5:14b"
                raise RuntimeError(f"FASE 2: Output vuoto o troppo corto. Verifica {api_name}.")

            # ========================================================================
            # Export finale
            # ========================================================================
            try:
                chapter_md_path = out_dir / f"{stem}.capitolo.md"
                save_markdown(chapter_md_path, chapter_text)
                self._emit("log", f"Salvato capitolo Markdown: {chapter_md_path.name}")
            except Exception as e:
                self._emit("log", f"ERRORE durante salvataggio Markdown: {e}")
                raise

            try:
                docx_path = out_dir / f"{stem}.docx"
                save_docx(docx_path, chapter_text, title=f"Capitolo - {stem}")
                self._emit("log", f"Creato DOCX: {docx_path.name}")
            except Exception as e:
                self._emit("log", f"ERRORE durante creazione DOCX: {e}")
                raise

            elapsed = time.time() - t0
            self._emit("log", f"Output in: {out_dir}")
            self._emit("log", f"Tempo totale: {elapsed:.1f}s")
            self._emit("done", True)
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            tb_str = traceback.format_exc()
            self._emit("log", f"ERRORE nel workflow:\n{tb_str}")
            self._emit("error", error_msg)


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
