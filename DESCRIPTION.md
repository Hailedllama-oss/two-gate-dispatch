# Two-Gate Dispatch - Project Description

## Summary

Two-Gate Dispatch is a local prompt checker for coding-agent operators. It runs a form gate and a filesystem gate before a prompt is handed to another dispatch command.

## Installed Commands

`pip install .` installs these console commands:

```text
gate-form          two-gate-form
gate-substance     two-gate-substance
dispatch-gate      two-gate-dispatch
```

The short names are the primary operator interface. The `two-gate-*` names are compatibility aliases.

## Gate 1: Form Validation

`gate-form` checks prompt structure and scores these 9 items:

| # | Check name | What it looks for |
|---|------------|-------------------|
| 1 | `role` | Meaningful `Role:` content |
| 2 | `objective` | Meaningful `Objective:` content |
| 3 | `owned_paths` | Meaningful `Owned paths:` content |
| 4 | `workflow` | Meaningful `Workflow:` content |
| 5 | `verification` | At least one parsed verification command |
| 6 | `output_format` | Meaningful `Output Format:` content |
| 7 | `self_challenge` | Meaningful `Self-Challenge:` or `Self Challenge:` content |
| 8 | `owned_path_intent` | At least one owned path with a supported intent |
| 9 | `ordered_steps` | Numbered workflow steps or parsed verification commands |

The form gate passes when the score is at least 7/9. With `--strict`, mandatory sections must also contain meaningful content. Placeholder values such as `todo`, `tbd`, `n/a`, and `none` do not count as meaningful content.

The form gate supports `--help`, `--version`, `--json`, and `--strict`.

## Gate 2: Substance Validation

`gate-substance` validates prompt claims against the local filesystem. It recognizes these owned path intents:

```text
READ, WRITE, CREATE, APPEND, MODIFY, DELETE
```

It performs these checks:

1. `Owned paths:`, `Verification:`, and `Output Format:` sections are required.
2. Duplicate section headings are rejected.
3. Malformed owned path lines are reported with source line numbers.
4. With `--strict`, at least one owned path must parse successfully.
5. `READ`, `MODIFY`, and `DELETE` targets must already exist.
6. `WRITE`, `CREATE`, and `APPEND` targets must have an existing writable target or parent directory.
7. Verification lines in the form `- Run: <command> <args>` are parsed.
8. Malformed shell quoting in verification lines is blocked.
9. Verification command binaries are validated where statically detectable via `shutil.which()`.
   Unsupported shell constructs skip binary validation only. Path existence checks still run.
10. Local paths found in verification command arguments must exist.
11. Concrete output paths in `Output Format:` must be declared in `Owned paths:` with a write intent.
12. A `READ` path cannot also be used as an output target.
13. Backticked home-relative paths beginning with `~/` or `$HOME/` produce warnings.
14. Writable owned paths must not also appear in an optional `Kill List:` section.

Relative paths are resolved from the prompt file's directory. Verification
paths must use an explicit path prefix such as `./`, `../`, `/`, `~/`, or
`$HOME/`; bare filenames in commands such as `test -f output.txt` are not
treated as paths because they are ambiguous with command names and arguments.

The substance gate supports `--help`, `--version`, `--json`, and `--strict`.

## Wrapper

`dispatch-gate` runs both gates in strict mode:

```bash
gate-form "$PROMPT" --strict
gate-substance "$PROMPT" --strict
```

It accepts `--help`, `-h`, and `--version`. Otherwise it requires exactly one prompt file. It prints `SAFE TO DISPATCH` only after both gates pass.

## Exit Behavior

| Command | Passing validation | Blocked validation or unreadable prompt | CLI argument error |
|---------|--------------------|-----------------------------------------|--------------------|
| `gate-form` | 0 | 1 | 2 |
| `gate-substance` | 0 | 1 | 2 |
| `dispatch-gate` | 0 | 1 | 1 for missing or extra arguments |

`dispatch-gate --help`, `dispatch-gate -h`, and `dispatch-gate --version` return 0.

## Reproduction Commands

Install locally:

```bash
pip install .
```

Run a known-good prompt:

```bash
dispatch-gate examples/good-prompt.txt
```

Run the focused test suite:

```bash
python3 -m pytest tests/test_gates.py -q
```

Check JSON output:

```bash
gate-form --json examples/good-prompt.txt
gate-substance --json examples/bad-missing-path.txt
```

## Scope

The gates are local validators. They do not execute verification commands, validate remote resources, inspect SQL databases, run Codex, or prove that a future worker can complete the requested task.
