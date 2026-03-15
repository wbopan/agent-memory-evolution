"""WebArena benchmark — web navigation task completion via BrowserGym.

Train items: human demonstration trajectories parsed from Playwright trace files.
Val items: task intents with BrowserGym task entrypoints for live browser episodes.
"""

from __future__ import annotations

import json
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
    for event in events:
        action = trace_event_to_action(event)
        if action is None:
            continue
        steps.append({"step_num": step_num, "action": action, "observation": ""})
        step_num += 1

    return steps
