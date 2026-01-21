from __future__ import annotations

from pathlib import Path

from moviepy import VideoFileClip


def extract_audio_to_wav(
    mp4_path: str | Path,
    wav_path: str | Path,
    *,
    sample_rate: int = 16000,
) -> Path:
    """
    Estrae l'audio da MP4 e salva in WAV 16kHz mono (pcm_s16le).
    """
    mp4_path = Path(mp4_path)
    wav_path = Path(wav_path)
    wav_path.parent.mkdir(parents=True, exist_ok=True)

    clip = None
    try:
        clip = VideoFileClip(str(mp4_path))
        if clip.audio is None:
            raise RuntimeError("Il video non contiene una traccia audio.")

        # moviepy usa ffmpeg sotto; forziamo mono + 16kHz + PCM 16-bit
        clip.audio.write_audiofile(
            filename=str(wav_path),
            fps=sample_rate,
            nbytes=2,
            codec="pcm_s16le",
            ffmpeg_params=["-ac", "1"],
            logger=None,
        )
    finally:
        # Chiudiamo sempre per rilasciare handle/file
        try:
            if clip is not None:
                clip.close()
        except Exception:
            pass

    return wav_path

