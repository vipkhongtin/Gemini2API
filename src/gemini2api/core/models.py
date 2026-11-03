"""Model registry: public model ids exposed on /v1/models, Gemini 4 Argon variants,
and thinking/reasoning effort resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..errors import InvalidRequest

ModelKind = Literal["chat", "image", "video"]
ThinkingLevel = Literal["none", "low", "medium", "high"]


@dataclass(frozen=True)
class ModelSpec:
    id: str
    kind: ModelKind
    description: str = ""
    owned_by: str = "google"
    upstream_id: str = "gemini-4-argon"
    default_thinking: ThinkingLevel | None = None


MODELS: dict[str, ModelSpec] = {
    m.id: m
    for m in (
        ModelSpec(
            "gemini-4-argon",
            "chat",
            "Gemini 4 Argon (Google AI Studio) — High Reasoning by default",
            upstream_id="gemini-4-argon",
            default_thinking="high",
        ),
        ModelSpec(
            "gemini-4-argon-high",
            "chat",
            "Gemini 4 Argon — High Thinking Effort",
            upstream_id="gemini-4-argon",
            default_thinking="high",
        ),
        ModelSpec(
            "gemini-4-argon-medium",
            "chat",
            "Gemini 4 Argon — Medium Thinking Effort",
            upstream_id="gemini-4-argon",
            default_thinking="medium",
        ),
        ModelSpec(
            "gemini-4-argon-low",
            "chat",
            "Gemini 4 Argon — Low Thinking Effort",
            upstream_id="gemini-4-argon",
            default_thinking="low",
        ),
        ModelSpec(
            "gemini-4-argon-nothink",
            "chat",
            "Gemini 4 Argon — Thinking Disabled",
            upstream_id="gemini-4-argon",
            default_thinking="none",
        ),
        ModelSpec(
            "gemini-2.5-pro",
            "chat",
            "Gemini 2.5 Pro (Google AI Studio)",
            upstream_id="gemini-2.5-pro",
            default_thinking="high",
        ),
        ModelSpec(
            "gemini-2.5-flash",
            "chat",
            "Gemini 2.5 Flash (Google AI Studio)",
            upstream_id="gemini-2.5-flash",
            default_thinking="low",
        ),
        ModelSpec(
            "gemini-image",
            "image",
            "Text-to-image / image editing (Imagen 3 / Gemini Image)",
            upstream_id="imagen-3.0-generate-002",
        ),
        ModelSpec(
            "gemini-video",
            "video",
            "Text-to-video / first-frame image-to-video (Veo 2)",
            upstream_id="veo-2.0-generate-001",
        ),
    )
}

# Popular client-side model names are routed to the closest Gemini 4 Argon / Gemini model
# so that existing OpenAI tooling works without reconfiguration.
ALIASES: dict[str, str] = {
    # Gemini 4 Argon shorthand & variants
    "gemini4-argon": "gemini-4-argon",
    "gemini-argon": "gemini-4-argon",
    "gemini-4": "gemini-4-argon",
    "gemini-4-argon-thinking": "gemini-4-argon-high",
    "gemini-4-argon-none": "gemini-4-argon-nothink",
    # OpenAI / Anthropic / DeepSeek client aliases
    "gpt-4o": "gemini-4-argon",
    "gpt-4o-mini": "gemini-4-argon-low",
    "gpt-4.1": "gemini-4-argon",
    "gpt-5": "gemini-4-argon-high",
    "o1": "gemini-4-argon-high",
    "o3": "gemini-4-argon-high",
    "o3-mini": "gemini-4-argon-low",
    "claude-sonnet-4": "gemini-4-argon",
    "deepseek-chat": "gemini-4-argon",
    "deepseek-reasoner": "gemini-4-argon-high",
    # Image & Video aliases
    "dall-e-3": "gemini-image",
    "gpt-image-1": "gemini-image",
    "imagen-3": "gemini-image",
    "sora": "gemini-video",
    "veo-2": "gemini-video",
    # Backwards-compatible muse2api aliases
    "muse-chat": "gemini-4-argon",
    "muse-image": "gemini-image",
    "muse-video": "gemini-video",
}

DEFAULT_BY_KIND: dict[ModelKind, str] = {
    "chat": "gemini-4-argon",
    "image": "gemini-image",
    "video": "gemini-video",
}

_VALID_THINKING: dict[str, ThinkingLevel] = {
    "none": "none",
    "off": "none",
    "disabled": "none",
    "false": "none",
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "med": "medium",
    "high": "high",
    "max": "high",
    "enabled": "high",
    "true": "high",
}


def resolve_model(name: str | None, kind: ModelKind) -> ModelSpec:
    """Resolve a client-supplied model name to a ``ModelSpec`` of the expected kind.

    Unknown names fall back to the default model of that kind; a known model of the
    wrong kind is rejected so that e.g. ``gemini-video`` is never silently used for chat.
    """
    if not name:
        return MODELS[DEFAULT_BY_KIND[kind]]
    clean = name.strip()
    key = ALIASES.get(clean, clean)
    spec = MODELS.get(key)
    if spec is None:
        return MODELS[DEFAULT_BY_KIND[kind]]
    if spec.kind != kind:
        raise InvalidRequest(f"model '{name}' is a {spec.kind} model, expected {kind}")
    return spec


def resolve_thinking_level(
    spec: ModelSpec,
    reasoning_effort: str | None = None,
    thinking: Any = None,
    default_level: ThinkingLevel = "high",
) -> ThinkingLevel:
    """Resolve the effective thinking level for a chat request.

    Priority:
    1. Explicit ``reasoning_effort`` in the request body (``high``, ``low``, ``medium``, ``none``)
    2. Explicit ``thinking`` parameter in the request body (bool, str, or dict with ``type``/``level``/``budget_tokens``)
    3. Model-specific default (e.g. ``gemini-4-argon-low`` -> ``low``, ``gemini-4-argon-high`` -> ``high``)
    4. Server ``default_level`` (``GEMINI2API_DEFAULT_THINKING_LEVEL``, default ``high``)
    """
    if reasoning_effort:
        lvl = _VALID_THINKING.get(str(reasoning_effort).strip().lower())
        if lvl:
            return lvl

    if thinking is not None:
        if isinstance(thinking, bool):
            return "high" if thinking else "none"
        if isinstance(thinking, str):
            lvl = _VALID_THINKING.get(thinking.strip().lower())
            if lvl:
                return lvl
        if isinstance(thinking, dict):
            ttype = str(thinking.get("type") or "").strip().lower()
            if ttype in ("disabled", "none", "off"):
                return "none"
            tlevel = str(thinking.get("level") or thinking.get("effort") or "").strip().lower()
            if tlevel in _VALID_THINKING:
                return _VALID_THINKING[tlevel]
            budget = thinking.get("budget_tokens") or thinking.get("thinking_budget")
            if isinstance(budget, (int, float)):
                if budget <= 0:
                    return "none"
                if budget <= 2048:
                    return "low"
                if budget <= 8192:
                    return "medium"
                return "high"
            if ttype == "enabled":
                return "high"

    if spec.default_thinking is not None:
        return spec.default_thinking
    return default_level
