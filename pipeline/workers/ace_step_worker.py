"""
ACE-Step 1.5 worker. Runs inside third_party/ACE-Step-1.5/.venv (Python 3.12,
torch 2.7) -- NOT the backend's environment. See protocol.py for the contract.

Everything ACE-Step-specific lives here; the rest of the app only knows the
model id and the capabilities declared in pipeline/models.json.
"""

import shutil
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from protocol import emit, emit_progress, emit_status, load_request  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MAX_CAPTION_CHARS = 512   # ACE-Step's documented caption limit


def _trim_reference(src: str, seconds: int, dest_dir: Path) -> str:
    """The requested duration is the OUTPUT length, so a longer reference is
    trimmed to it rather than dragged through the whole model."""
    import soundfile as sf

    info = sf.info(src)
    if info.duration <= seconds + 0.5:
        return src
    audio, sr = sf.read(src, frames=int(seconds * info.samplerate), always_2d=True)
    out = dest_dir / "reference_trimmed.wav"
    sf.write(str(out), audio, sr)
    return str(out)


# ACE-Step's `lyrics` field is a *temporal* description, not just words: for
# instrumental music its documented use is structure tags marking how the piece
# develops (Tutorial.md, "Writing Instrumental Music"). Sending the bare
# "[Instrumental]" -- which is what this app did -- gives the model nothing to
# lay out in time, and the result is a track that loops and has no intro or
# outro. Measured on four 180s generations made that way: repeats every 6-64s,
# and first/last 15s indistinguishable from the middle.
#
# The docs are explicit that these stay terse: "Keep structure tags concise; put
# complex style descriptions in Caption."
_STRUCTURE_PLANS = {
    "simple": ["Intro - sparse", "Main Theme", "Outro - fade out"],
    "full": ["Intro - sparse", "Main Theme", "Build", "Climax - powerful",
             "Breakdown - stripped back", "Outro - fade out"],
}
_MIN_SEC_PER_SECTION = 18   # below this, sections are too short to read as sections


def _instrumental_lyrics(level: str, seconds: float) -> str:
    """Structure tags for an instrumental piece, thinned to fit the duration."""
    plan = _STRUCTURE_PLANS.get(level)
    if not plan:
        return "[Instrumental]"

    room = max(2, int(seconds // _MIN_SEC_PER_SECTION))
    if len(plan) > room:
        # Keep the intro and the outro -- they are the whole point -- and thin
        # the middle evenly rather than truncating, which would drop the outro.
        middle = plan[1:-1]
        keep = max(0, room - 2)
        if keep and middle:
            step = len(middle) / keep
            middle = [middle[int(i * step)] for i in range(keep)]
        else:
            middle = []
        plan = [plan[0], *middle, plan[-1]]
    return "\n\n".join(f"[{s}]" for s in plan)


def _write_trimmed_wav(tensor, sample_rate: int, seconds: float, fade_sec: float, out: Path) -> None:
    """Write `tensor` ([channels, samples]) as 16-bit WAV, cut to `seconds`
    with a short cosine fade-out so the cut never clicks."""
    import numpy as np
    import soundfile as sf

    audio = tensor.detach().cpu().numpy().T.astype("float32")      # -> [samples, channels]
    audio = audio[: int(round(seconds * sample_rate))]
    n_fade = min(int(fade_sec * sample_rate), len(audio))
    if n_fade > 1:
        audio[-n_fade:] *= (0.5 * (1 + np.cos(np.linspace(0, np.pi, n_fade))))[:, None]
    sf.write(str(out), audio, sample_rate, subtype="PCM_16")


def main() -> None:
    req = load_request()
    args = req["args"]
    opts = req["options"]
    reference = req.get("reference")
    # The planner (5Hz LM) is worth its ~2x time only on sung songs: A/B by ear
    # (SFT, fixed seed) it made the vocal melody, tone and phrasing clearly
    # better, but on instrumentals it piled ten-odd instruments on top of each
    # other with no clear lead. So the manifest can scope it: "lyrics" runs it
    # only when the request has written lyrics; true/false mean always/never.
    lm_scope = args.get("use_lm", False)
    if lm_scope == "lyrics":
        use_lm = opts.get("vocals") == "lyrics" and bool((req.get("lyrics") or "").strip())
    else:
        use_lm = bool(lm_scope)
    if use_lm:
        import os
        lm_dir = Path(os.environ["ACESTEP_CHECKPOINTS_DIR"]) / args["lm_model"]
        if not lm_dir.is_dir():
            # A missing planner should cost quality, not the song.
            print(f"[ace-step] planner not found at {lm_dir}; generating without it", file=sys.stderr)
            use_lm = False

    emit_status("loading_model")

    # Imported late: these pull in torch and take seconds -- the protocol's
    # first event should reach the backend before that cost is paid.
    from acestep.handler import AceStepHandler
    from acestep.inference import GenerationConfig, GenerationParams, generate_music
    from acestep.llm_inference import LLMHandler

    project_root = str((PROJECT_ROOT / args["project_root"]).resolve())

    dit = AceStepHandler()
    status, ok = dit.initialize_service(
        project_root=project_root,
        config_path=args["dit"],
        device="cuda",
        offload_to_cpu=bool(args.get("offload_to_cpu", False)),
        offload_dit_to_cpu=bool(args.get("offload_dit_to_cpu", False)),
        quantization=args.get("quantization"),
        compile_model=bool(args.get("compile_model", False)),
    )
    if not ok:
        raise RuntimeError(f"ACE-Step init failed: {status}")

    llm = LLMHandler()  # left uninitialized unless use_lm: generate_music treats that as "no LM"
    if use_lm:
        llm_status, llm_ok = llm.initialize(
            checkpoint_dir=os.environ["ACESTEP_CHECKPOINTS_DIR"],
            lm_model_path=args["lm_model"],
            backend=args.get("lm_backend", "pt"),
            device="cuda",
            # Park the planner on the CPU once it has planned, so it does not
            # share an 8 GB card with the DiT.
            offload_to_cpu=bool(args.get("offload_to_cpu", False)),
        )
        if not llm_ok:
            raise RuntimeError(f"ACE-Step LM init failed: {llm_status}")

    emit_status("processing")

    tmp = Path(tempfile.mkdtemp(prefix="acestep_"))
    try:
        # Vocal songs arrive with their lyrics already written (pipeline/lyrics.py:
        # drafted lyrics, or wordless vocalise for "chops"). Everything else is
        # instrumental and gets structure tags instead.
        lyrics = (req.get("lyrics") or "").strip()
        instrumental = not lyrics
        requested = float(req["duration"])
        # ACE-Step ends every text-to-music generation with ~3s of near-silence
        # whatever the length (measured at 15s and 30s), so ask for `pad` extra
        # seconds and trim back to the requested length below. Not done for
        # cover mode, where the length follows the source audio.
        pad = 0.0 if (reference and reference["mode"] == "cover") else float(args.get("tail_pad_sec", 0.0))
        kwargs = dict(
            caption=req["prompt"][:_MAX_CAPTION_CHARS],
            lyrics=(_instrumental_lyrics(str(opts.get("structure", "off")), requested)
                    if instrumental else lyrics),
            instrumental=instrumental,
            duration=requested + pad,
            inference_steps=int(args["steps"]),
            shift=float(args.get("shift", 1.0)),
            # The planner ("thinking"): the 5Hz LM sketches the song as audio
            # codes before the DiT renders it. Its optional rewrites are
            # separate switches and default OFF where they would undo our own
            # inputs: use_cot_caption replaces the caption (and with it the
            # vocal wording), use_cot_language overrides the chosen lyrics
            # language. use_cot_metas only fills what is missing (key, time
            # signature) -- a locked bpm is passed through and kept.
            thinking=use_lm,
            use_cot_caption=use_lm and bool(args.get("lm_cot_caption", False)),
            use_cot_language=use_lm and bool(args.get("lm_cot_language", False)),
            use_cot_metas=use_lm and bool(args.get("lm_cot_metas", True)),
        )
        # Optional DiT tuning knobs, passed straight through when the manifest
        # sets them. Each is a GenerationParams field; leaving one out means
        # "ACE-Step's own default", so a model that declares none behaves
        # exactly as it did before these were added.
        for key in ("guidance_scale", "velocity_norm_threshold", "velocity_ema_factor",
                    "sampler_mode", "infer_method", "use_adg", "seed"):
            if key in args:
                kwargs[key] = args[key]

        # Only real lyrics have a language; wordless chops leave it to the model.
        if opts.get("vocals") == "lyrics":
            kwargs["vocal_language"] = str(opts.get("vocal_language", "unknown"))

        # Knobs the manifest exposes to the user win over its own defaults.
        # Validated against the manifest before reaching here (pipeline/models.py).
        for key in ("sampler_mode",):
            if key in opts:
                kwargs[key] = opts[key]

        # ACE-Step estimates tempo itself when bpm is None, and that estimate
        # is free to wander over a long generation. Passing a value pins the
        # grid instead. 0 is the manifest's "let the model decide".
        if opts.get("bpm"):
            kwargs["bpm"] = int(opts["bpm"])

        if reference and reference["mode"] == "cover":
            # The instruction must match the task. GenerationParams defaults
            # it to the text2music sentence, and ACE-Step only swaps in the
            # cover one when it *auto-detects* a cover -- an explicit
            # task_type="cover" keeps whatever it's given. With the text2music
            # instruction the DiT gets the source's structure but is told to
            # make a fresh song, and the result is a structureless smear with
            # a nasal blip on every note (measured: SFT, every source, every
            # strength/guidance/shift tried, even via ACE-Step's own API).
            from acestep.constants import TASK_INSTRUCTIONS
            kwargs.update(
                task_type="cover",
                instruction=TASK_INSTRUCTIONS["cover"],
                src_audio=_trim_reference(reference["path"], int(req["duration"]), tmp),
                audio_cover_strength=float(opts.get("cover_strength", 0.8)),
            )
        elif reference and reference["mode"] == "style":
            kwargs.update(task_type="text2music", reference_audio=reference["path"])
        else:
            kwargs.update(task_type="text2music")

        # generate_music reports 0.5-1.0 when no LM runs before it (0-0.5 is
        # reserved for LM phases) -- rescale so the UI bar starts at empty.
        lo = 0.0 if use_lm else 0.5

        def _progress(value, desc=None, **_):
            emit_progress((float(value) - lo) / (1.0 - lo))

        result = generate_music(
            dit, llm,
            GenerationParams(**kwargs),
            GenerationConfig(batch_size=1, audio_format="wav"),
            save_dir=str(tmp),
            progress=_progress,
        )
        if not result.success or not result.audios:
            raise RuntimeError(result.error or "ACE-Step returned no audio.")

        audio = result.audios[0]
        out = Path(req["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        _write_trimmed_wav(audio["tensor"], int(audio["sample_rate"]), requested,
                           float(args.get("fade_out_sec", 0.0)), out)
        emit_progress(1.0)
        emit(
            "result",
            path=str(out),
            sample_rate=int(audio["sample_rate"]),
            meta={"seed": audio["params"].get("seed"), "task_type": kwargs["task_type"]},
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 -- the runner needs the message, whatever it is
        traceback.print_exc()
        emit("error", message=f"{type(exc).__name__}: {exc}")
        sys.exit(1)
