from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from faster_whisper import WhisperModel


@dataclass(frozen=True)
class TranscriptSegment:
    start_s: float
    end_s: float
    text: str

    @property
    def start_mmss(self) -> str:
        return _format_mmss(self.start_s)

    @property
    def end_mmss(self) -> str:
        return _format_mmss(self.end_s)

    def to_line(self) -> str:
        # Output richiesto: include timestamp [MM:SS]
        return f"[{self.start_mmss}] {self.text.strip()}"


def _format_mmss(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    s = int(seconds)
    mm = s // 60
    ss = s % 60
    return f"{mm:02d}:{ss:02d}"


def transcribe_wav(
    wav_path: str | Path,
    *,
    model_name: str = "large-v3",
    language: str = "it",
    beam_size: int = 5,
    vad_filter: bool = True,
    progress_cb=None,  # progress_cb(done_seconds: float, total_seconds: float | None)
    partial_path: str | Path | None = None,
    partial_interval_s: int = 300,
) -> list[TranscriptSegment]:
    """
    Trascrive un WAV usando Faster-Whisper su CUDA con float16.
    - progress_cb: aggiornamenti di avanzamento in secondi (per la GUI).
    - partial_path: se passato, appende la trascrizione ogni `partial_interval_s`.
    """
    wav_path = Path(wav_path)

    try:
        model = WhisperModel(model_name, device="cuda", compute_type="float16")
    except Exception as e:
        # Richiesto: gestione esplicita mancanza CUDA
        raise RuntimeError(
            "Impossibile inizializzare Faster-Whisper su CUDA (verifica driver/CUDA 12.x e GPU disponibile)."
        ) from e

    try:
        segments, info = model.transcribe(
            str(wav_path),
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
        )
    except Exception as e:
        raise RuntimeError("Errore durante la trascrizione Faster-Whisper.") from e

    total_seconds = getattr(info, "duration", None)
    out: list[TranscriptSegment] = []

    next_flush = float(partial_interval_s) if partial_interval_s else None
    buffer_lines: list[str] = []
    partial_file = Path(partial_path) if partial_path else None

    for seg in segments:
        text = str(seg.text or "").strip()
        if not text:
            # salta silenzi o segmenti vuoti
            continue

        ts = TranscriptSegment(start_s=float(seg.start), end_s=float(seg.end), text=text)
        out.append(ts)

        line = ts.to_line()
        buffer_lines.append(line)

        # progresso
        if progress_cb:
            try:
                progress_cb(ts.end_s, total_seconds)
            except Exception:
                pass

        # flush parziale ogni intervallo
        if partial_file and next_flush is not None and ts.end_s >= next_flush:
            _append_lines(partial_file, buffer_lines)
            buffer_lines.clear()
            next_flush += float(partial_interval_s)

    # flush finale
    if partial_file and buffer_lines:
        _append_lines(partial_file, buffer_lines)

    # completamento al 100%
    if progress_cb and total_seconds:
        try:
            progress_cb(total_seconds, total_seconds)
        except Exception:
            pass

    return out


def _append_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln.rstrip() + "\n")


def segments_to_timestamped_text(segments: list[TranscriptSegment]) -> str:
    return "\n".join(s.to_line() for s in segments)


def load_transcript_from_markdown(md_path: str | Path) -> str | None:
    """
    Carica il testo della trascrizione da un file .transcript.md esistente.
    Rimuove l'header "# Trascrizione (timestamp)" se presente.
    Restituisce None se il file non esiste o è vuoto/invalido.
    """
    md_path = Path(md_path)
    if not md_path.exists() or md_path.stat().st_size == 0:
        return None
    
    try:
        content = md_path.read_text(encoding="utf-8").strip()
        if not content:
            return None
        
        # Rimuove header se presente
        if content.startswith("# Trascrizione"):
            lines = content.splitlines()
            # Salta le prime 2 righe (header + blank)
            if len(lines) > 2:
                content = "\n".join(lines[2:]).strip()
        
        if not content or len(content) < 10:  # minimo ragionevole
            return None
        
        return content
    except Exception:
        return None

