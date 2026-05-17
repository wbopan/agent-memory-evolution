"""STATE-Bench benchmark — multi-turn task completion through STATE-Bench v0.4.0.

Renders task annotations into a deterministic D-full `raw_text` (zero LLM cost,
ground truth included) and runs each item through STATE-Bench's pure-Python
orchestrator with a custom `MstarKBAgent` whose `prepare_conversation` hook
injects the mstar KB result.
"""

from __future__ import annotations

import concurrent.futures
import json
import random
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

from mstar.datasets import register_dataset
from mstar.evolution.evaluator import RuntimeViolationError
from mstar.evolution.types import DataItem, Dataset

DEFAULT_DATA_DIR = "Data/STATE-Bench"
DEFAULT_DOMAINS = ("customer_support", "travel", "shopping_assistant")

_INIT_LOCK = threading.Lock()
_INITIALIZED = False
_DEFAULT_JUDGE_REASONING = "low"


class StateBenchInfraError(RuntimeViolationError):
    """Infrastructure failure during a STATE-Bench task (proxy/network/Azure).

    MUST inherit RuntimeViolationError so the evaluator's existing infra-error
    handling (evaluator.py:552, 666) catches it. Subclassing RuntimeError
    directly will crash the evolution run.
    """


def _render_d_full(task: dict[str, Any]) -> str:
    """Render a STATE-Bench task definition into the D-full template.

    D-full is a deterministic flat string with:
    - Header line `# Task: <task_id>`
    - The full `task_summary` markdown text (already includes `**Task:**` /
      `**Challenge:**` sections in upstream files)
    - MUST / MUST NOT requirement lists from `task_requirements`
    - State requirement lines `- <entity_type>.<record_key>.<field><pad> = <value>`
      with a per-task computed pad so the `=` column aligns
    - opening_message

    Returns a single string suitable for use as `DataItem.raw_text`.
    """
    parts: list[str] = []
    parts.append(f"# Task: {task.get('task_id', '<unknown>')}")
    parts.append("")

    summary = task.get("task_summary") or ""
    if summary:
        parts.append(summary.strip())
        parts.append("")

    reqs = task.get("task_requirements") or []
    musts = [r for r in reqs if (r.get("kind") or "").lower() == "must"]
    must_nots = [r for r in reqs if (r.get("kind") or "").lower() == "must_not"]

    if musts:
        parts.append("MUST:")
        for r in musts:
            parts.append(f"- {r.get('requirement', '')}")
        parts.append("")
    if must_nots:
        parts.append("MUST NOT:")
        for r in must_nots:
            parts.append(f"- {r.get('requirement', '')}")
        parts.append("")

    state = task.get("state_requirements") or []
    if state:
        # Per-task computed padding so `=` aligns across lines.
        keys = [
            f"{s.get('entity_type', '')}.{s.get('record_key', '')}.{s.get('field', '')}"
            for s in state
        ]
        pad = max((len(k) for k in keys), default=0)
        parts.append("Final state:")
        for s, full_key in zip(state, keys, strict=True):
            value = s.get("expected_value", "")
            parts.append(f"- {full_key}{' ' * (pad - len(full_key))} = {value!r}")
        parts.append("")

    opening = task.get("opening_message")
    if opening:
        parts.append(f"User opens with: {opening}")

    return "\n".join(parts).rstrip() + "\n"


def _render_question(task: dict[str, Any]) -> str:
    """Render the question string used as `DataItem.question`.

    Deterministic; includes task_id (so failed-case logs are easy to grep) and
    the opening_message (or task_summary as fallback) so the agent has the
    starting context the orchestrator will inject.
    """
    task_id = task.get("task_id", "<unknown>")
    opening = (task.get("opening_message") or "").strip()
    if not opening:
        opening = (task.get("task_summary") or "").strip()
    return f"[{task_id}] {opening}"


def _split_train_val(items: list, val_size: int, seed: int = 0) -> tuple[list, list]:
    """Deterministic train/val split of the official-train pool.

    ``val`` receives ``val_size`` items, ``train`` the rest; original order is
    preserved within each side. Returns ``(train, val)``. ``val_size`` equal to
    half the pool reproduces the legacy 50/50 split for a given seed.
    """
    if not 0 < val_size < len(items):
        raise ValueError(
            f"val_size must be in (1, {len(items) - 1}) for a pool of "
            f"{len(items)} items; got {val_size}"
        )
    rng = random.Random(seed)
    indices = list(range(len(items)))
    rng.shuffle(indices)
    train_size = len(items) - val_size
    train_idx = sorted(indices[:train_size])
    val_idx = sorted(indices[train_size:])
    return [items[i] for i in train_idx], [items[i] for i in val_idx]


def _load_task_dict(task_path: Path) -> dict[str, Any]:
    return json.loads(task_path.read_text())


def _build_data_item(task: dict[str, Any], *, domain: str, task_path: Path) -> DataItem:
    return DataItem(
        raw_text=_render_d_full(task),
        question=_render_question(task),
        expected_answer="",  # state_bench has no string answer; scoring is state+requirements based
        metadata={
            "task_id": task.get("task_id"),
            "domain": domain,
            "task_path": str(task_path),
        },
    )


def _read_split_ids(splits_path: Path) -> tuple[list[str], list[str]]:
    """Parse a STATE-Bench splits/train_test_v1.json file.

    Splits live nested under the `splits` key:
        {"domain": ..., "version": ..., "splits": {"train": [...], "test": [...]}, ...}
    """
    raw = json.loads(splits_path.read_text())
    splits = raw.get("splits", {})
    return list(splits.get("train", [])), list(splits.get("test", []))


@register_dataset("state_bench")
def load_state_bench(
    *,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    domain: str | None = None,
    category: str | None = None,
    seed: int = 0,
    val_size: int | None = None,
) -> Dataset:
    """Load STATE-Bench tasks as an mstar Dataset.

    Splits: official train pool -> (pool - val_size) train + val_size val
            (deterministic by seed). official test -> kept as-is.

    ``val_size`` defaults to ``None`` -> a 50/50 train/val split (legacy
    behavior). A larger ``val_size`` makes evolution scoring less noisy but
    leaves fewer train examples for the KB — pass e.g. ``val_size=70`` as a
    benchmark kwarg.

    Restrict to a single STATE-Bench domain via ``domain=`` or ``category=``
    (mstar's CLI uses ``--category`` as the canonical knob; both forms accepted).
    """
    data_root = Path(data_dir)
    selected = domain or category
    domains: tuple[str, ...] = (selected,) if selected else DEFAULT_DOMAINS

    train_items: list[DataItem] = []
    val_items: list[DataItem] = []
    test_items: list[DataItem] = []

    for d in domains:
        dom_root = data_root / "domains" / d
        splits_path = dom_root / "splits" / "train_test_v1.json"
        if not splits_path.exists():
            raise FileNotFoundError(
                f"STATE-Bench splits file not found: {splits_path}. "
                f"Did Task 0 (vendor + extract) complete?"
            )
        official_train, official_test = _read_split_ids(splits_path)
        n_val = int(val_size) if val_size is not None else len(official_train) // 2
        train_ids, val_ids = _split_train_val(official_train, val_size=n_val, seed=seed)

        for tid in train_ids:
            tp = dom_root / "tasks" / f"{tid}.json"
            train_items.append(_build_data_item(_load_task_dict(tp), domain=d, task_path=tp))
        for tid in val_ids:
            tp = dom_root / "tasks" / f"{tid}.json"
            val_items.append(_build_data_item(_load_task_dict(tp), domain=d, task_path=tp))
        for tid in official_test:
            tp = dom_root / "tasks" / f"{tid}.json"
            test_items.append(_build_data_item(_load_task_dict(tp), domain=d, task_path=tp))

    return Dataset(
        train=train_items,
        val=val_items,
        test=test_items,
        compare_fn=None,
        val_scorer=StateBenchValScorer(),
        available_categories=list(DEFAULT_DOMAINS),
        category_key="domain",
    )


def _ensure_state_bench_initialized() -> None:
    """Idempotent: append vendored Data/STATE-Bench to sys.path + load .env once.

    Double-checked locking: cheap fast-path read, locked slow-path init. Safe
    under threads (we run tasks via ThreadPoolExecutor).
    """
    global _INITIALIZED
    if _INITIALIZED:
        return
    with _INIT_LOCK:
        if _INITIALIZED:
            return
        vendor_root = Path(DEFAULT_DATA_DIR).resolve()
        if str(vendor_root) not in sys.path:
            sys.path.insert(0, str(vendor_root))

        env_path = vendor_root / ".env"
        if env_path.exists():
            try:
                from dotenv import load_dotenv
            except ImportError as exc:
                raise StateBenchInfraError(
                    "python-dotenv is required for STATE-Bench. "
                    "Install with: uv sync --extra state-bench"
                ) from exc
            load_dotenv(env_path, override=False)
        _INITIALIZED = True


def _looks_like_infra_error(exc: BaseException) -> bool:
    """True if the exception is an openai/network/proxy infrastructure issue."""
    try:
        import openai
    except ImportError:
        return False
    return isinstance(
        exc,
        (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        ),
    )


def _build_pooled_client() -> Any:
    """Construct a STATE-Bench PooledLLMClient. Surfaces infra failures cleanly."""
    try:
        from state_bench.client import PooledLLMClient
    except ImportError as exc:
        raise StateBenchInfraError(f"STATE-Bench import failed: {exc}") from exc
    try:
        return PooledLLMClient()
    except Exception as exc:
        if _looks_like_infra_error(exc):
            raise StateBenchInfraError(f"PooledLLMClient init failed: {exc}") from exc
        raise


def _extract_tool_calls(conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pull tool_calls list from a STATE-Bench conversation.

    The conversation is the Responses API input array — function_call items
    appear as raw output items in the conversation. The orchestrator's run_task
    builds these as dicts; we mirror scripts/run_task.py's extraction.
    """
    out: list[dict[str, Any]] = []
    for msg in conversation:
        if isinstance(msg, dict):
            tc = msg.get("tool_calls")
            if tc:
                out.extend(tc)
    return out


def _last_assistant_text(conversation: list[dict[str, Any]], max_chars: int = 400) -> str:
    """Return the final assistant message content, truncated."""
    for msg in reversed(conversation):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            content = msg.get("content") or ""
            if isinstance(content, str) and content.strip():
                return content.strip()[:max_chars]
    return ""


def _build_compact_transcript(task: Any, trajectory: Any) -> str:
    """Render a compact action/utterance trace in the ALFWorld 'output' style.

    Keeps each turn ≤ ~150 chars (user/agent text snippet + tool names called),
    so a multi-turn task fits in ~1-2 KB rather than the 16-30 KB full JSON
    dump. Combined with the rationale field, this gives the reflector both
    'what the agent did' (transcript) and 'why it failed' (rationale) in
    bounded tokens.
    """
    lines: list[str] = [f"# Task {task.task_id}"]
    turn = 0
    for msg in trajectory.conversation:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role == "user" and isinstance(content, str):
            turn += 1
            lines.append(f"User T{turn}: {content.strip()[:150]}")
        elif role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            tool_names = [tc.get("name", "?") for tc in tool_calls if isinstance(tc, dict)]
            text = (content if isinstance(content, str) else "") or ""
            text = text.strip().replace("\n", " ")[:150]
            if tool_names:
                lines.append(f"Agent T{turn}: tools={tool_names} | {text}")
            elif text:
                lines.append(f"Agent T{turn}: {text}")
    return "\n".join(lines)


def _build_rationale(task: Any, trajectory: Any) -> str:
    """Build a reflection-grade rationale string from a scored trajectory.

    Includes: task summary (truncated), pass/fail per axis, efficiency,
    judge reasoning, per-requirement failures with the judge's explanations,
    and a snippet of the agent's final message. Designed so the mstar
    reflector has enough signal to propose KB improvements.
    """
    state_score = trajectory.state_requirements_score
    reqs_score = trajectory.task_requirements_score
    completion = trajectory.task_completion_pass

    state_pass = state_score.score if state_score else None
    reqs_pass = reqs_score.score if reqs_score else None
    eff = trajectory.efficiency
    turns = eff.turns if eff else "?"
    tool_calls = eff.tool_calls if eff else "?"

    parts: list[str] = []
    verdict = "PASS" if completion == 1 else "FAIL"
    parts.append(
        f"STATE-Bench task {task.task_id} ({verdict}): "
        f"state_pass={state_pass} task_reqs_pass={reqs_pass} "
        f"completion={completion} turns={turns} tool_calls={tool_calls}"
    )

    summary = (task.task_summary or "").strip()
    if summary:
        parts.append(f"Task: {summary[:400]}")

    if state_score and state_score.reasoning:
        marker = "OK" if state_pass == 1 else "FAIL"
        parts.append(f"State [{marker}]: {state_score.reasoning.strip()[:400]}")

    if reqs_score:
        marker = "OK" if reqs_pass == 1 else "FAIL"
        if reqs_score.reasoning:
            parts.append(f"Task-requirements [{marker}]: {reqs_score.reasoning.strip()[:400]}")
        # Surface failed per-requirement details (most useful signal for the reflector)
        failed_details = [d for d in (reqs_score.details or []) if not d.get("passed", False)]
        if failed_details:
            parts.append("Failed requirements:")
            for d in failed_details[:5]:
                rid = d.get("id", "<no-id>")
                reason = (d.get("reasoning") or "").strip()[:300]
                parts.append(f"  - {rid}: {reason}")

    last_msg = _last_assistant_text(trajectory.conversation, max_chars=300)
    if last_msg:
        parts.append(f"Final agent turn: {last_msg}")

    return "\n".join(parts)


def _run_single_task(
    item: DataItem,
    kb_text: str,
    task_model: str,  # noqa: ARG001 — STATE-Bench routes via PooledLLMClient + AZURE_OPENAI_DEPLOYMENTS env
    reasoning_effort: str | None,
) -> tuple[str, float, str]:
    """Run one STATE-Bench task and return (transcript, score, rationale).

    Score: ``state_pass@1 ∧ task_requirements_pass@1`` -> 1.0 or 0.0.
    Infra exceptions become ``StateBenchInfraError``; other exceptions bubble
    up to the scorer which records them as model failures.
    """
    _ensure_state_bench_initialized()

    try:
        from state_bench.domain import get_domain_config
        from state_bench.env_loader import load_task_environment
        from state_bench.orchestrator import run_task
        from state_bench.schemas import TaskDefinition
        from state_bench.scoring import (
            TaskRequirementsJudge,
            combine_task_completion,
            evaluate_state_requirements,
        )
        from agents.base import AgentRuntimeContext
        from agents.mstar_kb_agent import MstarKBAgent
    except ImportError as exc:
        raise StateBenchInfraError(f"STATE-Bench import failed: {exc}") from exc

    try:
        task_path = Path(item.metadata["task_path"])
        domain_name = item.metadata["domain"]
        task = TaskDefinition.load(task_path)
        domain = get_domain_config(domain_name)

        # Resolve task_env_path to an absolute path rooted at the vendored
        # STATE-Bench dir. Upstream resolves non-absolute paths via Path.cwd(),
        # which fails when CWD is the mstar repo root rather than Data/STATE-Bench.
        if task.task_env_path and not Path(task.task_env_path).is_absolute():
            vendor_root = Path(DEFAULT_DATA_DIR).resolve()
            task.task_env_path = str(vendor_root / task.task_env_path)

        env_data, _env_path = load_task_environment(domain, task)

        client = _build_pooled_client()

        agent_system_prompt = domain.agent_system_prompt.format(
            now=task.now, user_id=task.user_id
        )
        env = domain.environment_class(env_data.deep_copy(), now=task.now)

        runtime_ctx = AgentRuntimeContext(
            task_id=task.task_id,
            user_id=task.user_id,
            domain=domain.name,
            now=task.now,
            task_summary=task.task_summary,
            state_requirements=task.state_requirements,
            task_requirements=task.task_requirements,
        )

        agent = MstarKBAgent(
            client=client,
            system_prompt=agent_system_prompt,
            tools=domain.tool_schemas,
            tool_handlers=env.tool_handlers,
            runtime_context=runtime_ctx,
            kb_text=kb_text,
        )

        trajectory = run_task(
            task,
            env_data,
            task.user_id,
            client,
            domain=domain,
            agent=agent,
            env=env,
        )

        # Score state (deterministic) + task requirements (LLM judge)
        trajectory.state_requirements_score = evaluate_state_requirements(task, trajectory.state_diff)
        tool_calls = _extract_tool_calls(trajectory.conversation)
        judge = TaskRequirementsJudge(
            client=client,
            prompts_dir=domain.prompts_dir,
            system_prompt=domain.judge_system_prompt,
            reasoning_effort=reasoning_effort or _DEFAULT_JUDGE_REASONING,
        )
        trajectory.task_requirements_score = judge.evaluate(
            task, trajectory.conversation, tool_calls, trajectory.state_diff
        )
        trajectory.task_completion_pass = combine_task_completion(
            trajectory.state_requirements_score, trajectory.task_requirements_score
        )

        score = 1.0 if trajectory.task_completion_pass == 1 else 0.0
        # transcript = compact behavior trace; rationale = judge analysis.
        # We deliberately do NOT use the full Responses API conversation JSON
        # (16-30 KB per task): with reflection_max_failed_cases=3 it produced
        # ~22K-token reflect prompts that hung gpt-5.1/gpt-5.4 with
        # reasoning_effort=high for 10+ min per call. The compact trace keeps
        # the reflection prompt under ~5K tokens while still surfacing what
        # the agent did.
        transcript = _build_compact_transcript(task, trajectory)
        rationale = _build_rationale(task, trajectory)
        return (transcript, score, rationale)
    except StateBenchInfraError:
        raise
    except Exception as exc:
        if _looks_like_infra_error(exc):
            raise StateBenchInfraError(
                f"infra failure during task {item.metadata.get('task_id')}: {exc}"
            ) from exc
        raise


class StateBenchValScorer:
    """val_scorer for STATE-Bench. One thread per task with bounded timeout.

    Concurrency uses ``ThreadPoolExecutor`` with a try/finally + ``shutdown(wait=False,
    cancel_futures=True)`` exit. Do NOT use ``with`` — ``__exit__`` calls
    ``shutdown(wait=True)`` and hangs forever on a stuck Azure thread.
    """

    def __init__(self, max_workers: int = 8, task_timeout: float = 600.0) -> None:
        self.max_workers = max_workers
        self.task_timeout = task_timeout

    def score_batch(
        self,
        items: list[DataItem],
        retrieved: list[str],
        task_model: str,
        instruction_response: str,  # noqa: ARG002 — required by ValScorer protocol, unused
        always_on_knowledge: str = "",  # noqa: ARG002 — same
        *,
        reasoning_effort: str | None = None,
    ) -> list[tuple[str, float, str]]:
        if not items:
            return []
        workers = min(self.max_workers, len(items))
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        results: list[tuple[str, float, str] | None] = [None] * len(items)
        try:
            futures = {
                executor.submit(_run_single_task, item, retrieved[i], task_model, reasoning_effort): i
                for i, item in enumerate(items)
            }
            for fut in concurrent.futures.as_completed(futures, timeout=None):
                idx = futures[fut]
                try:
                    results[idx] = fut.result(timeout=self.task_timeout)
                except StateBenchInfraError:
                    raise
                except concurrent.futures.TimeoutError:
                    results[idx] = (
                        f"task timed out after {self.task_timeout}s",
                        0.0,
                        f"STATE-Bench task. Hit per-task timeout ({self.task_timeout}s).",
                    )
                except Exception as exc:
                    tb = traceback.format_exc()
                    results[idx] = (
                        f"task crashed: {exc}\n{tb}",
                        0.0,
                        f"STATE-Bench task. Model-side exception: {exc}",
                    )
        finally:
            # Do NOT use `with ThreadPoolExecutor(...)` — its __exit__ calls
            # shutdown(wait=True) which hangs forever on stuck Azure threads.
            executor.shutdown(wait=False, cancel_futures=True)

        return [
            r if r is not None else ("missing result", 0.0, "scorer bug: result not populated")
            for r in results
        ]
