from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from tkinter import END, BOTH, DISABLED, NORMAL, Tk, filedialog, messagebox
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from audio import extract_audio_to_wav
from exporter import save_docx, save_markdown
from processor import phase1_clean_transcript, phase2_write_chapter
from transcriber import load_transcript_from_markdown, segments_to_timestamped_text, transcribe_wav


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
        self.title("Trascrizioni - MP4 → DOC")
        self.minsize(820, 520)

        self._event_q: Queue[tuple[str, object]] = Queue()
        self._worker: threading.Thread | None = None
        self._phase = "idle"

        self._build_ui()
        self.after(100, self._drain_events)

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 8}

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)

        self.btn_pick = ttk.Button(top, text="Carica MP4...", command=self._pick_mp4)
        self.btn_pick.pack(side="left")

        self.lbl_file = ttk.Label(top, text="Nessun file selezionato")
        self.lbl_file.pack(side="left", padx=10)

        options = ttk.Frame(self)
        options.pack(fill="x", **pad)

        ttk.Label(options, text="Chunk max parole (Fase 1):").pack(side="left")
        self.chunk_var = ttk.Entry(options, width=8)
        self.chunk_var.insert(0, "2500")
        self.chunk_var.pack(side="left", padx=(6, 14))

        ttk.Label(options, text="Overlap (caratteri):").pack(side="left")
        self.overlap_var = ttk.Entry(options, width=8)
        self.overlap_var.insert(0, "100")
        self.overlap_var.pack(side="left", padx=(6, 14))

        self.btn_start = ttk.Button(options, text="Avvia workflow", command=self._start, state=DISABLED)
        self.btn_start.pack(side="left")

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(fill="x", **pad)
        self.progress_label = ttk.Label(self, text="Pronto.")
        self.progress_label.pack(fill="x", padx=10, pady=(0, 6))

        self.log = ScrolledText(self, height=18)
        self.log.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))
        self._log("Pronto. Seleziona un MP4.")

        self.mp4_path: Path | None = None

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
        self.mp4_path = Path(path)
        self.lbl_file.configure(text=str(self.mp4_path))
        self.btn_start.configure(state=NORMAL)
        self._log(f"Selezionato: {self.mp4_path.name}")

    def _start(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("In esecuzione", "Il workflow è già in esecuzione.")
            return
        if not self.mp4_path:
            messagebox.showwarning("File mancante", "Seleziona prima un file MP4.")
            return

        try:
            max_words = int(self.chunk_var.get().strip())
        except Exception:
            messagebox.showerror("Valore non valido", "Chunk max parole deve essere un numero intero.")
            return

        try:
            overlap = int(self.overlap_var.get().strip())
            if overlap < 0:
                raise ValueError("Overlap non può essere negativo")
        except Exception:
            messagebox.showerror("Valore non valido", "Overlap deve essere un numero intero non negativo.")
            return

        self.progress.configure(value=0, maximum=100)
        self.btn_start.configure(state=DISABLED)
        self.btn_pick.configure(state=DISABLED)
        self._log("Avvio workflow in background... (trascrizione + 2 fasi Ollama)")

        self._worker = threading.Thread(
            target=self._run_workflow,
            kwargs={"mp4_path": self.mp4_path, "max_words": max_words, "overlap": overlap},
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
                elif kind == "done":
                    self._log("Completato.")
                    self.btn_start.configure(state=NORMAL)
                    self.btn_pick.configure(state=NORMAL)
                    self.progress.configure(value=100)
                    self.progress_label.configure(text="Completato.")
                elif kind == "error":
                    self.btn_start.configure(state=NORMAL)
                    self.btn_pick.configure(state=NORMAL)
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

    def _run_workflow(self, *, mp4_path: Path, max_words: int, overlap: int) -> None:
        """
        Pipeline completa a due fasi:
        1) MP4 -> WAV
        2) Whisper -> segmenti (timestamp)
        3) FASE 1: Cleaner (llama3.1:8b) -> appunti puliti
        4) FASE 2: Author (qwen2.5:14b) -> capitolo di libro
        5) Export MD + DOCX
        """
        try:
            t0 = time.time()
            out_dir = mp4_path.parent / "output" / mp4_path.stem
            out_dir.mkdir(parents=True, exist_ok=True)

            transcript_md_path = out_dir / f"{mp4_path.stem}.transcript.md"
            
            # Controlla se transcript.md esiste già
            transcript_text = load_transcript_from_markdown(transcript_md_path)
            if transcript_text:
                self._emit("log", f"Trascrizione già presente, salto → {transcript_md_path.name}")
                self._emit("progress", ("transcription", 100.0, 100.0))  # completa al 100%
            else:
                # Estrazione audio
                wav_path = out_dir / f"{mp4_path.stem}.wav"
                if wav_path.exists() and wav_path.stat().st_size > 0:
                    self._emit("log", f"WAV già presente, salto estrazione → {wav_path.name}")
                else:
                    self._emit("log", f"Estrazione audio → {wav_path.name}")
                    extract_audio_to_wav(mp4_path, wav_path)

                # Trascrizione
                self._emit("log", "Trascrizione Faster-Whisper (large-v3, cuda float16)...")
                # header iniziale per append parziali
                transcript_md_path.write_text("# Trascrizione (timestamp)\n\n", encoding="utf-8")

                segments = transcribe_wav(
                    wav_path,
                    progress_cb=lambda done, total: self._emit("progress", ("transcription", done, total)),
                    partial_path=transcript_md_path,
                    partial_interval_s=300,  # ogni 5 minuti
                )
                transcript_text = segments_to_timestamped_text(segments)

                # Riscrive il file con la trascrizione completa (sovrascrive gli append parziali)
                save_markdown(transcript_md_path, "# Trascrizione (timestamp)\n\n" + transcript_text)
                self._emit("log", f"Salvata trascrizione: {transcript_md_path.name}")
            
            # Verifica che il testo non sia vuoto
            if not transcript_text or len(transcript_text.strip()) < 10:
                raise RuntimeError("Trascrizione vuota o troppo corta. Verifica il file audio o riprova la trascrizione.")

            # ========================================================================
            # FASE 1: "The Cleaner" - Pulizia e compressione
            # ========================================================================
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
            temp_cleaned_path = out_dir / "temp_cleaned_notes.md"
            save_markdown(temp_cleaned_path, cleaned_notes)
            self._emit("log", f"FASE 1 completata: appunti puliti salvati in {temp_cleaned_path.name}")

            # ========================================================================
            # FASE 2: "The Author" - Scrittura capitolo
            # ========================================================================
            self._emit("log", "FASE 2: Scrittura capitolo con qwen2.5:14b (può richiedere tempo)...")
            try:
                chapter_text = phase2_write_chapter(
                    cleaned_notes,
                    prompts_md_path=Path(__file__).with_name("prompts.md"),
                    model="qwen2.5:14b",
                    progress_cb=lambda d, t: self._emit("progress", ("phase2", d, t)),
                    log_cb=lambda m: self._emit("log", m),
                )
            except Exception as e:
                self._emit("log", f"ERRORE durante FASE 2 (Author): {e}")
                raise

            if not chapter_text or len(chapter_text.strip()) < 10:
                raise RuntimeError("FASE 2: Output vuoto o troppo corto. Verifica il modello qwen2.5:14b.")

            # ========================================================================
            # Export finale
            # ========================================================================
            try:
                chapter_md_path = out_dir / f"{mp4_path.stem}.capitolo.md"
                save_markdown(chapter_md_path, chapter_text)
                self._emit("log", f"Salvato capitolo Markdown: {chapter_md_path.name}")
            except Exception as e:
                self._emit("log", f"ERRORE durante salvataggio Markdown: {e}")
                raise

            try:
                docx_path = out_dir / f"{mp4_path.stem}.docx"
                save_docx(docx_path, chapter_text, title=f"Capitolo - {mp4_path.stem}")
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
