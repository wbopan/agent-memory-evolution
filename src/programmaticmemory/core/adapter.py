from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

# Generic type aliases
RolloutOutput = TypeVar("RolloutOutput")
Trajectory = TypeVar("Trajectory")
DataInst = TypeVar("DataInst")
Candidate = dict[str, str]


@dataclass
class EvaluationBatch(Generic[Trajectory, RolloutOutput]):
    """Container for the result of evaluating a proposed candidate on a batch of data.

    - outputs: raw per-example outputs from upon executing the candidate.
    - scores: per-example numeric scores (floats).
    - trajectories: optional per-example traces used by make_reflective_dataset to build
      a reflective dataset. If capture_traces=True is passed to `evaluate`, trajectories
      should be provided and align one-to-one with `outputs` and `scores`.
    - objective_scores: optional per-example maps of objective name -> score.
    """

    outputs: list[RolloutOutput]
    scores: list[float]
    trajectories: list[Trajectory] | None = None
    objective_scores: list[dict[str, float]] | None = None


class ProposalFn(Protocol):
    def __call__(
        self,
        candidate: dict[str, str],
        reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: list[str],
    ) -> dict[str, str]:
        """Given the current `candidate`, a reflective dataset, and a list of component
        names to update, return a mapping component_name -> new component text (str).
        """
        ...


class Adapter(Protocol[DataInst, Trajectory, RolloutOutput]):
    """Adapter is the single integration point between your system and the optimization engine.

    The following are user-defined types:
    DataInst: User-defined type of input data to the program under optimization.
    Trajectory: User-defined type of trajectory data.
    RolloutOutput: User-defined type of output data from the program candidate.

    Responsibilities:
    1) Program construction and evaluation (evaluate)
    2) Reflective dataset construction (make_reflective_dataset)
    3) Optional instruction proposal (propose_new_texts)
    """

    def evaluate(
        self,
        batch: list[DataInst],
        candidate: dict[str, str],
        capture_traces: bool = False,
    ) -> EvaluationBatch[Trajectory, RolloutOutput]:
        """Run the program defined by `candidate` on a batch of data.

        Parameters
        - batch: list of task-specific inputs (DataInst).
        - candidate: mapping from component name -> component text.
        - capture_traces: when True, populate `EvaluationBatch.trajectories`.

        Returns
        - EvaluationBatch with outputs, scores, and optional trajectories.
        """
        ...

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: EvaluationBatch[Trajectory, RolloutOutput],
        components_to_update: list[str],
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        """Build a small, JSON-serializable dataset (per component) to drive instruction
        refinement by a teacher LLM.

        Parameters
        - candidate: the same candidate evaluated in evaluate().
        - eval_batch: The result of evaluate(..., capture_traces=True).
        - components_to_update: subset of component names for which updates are requested.

        Returns
        - A dict: component_name -> list of dict records (the "reflective dataset").
        """
        ...

    propose_new_texts: ProposalFn | None = None
