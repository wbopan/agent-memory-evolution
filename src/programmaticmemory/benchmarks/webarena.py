"""WebArena benchmark — web navigation task completion via BrowserGym.

Train items: human demonstration trajectories parsed from Playwright trace files.
Val items: task intents with BrowserGym task entrypoints for live browser episodes.
"""

from __future__ import annotations

import json
import multiprocessing
import random
import re
import zipfile
from pathlib import Path

try:
    import browsergym.core
    import browsergym.webarena  # noqa: F401  # registers WebArena tasks as side-effect

    _HAS_BROWSERGYM = True
except ImportError:
    _HAS_BROWSERGYM = False


def format_trajectory_step(step_num: int, action: str, observation: str) -> str:
    """Format a single trajectory step as a human-readable string.

    Args:
        step_num: 1-based step number.
        action: BrowserGym-style action string (e.g. 'click(button "Submit")').
        observation: Page observation text after the action.

    Returns:
        Formatted string: "Step N: action\\nObservation: obs"
    """
    return f"Step {step_num}: {action}\nObservation: {observation}"


# Maps (Playwright class, method) tuples to BrowserGym action names.
# Only covers actions that appear in recorded Playwright traces.
PLAYWRIGHT_TO_BROWSERGYM: dict[tuple[str, str], str] = {
    ("Frame", "click"): "click",
    ("Frame", "fill"): "fill",
    ("Frame", "type"): "fill",
    ("Frame", "hover"): "hover",
    ("Frame", "selectOption"): "select_option",
    ("Frame", "goto"): "goto",
    ("Frame", "goBack"): "go_back",
    ("Frame", "goForward"): "go_forward",
    ("Frame", "press"): "keyboard_press",
    ("Keyboard", "press"): "keyboard_press",
    ("Keyboard", "type"): "keyboard_press",
    ("Mouse", "wheel"): "scroll",
    ("Page", "close"): "tab_close",
}


def parse_selector(selector: str) -> str:
    """Convert a Playwright internal selector to a readable description.

    Examples:
        'internal:role=button[name="Submit"]'  →  'button "Submit"'
        'internal:role=textbox'                →  'textbox'
        'internal:text="Sign in"i'             →  '"Sign in"'
        'internal:label="Email"'               →  '"Email"'
        'css=#search'                          →  '#search'
        'xpath=//div'                          →  '//div'
        'sel1 >> sel2'                         →  parse_selector('sel1')
        ''                                     →  ''

    Args:
        selector: Raw Playwright selector string.

    Returns:
        Human-readable element description.
    """
    if not selector:
        return ""

    # Chained selectors: take only the first part
    if " >> " in selector:
        selector = selector.split(" >> ")[0].strip()

    # internal:role=button[name="Submit"] or internal:role=textbox
    m = re.match(r'internal:role=(\w+)(?:\[name="([^"]+)"\])?', selector)
    if m:
        role = m.group(1)
        name = m.group(2)
        if name:
            return f'{role} "{name}"'
        return role

    # internal:text="Sign in"i  (optional trailing 'i' for case-insensitive)
    m = re.match(r'internal:text="([^"]*)"i?', selector)
    if m:
        return f'"{m.group(1)}"'

    # internal:label="Email"
    m = re.match(r'internal:label="([^"]*)"', selector)
    if m:
        return f'"{m.group(1)}"'

    # css=...
    m = re.match(r"css=(.+)", selector)
    if m:
        return m.group(1)

    # xpath=...
    m = re.match(r"xpath=(.+)", selector)
    if m:
        return m.group(1)

    # Fallback: return as-is
    return selector


def trace_event_to_action(event: dict) -> str | None:
    """Convert a Playwright trace 'before' event dict to a BrowserGym-style action string.

    Args:
        event: A dict from a Playwright trace NDJSON with type='before'.
            Expected keys: 'apiName' (e.g. 'Frame.click'), 'params' (dict of call params).

    Returns:
        BrowserGym action string, or None if the event is not a recordable action.
    """
    api_name: str = event.get("apiName", "")
    if not api_name or "." not in api_name:
        return None

    cls, method = api_name.split(".", 1)
    action_name = PLAYWRIGHT_TO_BROWSERGYM.get((cls, method))
    if action_name is None:
        return None

    params: dict = event.get("params", {}) or {}

    if action_name == "click":
        selector = parse_selector(params.get("selector", ""))
        return f"click({selector})"

    if action_name == "fill":
        selector = parse_selector(params.get("selector", ""))
        value = params.get("value", "")
        return f'fill({selector}, "{value}")'

    if action_name == "goto":
        url = params.get("url", "")
        return f'goto("{url}")'

    if action_name == "keyboard_press":
        # Frame.press has selector + key; Keyboard.press / Keyboard.type has key / text
        key = params.get("key") or params.get("text") or ""
        return f'keyboard_press("{key}")'

    if action_name == "select_option":
        selector = parse_selector(params.get("selector", ""))
        # options may be a list of strings or a list of dicts with 'value'
        options = params.get("options", []) or []
        if options and isinstance(options[0], dict):
            value = options[0].get("value", "")
        elif options:
            value = str(options[0])
        else:
            value = params.get("value", "")
        return f'select_option({selector}, "{value}")'

    if action_name == "scroll":
        dx = params.get("deltaX", 0)
        dy = params.get("deltaY", 0)
        return f"scroll({dx}, {dy})"

    if action_name == "hover":
        selector = parse_selector(params.get("selector", ""))
        return f"hover({selector})"

    if action_name == "go_back":
        return "go_back()"

    if action_name == "go_forward":
        return "go_forward()"

    if action_name == "tab_close":
        return "tab_close()"

    return None


def parse_trace_zip(zip_path: Path) -> list[dict]:
    """Parse a Playwright trace zip file into a list of trajectory step dicts.

    Reads the 'trace.trace' NDJSON file from the zip, filters 'before' events,
    sorts by startTime, and converts each to a BrowserGym action string.

    Args:
        zip_path: Path to the .zip file produced by Playwright tracing.

    Returns:
        List of {"step_num": int, "action": str, "observation": str} dicts,
        one per recordable action found in the trace. observation is always "".
    """
    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except (zipfile.BadZipFile, OSError):
        return []

    with zf:
        if "trace.trace" not in zf.namelist():
            return []
        raw = zf.read("trace.trace").decode("utf-8", errors="replace")

    events: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "before":
            events.append(obj)

    # Sort by startTime (float millis); events without startTime sort last
    events.sort(key=lambda e: e.get("startTime", float("inf")))

    steps: list[dict] = []
    step_num = 1
    # Observation = page URL, tracked from goto events only.
    # Unlike live agent trajectories (where the browser URL updates after every
    # step), Playwright traces don't record post-action browser state, so the
    # URL stays at the last goto target. This is acceptable because the KB
    # learns action-sequence strategies, not state-transition models.
    current_url = ""
    for event in events:
        action = trace_event_to_action(event)
        if action is None:
            continue
        # Update current URL from goto events
        params = event.get("params", {}) or {}
        if action.startswith("goto("):
            current_url = params.get("url", "")
        steps.append({"step_num": step_num, "action": action, "observation": current_url})
        step_num += 1

    return steps


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _load_traces(traces_path: str | Path | None = None) -> list[dict]:
    """Load WebArena demonstration traces from a JSON file.

    Args:
        traces_path: Path to the traces JSON file. Defaults to
            ``<repo_root>/data/webarena_traces.json``.

    Returns:
        List of trace dicts, or an empty list if the file is missing/invalid.
    """
    if traces_path is None:
        traces_path = Path(__file__).resolve().parents[4] / "data" / "webarena_traces.json"
    traces_path = Path(traces_path)
    if not traces_path.exists():
        return []
    try:
        return json.loads(traces_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _load_task_configs() -> list[dict]:
    """Load WebArena task configs from the ``webarena`` package.

    Reads ``test.raw.json`` bundled with the webarena package (installed as a
    dependency of browsergym-webarena).  URL placeholders (``__SHOPPING__`` etc.)
    are left as-is since we only need ``task_id``, ``sites``, and ``intent``.

    Returns:
        List of task config dicts with ``task_id``, ``sites``, ``intent``, and
        ``eval`` keys.  Returns an empty list when the package is not installed.
    """
    try:
        import importlib.resources

        import webarena  # type: ignore[import-untyped]

        all_configs_str = importlib.resources.files(webarena).joinpath("test.raw.json").read_text()
        all_configs = json.loads(all_configs_str)
        return [
            {
                "task_id": int(cfg.get("task_id", 0)),
                "sites": cfg.get("sites", []),
                "intent": cfg.get("intent", ""),
                "eval": cfg.get("eval", {}),
            }
            for cfg in all_configs
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXCLUDED_SITES: set[str] = {"map", "wikipedia"}
_AVAILABLE_CATEGORIES: list[str] = ["shopping", "shopping_admin", "reddit", "gitlab"]

# ---------------------------------------------------------------------------
# Dataset loader
# ---------------------------------------------------------------------------

from programmaticmemory.datasets import register_dataset  # noqa: E402
from programmaticmemory.evolution.evaluator import ExactMatchScorer  # noqa: E402
from programmaticmemory.evolution.types import DataItem, Dataset  # noqa: E402


@register_dataset("webarena")
def load_webarena(
    *, category: str | None = None, seed: int = 42, traces_path: str | Path | None = None, **kwargs
) -> Dataset:
    """Load the WebArena benchmark.

    Train items come from recorded human demonstration traces (raw_text = trajectory text).
    Val items are tasks without traces that require live browser episodes.

    Args:
        category: Filter to a specific site category (e.g. "shopping", "reddit").
            None = all non-excluded categories.
        seed: Random seed for shuffling.
        traces_path: Override path to ``webarena_traces.json``.

    Returns:
        Dataset with WebArenaValScorer (if BrowserGym is available).
    """
    if not _HAS_BROWSERGYM:
        raise ImportError(
            "BrowserGym is required for the WebArena benchmark. "
            "Install it with: pip install browsergym-core browsergym-webarena playwright"
        )

    traces = _load_traces(traces_path)
    task_configs = _load_task_configs()

    # Build a set of task_ids that have traces
    traced_task_ids: set[int] = {int(t["task_id"]) for t in traces if "task_id" in t}

    # Build a lookup from task_id → trace text
    trace_by_id: dict[int, str] = {}
    for t in traces:
        tid = t.get("task_id")
        if tid is not None:
            trace_by_id[int(tid)] = t.get("trajectory", "")

    train_items: list[DataItem] = []
    val_items: list[DataItem] = []

    for cfg in task_configs:
        sites: list[str] = cfg.get("sites", [])
        # Exclude tasks that touch excluded sites
        if any(s in _EXCLUDED_SITES for s in sites):
            continue

        # Category filter
        if category is not None and category not in sites:
            continue

        task_id = int(cfg.get("task_id", 0))
        intent = cfg.get("intent", "")

        metadata = {
            "task_id": task_id,
            "sites": sites,
        }

        if task_id in traced_task_ids:
            raw_text = trace_by_id.get(task_id, "")
            train_items.append(DataItem(raw_text=raw_text, question="", expected_answer="", metadata=metadata))
        else:
            val_items.append(
                DataItem(
                    raw_text="",
                    question=intent,
                    expected_answer="Task completed successfully (reward=1.0)",
                    metadata=metadata,
                )
            )

    rng = random.Random(seed)
    rng.shuffle(train_items)
    rng.shuffle(val_items)

    return Dataset(
        train=train_items,
        val=val_items,
        test=[],
        scorer=ExactMatchScorer(),
        val_scorer=WebArenaValScorer(),
        available_categories=_AVAILABLE_CATEGORIES,
        category_key="sites",
    )


# ---------------------------------------------------------------------------
# Agent action selection
# ---------------------------------------------------------------------------

import litellm  # noqa: E402

_ACTION_PROMPT = """\
You are a web automation agent. You observe a web page as an accessibility tree \
(AXTree) and output ONE action per turn.

# Actions
Each <element> argument is a BID number from the AXTree (the number in square brackets, e.g. 226).

  click(<bid>)                       — click element, e.g. click("226")
  fill(<bid>, "<text>")              — type into input, e.g. fill("679", "hello")
  hover(<bid>)                       — hover element
  select_option(<bid>, "<value>")    — pick dropdown option
  goto("<url>")                      — navigate to URL
  go_back()                          — browser back
  go_forward()                       — browser forward
  keyboard_press("<key>")            — press key (Enter, Tab, etc.)
  scroll(0, <dy>)                    — scroll down (positive) or up (negative)
  send_msg_to_user("<message>")      — send final answer to user (use when task is done)
  noop(1000)                         — wait 1 second

# Rules
- Output EXACTLY ONE action per turn.
- Use the BID numbers from the AXTree, NOT element text or CSS selectors.
- When the task asks a question, find the answer and use send_msg_to_user("answer").
- When the task asks to perform an action, navigate and act, then send_msg_to_user("done").
- Wrap the action in a code block: ```action
<your action here>
```

# Example
AXTree shows: [226] link 'MARKETING'
To click it: click("226")
"""


def _select_action(
    axtree_text: str,
    intent: str,
    tips: str,
    action_history: list[str],
    task_model: str,
    always_on_knowledge: str = "",
    *,
    reasoning_effort: str | None = None,
) -> str:
    """Use an LLM to select the next BrowserGym action.

    Args:
        axtree_text: Accessibility-tree text of the current page state.
        intent: Natural-language task intent / goal.
        tips: Retrieved procedural tips from the Knowledge Base.
        action_history: List of actions taken so far (most recent last).
        task_model: LiteLLM model string.
        always_on_knowledge: Always-on knowledge injected into every prompt.
        reasoning_effort: Optional reasoning effort level for the model.

    Returns:
        A BrowserGym-style action string.
    """
    # Build a single user prompt (no system message — compatible with chatgpt/ provider)
    parts: list[str] = [_ACTION_PROMPT]

    aok = always_on_knowledge.strip() if always_on_knowledge else ""
    if aok:
        parts += ["# Domain knowledge", aok]

    parts.append(f"\n# Task\n{intent.strip()}")

    if tips and tips.strip():
        parts += ["", "# Retrieved tips", tips.strip()]

    if action_history:
        recent = action_history[-10:]
        parts += ["", "# Previous actions"]
        for i, act in enumerate(recent, 1):
            parts.append(f"{i}. {act}")

    # Truncate AXTree to avoid exceeding context
    ax = axtree_text.strip() if axtree_text else "(empty page)"
    if len(ax) > 16000:
        ax = ax[:16000] + "\n... (truncated)"
    parts += ["", "# Current page (AXTree)", ax]

    parts.append("\nOutput ONE action in a ```action code block.")

    extra: dict = {}
    if reasoning_effort is not None:
        extra["reasoning_effort"] = reasoning_effort

    resp = litellm.completion(
        model=task_model,
        messages=[
            {"role": "system", "content": " "},
            {"role": "user", "content": "\n".join(parts)},
        ],
        max_tokens=512,
        caching=True,
        **extra,
    )
    raw = resp.choices[0].message.content or ""

    # Extract action from fenced code block
    m = re.search(r"```(?:action)?\s*\n(.*?)\n```", raw, re.DOTALL)
    if m:
        # Take only the first line (in case LLM outputs multiple actions)
        first_line = m.group(1).strip().split("\n")[0].strip()
        if first_line:
            return first_line

    # Fallback: look for a single function call pattern
    m = re.search(
        r"((?:click|fill|hover|select_option|goto|go_back|go_forward|keyboard_press|scroll|send_msg_to_user|noop|tab_close)\([^)]*\))",
        raw,
    )
    if m:
        return m.group(1).strip()

    return "noop(1000)"


# ---------------------------------------------------------------------------
# Episode runner (module-level for picklability)
# ---------------------------------------------------------------------------


def _run_episode(
    task_id: int,
    intent: str,
    tips: str,
    task_model: str,
    always_on_knowledge: str = "",
    reasoning_effort: str | None = None,
    max_steps: int = 30,
) -> tuple[str, float]:
    """Run a single WebArena episode inside a spawned process.

    Module-level function (not a method) so it can be pickled for ProcessPoolExecutor.

    Args:
        task_id: WebArena numeric task ID (0–811).
        intent: Natural-language task intent.
        tips: Retrieved procedural tips from the Knowledge Base.
        task_model: LiteLLM model string for the task agent.
        always_on_knowledge: Always-on knowledge string.
        reasoning_effort: Optional reasoning effort level.
        max_steps: Maximum number of agent steps per episode.

    Returns:
        (trajectory_text, reward) tuple.
    """
    try:
        from browsergym.core.env import BrowserEnv  # type: ignore[import-untyped]
        from browsergym.utils.obs import flatten_axtree_to_str  # type: ignore[import-untyped]
        from browsergym.webarena.task import GenericWebArenaTask  # type: ignore[import-untyped]
    except ImportError as exc:
        return (f"BrowserGym not available: {exc}", 0.0)

    def _extract_axtree(obs: dict) -> str:
        if "axtree_object" in obs:
            return flatten_axtree_to_str(obs["axtree_object"])
        return obs.get("axtree_txt", str(obs))

    trajectory_lines: list[str] = []
    action_history: list[str] = []
    reward = 0.0

    # Use system proxy if available (needed for mihomo/Stash setups)
    import os

    proxy_url = os.environ.get("ALL_PROXY") or os.environ.get("HTTP_PROXY")
    pw_chromium_kwargs: dict = {}
    if proxy_url:
        pw_chromium_kwargs["proxy"] = {"server": proxy_url}

    try:
        env = BrowserEnv(
            task_entrypoint=GenericWebArenaTask,
            task_kwargs={"task_id": task_id},
            headless=True,
            timeout=60000,
            pw_chromium_kwargs=pw_chromium_kwargs,
        )
        try:
            obs, _info = env.reset()
            axtree = _extract_axtree(obs)

            for _step in range(max_steps):
                action = _select_action(
                    axtree,
                    intent,
                    tips,
                    action_history,
                    task_model,
                    always_on_knowledge,
                    reasoning_effort=reasoning_effort,
                )
                action_history.append(action)
                trajectory_lines.append(f"ACTION: {action}")

                obs, step_reward, terminated, truncated, _info = env.step(action)
                reward = float(step_reward)
                axtree = _extract_axtree(obs)
                trajectory_lines.append(f"OBSERVATION: {axtree[:500]}")

                if terminated or truncated:
                    break
        finally:
            try:
                env.close()
            except Exception:
                pass
    except Exception as exc:
        trajectory_lines.append(f"Episode error: {exc}")

    return ("\n".join(trajectory_lines), reward)


# ---------------------------------------------------------------------------
# ValScorer
# ---------------------------------------------------------------------------


class WebArenaValScorer:
    """Pluggable val scorer that runs live WebArena browser episodes via BrowserGym.

    Episodes run in parallel via ProcessPoolExecutor with a 'spawn' context to
    isolate Playwright / browser state between episodes.
    """

    def __init__(self, max_steps: int = 30, max_workers: int = 4, episode_timeout: float = 600.0) -> None:
        self.max_steps = max_steps
        self.max_workers = max_workers
        self.episode_timeout = episode_timeout

    def score_batch(
        self,
        items: list[DataItem],
        retrieved: list[str],
        task_model: str,
        instruction_response: str,
        always_on_knowledge: str = "",
        *,
        reasoning_effort: str | None = None,
    ) -> list[tuple[str, float]]:
        """Run one live episode per item in parallel, return (trajectory, score) pairs."""
        import concurrent.futures

        workers = min(self.max_workers, len(items)) if items else 1
        ctx = multiprocessing.get_context("spawn")
        results: list[tuple[str, float]] = []
        try:
            with concurrent.futures.ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
                futures = [
                    pool.submit(
                        _run_episode,
                        item.metadata["task_id"],
                        item.question,
                        tips,
                        task_model,
                        always_on_knowledge,
                        reasoning_effort,
                        self.max_steps,
                    )
                    for item, tips in zip(items, retrieved, strict=True)
                ]
                for f in futures:
                    try:
                        results.append(f.result(timeout=self.episode_timeout))
                    except Exception as exc:
                        results.append((f"Episode failed: {exc}", 0.0))
        except Exception:
            while len(results) < len(items):
                results.append(("Episode failed: broken process pool", 0.0))
        return results
