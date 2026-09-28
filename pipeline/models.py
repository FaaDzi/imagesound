"""
Model registry -- the single place that knows which music models exist.

Everything model-specific lives in pipeline/models.json (worker command,
weights it needs, what it can do, which user-facing options it exposes) plus
one worker script per model family. The API and the UI read this registry, so
adding a model never means touching routes, jobs or components.
"""

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MANIFEST = Path(__file__).resolve().parent / "models.json"


class ModelError(ValueError):
    """Raised for an unknown model or an invalid request against a model's capabilities."""


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    tier: str
    description: str
    prompt_style: str
    worker: dict
    requires: tuple
    capabilities: dict
    options: tuple = field(default_factory=tuple)
    # True only once the FAD quality thresholds (fad_common.py) have been
    # calibrated against THIS model's output. They were tuned on a previous model's output, and
    # applying them blindly to another model flags every song "unsatisfactory".
    quality_scoring: bool = False

    @property
    def min_duration(self) -> int:
        return int(self.capabilities["duration"]["min"])

    @property
    def max_duration(self) -> int:
        return int(self.capabilities["duration"]["max"])

    @property
    def reference_modes(self) -> tuple:
        return tuple(self.capabilities.get("reference", ()))

    def resolve(self, rel: str) -> Path:
        return (PROJECT_ROOT / rel).resolve()

    def availability(self) -> "tuple[bool, str | None]":
        """(usable, reason-if-not). Checked on every call so dropping weights
        into place makes a model appear without restarting the backend."""
        if not self.resolve(self.worker["python"]).is_file():
            return False, f"Runtime not installed ({self.worker['python']})."
        missing = [r for r in self.requires if not self.resolve(r).exists()]
        if missing:
            return False, f"Model weights missing: {', '.join(missing)}."
        return True, None

    def public_view(self) -> dict:
        """What GET /models exposes -- never worker paths or environment."""
        available, reason = self.availability()
        return {
            "id": self.id,
            "label": self.label,
            "tier": self.tier,
            "description": self.description,
            "available": available,
            "unavailable_reason": reason,
            "capabilities": self.capabilities,
            "options": list(self.options),
        }

    def validate_request(self, duration: int, reference_mode: "str | None", options: dict) -> dict:
        """Check a request against this model's declared capabilities and
        return the sanitized options (defaults filled in, unknown keys
        rejected). This is the trust boundary: only declared options reach a worker."""
        if not (self.min_duration <= duration <= self.max_duration):
            raise ModelError(
                f"{self.label} generates {self.min_duration}-{self.max_duration}s songs "
                f"(requested {duration}s)."
            )
        if reference_mode is not None and reference_mode not in self.reference_modes:
            raise ModelError(f"{self.label} does not support reference mode '{reference_mode}'.")

        declared = {o["key"]: o for o in self.options}
        unknown = set(options) - set(declared)
        if unknown:
            raise ModelError(f"Unknown option(s) for {self.label}: {', '.join(sorted(unknown))}.")

        clean: dict = {}
        for key, opt in declared.items():
            value = options.get(key, opt.get("default"))
            when = opt.get("when_reference_mode")
            if when is not None and reference_mode != when:
                continue
            # {"other_key": [values]}: only applies while another option has
            # one of those values (e.g. the lyrics language only with vocals).
            # Dropped rather than rejected, like when_reference_mode above --
            # the UI sends every option it has a value for.
            if any(options.get(k, declared[k].get("default")) not in vals
                   for k, vals in opt.get("when_option", {}).items()):
                continue
            if opt["type"] == "bool":
                if not isinstance(value, bool):
                    raise ModelError(f"Option '{key}' must be true or false.")
            elif opt["type"] == "number":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ModelError(f"Option '{key}' must be a number.")
                if not (opt["min"] <= value <= opt["max"]):
                    raise ModelError(f"Option '{key}' must be between {opt['min']} and {opt['max']}.")
            elif opt["type"] == "choice":
                if value not in opt["choices"]:
                    raise ModelError(f"Option '{key}' must be one of {opt['choices']}.")
                # Listed but not selectable yet (e.g. an untested lyrics language).
                if value in opt.get("locked_choices", {}):
                    raise ModelError(f"'{value}' for {opt['label']} is locked: {opt['locked_choices'][value]}")
            else:
                raise ModelError(f"Manifest error: option '{key}' has unknown type '{opt['type']}'.")
            clean[key] = value
        return clean


@lru_cache(maxsize=1)
def _load() -> "tuple[str, dict[str, ModelSpec]]":
    raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    specs: dict[str, ModelSpec] = {}
    for m in raw["models"]:
        spec = ModelSpec(
            id=m["id"],
            label=m["label"],
            tier=m["tier"],
            description=m["description"],
            prompt_style=m["prompt_style"],
            worker=m["worker"],
            requires=tuple(m.get("requires", ())),
            capabilities=m["capabilities"],
            options=tuple(m.get("options", ())),
            quality_scoring=bool(m.get("quality_scoring", False)),
        )
        if spec.id in specs:
            raise ModelError(f"Duplicate model id in manifest: {spec.id}")
        specs[spec.id] = spec
    if raw["default"] not in specs:
        raise ModelError(f"Manifest default '{raw['default']}' is not a listed model.")
    return raw["default"], specs


def default_model_id() -> str:
    return _load()[0]


def list_models() -> "list[ModelSpec]":
    return list(_load()[1].values())


def get_model(model_id: str) -> ModelSpec:
    try:
        return _load()[1][model_id]
    except KeyError:
        raise ModelError(f"Unknown model '{model_id}'.") from None
