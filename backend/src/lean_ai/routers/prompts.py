"""REST endpoints for prompt management (view, override, reset)."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lean_ai.llm.prompt_registry import CATEGORY_ORDER, registry

logger = logging.getLogger(__name__)

prompts_router = APIRouter()


# ── Request / response models ─────────────────────────────────────────


class UpdatePromptsRequest(BaseModel):
    repo_root: str
    overrides: dict[str, str]


class ResetPromptsRequest(BaseModel):
    repo_root: str
    keys: list[str] | None = None  # None = reset all


# ── Endpoints ─────────────────────────────────────────────────────────


@prompts_router.get("/prompts")
async def get_prompts(repo_root: str) -> dict[str, Any]:
    """Return all prompts with defaults, overrides, and metadata."""
    registry.load(repo_root)
    return {
        "prompts": registry.get_all(),
        "categories": CATEGORY_ORDER,
    }


@prompts_router.put("/prompts")
async def update_prompts(request: UpdatePromptsRequest) -> dict[str, str]:
    """Save prompt overrides to .lean_ai/prompts.yaml."""
    registry.load(request.repo_root)

    # Validate all overrides before saving
    all_errors: dict[str, list[str]] = {}
    for key, text in request.overrides.items():
        errors = registry.validate(key, text)
        if errors:
            all_errors[key] = errors

    if all_errors:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Validation failed — prompt contains missing placeholders "
                    "or invalid format syntax"
                ),
                "errors": all_errors,
            },
        )

    registry.save_overrides(request.repo_root, request.overrides)
    return {"status": "saved"}


@prompts_router.post("/prompts/reset")
async def reset_prompts(request: ResetPromptsRequest) -> dict[str, str]:
    """Reset prompt overrides (specific keys or all)."""
    registry.load(request.repo_root)
    registry.reset(request.repo_root, request.keys)
    return {"status": "reset"}
