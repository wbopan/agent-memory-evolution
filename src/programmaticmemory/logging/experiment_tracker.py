"""Experiment tracking using wandb and weave."""

import os
from typing import Any

import weave


class ExperimentTracker:
    """Experiment tracking using wandb weave."""

    def __init__(
        self,
        use_weave: bool = False,
        weave_project_name: str | None = None,
    ):
        self.use_weave = use_weave
        self.weave_project_name = weave_project_name or "programmaticmemory"

    def __enter__(self):
        self.start_run()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_run()
        return False

    def start_run(self):
        """Initialize weave and start wandb run."""
        if self.use_weave:
            import wandb

            # Suppress weave trace URL printing by default
            os.environ.setdefault("WEAVE_PRINT_CALL_LINK", "false")

            wandb.login()
            wandb.init(project=self.weave_project_name)

            wandb.define_metric("iteration")
            wandb.define_metric("*", step_metric="iteration")

            weave.init(project_name=self.weave_project_name)
        else:
            # Initialize weave with tracing disabled
            weave.init(project_name=self.weave_project_name, settings={"disabled": True})

    def log_metrics(self, metrics: dict[str, Any], iteration: int | None = None):
        """Log time-series metrics to wandb.

        Args:
            metrics: Dictionary of metric name to value.
            iteration: The iteration number. When provided, all metrics logged
                      with the same iteration value will be grouped together in wandb.
        """
        if self.use_weave:
            try:
                import wandb

                if iteration is not None:
                    metrics = {**metrics, "iteration": iteration}
                wandb.log(metrics)
            except Exception as e:
                print(f"Warning: Failed to log metrics: {e}")

    def log_summary(self, metrics: dict[str, Any]):
        """Log one-time summary metrics to wandb."""
        if self.use_weave:
            try:
                import wandb

                for key, value in metrics.items():
                    wandb.summary[key] = value
            except Exception as e:
                print(f"Warning: Failed to log summary: {e}")

    def end_run(self):
        """End the wandb run."""
        if self.use_weave:
            try:
                import wandb

                if wandb.run is not None:
                    wandb.finish()
            except Exception as e:
                print(f"Warning: Failed to end run: {e}")

    def is_active(self) -> bool:
        """Check if tracking is active."""
        if self.use_weave:
            try:
                import wandb

                return wandb.run is not None
            except Exception:
                pass
        return False

    def log_final_results(
        self,
        best_candidate: dict[str, str],
        best_score: float,
        total_candidates: int,
        total_metric_calls: int,
    ) -> None:
        """Save final optimization results to wandb summary."""
        self.log_summary(
            {
                "candidate/best_score": best_score,
                "candidate/total": total_candidates,
                "candidate/total_evals": total_metric_calls,
                "candidate/best": best_candidate,
            }
        )


def create_experiment_tracker(
    use_weave: bool = False,
    weave_project_name: str | None = None,
) -> ExperimentTracker:
    return ExperimentTracker(
        use_weave=use_weave,
        weave_project_name=weave_project_name,
    )
