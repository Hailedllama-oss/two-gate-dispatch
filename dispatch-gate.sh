#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
    -h|--help)
        printf 'Usage: dispatch-gate <prompt-file>\n'
        exit 0
        ;;
    --version)
        printf 'two-gate-dispatch 1.0.0\n'
        exit 0
        ;;
esac

if [ "$#" -ne 1 ]; then
    printf 'Usage: dispatch-gate <prompt-file>\n' >&2
    exit 1
fi

PYTHONPATH="${SCRIPT_DIR}${PYTHONPATH:+:${PYTHONPATH}}" \
    exec python3 -c 'from gate_substance import dispatch_main; raise SystemExit(dispatch_main())' "$1"
