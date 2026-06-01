# Benchmark

Measured on 2026-05-31 from the repository root with:

```bash
python3 gate_form.py <prompt> --json --strict
python3 gate_substance.py <prompt> --json --strict
```

Token estimates use `ceil(prompt_bytes / 4)`. Cost estimates use the requested
range of `$0.01-$0.05 / 1K tokens`. These are dispatch prompt-token estimates,
not worker telemetry; they do not include reasoning, tool output, retries, or
generated files.

Typical prompts validate in under 2 seconds. Large inputs of 4 MB or more may
take 2-4 seconds; this is expected for maximum-size inputs.

| Prompt | Bytes | Est. tokens | Before preflight | Gate 1 | Gate 2 | After preflight | Averted token floor | Averted cost floor |
| --- | ---: | ---: | --- | --- | --- | --- | ---: | ---: |
| `examples/good-prompt.txt` | 481 | 121 | Would dispatch | PASS, 9/9 | PASS | Dispatch allowed | 0 | `$0.00000-$0.00000` |
| `examples/bad-missing-path.txt` | 387 | 97 | Would dispatch, then worker discovers missing input | PASS, 9/9 | BLOCK: missing `(READ)` path and verification path | Dispatch blocked | 97 | `$0.00097-$0.00485` |
| `examples/bad-contradiction.txt` | 336 | 84 | Would dispatch, then worker discovers unwritable output parent | PASS, 9/9 | BLOCK: no writable parent for `(CREATE)` path | Dispatch blocked | 84 | `$0.00084-$0.00420` |
| `examples/realworld-read-output-conflict.txt` | 441 | 111 | Would dispatch a release-summary task that overwrites its read-only source manifest | PASS, 9/9 | BLOCK: output target is declared `(READ)` | Dispatch blocked | 111 | `$0.00111-$0.00555` |
| `examples/realworld-missing-verification-path.txt` | 527 | 132 | Would dispatch a deployment-readiness task without the required production environment file | PASS, 9/9 | BLOCK: verification path is missing | Dispatch blocked | 132 | `$0.00132-$0.00660` |

## Real-World Workflow Examples

The final two fixtures model release automation and deployment readiness
dispatches. Before preflight, both prompts are structurally plausible and would
be sent to a worker. After preflight, both are blocked locally: one prevents an
input overwrite, and one catches a missing deployment prerequisite. The saved
token figures are estimates from prompt size, while the PASS/BLOCK outcomes are
measured by the commands above.

## Summary

Across the five owned example prompts:

- Before preflight: 5/5 prompts would be sent to a worker.
- After preflight: 1/5 prompts pass, 4/5 prompts are blocked locally.
- Blocked dispatch prompt-token estimate: 424 tokens.
- Averted dispatch prompt-token cost estimate: `$0.00424-$0.02120`.

The useful result is not the small prompt-token floor by itself. The gates stop
known-bad dispatches before worker execution, which is where the larger token
waste normally comes from.
