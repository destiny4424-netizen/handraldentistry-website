"""Speech-to-text (audio) and OCR (on-screen overlay) helpers.

Both heavy dependencies (faster-whisper, pytesseract) are imported lazily
so that the rest of this tool works without them installed.
"""
from __future__ import annotations

from pathlib import Path

_whisper_model = None


def transcribe_audio(audio_path: Path, model_size: str = "base") -> str:
    global _whisper_model
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise SystemExit(
            "faster-whisper is not installed. Run: pip install -r requirements.txt"
        ) from e
    if _whisper_model is None:
        _whisper_model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = _whisper_model.transcribe(str(audio_path))
    return " ".join(seg.text for seg in segments).strip()


def ocr_frame(frame_path: Path) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise SystemExit(
            "pytesseract/Pillow are not installed. Run: pip install -r requirements.txt "
            "(also requires the tesseract-ocr system package -- see the README)"
        ) from e
    return pytesseract.image_to_string(Image.open(frame_path)).strip()
