# Baselines

Committed baselines are the reference reports used by CI and local comparison commands.

## Files

- `deterministic.json`: the current expected output for the deterministic eval suites

## Workflow

1. Run the deterministic evals:
   `python -m evals.runner --write-artifacts`
2. Compare the result with the committed baseline:
   `python -m evals.baseline compare --baseline evals/baselines/deterministic.json --candidate evals/reports/latest.json --fail-on-new-cases`
3. If the new result is the intended new normal, refresh the baseline:
   `python -m evals.baseline refresh --source evals/reports/latest.json --target evals/baselines/deterministic.json`

Baselines should only be refreshed when the changed eval output is intentional.
