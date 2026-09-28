"""
GET /models

The frontend's single source of truth for what can be generated: which models
exist, whether each is usable right now, and what each can do (duration range,
reference-audio modes, extra options). The UI renders whatever this returns,
so adding a model to pipeline/models.json needs no frontend change.
"""

import copy

from fastapi import APIRouter, Request

from app.access import is_admin, max_duration_for

router = APIRouter()


def _cap_duration(view: dict, cap: int | None) -> dict:
    """Shrink a model's advertised duration range to this caller's cap, so the
    UI's slider simply stops there (POST /generate enforces it regardless)."""
    if cap is None:
        return view
    view = copy.deepcopy(view)  # public_view shares the manifest's own dicts
    duration = view["capabilities"]["duration"]
    duration["max"] = max(duration["min"], min(duration["max"], cap))
    duration["default"] = min(duration["default"], duration["max"])
    return view


def _drop_admin_only(view: dict) -> dict:
    """Hide options marked admin_only (vocals, for now) from other accounts.
    POST /generate refuses them regardless; this just keeps the UI honest."""
    view = copy.deepcopy(view)
    view["options"] = [o for o in view["options"] if not o.get("admin_only")]
    return view


@router.get("/models")
def list_available_models(request: Request):
    from pipeline.models import default_model_id, list_models

    cap = max_duration_for(request)
    views = [_cap_duration(m.public_view(), cap) for m in list_models()]
    if not is_admin(request):
        views = [_drop_admin_only(v) for v in views]
    return {"default": default_model_id(), "models": views}
