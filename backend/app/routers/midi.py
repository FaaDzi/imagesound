"""
POST /midi/convert/{id}

Queues a Basic Pitch audio-to-MIDI conversion job for an existing, completed
audio entry.  Returns 202 immediately with the new MIDI entry's id; the caller
should poll GET /status/{id} for progress and GET /download/{id}?format=midi
once done.
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from app.access import current_user, owner_filter
from app.config import DIR_CONVERTED, DIR_MIDI, RATE_LIMIT_MIDI, UNSAVED_EXPIRY_SECONDS
from app.database import get_connection
from app.jobs import Job, enqueue
from app.limiter import limiter

router = APIRouter()


def _require_done_midi(file_id: str, request: Request, view: str = "full"):
    """404/409 unless this id is a finished MIDI entry.

    `view="lead"` selects the reduced melody/chords/bass/drums file. Entries
    converted before that existed have no lead file, so this falls back to the
    full one rather than 404ing on a library that predates the feature.
    """
    if view not in ("full", "lead"):
        raise HTTPException(status_code=422, detail="view must be 'full' or 'lead'.")

    clause, params = owner_filter(request)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT job_status, output_format FROM files WHERE id=?" + clause,
            (file_id, *params),
        ).fetchone()

    if row is None or row["output_format"] != "midi":
        raise HTTPException(status_code=404, detail="MIDI entry not found.")
    if row["job_status"] != "done":
        raise HTTPException(status_code=409, detail="MIDI conversion not complete yet.")

    if view == "lead":
        lead_path = DIR_MIDI / f"{file_id}_lead.mid"
        if lead_path.is_file():
            return lead_path
    midi_path = DIR_MIDI / f"{file_id}.mid"
    if not midi_path.is_file():
        raise HTTPException(status_code=404, detail="MIDI file not available for this entry.")
    return midi_path


@router.get("/midi/tracks/{file_id}")
def get_midi_tracks(file_id: str, request: Request, view: str = "full"):
    """Which parts this MIDI contains, for the solo buttons."""
    midi_path = _require_done_midi(file_id, request, view)
    import pretty_midi

    try:
        midi = pretty_midi.PrettyMIDI(str(midi_path))
    except Exception:
        raise HTTPException(status_code=422, detail="MIDI file could not be read.")
    return {
        # Says which file actually answered, so the UI can tell the user when a
        # pre-existing entry has no lead sheet instead of silently showing five
        # stems under a "LEAD" heading.
        "view": "lead" if midi_path.stem.endswith("_lead") else "full",
        "tracks": [
            {"name": inst.name or f"Track {n + 1}",
             "notes": len(inst.notes),
             "is_drum": bool(inst.is_drum)}
            for n, inst in enumerate(midi.instruments) if inst.notes
        ],
    }


@router.get("/midi/preview/{file_id}")
def get_midi_preview(file_id: str, request: Request, track: str | None = None, view: str = "full"):
    """Serve the sonified WAV preview for a completed MIDI entry.

    `track` solos one part by name. Three parts at once is right for judging the
    whole thing and hopeless for checking whether one line is correct, which is
    what you want when a transcription sounds off.

    Solo renders are produced on demand and cached next to the MIDI: synthesis
    is ~0.2s, so the first press is not worth a spinner, but re-rendering on
    every replay would be.
    """
    midi_path = _require_done_midi(file_id, request, view)
    suffix = "_lead" if midi_path.stem.endswith("_lead") else ""

    if track is None:
        preview_path = DIR_MIDI / f"{file_id}{suffix}_preview.wav"
        if not preview_path.is_file():
            raise HTTPException(status_code=404,
                                detail="Preview WAV not available for this entry.")
        return FileResponse(path=str(preview_path), media_type="audio/wav")

    # Track names come from our own stem map, so anything outside it is either a
    # typo or someone poking at the URL; either way it must not reach the
    # filesystem as a path fragment.
    if not track.isalnum() or len(track) > 32:
        raise HTTPException(status_code=422, detail="Invalid track name.")

    solo_path = DIR_MIDI / f"{file_id}{suffix}_preview_{track.lower()}.wav"
    if not solo_path.is_file():
        import pretty_midi
        from pipeline.midi_convert import _write_preview_wav

        try:
            midi = pretty_midi.PrettyMIDI(str(midi_path))
            _write_preview_wav(midi, solo_path, only=track)
        except ValueError:
            raise HTTPException(status_code=404, detail=f"No track named {track!r}.")
        except Exception:
            raise HTTPException(status_code=422, detail="Could not render that track.")

    return FileResponse(path=str(solo_path), media_type="audio/wav")


@router.get("/midi/strudel/{file_id}")
def get_strudel_code(file_id: str, request: Request, mode: str = "chords", view: str = "full"):
    """Render a completed MIDI entry as Strudel pattern code.

    Derived on demand rather than stored: parsing the .mid and emitting the
    snippet takes milliseconds and needs none of the heavy transcription
    dependencies, so MIDI entries made before this endpoint existed work too.

    `view="lead"` reads the reduced file, which is usually the better source:
    the emitter's job is to find drums/harmony/bass/melody, and the lead sheet
    has already separated exactly those.
    """
    from pipeline.strudel_convert import midi_to_strudel

    clause, params = owner_filter(request)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT job_status, output_format, converted_key, prompt FROM files WHERE id=?" + clause,
            (file_id, *params),
        ).fetchone()

    if row is None or row["output_format"] != "midi":
        raise HTTPException(status_code=404, detail="MIDI entry not found.")
    if row["job_status"] != "done" or not row["converted_key"]:
        raise HTTPException(status_code=409, detail="MIDI conversion not complete yet.")

    if view not in ("full", "lead"):
        raise HTTPException(status_code=422, detail="view must be 'full' or 'lead'.")

    midi_path = DIR_MIDI / row["converted_key"]
    if view == "lead":
        lead_path = midi_path.with_name(midi_path.stem + "_lead.mid")
        if lead_path.is_file():
            midi_path = lead_path
    if not midi_path.is_file():
        raise HTTPException(status_code=404, detail="MIDI file missing from storage.")

    if mode not in ("chords", "notes"):
        raise HTTPException(status_code=422, detail="mode must be 'chords' or 'notes'.")
    try:
        code = midi_to_strudel(midi_path, title=row["prompt"], mode=mode)
    except Exception as exc:  # noqa: BLE001 -- a malformed .mid shouldn't 500
        raise HTTPException(
            status_code=422, detail=f"Could not convert this MIDI to Strudel: {exc}"
        ) from exc

    return {"id": file_id, "code": code}


@router.post("/midi/convert/{file_id}", status_code=202)
@limiter.limit(RATE_LIMIT_MIDI)
def convert_to_midi(request: Request, file_id: str):
    clause, params = owner_filter(request)
    with get_connection() as conn:
        row = conn.execute(
            """SELECT id, job_status, converted_key, prompt, duration, output_format
                 FROM files WHERE id=?""" + clause,
            (file_id, *params),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="File not found.")
    if row["output_format"] == "midi":
        raise HTTPException(status_code=400, detail="Cannot convert a MIDI entry to MIDI.")
    if row["job_status"] != "done" or not row["converted_key"]:
        raise HTTPException(
            status_code=400,
            detail=f"Source audio is not ready (status: {row['job_status']}). "
                   "Wait until generation is complete.",
        )

    wav_path = DIR_CONVERTED / row["converted_key"]
    if not wav_path.is_file():
        raise HTTPException(status_code=404, detail="Source audio file missing from storage.")

    # Already converting this song (a double tap, a second tab, a phone that
    # lost track of it): hand back that job rather than queueing a duplicate.
    with get_connection() as conn:
        running = conn.execute(
            """SELECT id, job_status FROM files
                WHERE source_file_id=? AND output_format='midi' AND owner_id=?
                  AND job_status IN ('queued', 'processing', 'loading_model')
             ORDER BY created_at DESC LIMIT 1""",
            (file_id, current_user(request)["id"]),
        ).fetchone()
    if running is not None:
        return JSONResponse(
            status_code=202,
            content={
                "id": running["id"],
                "status": running["job_status"],
                "message": "This song is already converting.",
            },
        )

    midi_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=UNSAVED_EXPIRY_SECONDS)).isoformat()

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO files
                   (id, owner_id, input_type, original_key, converted_key,
                    prompt, output_format, duration, job_status,
                    created_at, expires_at, saved, source_file_id)
               VALUES (?, ?, 'midi', NULL, NULL, ?, 'midi', ?, 'queued', ?, ?, 0, ?)""",
            (midi_id, current_user(request)["id"], row["prompt"], row["duration"],
             now.isoformat(), expires_at, file_id),
        )
        conn.commit()

    job = Job(
        file_id=midi_id,
        input_type="midi",
        source=str(wav_path),
        duration=0,
        source_file_id=file_id,
    )
    enqueue(job)

    return JSONResponse(
        status_code=202,
        content={
            "id": midi_id,
            "status": "queued",
            "message": f"MIDI conversion queued. Poll GET /status/{midi_id} for progress.",
        },
    )
