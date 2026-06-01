# Before vs After Preflight

This repository does not include a prompt generator. The examples below compare
raw, underspecified dispatch requests against hand-written prompts that this
repo's gates can validate.

Run the owned fixtures from the repository root:

```bash
bash dispatch-gate.sh examples/good-prompt.txt
bash dispatch-gate.sh examples/bad-missing-path.txt
bash dispatch-gate.sh examples/bad-contradiction.txt
```

## Example 1: Valid Prompt

### Before

```text
Check this example prompt and write a report.
```

This raw request does not name owned paths, verification, or output format, so it
is not ready for Codex CLI dispatch.

### After

<!-- gate-example:pass -->
```text
Role: Python packaging validation engineer

Objective: Check this example prompt and write a concise validation report.

Owned paths:
- ./examples/good-prompt.txt (READ)
- ./examples/generated-report.md (CREATE)

Workflow:
1. Read the prompt.
2. Create the report.

Verification:
- Run: test -f ./examples/good-prompt.txt

Output Format:
- Write a short Markdown report to `./examples/generated-report.md`.

Self-Challenge:
- Did every referenced path resolve correctly?
```

Expected result: Gate 1 passes and Gate 2 passes.

## Example 2: Missing Read Path

### Before

```text
Read the project brief and report its status.
```

The raw request does not prove the input exists before dispatch.

### After

<!-- gate-example:block -->
```text
Role: Python packaging validation engineer

Objective: Read a project brief and report its status.

Owned paths:
- ./examples/project/brief-that-does-not-exist.md (READ)

Workflow:
1. Read the brief.

Verification:
- Run: test -f ./examples/project/brief-that-does-not-exist.md

Output Format:
- Report the validation result in the final response.

Self-Challenge:
- Did the input path resolve correctly?
```

Expected result: Gate 1 passes and Gate 2 blocks before worker execution because
the read path is missing.

## Example 3: Unwritable Output Target

### Before

```text
Write a report somewhere under no-such-dir.
```

The raw request does not make the output parent explicit.

### After

<!-- gate-example:block -->
```text
Role: Python packaging validation engineer

Objective: Write a complete validation report.

Owned paths:
- ./examples/no-such-dir/bad-report.md (CREATE)

Workflow:
1. Create the report.

Verification:
- Run: test -d .

Output Format:
- Write Markdown to `./examples/no-such-dir/bad-report.md`.

Self-Challenge:
- Is the output parent directory writable?
```

Expected result: Gate 1 passes and Gate 2 blocks before worker execution because
the output parent directory does not exist.

## What Changed

The after examples are dispatch-ready because each one names:

- A concrete role and objective.
- Every owned path with an explicit intent such as `(READ)` or `(CREATE)`.
- A workflow the worker can follow.
- Verification commands that Gate 2 can inspect.
- A final output format and self-challenge.

These examples demonstrate what the gates verify in this repository, not
behavior from an external generator or parent project.
