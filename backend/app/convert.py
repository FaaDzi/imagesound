"""
Audio format conversion via PyAV (bundled FFmpeg libraries).

Convert on-demand — the canonical stored file is always the original WAV.
Conversions are written to a temporary file, served once, then deleted by
the caller (use a BackgroundTask on the returned path).

PyAV ships with libmp3lame, libvorbis, aac, and flac built in, so no
system ffmpeg binary is required.
"""

import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

# fmt -> (PyAV codec name, container format, MIME type, optional bit_rate)
# Quality settings are intentionally easy to adjust here.
_FORMAT_CONFIG: dict[str, tuple[str, str, str, int | None]] = {
    #         codec          container  MIME              bit_rate
    "wav":  ("pcm_s16le",   "wav",     "audio/wav",      None),
    "mp3":  ("libmp3lame",  "mp3",     "audio/mpeg",     192_000),
    "flac": ("flac",        "flac",    "audio/flac",     None),
    "m4a":  ("aac",         "ipod",    "audio/mp4",      192_000),
    "ogg":  ("libvorbis",   "ogg",     "audio/ogg",      None),
}

ALLOWED_FORMATS: frozenset[str] = frozenset(_FORMAT_CONFIG)


def conversion_available() -> bool:
    """Return True if PyAV is importable (it bundles FFmpeg — no system binary needed)."""
    try:
        import av  # noqa: F401
        return True
    except ImportError:
        return False


def content_type_for(fmt: str) -> str:
    return _FORMAT_CONFIG[fmt][2]


def convert_audio(src_path: Path, target_format: str) -> Path:
    """
    Convert *src_path* (WAV) to *target_format* using PyAV.

    Returns the path of a newly created temp file.  The caller is responsible
    for deleting it (use a BackgroundTask so it is removed after the response
    is sent).

    Raises ValueError for unsupported/wav target, RuntimeError on encode failure.
    """
    if target_format not in ALLOWED_FORMATS:
        raise ValueError(
            f"Format '{target_format}' is not supported. "
            f"Allowed: {sorted(ALLOWED_FORMATS)}"
        )
    if target_format == "wav":
        raise ValueError("WAV is stored natively — serve the source file directly.")

    codec, container, _, bit_rate = _FORMAT_CONFIG[target_format]

    fd, tmp_str = tempfile.mkstemp(suffix=f".{target_format}")
    tmp_path = Path(tmp_str)
    try:
        os.close(fd)
    except OSError:
        pass

    try:
        import av

        with av.open(str(src_path), "r") as inp:
            in_stream = inp.streams.audio[0]
            with av.open(str(tmp_path), "w", format=container) as out:
                out_stream = out.add_stream(codec, rate=in_stream.rate)
                if bit_rate is not None:
                    out_stream.bit_rate = bit_rate

                for frame in inp.decode(in_stream):
                    frame.pts = None
                    for packet in out_stream.encode(frame):
                        out.mux(packet)

                for packet in out_stream.encode(None):  # flush encoder
                    out.mux(packet)

        return tmp_path

    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Audio conversion to {target_format} failed: {exc}") from exc
