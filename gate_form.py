#!/usr/bin/env python3
"""Gate 1: score the structure of an AI coding-agent prompt."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from prompt_parser import (
    SectionBlock,
    extract_sections,
    has_meaningful_content,
    parse_owned_path_lines,
    parse_verification_commands,
    parse_workflow_steps,
)


VERSION = "two-gate-dispatch 1.0.0"
MAX_PROMPT_BYTES = 5 * 1024 * 1024
CHECK_WARNING_SECONDS = 5.0

MIN_HEADING_CONTENT_CHARS = 10
MIN_MANDATORY_CONTENT_CHARS = 20

HEADING_CRITERIA = (
    ("role", ("role",)),
    ("objective", ("objective",)),
    ("owned_paths", ("owned paths",)),
    ("workflow", ("workflow",)),
    ("verification", ("verification",)),
    ("output_format", ("output format",)),
    ("self_challenge", ("self-challenge", "self challenge")),
)
MANDATORY_CRITERIA = {"role", "objective", "verification", "output_format"}
MIN_CONTENT_BY_CRITERION = {
    "role": MIN_MANDATORY_CONTENT_CHARS,
    "objective": MIN_MANDATORY_CONTENT_CHARS,
    "output_format": MIN_MANDATORY_CONTENT_CHARS,
}
PLACEHOLDER_VALUES = {
    "",
    "placeholder",
    "todo",
    "tbd",
    "n/a",
    "na",
    "none",
}

class GateArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if "--json" in sys.argv[1:]:
            print(json.dumps({"error": message, "status": "ERROR"}))
        else:
            self.print_usage()
            print(f"ERROR: {message}")
        raise SystemExit(2)


def emit_error(error: str, as_json: bool, **details: object) -> None:
    report = {"error": error, "status": "ERROR", **details}
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(f"ERROR: {error}")
        for key, value in details.items():
            print(f"  {key}: {value}")


def read_prompt(path: Path, as_json: bool) -> tuple[str | None, int]:
    if not path.is_file():
        emit_error("prompt_file_not_found", as_json, path=str(path))
        return None, 1
    size = path.stat().st_size
    if size > MAX_PROMPT_BYTES:
        emit_error("file_too_large", as_json, max_bytes=MAX_PROMPT_BYTES, actual_bytes=size)
        return None, 1
    try:
        data = path.read_bytes()
        if b"\x00" in data:
            raise UnicodeDecodeError("utf-8", data, 0, 1, "NUL byte found")
        return data.decode("utf-8"), 0
    except UnicodeDecodeError:
        emit_error("binary_or_undecodable_input", as_json)
        return None, 1


def normalize_content(value: str) -> str:
    return " ".join(value.strip().lower().split())


def is_placeholder_content(value: str) -> bool:
    normalized = normalize_content(value).strip(" .:-_")
    if normalized in PLACEHOLDER_VALUES:
        return True
    tokens = [token.strip(" .:-_") for token in normalized.replace("/", " / ").split()]
    return bool(tokens) and all(token in PLACEHOLDER_VALUES for token in tokens)


def section_content(sections: dict[str, SectionBlock], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        if alias in sections:
            return sections[alias].content
    return None


def heading_has_content(sections: dict[str, SectionBlock], aliases: tuple[str, ...], min_chars: int = MIN_HEADING_CONTENT_CHARS) -> bool:
    content = section_content(sections, aliases)
    if content is None:
        return False
    return has_meaningful_content(content, min_chars) and not is_placeholder_content(content)


def verification_has_command(sections: dict[str, SectionBlock], aliases: tuple[str, ...]) -> bool:
    content = section_content(sections, aliases)
    return bool(content is not None and parse_verification_commands(content))


def criterion_passed(sections: dict[str, SectionBlock], name: str, aliases: tuple[str, ...]) -> bool:
    if name == "verification":
        return verification_has_command(sections, aliases)
    return heading_has_content(sections, aliases, MIN_CONTENT_BY_CRITERION.get(name, MIN_HEADING_CONTENT_CHARS))


def owned_path_intent_passed(sections: dict[str, SectionBlock]) -> bool:
    content = section_content(sections, ("owned paths",))
    paths, _malformed = parse_owned_path_lines(content or "")
    return bool(content is not None and paths)


def ordered_steps_passed(sections: dict[str, SectionBlock]) -> bool:
    workflow = section_content(sections, ("workflow",))
    verification = section_content(sections, ("verification",))
    return bool((workflow and parse_workflow_steps(workflow)) or (verification and parse_verification_commands(verification)))


def performance_warning(name: str, elapsed: float) -> str | None:
    if elapsed <= CHECK_WARNING_SECONDS:
        return None
    return f"Performance: {name} took {elapsed:.2f}s (threshold: {CHECK_WARNING_SECONDS:.2f}s)"


def score_prompt(text: str, strict: bool = False, sections: dict[str, SectionBlock] | None = None) -> dict:
    warnings = []
    if sections is None:
        started = time.perf_counter()
        sections = extract_sections(text)
        warning = performance_warning("extract_sections", time.perf_counter() - started)
        if warning:
            warnings.append(warning)
    started = time.perf_counter()
    checks = [
        {"name": name, "passed": criterion_passed(sections, name, aliases)}
        for name, aliases in HEADING_CRITERIA
    ]
    checks.extend(
        (
            {"name": "owned_path_intent", "passed": owned_path_intent_passed(sections)},
            {"name": "ordered_steps", "passed": ordered_steps_passed(sections)},
        )
    )
    score = sum(check["passed"] for check in checks)
    warning = performance_warning("score_checks", time.perf_counter() - started)
    if warning:
        warnings.append(warning)
    missing_mandatory = [
        check["name"] for check in checks if check["name"] in MANDATORY_CRITERIA and not check["passed"]
    ]
    return {
        "passed": score >= 7 and (not strict or not missing_mandatory),
        "score": score,
        "required": 7,
        "checks": checks,
        "missing_mandatory": missing_mandatory,
        "min_heading_content_chars": MIN_HEADING_CONTENT_CHARS,
        "min_mandatory_content_chars": MIN_MANDATORY_CONTENT_CHARS,
        "strict": strict,
        "warnings": warnings,
    }


def main() -> int:
    parser = GateArgumentParser(
        description="Gate 1: validate prompt structure before dispatch.",
        epilog=(
            "Examples:\n"
            "  gate-form examples/good-prompt.txt\n"
            "  gate-form --strict examples/good-prompt.txt\n"
            "  gate-form --json examples/good-prompt.txt\n\n"
            "A passing prompt scores at least 7 of 9 checks. In --strict mode, "
            "Role, Objective, Verification, and Output Format must also contain "
            "meaningful content."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("prompt_file", help="Path to the dispatch prompt to validate")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON report")
    parser.add_argument("--strict", action="store_true", help="Require meaningful mandatory sections")
    args = parser.parse_args()
    path = Path(args.prompt_file)
    text, status = read_prompt(path, args.json)
    if text is None:
        return status
    started = time.perf_counter()
    sections = extract_sections(text)
    parse_warning = performance_warning("extract_sections", time.perf_counter() - started)
    report = score_prompt(text, strict=args.strict, sections=sections)
    if parse_warning:
        report["warnings"].append(parse_warning)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        status = "PASS" if report["passed"] else "BLOCKED"
        print(f"=== FORM GATE: {path} ===")
        total = len(report["checks"])
        print(f"{status}: {report['score']}/{total} (requires {report['required']}/{total})")
        for check in report["checks"]:
            print(f"  [{'PASS' if check['passed'] else 'MISS'}] {check['name']}")
        for name in report["missing_mandatory"]:
            print(f"  [FAIL] mandatory section lacks meaningful content: {name}")
        for warning in report["warnings"]:
            print(f"  [WARN] {warning}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({"error": "internal_error", "status": "ERROR"}))
        raise SystemExit(1)
