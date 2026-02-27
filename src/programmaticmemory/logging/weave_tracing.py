"""Weave tracing utilities for hierarchical call tracing with feedback."""

import weave


def add_call_feedback(
    score: float | None = None,
    scores: dict[str, float] | None = None,
) -> None:
    """Add feedback scores to the current weave call.

    This safely adds feedback scores to the current weave call.
    If no call is active, this is a no-op.

    Args:
        score: A single score value to add as feedback.
        scores: A dictionary of named scores to add as feedback.
    """
    try:
        call = weave.require_current_call()

        # Add single score feedback
        if score is not None:
            call.feedback.add("score", {"value": score})

        # Add multiple named scores
        if scores is not None:
            for name, value in scores.items():
                call.feedback.add(name, {"value": value})

    except Exception:
        # Silently ignore if no current call or other errors
        pass
