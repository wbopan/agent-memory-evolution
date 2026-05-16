"""
Shared Azure OpenAI client for STATE-Bench.

This module provides a simple, reusable Azure OpenAI client for components
that need basic LLM access. Components with more complex needs (like
data_generation's AzureQueryLLM with conversation threading) should keep
their specialized implementations.

Usage:
    from state_bench.client import LLMClient

    # Chat completion
    client = LLMClient()
    response = client.complete_chat(
        messages=[{"role": "user", "content": "Hello"}],
    )

    # JSON response
    data = client.complete_json(
        prompt="Return a JSON with name and age",
        system_prompt="Return valid JSON only.",
    )
"""

import json
import os
import pprint
import threading
from pathlib import Path
from typing import Any

import yaml
from azure.identity import AzureCliCredential, get_bearer_token_provider
from dotenv import load_dotenv
from openai import APIStatusError, AuthenticationError, AzureOpenAI
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
)

EndpointDeployment = tuple[str, str]  # (endpoint_url, deployment_name)


class ContentFilterError(Exception):
    """Raised when Azure content filter blocks or truncates a response."""


load_dotenv()

_CONFIGS_DIR = Path(__file__).parent.parent / "configs"


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f) or {}


# Core LLM config (max_tokens, retry, defaults)
CONFIG: dict[str, Any] = _load_yaml(_CONFIGS_DIR / "llm.yaml")

_DEFAULT_MAX_TOKENS: int = CONFIG["max_tokens"]["default"]


def _before_sleep_print(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    wait = retry_state.next_action.sleep if retry_state.next_action else 0
    fn = retry_state.fn
    name = f"{fn.__module__}.{fn.__qualname__}" if fn else "unknown"
    print(f"Retrying {name} in {wait:.1f} seconds as it raised {type(exc).__name__}: {exc}")


def _wait_by_error_type(retry_state: RetryCallState) -> float:
    """Return wait time based on exception type.

    Auth errors (expired token) retry after 2 seconds since the token provider
    refreshes automatically. Other errors (rate limits, server errors) use the
    configured wait time.
    """
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, AuthenticationError):
        return 2
    return float(CONFIG["retry"]["wait_seconds"])


_llm_retry = retry(
    stop=stop_after_attempt(CONFIG["retry"]["max_attempts"]),
    wait=_wait_by_error_type,
    retry=retry_if_exception_type((APIStatusError, AuthenticationError, json.JSONDecodeError, ContentFilterError)),
    before_sleep=_before_sleep_print,
    reraise=True,
)


def _check_content_filter(response: Any) -> None:
    """Raise ContentFilterError if the response was truncated by Azure content filter.

    Checks response.incomplete_details.reason == "content_filter". This is the
    structured signal from the API — more reliable than keyword matching.
    """
    if (
        response.status == "incomplete"
        and response.incomplete_details
        and response.incomplete_details.reason == "content_filter"
    ):
        # Extract filter details for the error message
        details = ""
        resp_dict = response.model_dump()
        for cf in resp_dict.get("content_filters", []):
            if cf.get("blocked") and cf.get("source_type") == "completion":
                categories = cf.get("content_filter_results", {})
                triggered = [cat for cat, info in categories.items() if info.get("filtered")]
                if triggered:
                    details = f" (categories: {', '.join(triggered)})"
        raise ContentFilterError(f"Azure content filter blocked completion{details}")


def _parse_env_list(var_name: str) -> list[str]:
    """Parse a comma-separated environment variable into a list of stripped strings."""
    raw = os.environ.get(var_name, "")
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _get_env_with_numbered_fallback(var_name: str) -> str:
    """Return env var, falling back to the canonical _1 slot when present."""
    return os.environ.get(var_name) or os.environ.get(f"{var_name}_1", "")


_COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"

# Process-wide, lazily built AzureCliCredential token provider. One credential
# for the whole run: azure-identity caches the bearer token and refreshes it
# before expiry, so we shell out to `az` roughly once per token lifetime rather
# than once per client. AzureCliCredential (not DefaultAzureCredential) is used
# explicitly so the identity always matches the developer's `az login` session
# instead of an Azure VM's Managed Identity.
_cli_token_provider: Any = None
_cli_token_provider_lock = threading.Lock()


def _get_cli_token_provider() -> Any:
    """Return the shared auto-refreshing AzureCliCredential bearer-token provider."""
    global _cli_token_provider
    if _cli_token_provider is None:
        with _cli_token_provider_lock:
            if _cli_token_provider is None:
                _cli_token_provider = get_bearer_token_provider(
                    AzureCliCredential(), _COGNITIVE_SERVICES_SCOPE
                )
    return _cli_token_provider


def _build_azure_openai_client(endpoint: str, api_version: str) -> AzureOpenAI:
    """Build an authenticated AzureOpenAI client.

    Auth precedence:
    1. ``AZURE_OPENAI_TOKEN`` env var — honored as an explicit static token if
       set (e.g. when a token-injecting proxy fronts the endpoint).
    2. Otherwise an auto-refreshing AzureCliCredential bearer-token provider,
       so direct-to-Azure calls keep working across long runs without a proxy.
    """
    static_token = os.environ.get("AZURE_OPENAI_TOKEN")
    if static_token:
        return AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token=static_token,
            api_version=api_version,
        )

    return AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=_get_cli_token_provider(),
        api_version=api_version,
    )


def _get_default_deployment() -> str:
    """Get default deployment (first from AZURE_OPENAI_DEPLOYMENTS).

    Raises:
        ValueError: If AZURE_OPENAI_DEPLOYMENTS is not set.
    """
    deployments = _parse_env_list("AZURE_OPENAI_DEPLOYMENTS") or _parse_env_list("AZURE_OPENAI_DEPLOYMENTS_1")
    if not deployments:
        raise ValueError("No deployments configured. Set AZURE_OPENAI_DEPLOYMENTS environment variable.")
    return deployments[0]


def _discover_all_endpoints(
    endpoint_var: str = "AZURE_OPENAI_ENDPOINT",
    deployments_var: str = "AZURE_OPENAI_DEPLOYMENTS",
) -> list[EndpointDeployment]:
    """Discover all (endpoint, deployment) pairs from environment variables.

    Supports either unnumbered vars as the primary pool entry or a fully
    numbered layout starting at {endpoint_var}_1 / {deployments_var}_1.
    Additional numbered pools are then read from _2.._10.
    """
    pairs: list[EndpointDeployment] = []

    def add_pairs(endpoint_name: str, deployments_name: str) -> None:
        endpoint = os.environ.get(endpoint_name, "")
        deployments = _parse_env_list(deployments_name)
        if endpoint and deployments:
            for deployment in deployments:
                pairs.append((endpoint, deployment))

    add_pairs(endpoint_var, deployments_var)
    add_pairs(f"{endpoint_var}_1", f"{deployments_var}_1")

    for n in range(2, 11):
        add_pairs(f"{endpoint_var}_{n}", f"{deployments_var}_{n}")

    return pairs


class LeastBusyPool:
    """Mixin providing least-busy selection across a pool of resources.

    Subclasses must set `self._pool_items` (list of resources) before use.
    """

    def __init__(self) -> None:
        self._pool_items: list[Any] = []
        self._in_flight: list[int] = []
        self._pool_lock = threading.Lock()

    def _init_pool(self, items: list[Any]) -> None:
        self._pool_items = items
        self._in_flight = [0] * len(items)

    def _acquire(self) -> tuple[int, Any]:
        with self._pool_lock:
            idx = min(range(len(self._in_flight)), key=lambda i: self._in_flight[i])
            self._in_flight[idx] += 1
            return idx, self._pool_items[idx]

    def _release(self, idx: int) -> None:
        with self._pool_lock:
            self._in_flight[idx] -= 1


class LLMClient:
    """Simple Azure OpenAI client for general-purpose LLM queries.

    This client handles:
    - Azure AD authentication via DefaultAzureCredential
    - Chat completions
    - JSON-formatted responses

    For complex use cases with conversation threading or state management,
    use a specialized client (e.g., AzureQueryLLM in data_generation.py).
    """

    def __init__(
        self,
        endpoint: str | None = None,
        deployment: str | None = None,
        api_version: str | None = None,
    ):
        """Initialize the Azure OpenAI client.

        Args:
            endpoint: Azure OpenAI endpoint URL. Defaults to AZURE_OPENAI_ENDPOINT env var.
            deployment: Model deployment name. Defaults to first AZURE_OPENAI_DEPLOYMENTS entry.
            api_version: API version. Defaults to AZURE_OPENAI_API_VERSION env var or config default.

        Raises:
            ValueError: If endpoint is not provided and AZURE_OPENAI_ENDPOINT is not set.
            ValueError: If no deployments are configured.
        """
        self.endpoint = endpoint or _get_env_with_numbered_fallback("AZURE_OPENAI_ENDPOINT")
        self.deployment = deployment or _get_default_deployment()
        self.api_version = api_version or os.environ.get("AZURE_OPENAI_API_VERSION", CONFIG["defaults"]["api_version"])

        if not self.endpoint:
            raise ValueError(
                "Azure OpenAI endpoint required. Set AZURE_OPENAI_ENDPOINT environment variable "
                "or pass endpoint parameter."
            )

        # Set up Azure AD authentication. Prefer deterministic local CLI token minting
        # over DefaultAzureCredential discovery so long-running batch jobs don't depend on
        # whichever background credential source happens to be valid.
        self._client = _build_azure_openai_client(self.endpoint, self.api_version)

    @_llm_retry
    def complete_chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        temperature: float = 1.0,
    ) -> str:
        """Generate a completion from a list of messages.

        Args:
            messages: List of message dicts with "role" and "content" keys.
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.

        Returns:
            The generated text response.
        """
        response = self._client.responses.create(
            model=self.deployment,
            input=messages,  # type: ignore[arg-type]  # SDK accepts list[dict] at runtime
            max_output_tokens=max_tokens,
            temperature=temperature,
        )
        _check_content_filter(response)

        return response.output_text

    @_llm_retry
    def complete_with_tools(
        self,
        *,
        instructions: str,
        input: list[Any],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0,
    ) -> Any:
        """Call the Responses API with tool schemas.

        Returns the raw Response object (with .output, .id, .output_text, etc.).
        Used by the agent turn loop for tool-calling conversations.
        """
        response = self._client.responses.create(
            model=self.deployment,
            instructions=instructions,
            input=input,
            tools=tools,
            previous_response_id=previous_response_id,
            max_output_tokens=max_tokens,
            temperature=temperature,
        )
        _check_content_filter(response)
        return response

    @_llm_retry
    def complete_json_response(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        reasoning_effort: str | None = None,
    ) -> Any:
        """Generate a raw JSON-formatted Responses API result.

        Args:
            prompt: The user prompt/question.
            system_prompt: Optional system prompt to set context.
            max_tokens: Maximum tokens in response.
            reasoning_effort: Optional reasoning effort level ("low", "medium", "high").

        Returns:
            Raw Response object.
        """
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        kwargs: dict[str, Any] = {
            "model": self.deployment,
            "input": messages,
            "max_output_tokens": max_tokens,
            "text": {"format": {"type": "json_object"}},
        }
        if reasoning_effort:
            kwargs["reasoning"] = {"effort": reasoning_effort}
        response = self._client.responses.create(**kwargs)
        _check_content_filter(response)
        return response

    @_llm_retry
    def complete_json(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        """Generate a JSON response.

        Args:
            prompt: The user prompt/question.
            system_prompt: Optional system prompt to set context.
            max_tokens: Maximum tokens in response.
            reasoning_effort: Optional reasoning effort level ("low", "medium", "high").

        Returns:
            Parsed JSON as a dictionary.

        Raises:
            json.JSONDecodeError: If the response is not valid JSON.
        """
        response = self.complete_json_response(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )

        try:
            return json.loads(response.output_text)
        except json.JSONDecodeError:
            # Log raw output for debugging truncation/malformed JSON
            raw = response.output_text
            status = response.status
            reason = response.incomplete_details.reason if response.incomplete_details else None
            # Dump full response dict to find Azure-specific content filter fields
            try:
                resp_dict = response.model_dump()
                # Look for any content_filter keys in the response
                filter_keys = {
                    k: v for k, v in resp_dict.items() if "filter" in str(k).lower() or "safety" in str(k).lower()
                }
                if filter_keys:
                    print(f"  Content filter details: {pprint.pformat(filter_keys)}")
                # Also check output items for extra fields
                for i, item in enumerate(resp_dict.get("output", [])):
                    extra = {k: v for k, v in item.items() if k not in ("id", "type", "text", "status")}
                    if extra:
                        print(f"  Output item {i} extra fields: {pprint.pformat(extra)}")
            except Exception:
                pass
            print(f"  JSON parse failed | status={status} | reason={reason} | len={len(raw)} | tail=...{raw[-100:]!r}")
            raise


def _resolve_deployments(deployments: list[str] | None = None) -> list[EndpointDeployment]:
    """Resolve deployment list from argument or environment, raising if empty.

    When deployments is None (the common case), auto-discovers all configured
    endpoints from AZURE_OPENAI_ENDPOINT[_N] / AZURE_OPENAI_DEPLOYMENTS[_N].

    When deployments is an explicit list of names, pairs each with the primary
    AZURE_OPENAI_ENDPOINT.
    """
    if deployments is None:
        pairs = _discover_all_endpoints()
        if pairs:
            return pairs
        primary_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        if not primary_endpoint:
            raise ValueError(
                "Azure OpenAI endpoint required. Set AZURE_OPENAI_ENDPOINT environment variable "
                "or pass endpoint parameter."
            )
        return [(primary_endpoint, _get_default_deployment())]

    if not deployments:
        raise ValueError("No deployments configured")

    primary_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    if not primary_endpoint:
        raise ValueError(
            "Azure OpenAI endpoint required. Set AZURE_OPENAI_ENDPOINT environment variable or pass endpoint parameter."
        )
    return [(primary_endpoint, d) for d in deployments]


class PooledLLMClient(LeastBusyPool):
    """LLM client that routes every call to the least-busy deployment.

    Drop-in replacement for LLMClient that tracks in-flight requests
    per deployment and always picks the one with the fewest active calls.
    This ensures no endpoint sits idle while another is saturated.

    Usage:
        pool = PooledLLMClient()  # Uses AZURE_OPENAI_DEPLOYMENTS env var
        response = pool.complete_chat(messages)  # Routes to least-busy deployment
    """

    def __init__(self, deployments: list[str] | None = None):
        super().__init__()

        endpoint_deployments = _resolve_deployments(deployments)

        self.deployments = [d for _, d in endpoint_deployments]
        self.clients = [LLMClient(endpoint=ep, deployment=d) for ep, d in endpoint_deployments]
        self._init_pool(self.clients)
        self._response_client_map: dict[str, int] = {}
        self._response_client_lock = threading.Lock()

        endpoints_summary: dict[str, int] = {}
        for ep, _ in endpoint_deployments:
            endpoints_summary[ep] = endpoints_summary.get(ep, 0) + 1
        for ep, count in endpoints_summary.items():
            print(f"PooledLLMClient: {ep} -> {count} deployments")

    def _remember_response_client(self, response_id: str | None, client_idx: int) -> None:
        if not response_id:
            return
        with self._response_client_lock:
            self._response_client_map[response_id] = client_idx

    def _client_for_previous_response(self, previous_response_id: str | None) -> tuple[int, Any] | None:
        if not previous_response_id:
            return None
        with self._response_client_lock:
            idx = self._response_client_map.get(previous_response_id)
        if idx is None:
            return None
        return idx, self.clients[idx]

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Route a call to the least-busy client, with sticky routing for threaded tool calls."""
        previous_response_id = kwargs.get("previous_response_id") if method == "complete_with_tools" else None
        pinned = self._client_for_previous_response(previous_response_id)
        if pinned is not None:
            idx, client = pinned
            result = getattr(client, method)(*args, **kwargs)
            if method == "complete_with_tools":
                self._remember_response_client(getattr(result, "id", None), idx)
            return result

        idx, client = self._acquire()
        try:
            result = getattr(client, method)(*args, **kwargs)
        except AuthenticationError as first_exc:
            self._release(idx)
            failed = {idx}
            print(f"  Auth error on {client.endpoint}/{client.deployment}, trying other clients...")
            for fallback_idx, fallback_client in enumerate(self.clients):
                if fallback_idx in failed:
                    continue
                try:
                    result = getattr(fallback_client, method)(*args, **kwargs)
                    if method == "complete_with_tools":
                        self._remember_response_client(getattr(result, "id", None), fallback_idx)
                    return result
                except AuthenticationError:
                    failed.add(fallback_idx)
                    print(f"  Auth error on {fallback_client.endpoint}/{fallback_client.deployment}, trying another...")
            raise first_exc
        except Exception:
            self._release(idx)
            raise
        self._release(idx)
        if method == "complete_with_tools":
            self._remember_response_client(getattr(result, "id", None), idx)
        return result

    def complete_chat(
        self, messages: list[dict[str, str]], max_tokens: int = _DEFAULT_MAX_TOKENS, temperature: float = 1.0
    ) -> str:
        return self._call("complete_chat", messages, max_tokens, temperature)

    def complete_json(
        self, prompt: str, system_prompt: str | None = None, max_tokens: int = _DEFAULT_MAX_TOKENS, reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        return self._call("complete_json", prompt, system_prompt, max_tokens, reasoning_effort)

    def complete_json_response(
        self,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        reasoning_effort: str | None = None,
    ) -> Any:
        """Forward to pooled `_call('complete_json_response', ...)`.

        Upstream omits this wrapper; some agents (e.g. OracleAgent) call it
        directly on PooledLLMClient and would otherwise hit AttributeError.
        """
        return self._call(
            "complete_json_response",
            prompt,
            system_prompt,
            max_tokens,
            reasoning_effort,
        )

    def complete_with_tools(self, **kwargs: Any) -> Any:
        return self._call("complete_with_tools", **kwargs)

    def pinned(self) -> "PinnedClient":
        """Acquire a single deployment for the duration of a tool-calling loop.

        Usage:
            with pool.pinned() as client:
                response = client.complete_with_tools(...)
                # follow-up calls with previous_response_id go to same deployment
                response = client.complete_with_tools(..., previous_response_id=response.id)
        """
        return PinnedClient(self)


class PinnedClient:
    """Context manager that pins a PooledLLMClient to one deployment."""

    def __init__(self, pool: PooledLLMClient):
        self._pool = pool
        self._idx: int | None = None
        self._client: LLMClient | None = None

    def __enter__(self) -> LLMClient:
        self._idx, self._client = self._pool._acquire()
        return self._client

    def __exit__(self, *exc: Any) -> None:
        if self._idx is not None:
            self._pool._release(self._idx)


def build_judge_client(env_prefix: str = "JUDGE") -> PooledLLMClient:
    """Compatibility shim: judge traffic uses the shared pooled client."""
    _ = env_prefix
    return PooledLLMClient()
