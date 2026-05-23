"""Azure Responses-API adapter for codex models — bypasses litellm.

Codex deployments (e.g. ``gpt-5.3-codex``) are Responses-API-only: they reject
``/chat/completions``. litellm *can* target the Responses API, but its Azure
Responses path (v1.81.x) only supports key auth — it ignores
``azure_ad_token_provider`` (BerriAI/litellm#13056 / #10868). On an Entra-only
resource that means every codex call 403s.

This module calls the Responses API directly through ``openai.AzureOpenAI``
with the shared AzureCliCredential token provider, then adapts the result back
into a ``litellm.ModelResponse`` so callers of ``completion_with_retry`` need no
changes.

A model is routed here when its name starts with ``azure/responses/`` — e.g.
``--reflect-model azure/responses/gpt-5.3-codex``.
"""

from __future__ import annotations

import threading
from typing import Any

import litellm

from mstar.evolution.azure_config import get_azure_config

# Model-name prefix marking a codex / Responses-API-only deployment.
RESPONSES_PREFIX = "azure/responses/"

_client: Any = None
_client_lock = threading.Lock()


def is_responses_model(model: object) -> bool:
    """True if ``model`` should be routed through the Responses-API adapter."""
    return isinstance(model, str) and model.startswith(RESPONSES_PREFIX)


def _deployment_name(model: str) -> str:
    """Strip the ``azure/responses/`` prefix to get the bare deployment name."""
    return model[len(RESPONSES_PREFIX) :]


def _build_create_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Translate completion()-style kwargs into responses.create() kwargs.

    Pure function (no I/O) so it can be unit-tested. litellm-only kwargs
    (``caching``, ``api_base``, ``api_version``, ``azure_ad_token_provider``)
    are intentionally dropped — the Responses adapter does not use them.
    """
    model = kwargs["model"]
    create: dict[str, Any] = {
        "model": _deployment_name(model),
        # The Responses API accepts the chat-style messages list as `input`.
        "input": kwargs.get("messages") or [],
    }
    effort = kwargs.get("reasoning_effort")
    if effort:
        create["reasoning"] = {"effort": effort}
    max_tokens = kwargs.get("max_completion_tokens") or kwargs.get("max_tokens")
    if max_tokens:
        create["max_output_tokens"] = max_tokens
    temperature = kwargs.get("temperature")
    if temperature is not None:
        create["temperature"] = temperature
    return create


def _adapt_response(resp: Any, model: str) -> litellm.ModelResponse:
    """Wrap a Responses-API result as a litellm.ModelResponse.

    Callers only read ``.choices[0].message.content``; this builds exactly that
    shape. ``resp.output_text`` is the openai SDK's aggregated text helper.
    """
    text = getattr(resp, "output_text", None) or ""
    return litellm.ModelResponse(
        model=model,
        choices=[
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
    )


def _get_client() -> Any:
    """Lazily build a process-wide AzureOpenAI client with AAD auth."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                from openai import AzureOpenAI

                cfg = get_azure_config()
                if cfg is None:
                    raise RuntimeError(
                        "azure/responses/ model used but configure_azure_auth() "
                        "has not run — no Azure endpoint/credential available."
                    )
                _client = AzureOpenAI(
                    azure_endpoint=cfg.api_base,
                    azure_ad_token_provider=cfg.token_provider,
                    api_version=cfg.api_version,
                )
    return _client


def responses_completion(**kwargs: Any) -> litellm.ModelResponse:
    """completion()-compatible call for ``azure/responses/`` codex models.

    Accepts the same kwargs as ``completion_with_retry``; returns a
    ``litellm.ModelResponse`` so callers see the usual ``.choices[0].message``
    shape. Does not cache (reflect prompts are unique per iteration anyway).
    """
    model = kwargs["model"]
    create_kwargs = _build_create_kwargs(kwargs)
    resp = _get_client().responses.create(**create_kwargs)
    return _adapt_response(resp, model)
