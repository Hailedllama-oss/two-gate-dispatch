# Two-Gate Dispatch

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)

Two-Gate is for "prompt perfection" to help one shot coding or planning ideas, it makes the ai question themselves and do validation loops, then inputs a bunch of questions it can ask itself at the end to avoid halucation when prompting AI's its aim is to add structure, guidance, frame work when coding, etc. It aims to reduce token usage and make "one shot" prompts to check for validation loops and rates prompt structure


The rest was all written and by my Codex model, it works well personally and I just use it with hermes to ensure if its prompting agents or doing dispatches, it makes sure each part of a job the agents do to ensure E2E work is done with no halucinations.

## 60-Second Quickstart

```bash
cd two-gate-dispatch
pip install .

gate-form examples/good-prompt.txt
gate-substance examples/good-prompt.txt
dispatch-gate examples/good-prompt.txt
```

Expected final line:

```text
=== BOTH GATES PASSED: SAFE TO DISPATCH ===
```

Use it before dispatch:

```bash
dispatch-gate my-prompt.txt && codex exec < my-prompt.txt
```

For local development, use an editable install:

```bash
pip install -e .
```

The package installs both short and compatibility command names:

```text
gate-form          two-gate-form
gate-substance     two-gate-substance
dispatch-gate      two-gate-dispatch
```

## What the Gates Check

`gate-form` scores prompt structure. It looks for Role, Objective, Owned paths, Workflow, Verification, Output Format, Self-Challenge, owned path intents, and ordered steps. A prompt passes at 7/9 checks. With `--strict`, mandatory sections must contain meaningful content.

`gate-substance` checks local claims. It validates required sections, owned path syntax, read/write path existence, output paths declared with write intent, duplicate sections, verification command paths, workflow path ownership, and home-relative path warnings.

`dispatch-gate` runs both gates in strict mode and blocks on the first failure.

## Prompt Format

Owned paths must include an intent:

```text
Owned paths:
- ./project/input.md (READ)
- ./project/report.md (CREATE)
```

Supported intents are `READ`, `WRITE`, `CREATE`, `APPEND`, `MODIFY`, and `DELETE`.

Verification commands should use `- Run:` lines:

```text
Verification:
- Run: test -f ./project/input.md
```

Every local path referenced by verification commands must be declared in
`Owned paths`. Read commands such as `cat`, `grep`, and `pytest path` require a
read-compatible intent (`READ` or `MODIFY`). Write targets such as
`--junitxml=report.xml` or shell output redirections require a write-compatible
intent. Existence checks such as `test -f ./project/report.md` may also target a
declared `CREATE`, `WRITE`, or `APPEND` output, so prompts can verify files that
will be produced by the task.

Output file paths should be declared in `Owned paths` with a write intent:

```text
Output Format:
- Write Markdown to `./project/report.md`.
```

## Example Output

Passing prompt:

```text
=== FORM GATE: examples/good-prompt.txt ===
PASS: 9/9 (requires 7/9)
  [PASS] role
  [PASS] objective
  [PASS] owned_paths
  [PASS] workflow
  [PASS] verification
  [PASS] output_format
  [PASS] self_challenge
  [PASS] owned_path_intent
  [PASS] ordered_steps
```

Blocked prompt:

```text
=== SUBSTANCE GATE: /path/to/examples/bad-missing-path.txt ===
BLOCKED
  [FAIL] line 6: ./project/brief-that-does-not-exist.md is missing for READ
  [FAIL] line 12: Verification path is missing: ./project/brief-that-does-not-exist.md
```

Fix the owned path or verification command named in the failing line, then run the gate again.

## JSON Mode

Both gates can emit machine-readable output:

```bash
gate-form --json examples/good-prompt.txt
gate-substance --json examples/bad-missing-path.txt
```

## Common Fixes

If Gate 1 reports `[MISS]`, add meaningful content for that section. `--strict` also requires useful Role, Objective, Verification, and Output Format content.

If Gate 2 reports a missing `READ` path, make the file exist relative to the prompt file's directory or change the prompt to the correct path.

If Gate 2 reports `has no writable parent`, create the parent directory or declare a path under an existing writable directory.

If Gate 2 reports `Output path must be declared with a write intent`, add that exact output path to `Owned paths` with `CREATE`, `WRITE`, or `APPEND`.

If Gate 2 reports `Referenced path must be declared`, add the path to `Owned paths` with a compatible intent. For paths mentioned only as examples or context, mark the line as `Informational:`.

## Development

Run the focused tests:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_gates.py -q
```

The implementation uses only the Python standard library plus a Bash wrapper.

Typical prompts validate in under 2 seconds. Large inputs of 4 MB or more may
take 2-4 seconds.

## Scope

Two-Gate Dispatch validates local prompt readiness. It does not run `codex exec`, execute verification commands, validate remote services, or prove the future worker can complete the task.

## License

MIT.
