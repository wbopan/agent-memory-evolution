"""AWM (Agent Workflow Memory) baseline reproduction for AgentBoard benchmarks.

Faithfully reproduces the AWM pipeline (Wang et al., 2024, arXiv 2409.07429)
adapted for interactive text environments (ScienceWorld, BabyAI, PDDL).

AWM pipeline (§2 of paper):
1. Agent solves tasks, producing trajectories of (observation, reasoning, action)
2. Successful trajectories → workflow induction via LLM (§2.3)
   - Each experience is individually processed
   - Previously induced workflows are provided as context for sub-routine composition
   - The snowball effect: simple workflows become building blocks for complex ones
3. Workflows stored in memory with NL descriptions for retrieval
4. At test time, relevant workflows retrieved by semantic similarity and injected
   into the agent's prompt to guide action generation

Supports both modes from the paper:
- **Offline** (§3.2, Mind2Web style): induce from training trajectories, apply to val
- **Online** (§3.1, WebArena style): process val items sequentially, induce from
  successful ones, accumulate workflows for subsequent items

Adaptation decisions from web navigation to text environments:
- AWM's (observation, reasoning, action) trajectory structure maps directly:
  observation = env text, reasoning = LLM chain-of-thought, action = text command
- Web actions (CLICK, TYPE) → text commands (ScienceWorld), grid actions (BabyAI),
  PDDL operators. Workflow step format is identical.
- Sub-routine composition is preserved: induction prompt includes existing workflows
  as context, matching the snowball mechanism in Figure 1/6.
- Workflow retrieval uses embedding similarity (BGE-M3), matching AWM's mechanism.
- Evaluation uses the same AgentBoardValScorer (progress rate) for comparability.

Usage:
    # Offline mode (default): induce from train, evaluate on val
    python -m programmaticmemory.baselines.awm --category babyai
    # Online mode: streaming induction on val items
    python -m programmaticmemory.baselines.awm --category babyai --mode online
    # Both: offline induction + online accumulation
    python -m programmaticmemory.baselines.awm --category babyai --mode offline+online
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import re
import sys
from dataclasses import dataclass, field

import litellm

from programmaticmemory.benchmarks.agentboard import _parse_action_response
from programmaticmemory.datasets import load_dataset
from programmaticmemory.evolution.types import DataItem

# ---------------------------------------------------------------------------
# Data structures (§2.2 Workflow Representation)
# ---------------------------------------------------------------------------


@dataclass
class TrajectoryStep:
    """One step in an agent trajectory: (observation, reasoning, action).

    Matches AWM §2.2 Figure 2 step structure:
    (1) NL description of current environment state
    (2) Reasoning process to decide action
    (3) Executable action
    """

    observation: str
    reasoning: str
    action: str


@dataclass
class Trajectory:
    """A complete agent experience e = (q, P^e) from §2.1."""

    env_type: str
    objective: str  # NL instruction q
    env_config: dict
    steps: list[TrajectoryStep]  # P^e = (p1, ..., pn)
    progress: float


@dataclass
class WorkflowStep:
    """One step in a workflow trajectory (§2.2)."""

    state_description: str
    reasoning: str
    action: str


@dataclass
class Workflow:
    """A workflow w = (d, P^d) from §2.2.

    description: NL task description d (high-level goal)
    steps: workflow trajectory P^d = (p1, p2, ...)
    """

    description: str
    steps: list[WorkflowStep]
    source_env: str = ""
    embedding: list[float] = field(default_factory=list, repr=False)


# ---------------------------------------------------------------------------
# Trajectory collection — captures (observation, reasoning, action) per step
# ---------------------------------------------------------------------------


def _build_action_prompt(
    env_type: str,
    objective: str,
    trajectory_text: str,
    valid_actions: list[str],
    workflow_tips: str = "",
) -> str:
    """Build the action selection prompt, optionally augmented with workflows.

    When workflow_tips is non-empty, workflows are integrated into the agent's
    memory M (§2.1), matching AWM's L(q, M, o_i) → a_i formulation.
    """
    env_desc = {
        "scienceworld": "You are controlling a text-based ScienceWorld environment to perform science experiments.",
        "babyai": (
            "You are controlling a BabyAI grid-world environment."
            " Navigate and interact with objects to complete the mission."
        ),
        "pddl": "You are solving a PDDL planning problem. Choose actions to satisfy all goal conditions.",
    }.get(env_type, "You are controlling a text-based environment.")

    lines = [env_desc]

    # Workflows integrated as agent memory (§2.3)
    if workflow_tips and workflow_tips.strip():
        lines += [
            "",
            "You have learned the following reusable workflows from past experience.",
            "Use them as guidance when applicable:",
            workflow_tips.strip(),
        ]

    lines += [
        "",
        "Think step-by-step about what action to take next.",
        "First explain your REASONING (why this action), then state the ACTION.",
        "Format: REASONING: <your reasoning>",
        "ACTION: <exact command from valid actions>",
        "You MUST choose from the valid actions list and copy it EXACTLY.",
    ]
    if objective:
        lines += ["", "Goal:", objective.strip()]
    lines += ["", "Interaction history:"]
    if trajectory_text and trajectory_text.strip():
        traj_lines = trajectory_text.strip().splitlines()
        if len(traj_lines) > 40:
            traj_lines = ["(earlier history truncated)", "..."] + traj_lines[-40:]
        lines.append("\n".join(traj_lines))
    else:
        lines.append("(empty)")
    if valid_actions:
        lines += ["", "Valid actions (choose exactly ONE):"]
        for cmd in valid_actions:
            lines.append(f"- {cmd}")
    return "\n".join(lines)


def _parse_reasoning_and_action(response: str, valid_actions: list[str]) -> tuple[str, str]:
    """Extract reasoning and action from LLM response.

    Expected format: REASONING: ... ACTION: ...
    Falls back to treating entire response as action if format not found.
    """
    text = (response or "").strip()
    reasoning = ""
    action_text = text

    # Try to parse REASONING: ... ACTION: ... format
    reasoning_match = re.search(r"REASONING:\s*(.*?)(?=ACTION:|$)", text, re.DOTALL | re.IGNORECASE)
    action_match = re.search(r"ACTION:\s*(.*)", text, re.DOTALL | re.IGNORECASE)

    if reasoning_match:
        reasoning = reasoning_match.group(1).strip()
    if action_match:
        action_text = action_match.group(1).strip()

    action = _parse_action_response(action_text, valid_actions)
    return reasoning, action


def _select_action_with_reasoning(
    env_type: str,
    objective: str,
    trajectory_text: str,
    valid_actions: list[str],
    task_model: str,
    workflow_tips: str = "",
) -> tuple[str, str]:
    """Select action and capture reasoning chain.

    Returns (reasoning, action) matching AWM's trajectory step structure.
    """
    prompt = _build_action_prompt(env_type, objective, trajectory_text, valid_actions, workflow_tips)
    resp = litellm.completion(
        model=task_model, messages=[{"role": "user", "content": prompt}], max_tokens=256, caching=True
    )
    raw = resp.choices[0].message.content.strip()
    return _parse_reasoning_and_action(raw, valid_actions)


def _create_env_wrapper(env_type: str, env_config: dict, max_steps: int):
    """Create the appropriate environment wrapper."""
    if env_type == "scienceworld":
        from programmaticmemory.benchmarks._scienceworld_wrapper import ScienceWorldWrapper

        return ScienceWorldWrapper(env_config["task_name"], env_config["variation_idx"], step_limit=max_steps)
    if env_type == "babyai":
        from programmaticmemory.benchmarks._babyai_wrapper import BabyAIWrapper

        return BabyAIWrapper(env_config["env_id"], seed=env_config["seed"], max_steps=max_steps)
    if env_type == "pddl":
        from programmaticmemory.benchmarks._pddl_wrapper import PDDLWrapper

        return PDDLWrapper(env_config["env_id"], env_config["problem_idx"])
    msg = f"Unknown env_type: {env_type}"
    raise ValueError(msg)


def _run_episode_with_reasoning(
    env_type: str,
    env_config: dict,
    objective: str,
    task_model: str,
    max_steps: int,
    workflow_tips: str = "",
) -> Trajectory:
    """Run one episode capturing full (observation, reasoning, action) per step.

    This replaces the original _run_episode for AWM, since AWM requires the
    reasoning chain to be part of the trajectory for workflow induction.
    """
    wrapper = _create_env_wrapper(env_type, env_config, max_steps)

    steps: list[TrajectoryStep] = []
    try:
        obs = wrapper.reset()
        trajectory_display_lines = [obs.strip()]
        progress = 0.0
        for _ in range(max_steps):
            valid_actions = wrapper.get_valid_actions()
            if not valid_actions:
                break
            reasoning, action = _select_action_with_reasoning(
                env_type,
                objective,
                "\n".join(trajectory_display_lines),
                valid_actions,
                task_model,
                workflow_tips,
            )
            steps.append(TrajectoryStep(observation=obs.strip(), reasoning=reasoning, action=str(action)))
            trajectory_display_lines.append(f"ACTION: {action}")
            obs, progress, done = wrapper.step(action)
            trajectory_display_lines.append(f"OBSERVATION: {obs.strip()}")
            if done:
                break
    finally:
        wrapper.close()

    return Trajectory(
        env_type=env_type,
        objective=objective,
        env_config=env_config,
        steps=steps,
        progress=progress,
    )


def collect_trajectories(
    items: list[DataItem],
    task_model: str,
    max_steps: int = 30,
    max_workers: int = 5,
    timeout: float = 300.0,
    workflow_tips: str = "",
) -> list[Trajectory]:
    """Run episodes on items and collect structured trajectories with reasoning."""
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(
                _run_episode_with_reasoning,
                item.metadata["env"],
                item.metadata,
                item.raw_text or item.question,
                task_model,
                max_steps,
                workflow_tips,
            )
            for item in items
        ]
        trajectories: list[Trajectory] = []
        for i, f in enumerate(futures):
            try:
                traj = f.result(timeout=timeout)
                trajectories.append(traj)
            except Exception as exc:
                print(f"  Episode {i} failed: {exc}", file=sys.stderr)
    return trajectories


# ---------------------------------------------------------------------------
# Workflow induction (§2.3)
#
# Key paper details:
# - Induction module I(E) → W operates on experiences E = {e_i}
# - Each experience e = (q, P^e) with trajectory P^e = (p1, ..., pn)
# - AWM induces per-experience, then accumulates (not batch-all-at-once)
# - Previously induced workflows are fed back as context for sub-routine
#   composition (the snowball effect, Figure 1 and 6)
# ---------------------------------------------------------------------------

INDUCTION_PROMPT = """\
You are an expert at extracting reusable task workflows from agent action \
trajectories in interactive environments.

Given an agent's trajectory of solving a task, extract reusable sub-routine \
WORKFLOWS — common action patterns that could help solve similar tasks.

{existing_workflows_section}

Here is the agent trajectory:

Task instruction: {objective}
Environment: {env_type}

{trajectory_text}

Task outcome: progress = {progress:.2f} (1.0 = fully solved)

Extract reusable workflows from this trajectory. A workflow is a sub-routine \
that captures a common pattern.

Each workflow has:
1. "description" — NL summary of the workflow's goal \
(e.g., "Navigate to a specific object", "Heat a substance using a stove")
2. "steps" — a list, each with:
   - "state_description": abstracted NL description of environment state \
(NOT instance-specific, use general terms)
   - "reasoning": why this action is appropriate at this point
   - "action": the action command (use {{placeholder}} variables for \
instance-specific values like object names, locations)

Guidelines:
- Extract 0-3 workflows (0 if the trajectory has no reusable patterns)
- Abstract away specific names/values into {{placeholder}} variables
- Focus on SUB-ROUTINES, not the full task trajectory
{composition_guideline}
- Only extract patterns that would genuinely help with other tasks

Output valid JSON array (or empty array [] if no useful workflows):
```json
[
  {{
    "description": "...",
    "steps": [
      {{"state_description": "...", "reasoning": "...", "action": "..."}}
    ]
  }}
]
```

Output ONLY the JSON array, no other text.\
"""

COMPOSITION_GUIDELINE_WITH_EXISTING = """\
- You can COMPOSE new workflows from the existing ones listed above. \
For example, if "navigate to {{object}}" already exists, a new workflow \
"pick up {{object}} from {{location}}" can reference it as a sub-step. \
This builds increasingly complex workflows from simpler building blocks.\
"""

COMPOSITION_GUIDELINE_WITHOUT_EXISTING = """\
- Look for the most basic, atomic sub-routines first.\
"""


def _format_trajectory_for_induction(traj: Trajectory) -> str:
    """Format a trajectory with full (observation, reasoning, action) per step."""
    lines = []
    for i, step in enumerate(traj.steps):
        lines.append(f"Step {i + 1}:")
        lines.append(f"  Observation: {step.observation[:300]}")
        if step.reasoning:
            lines.append(f"  Reasoning: {step.reasoning[:200]}")
        lines.append(f"  Action: {step.action}")
    return "\n".join(lines)


def _format_existing_workflows(workflows: list[Workflow]) -> str:
    """Format existing workflows as context for sub-routine composition."""
    if not workflows:
        return ""
    parts = ["Previously induced workflows (you may build on these):"]
    for i, w in enumerate(workflows, 1):
        step_summary = "; ".join(s.action for s in w.steps[:3])
        if len(w.steps) > 3:
            step_summary += f"; ... ({len(w.steps)} steps total)"
        parts.append(f"  {i}. {w.description} [{step_summary}]")
    return "\n".join(parts)


def induce_workflows_from_trajectory(
    trajectory: Trajectory,
    existing_workflows: list[Workflow],
    model: str,
) -> list[Workflow]:
    """Induce workflows from a single trajectory (§2.3).

    Follows the paper's per-experience induction with existing workflows
    as context for sub-routine composition (the snowball effect).
    """
    existing_section = _format_existing_workflows(existing_workflows)
    if existing_workflows:
        composition_guideline = COMPOSITION_GUIDELINE_WITH_EXISTING
    else:
        composition_guideline = COMPOSITION_GUIDELINE_WITHOUT_EXISTING

    prompt = INDUCTION_PROMPT.format(
        existing_workflows_section=existing_section,
        objective=trajectory.objective,
        env_type=trajectory.env_type,
        trajectory_text=_format_trajectory_for_induction(trajectory),
        progress=trajectory.progress,
        composition_guideline=composition_guideline,
    )

    resp = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2048,
        temperature=0.0,
        caching=True,
    )
    raw = resp.choices[0].message.content.strip()

    workflows_data = _parse_workflow_json(raw)
    new_workflows: list[Workflow] = []
    for wd in workflows_data:
        steps = [
            WorkflowStep(
                state_description=s.get("state_description", ""),
                reasoning=s.get("reasoning", ""),
                action=s.get("action", ""),
            )
            for s in wd.get("steps", [])
        ]
        desc = wd.get("description", "")
        if desc and steps:
            new_workflows.append(Workflow(description=desc, steps=steps, source_env=trajectory.env_type))
    return new_workflows


def induce_workflows(
    trajectories: list[Trajectory],
    model: str,
    min_progress: float = 0.0,
) -> list[Workflow]:
    """Induce workflows from trajectories with iterative sub-routine composition.

    Processes trajectories one at a time, feeding previously induced workflows
    back as context for each subsequent induction. This implements the snowball
    effect from §2.3 and Figure 6.
    """
    successful = [t for t in trajectories if t.progress > min_progress]
    if not successful:
        return []

    all_workflows: list[Workflow] = []
    for traj in successful:
        # Filter existing workflows to same env for relevance
        same_env = [w for w in all_workflows if w.source_env == traj.env_type]
        new = induce_workflows_from_trajectory(traj, existing_workflows=same_env, model=model)
        # Deduplicate by description (simple exact match, paper doesn't specify)
        existing_descs = {w.description.lower().strip() for w in all_workflows}
        for w in new:
            if w.description.lower().strip() not in existing_descs:
                all_workflows.append(w)
                existing_descs.add(w.description.lower().strip())

    return all_workflows


def _parse_workflow_json(raw: str) -> list[dict]:
    """Extract JSON array from LLM response, handling markdown fences."""
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
    text = match.group(1).strip() if match else raw.strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        return [data]
    except json.JSONDecodeError:
        return []


# ---------------------------------------------------------------------------
# Workflow retrieval (§2.3 — semantic similarity matching)
# ---------------------------------------------------------------------------

EMBEDDING_MODEL = "openrouter/baai/bge-m3"


def embed_texts(texts: list[str], model: str = EMBEDDING_MODEL) -> list[list[float]]:
    """Get embeddings for a list of texts."""
    if not texts:
        return []
    resp = litellm.embedding(model=model, input=texts)
    return [item["embedding"] for item in resp.data]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def embed_workflows(workflows: list[Workflow], model: str = EMBEDDING_MODEL) -> None:
    """Compute and cache embeddings for all workflow descriptions."""
    to_embed = [w for w in workflows if not w.embedding]
    if not to_embed:
        return
    texts = [w.description for w in to_embed]
    embeddings = embed_texts(texts, model)
    for w, emb in zip(to_embed, embeddings, strict=True):
        w.embedding = emb


def retrieve_workflows(
    query: str,
    workflows: list[Workflow],
    top_k: int = 3,
    model: str = EMBEDDING_MODEL,
) -> list[Workflow]:
    """Retrieve top-k workflows most similar to the query."""
    if not workflows:
        return []
    # Ensure all workflows are embedded
    embed_workflows(workflows, model)
    query_emb = embed_texts([query], model)[0]
    scored = [(w, _cosine_similarity(query_emb, w.embedding)) for w in workflows if w.embedding]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [w for w, _ in scored[:top_k]]


def format_workflows_as_tips(workflows: list[Workflow]) -> str:
    """Format retrieved workflows for injection into agent memory.

    Matches AWM's workflow representation (§2.2): each workflow has a
    description (high-level goal) and steps (state, reasoning, action).
    """
    if not workflows:
        return ""
    parts = []
    for i, w in enumerate(workflows, 1):
        lines = [f"Workflow {i}: {w.description}"]
        for j, s in enumerate(w.steps, 1):
            lines.append(f"  Step {j}:")
            lines.append(f"    State: {s.state_description}")
            if s.reasoning:
                lines.append(f"    Reasoning: {s.reasoning}")
            lines.append(f"    Action: {s.action}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Evaluation with workflows
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AWM Offline mode (§3.2 — Mind2Web style)
# ---------------------------------------------------------------------------


def run_awm_offline(
    category: str,
    task_model: str = "openrouter/deepseek/deepseek-v3.2",
    induction_model: str = "openrouter/deepseek/deepseek-v3.2",
    train_size: int | None = None,
    val_size: int | None = None,
    max_steps: int = 30,
    max_train_workers: int = 5,
    max_val_workers: int = 10,
    min_progress: float = 0.0,
    top_k: int = 3,
    seed: int = 42,
) -> dict:
    """AWM offline: induce from train, evaluate on val (§3.2)."""
    dataset = load_dataset("agentboard", category=category, train_size=train_size, val_size=val_size, seed=seed)
    print(f"Dataset: train={len(dataset.train)}, val={len(dataset.val)}")

    # Phase 1: Collect training trajectories with reasoning
    print(f"\n[Phase 1] Collecting training trajectories ({len(dataset.train)} items)...")
    trajectories = collect_trajectories(dataset.train, task_model, max_steps=max_steps, max_workers=max_train_workers)
    successful = [t for t in trajectories if t.progress > min_progress]
    print(f"  Collected {len(trajectories)} trajectories, {len(successful)} successful")
    for t in trajectories:
        print(f"    {t.env_type}/{t.objective[:50]}... progress={t.progress:.2f} steps={len(t.steps)}")

    # Phase 2: Iterative workflow induction with sub-routine composition
    print(f"\n[Phase 2] Inducing workflows iteratively from {len(successful)} trajectories...")
    workflows = induce_workflows(successful, model=induction_model, min_progress=min_progress)
    print(f"  Induced {len(workflows)} workflows:")
    for w in workflows:
        print(f"    - {w.description} ({len(w.steps)} steps)")

    # Phase 3: Embed workflows for retrieval
    if workflows:
        print("\n[Phase 3] Computing workflow embeddings...")
        embed_workflows(workflows)
        print(f"  Embedded {len(workflows)} workflows")

    # Phase 4: Evaluate on val set with workflow retrieval
    print(f"\n[Phase 4] Evaluating on {len(dataset.val)} val items...")
    results = _evaluate_batch_with_workflows(dataset.val, workflows, task_model, max_steps, top_k, max_val_workers)

    return _build_results_dict(category, "offline", results, workflows, trajectories, successful)


# ---------------------------------------------------------------------------
# AWM Online mode (§3.1 — WebArena style, streaming)
# ---------------------------------------------------------------------------


def run_awm_online(
    category: str,
    task_model: str = "openrouter/deepseek/deepseek-v3.2",
    induction_model: str = "openrouter/deepseek/deepseek-v3.2",
    train_size: int | None = None,
    val_size: int | None = None,
    max_steps: int = 30,
    min_progress: float = 0.5,
    top_k: int = 3,
    seed: int = 42,
) -> dict:
    """AWM online: streaming workflow induction on val items (§3.1).

    Processes val items sequentially. After each successful episode, induces
    workflows and adds them to the library for subsequent items. This
    implements the snowball effect from Figure 1.

    No training data is used — this is supervision-free (§3.1):
    "AWM online can also run in a supervision-free setting, where it
    iteratively induces workflows from self-generated past predictions
    that are judged correct by an evaluator module."
    """
    dataset = load_dataset("agentboard", category=category, train_size=train_size, val_size=val_size, seed=seed)
    print(f"Dataset: val={len(dataset.val)} (online mode — no training data used)")

    workflows: list[Workflow] = []
    all_results: list[tuple[str, float]] = []
    all_trajectories: list[Trajectory] = []

    for idx, item in enumerate(dataset.val):
        objective = item.question or item.raw_text or ""
        env_type = item.metadata["env"]

        # Retrieve relevant workflows from current library
        if workflows:
            retrieved = retrieve_workflows(objective, workflows, top_k=top_k)
            tips = format_workflows_as_tips(retrieved)
        else:
            tips = ""

        n_wf = len(workflows)
        print(f"\n  [{idx + 1}/{len(dataset.val)}] {objective[:60]}... (workflows: {n_wf})")

        # Run episode with current workflow library
        try:
            traj = _run_episode_with_reasoning(env_type, item.metadata, objective, task_model, max_steps, tips)
            all_trajectories.append(traj)
            progress = traj.progress
            all_results.append((_format_trajectory_summary(traj), progress))
            print(f"    Progress: {progress:.2f} ({len(traj.steps)} steps)")

            # If successful, induce workflows (online induction)
            if progress >= min_progress:
                same_env = [w for w in workflows if w.source_env == env_type]
                new_wfs = induce_workflows_from_trajectory(traj, same_env, model=induction_model)
                existing_descs = {w.description.lower().strip() for w in workflows}
                added = 0
                for w in new_wfs:
                    if w.description.lower().strip() not in existing_descs:
                        workflows.append(w)
                        existing_descs.add(w.description.lower().strip())
                        added += 1
                if added:
                    # Embed new workflows immediately for next retrieval
                    embed_workflows(workflows)
                    print(f"    Induced {added} new workflows (total: {len(workflows)})")

        except Exception as exc:
            all_results.append((f"Episode failed: {exc}", 0.0))
            print(f"    Failed: {exc}")

    successful = [t for t in all_trajectories if t.progress >= min_progress]
    return _build_results_dict(category, "online", all_results, workflows, all_trajectories, successful)


# ---------------------------------------------------------------------------
# AWM Offline+Online mode (§C of paper, cross-website/domain)
# ---------------------------------------------------------------------------


def run_awm_offline_online(
    category: str,
    task_model: str = "openrouter/deepseek/deepseek-v3.2",
    induction_model: str = "openrouter/deepseek/deepseek-v3.2",
    train_size: int | None = None,
    val_size: int | None = None,
    max_steps: int = 30,
    max_train_workers: int = 5,
    min_progress_offline: float = 0.0,
    min_progress_online: float = 0.5,
    top_k: int = 3,
    seed: int = 42,
) -> dict:
    """AWM offline+online: induce from train, then continue accumulating on val.

    Combines offline pre-training of workflows from training data with online
    streaming accumulation during evaluation (§C of paper).
    """
    dataset = load_dataset("agentboard", category=category, train_size=train_size, val_size=val_size, seed=seed)
    print(f"Dataset: train={len(dataset.train)}, val={len(dataset.val)}")

    # Offline phase: collect and induce from training data
    print(f"\n[Offline] Collecting training trajectories ({len(dataset.train)} items)...")
    train_trajectories = collect_trajectories(
        dataset.train, task_model, max_steps=max_steps, max_workers=max_train_workers
    )
    successful_train = [t for t in train_trajectories if t.progress > min_progress_offline]
    print(f"  {len(successful_train)}/{len(train_trajectories)} successful")

    print(f"\n[Offline] Inducing workflows from {len(successful_train)} trajectories...")
    workflows = induce_workflows(successful_train, model=induction_model, min_progress=min_progress_offline)
    if workflows:
        embed_workflows(workflows)
    print(f"  Offline workflows: {len(workflows)}")

    # Online phase: streaming evaluation with continued induction
    print(f"\n[Online] Streaming evaluation on {len(dataset.val)} val items...")
    all_results: list[tuple[str, float]] = []
    all_trajectories: list[Trajectory] = []

    for idx, item in enumerate(dataset.val):
        objective = item.question or item.raw_text or ""
        env_type = item.metadata["env"]

        if workflows:
            retrieved = retrieve_workflows(objective, workflows, top_k=top_k)
            tips = format_workflows_as_tips(retrieved)
        else:
            tips = ""

        print(f"\n  [{idx + 1}/{len(dataset.val)}] {objective[:60]}... (workflows: {len(workflows)})")

        try:
            traj = _run_episode_with_reasoning(env_type, item.metadata, objective, task_model, max_steps, tips)
            all_trajectories.append(traj)
            all_results.append((_format_trajectory_summary(traj), traj.progress))
            print(f"    Progress: {traj.progress:.2f}")

            if traj.progress >= min_progress_online:
                same_env = [w for w in workflows if w.source_env == env_type]
                new_wfs = induce_workflows_from_trajectory(traj, same_env, model=induction_model)
                existing_descs = {w.description.lower().strip() for w in workflows}
                added = 0
                for w in new_wfs:
                    if w.description.lower().strip() not in existing_descs:
                        workflows.append(w)
                        existing_descs.add(w.description.lower().strip())
                        added += 1
                if added:
                    embed_workflows(workflows)
                    print(f"    Induced {added} new workflows (total: {len(workflows)})")

        except Exception as exc:
            all_results.append((f"Episode failed: {exc}", 0.0))
            print(f"    Failed: {exc}")

    successful_val = [t for t in all_trajectories if t.progress >= min_progress_online]
    return _build_results_dict(
        category,
        "offline+online",
        all_results,
        workflows,
        train_trajectories + all_trajectories,
        successful_train + successful_val,
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _evaluate_batch_with_workflows(
    val_items: list[DataItem],
    workflows: list[Workflow],
    task_model: str,
    max_steps: int,
    top_k: int,
    max_workers: int,
    timeout: float = 300.0,
) -> list[tuple[str, float]]:
    """Run val episodes in parallel with workflow-augmented tips (offline mode)."""
    tips_list: list[str] = []
    for item in val_items:
        query = item.question or item.raw_text or ""
        retrieved = retrieve_workflows(query, workflows, top_k=top_k)
        tips_list.append(format_workflows_as_tips(retrieved))

    workers = min(max_workers, len(val_items)) if val_items else 1
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _run_episode_with_reasoning,
                item.metadata["env"],
                item.metadata,
                item.question,
                task_model,
                max_steps,
                tips,
            )
            for item, tips in zip(val_items, tips_list, strict=True)
        ]
        results: list[tuple[str, float]] = []
        for f in futures:
            try:
                traj = f.result(timeout=timeout)
                results.append((_format_trajectory_summary(traj), traj.progress))
            except Exception as exc:
                results.append((f"Episode failed: {exc}", 0.0))
    return results


def _format_trajectory_summary(traj: Trajectory) -> str:
    """Compact trajectory summary for results."""
    lines = [f"Objective: {traj.objective}"]
    for i, step in enumerate(traj.steps):
        lines.append(f"  {i + 1}. [{step.action}] {step.observation[:80]}")
    lines.append(f"Progress: {traj.progress:.2f}")
    return "\n".join(lines)


def _build_results_dict(
    category: str,
    mode: str,
    results: list[tuple[str, float]],
    workflows: list[Workflow],
    all_trajectories: list[Trajectory],
    successful_trajectories: list[Trajectory],
) -> dict:
    scores = [score for _, score in results]
    mean_progress = sum(scores) / len(scores) if scores else 0.0

    print(f"\n{'=' * 60}")
    print(f"AWM Baseline Results — {category} ({mode})")
    print(f"Mean progress rate: {mean_progress:.4f}")
    print(f"Val items: {len(scores)}")
    print(f"Workflows: {len(workflows)}")
    if scores:
        print(
            f"Score distribution: min={min(scores):.3f} max={max(scores):.3f} "
            f"nonzero={sum(1 for s in scores if s > 0)}/{len(scores)}"
        )
    print(f"{'=' * 60}")

    return {
        "category": category,
        "mode": mode,
        "mean_progress": mean_progress,
        "num_val": len(scores),
        "num_workflows": len(workflows),
        "num_trajectories": len(all_trajectories),
        "num_successful_trajectories": len(successful_trajectories),
        "per_item_scores": scores,
        "workflows": [
            {"description": w.description, "num_steps": len(w.steps), "env": w.source_env} for w in workflows
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AWM baseline on AgentBoard")
    parser.add_argument("--category", required=True, choices=["scienceworld", "babyai", "pddl"])
    parser.add_argument(
        "--mode",
        default="offline",
        choices=["offline", "online", "offline+online"],
        help="AWM mode: offline (train→val), online (streaming on val), offline+online (both)",
    )
    parser.add_argument("--task-model", default="openrouter/deepseek/deepseek-v3.2")
    parser.add_argument("--induction-model", default="openrouter/deepseek/deepseek-v3.2")
    parser.add_argument("--train-size", type=int, default=None)
    parser.add_argument("--val-size", type=int, default=None)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--min-progress", type=float, default=0.0, help="Min progress for offline induction")
    parser.add_argument("--min-progress-online", type=float, default=0.5, help="Min progress for online induction")
    parser.add_argument("--top-k", type=int, default=3, help="Number of workflows to retrieve per item")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    from programmaticmemory.cache import configure_cache

    configure_cache("disk")

    print(f"\n{'=' * 60}")
    print(f"AWM Baseline — {args.category} ({args.mode} mode)")
    print(f"Task model: {args.task_model}")
    print(f"Induction model: {args.induction_model}")
    print(f"{'=' * 60}")

    if args.mode == "offline":
        results = run_awm_offline(
            category=args.category,
            task_model=args.task_model,
            induction_model=args.induction_model,
            train_size=args.train_size,
            val_size=args.val_size,
            max_steps=args.max_steps,
            min_progress=args.min_progress,
            top_k=args.top_k,
            seed=args.seed,
        )
    elif args.mode == "online":
        results = run_awm_online(
            category=args.category,
            task_model=args.task_model,
            induction_model=args.induction_model,
            train_size=args.train_size,
            val_size=args.val_size,
            max_steps=args.max_steps,
            min_progress=args.min_progress_online,
            top_k=args.top_k,
            seed=args.seed,
        )
    else:  # offline+online
        results = run_awm_offline_online(
            category=args.category,
            task_model=args.task_model,
            induction_model=args.induction_model,
            train_size=args.train_size,
            val_size=args.val_size,
            max_steps=args.max_steps,
            min_progress_offline=args.min_progress,
            min_progress_online=args.min_progress_online,
            top_k=args.top_k,
            seed=args.seed,
        )

    # Write results to JSON
    import datetime
    from pathlib import Path

    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    output_file = output_dir / f"awm-{args.category}-{args.mode}-{timestamp}.json"
    output_file.write_text(json.dumps(results, indent=2))
    print(f"\nResults saved to {output_file}")


if __name__ == "__main__":
    main()
