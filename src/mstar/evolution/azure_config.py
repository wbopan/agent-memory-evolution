"""Direct Azure OpenAI authentication for litellm — no local reverse proxy.

mstar talks to the Azure OpenAI / Azure AI Services data plane directly. The one
wrinkle is auth:

* These resources are Entra-ID only (key auth disabled), so a bearer token is
  required on every call.
* On Azure VMs, ``DefaultAzureCredential`` silently picks up the VM's Managed
  Identity instead of the developer's ``az login`` session — and the MI usually
  has no access to the resource. So we build an explicit ``AzureCliCredential``
  token provider, which always matches ``az login``.

litellm has no global hook for an ``azure_ad_token_provider``, so
:func:`configure_azure_auth` stashes the endpoint + provider in a module global
and :func:`apply_azure_kwargs` injects them into each litellm call.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# Token audience for the Azure OpenAI / Azure AI Services data plane.
_COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"


def _has_azure_prefix(models: list[str]) -> bool:
    """Return True if any model string starts with 'azure/'."""
    return any(m.startswith("azure/") for m in models if m)


@dataclass(frozen=True)
class _AzureConfig:
    """Endpoint + AAD token provider for direct Azure OpenAI access."""

    api_base: str
    api_version: str
    token_provider: Callable[[], str]


# Set by configure_azure_auth(); consumed by apply_azure_kwargs().
_CONFIG: _AzureConfig | None = None


def configure_azure_auth(
    models: list[str],
    azure_api_base: str | None = None,
    azure_api_version: str = "2024-12-01-preview",
) -> None:
    """Configure direct Azure OpenAI access for litellm.

    When any model uses the ``azure/`` prefix, build an ``AzureCliCredential``
    bearer-token provider and stash it together with the endpoint config.
    litellm calls then pick it up via :func:`apply_azure_kwargs`.

    Args:
        models: Model strings from the CLI.
        azure_api_base: Azure OpenAI / AI Services endpoint URL. Required when
            any model is ``azure/``. Example:
            ``https://myresource.services.ai.azure.com``
        azure_api_version: Azure API version string.

    Raises:
        ValueError: If azure/ models detected but azure_api_base not provided.
    """
    global _CONFIG

    if not _has_azure_prefix(models):
        _CONFIG = None
        return

    if not azure_api_base:
        raise ValueError(
            "--azure-api-base is required when using azure/ model prefix. "
            "Example: --azure-api-base https://myresource.services.ai.azure.com"
        )

    # Imported lazily so non-Azure runs never need azure-identity installed.
    # Constructing the credential / provider does no I/O — the first token is
    # fetched on the first actual completion call.
    from azure.identity import AzureCliCredential, get_bearer_token_provider

    token_provider = get_bearer_token_provider(
        AzureCliCredential(), _COGNITIVE_SERVICES_SCOPE
    )

    _CONFIG = _AzureConfig(
        api_base=azure_api_base,
        api_version=azure_api_version,
        token_provider=token_provider,
    )


def apply_azure_kwargs(model: object, kwargs: dict) -> None:
    """Inject Azure endpoint + AAD token provider into litellm call kwargs.

    No-op unless ``model`` is an ``azure/`` model and :func:`configure_azure_auth`
    has run. Uses ``setdefault`` so explicit per-call overrides always win.

    Safe to call for every litellm ``completion``/``embedding`` invocation: for
    non-Azure models (``openrouter/``, ``openai/`` …) it does nothing, which is
    why it must be applied per-call rather than via a global ``litellm.api_base``
    (a global base would misroute non-Azure traffic to the Azure endpoint).
    """
    if _CONFIG is None:
        return
    if not (isinstance(model, str) and model.startswith("azure/")):
        return
    kwargs.setdefault("api_base", _CONFIG.api_base)
    kwargs.setdefault("api_version", _CONFIG.api_version)
    kwargs.setdefault("azure_ad_token_provider", _CONFIG.token_provider)
