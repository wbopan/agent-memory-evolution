# Run Output — Local Output Directory for Evolution Runs

## Goal

Create a local `outputs/` directory that captures every evolution run's config, logs, summary metrics, and all LLM calls in an organized, browsable structure. Coexists with Weave — zero invasiveness to existing evaluator/reflector/toolkit code.

## Directory Structure

```
outputs/
  2026-02-28-14-30-00/
    config.json              # All CLI args + dataset info
    run.log                  # Full text log (tee from RichLogger)
    summary.json             # Final metrics after evolution
    llm_calls/
      iter_0/
        train_001.json
        val_001.json
      iter_1/
        reflect_001.json
        reflect_fix_001.json
        train_001.json
        val_001.json
```

## Components

### RunOutputManager (`logging/run_output.py`)

- `__init__(output_dir, config_dict)` — creates timestamped dir, writes `config.json`
- `set_phase(iteration, phase)` — updates current phase for callback file naming
- `write_summary(metrics)` — writes `summary.json`
- `get_log_path()` — returns `run.log` path
- Registers/deregisters LLMCallLogger as litellm callback

### LLMCallLogger (litellm CustomLogger)

- `log_success_event()` — writes JSON per call to `llm_calls/iter_N/phase_NNN.json`
- `log_failure_event()` — records failed calls
- JSON format: `{timestamp, iteration, phase, call_index, model, messages, response, duration_ms, usage}`

## Changes to Existing Code

| File | Change | Invasiveness |
|------|--------|-------------|
| `logging/run_output.py` | New file | None |
| `__main__.py` | Init RunOutputManager, pass args | Low (few lines) |
| `loop.py` | `manager.set_phase(i, "train")` etc before each stage | Low (one line per stage) |
| `experiment_tracker.py` | `log_summary` also calls `manager.write_summary()` | Low (one line) |
| `logging/logger.py` | Add optional `log_file` param to tee output | Low |

## Constraints

- `outputs/` in `.gitignore`
- `--no-output` flag disables entirely (default: enabled)
- Independent of Weave — both can run simultaneously
- No conflict with conftest disk cache wrapper
