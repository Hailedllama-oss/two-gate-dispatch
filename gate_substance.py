#!/usr/bin/env python3
"""Gate 2: validate prompt claims against the local filesystem."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import stat
import sys
import time
from pathlib import Path

from prompt_parser import (
    CommandEntry,
    SectionBlock,
    _is_bare_filename,
    _is_path_like,
    extract_sections,
    extract_inline_paths,
    find_duplicate_sections,
    normalize_heading,
    parse_output_paths_with_lines,
    parse_owned_path_lines,
    parse_verification_lines,
)


VERSION = "two-gate-dispatch 1.0.0"
MAX_PROMPT_BYTES = 5 * 1024 * 1024
CHECK_WARNING_SECONDS = 5.0
PATH_PREFIXES = ("./", "../", "~/", "$HOME/", "/")
ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*")
ENV_OPTIONS_WITH_OPERANDS = {
    "-u",
    "--unset",
    "--chdir",
    "-C",
    "--block-signal",
    "--default-signal",
    "--ignore-signal",
}
ENV_SPLIT_STRING_OPTIONS = {"-S", "--split-string"}
REQUIRED_SECTIONS = ("Owned paths", "Verification", "Output Format")
INTENTS = {"READ", "WRITE", "CREATE", "APPEND", "MODIFY", "DELETE"}
WRITE_INTENTS = INTENTS - {"READ"}
OUTPUT_VALID_INTENTS = {"CREATE", "WRITE", "APPEND"}
VERIFICATION_CREATED_INTENTS = {"CREATE", "WRITE", "APPEND"}
VERIFICATION_READ_INTENTS = {"READ", "MODIFY"}
VERIFICATION_WRITE_INTENTS = {"CREATE", "WRITE", "APPEND", "MODIFY"}
VERIFICATION_OUTPUT_CHECK_INTENTS = VERIFICATION_READ_INTENTS | VERIFICATION_CREATED_INTENTS
PATH_OPTIONS_WITH_OPERANDS = {
    "--junitxml",
    "--html",
    "--log-file",
    "--log-file-format",
}
NON_PATH_OPTIONS_WITH_OPERANDS = {
    "--format",
    "--color",
}
PYTEST_NON_PATH_OPTIONS_WITH_OPERANDS = {
    "-k",
    "-m",
    "--capture",
    "--color",
    "--durations",
    "--import-mode",
    "--maxfail",
    "--tb",
}
FIND_OPTIONS_WITH_OPERANDS = {
    "-amin",
    "-anewer",
    "-atime",
    "-cmin",
    "-cnewer",
    "-context",
    "-ctime",
    "-exec",
    "-execdir",
    "-fstype",
    "-gid",
    "-group",
    "-ilname",
    "-iname",
    "-inum",
    "-iwholename",
    "-links",
    "-lname",
    "-maxdepth",
    "-mindepth",
    "-mmin",
    "-mtime",
    "-name",
    "-newer",
    "-newerXY",
    "-path",
    "-perm",
    "-regex",
    "-samefile",
    "-size",
    "-type",
    "-uid",
    "-used",
    "-user",
    "-wholename",
}
PYTHON_COMMANDS = {"python", "python3"}
INTERPRETER_COMMANDS = {"bash", "sh", "python", "python3", "perl", "ruby", "node"}
INTERPRETER_CODE_OPTIONS = {"-c", "-e", "-E", "--eval", "--print"}
READ_OPERAND_COMMANDS = {
    "awk",
    "cat",
    "cmp",
    "diff",
    "du",
    "file",
    "find",
    "grep",
    "head",
    "less",
    "ls",
    "md5sum",
    "more",
    "sed",
    "sha1sum",
    "sha256sum",
    "sort",
    "stat",
    "tail",
    "tar",
    "uniq",
    "wc",
}
READ_COMMAND_OPTIONS_WITH_OPERANDS = {
    "awk": {"-F", "-v", "--assign", "--field-separator"},
    "cut": {
        "-b",
        "-c",
        "-d",
        "-f",
        "--bytes",
        "--characters",
        "--delimiter",
        "--fields",
        "--output-delimiter",
    },
    "grep": {
        "-A",
        "-B",
        "-C",
        "-D",
        "-d",
        "-e",
        "-m",
        "--after-context",
        "--before-context",
        "--binary-files",
        "--context",
        "--devices",
        "--directories",
        "--exclude",
        "--exclude-dir",
        "--exclude-from",
        "--group-separator",
        "--include",
        "--label",
        "--max-count",
        "--regexp",
    },
    "head": {"-c", "-n", "--bytes", "--lines"},
    "sed": {"-e", "--expression"},
    "sort": {
        "-k",
        "-S",
        "-t",
        "-T",
        "--batch-size",
        "--buffer-size",
        "--compress-program",
        "--field-separator",
        "--key",
        "--parallel",
        "--random-source",
        "--temporary-directory",
    },
    "tar": {"-C", "--directory", "--exclude", "--exclude-from", "--transform"},
    "tail": {"-c", "-n", "-s", "--bytes", "--lines", "--pid", "--sleep-interval"},
    "uniq": {"-f", "-s", "-w", "--check-chars", "--skip-chars", "--skip-fields"},
    "wc": {},
}
READ_COMMAND_READ_OPTIONS_WITH_OPERANDS = {
    "awk": {"-f", "--file"},
    "grep": {"-f", "--file"},
    "sed": {"-f", "--file"},
    "wc": {"--files0-from"},
}
READ_COMMAND_WRITE_OPTIONS_WITH_OPERANDS = {
    "sort": {"-o", "-O", "--output"},
}
WRITE_OPERAND_COMMANDS = {"mkdir", "tee", "touch"}
DELETE_OPERAND_COMMANDS = {"rm", "rmdir"}
READ_WRITE_OPERAND_COMMANDS = {"cp", "install", "ln", "mv"}
COMMAND_PATH_OPERAND_COMMANDS = WRITE_OPERAND_COMMANDS | DELETE_OPERAND_COMMANDS | READ_WRITE_OPERAND_COMMANDS
TARGET_DIRECTORY_OPTIONS = {"-t", "--target-directory"}
COMMAND_OPTIONS_WITH_OPERANDS = {
    "cp": {"-S", "--suffix"},
    "install": {
        "-g",
        "-m",
        "-o",
        "-S",
        "--group",
        "--mode",
        "--owner",
        "--strip-program",
        "--suffix",
    },
    "ln": {"-S", "--suffix", "--backup"},
    "mkdir": {"-m", "--mode"},
    "mv": {"-S", "--suffix"},
    "rm": set(),
    "rmdir": set(),
    "tee": {},
    "touch": {"-d", "-r", "-t", "--date", "--reference", "--time"},
}
TEST_PATH_OPERATORS = {"-a", "-b", "-c", "-d", "-e", "-f", "-g", "-h", "-k", "-L", "-p", "-r", "-s", "-S", "-u", "-w", "-x"}
PATH_REF_READ = "read"
PATH_REF_WRITE = "write"
PATH_REF_DELETE = "delete"
PATH_REF_OUTPUT_CHECK = "output_check"
REFERENCE_SCAN_EXCLUDED_SECTIONS = {"owned paths", "verification", "output format", "kill list"}
READ_REFERENCE_WORDS = {"check", "compare", "inspect", "load", "open", "read", "review", "source", "use", "verify"}
WRITE_REFERENCE_WORDS = {"append", "create", "edit", "modify", "output", "produce", "save", "update", "write"}
CONFLICTING_INTENT_PAIRS = {
    frozenset(("READ", "CREATE")),
    frozenset(("MODIFY", "CREATE")),
    frozenset(("READ", "DELETE")),
    frozenset(("WRITE", "DELETE")),
    frozenset(("CREATE", "DELETE")),
    frozenset(("APPEND", "DELETE")),
    frozenset(("MODIFY", "DELETE")),
}
SHELL_BUILTINS = {
    ".",
    "[",
    "alias",
    "bg",
    "break",
    "builtin",
    "caller",
    "cd",
    "command",
    "compgen",
    "complete",
    "continue",
    "declare",
    "dirs",
    "disown",
    "echo",
    "enable",
    "eval",
    "exec",
    "exit",
    "export",
    "false",
    "fc",
    "fg",
    "getopts",
    "hash",
    "help",
    "history",
    "jobs",
    "kill",
    "logout",
    "mapfile",
    "popd",
    "printf",
    "pushd",
    "pwd",
    "read",
    "return",
    "set",
    "shift",
    "shopt",
    "source",
    "suspend",
    "test",
    "times",
    "trap",
    "true",
    "type",
    "ulimit",
    "umask",
    "unalias",
    "unset",
    "wait",
}
UNSUPPORTED_SHELL_PATTERNS = (
    ("& (background)", re.compile(r"(?<![&>])&(?![&0-9>])")),
    ("$() command substitution", re.compile(r"\$\(")),
    ("backtick substitution", re.compile(r"`")),
    ("<() process substitution", re.compile(r"<\(")),
    (">() process substitution", re.compile(r">\(")),
    ("|| (OR operator)", re.compile(r"\|\|")),
)


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


def section_block(sections: dict[str, SectionBlock], name: str):
    normalized = normalize_heading(name)
    return sections.get(normalized)


def section(sections: dict[str, SectionBlock], name: str) -> str:
    block = section_block(sections, name)
    return block.content if block else ""


def section_lines(sections: dict[str, SectionBlock], name: str) -> list[tuple[int, str]]:
    block = section_block(sections, name)
    if not block:
        return []
    return [(block.content_start_line + offset, line) for offset, line in enumerate(block.content.splitlines())]


def clean_path(raw: str) -> str:
    return raw.strip().strip("`'\"").rstrip(".,;:)")


def unquote_single_path_token(raw: str) -> str:
    token = raw.strip()
    if len(token) >= 2 and token[0] == "'" and token[-1] == "'":
        return token[1:-1]
    return raw


def redirect_path_candidates(raw: str) -> list[str]:
    candidates: list[str] = []
    for match in re.finditer(r"[<>]+([^<>]*)", raw):
        cleaned = clean_path(match.group(1))
        if is_path_reference_candidate(cleaned):
            candidates.append(cleaned)
    return candidates


def redirect_path_refs(raw: str) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for match in re.finditer(r"([<>]+)([^<>]*)", raw):
        cleaned = clean_path(match.group(2))
        if raw.startswith("$(") and cleaned.endswith(")"):
            cleaned = cleaned.rstrip(")")
        if not cleaned.startswith("&") and (
            is_path_reference_candidate(cleaned) or is_command_path_operand_candidate(cleaned)
        ):
            operator = match.group(1)
            kind = PATH_REF_READ if operator.startswith("<") and operator != "<>" else PATH_REF_WRITE
            refs.append((cleaned, kind))
    return refs


def is_path_reference_candidate(raw: str) -> bool:
    return _is_path_like(clean_path(raw)) or _is_bare_filename(clean_path(raw))


def expand(raw: str, base_dir: Path) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
    if expanded.is_absolute():
        return expanded.resolve(strict=False)
    return (base_dir / expanded).resolve(strict=False)


def is_writable_path(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    has_write_bit = bool(mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    return has_write_bit and os.access(path, os.W_OK)


def parse_owned_paths(sections: dict[str, SectionBlock]) -> tuple[list[dict[str, str | int]], list[dict[str, str | int]]]:
    block = section_block(sections, "Owned paths")
    if not block:
        return [], []
    parsed, bad_lines = parse_owned_path_lines(block.content)
    paths = [
        {
            "path": clean_path(str(item["path"])),
            "intent": str(item["intent"]),
            "line": block.content_start_line + int(item["line_number"]) - 1,
        }
        for item in parsed
    ]
    malformed = [
        {
            "text": str(item["text"]),
            "line": block.content_start_line + int(item["line_number"]) - 1,
        }
        for item in bad_lines
    ]
    return paths, malformed


def inline_paths(text: str) -> list[str]:
    return [clean_path(raw) for _line, raw in extract_inline_paths(text)]


def inline_paths_with_lines(sections: dict[str, SectionBlock], name: str) -> list[tuple[int, str]]:
    block = section_block(sections, name)
    if not block:
        return []
    return [
        (block.content_start_line + line_number - 1, raw)
        for line_number, raw in parse_output_paths_with_lines(block.content)
    ]


def is_informational_reference(line: str) -> bool:
    lowered = line.lower()
    return "informational" in lowered or "for reference only" in lowered or "reference-only" in lowered


def is_directory_output_line(line: str) -> bool:
    lowered = line.lower()
    return bool(
        re.search(r"\b(directory|folder)-producing\b", lowered)
        or re.search(r"\b(produce|create|save|write|output)\s+(a\s+|an\s+)?(directory|folder)\b", lowered)
    )


def reference_intents_for_line(line: str) -> set[str]:
    words = set(re.findall(r"[A-Za-z]+", line.lower()))
    if words & WRITE_REFERENCE_WORDS:
        return OUTPUT_VALID_INTENTS | {"MODIFY"}
    if words & READ_REFERENCE_WORDS:
        return {"READ", "MODIFY"}
    return INTENTS


def referenced_path_errors(
    sections: dict[str, SectionBlock],
    owned_paths_by_target: dict[Path, list[dict[str, str | int]]],
    base_dir: Path,
) -> list[str]:
    errors = []
    for name, block in sections.items():
        if name in REFERENCE_SCAN_EXCLUDED_SECTIONS:
            continue
        lines = block.content.splitlines()
        for section_line, raw in extract_inline_paths(block.content):
            line_index = section_line - 1
            line_text = lines[line_index] if 0 <= line_index < len(lines) else ""
            if is_informational_reference(line_text):
                continue
            declarations = owned_paths_by_target.get(expand(raw, base_dir), [])
            source_line = block.content_start_line + section_line - 1
            if not declarations:
                errors.append(
                    f"line {source_line}: Referenced path must be declared in Owned paths "
                    f"or marked informational: {raw}"
                )
                continue
            required_intents = reference_intents_for_line(line_text)
            if any(str(item["intent"]) in required_intents for item in declarations):
                continue
            declared = ", ".join(sorted({str(item["intent"]) for item in declarations}))
            errors.append(
                f"line {source_line}: Referenced path has incompatible Owned paths intent "
                f"for this context: {raw} ({declared})"
            )
    return errors


def classify(raw: str, intent: str, base_dir: Path) -> tuple[bool, str]:
    path = expand(raw, base_dir)
    if has_glob(raw):
        ok = path.parent.exists()
        return ok, f"{raw} {'has an existing parent for glob' if ok else 'has no existing parent for glob'}"
    if intent == "READ":
        return path.exists(), f"{raw} {'exists' if path.exists() else 'is missing for READ'}"
    if intent == "MODIFY":
        if not path.exists():
            return False, f"{raw} is missing for MODIFY"
        ok = is_writable_path(path)
        return ok, f"{raw} {'is writable' if ok else 'exists but is not writable for MODIFY'}"
    if intent == "DELETE":
        return path.exists(), f"{raw} {'exists' if path.exists() else f'is missing for {intent}'}"
    if path.exists():
        ok = is_writable_path(path)
        return ok, f"{raw} {'is writable' if ok else f'exists but is not writable for {intent}'}"
    target = path.parent
    ok = target.exists() and os.access(target, os.W_OK)
    return ok, f"{raw} {'is writable' if ok else 'has no writable parent'}"


def writability_check(paths: list[dict[str, str | int]], base_dir: Path) -> list[str]:
    errors = []
    for item in paths:
        if item["intent"] not in WRITE_INTENTS:
            continue
        ok, detail = classify(item["path"], item["intent"], base_dir)
        if not ok:
            errors.append(f"line {item['line']}: CONTRADICTION: {detail}")
    return errors


def readability_check(paths: list[dict[str, str | int]], base_dir: Path) -> list[str]:
    errors = []
    for item in paths:
        if item["intent"] in WRITE_INTENTS:
            continue
        ok, detail = classify(item["path"], item["intent"], base_dir)
        if not ok:
            errors.append(f"line {item['line']}: {detail}")
    return errors


def command_errors(sections: dict[str, SectionBlock], base_dir: Path, warnings: list[str] | None = None) -> list[str]:
    errors = []
    block = section_block(sections, "Verification")
    if not block:
        return errors
    paths, _malformed_owned_paths = parse_owned_paths(sections)
    created_paths = {
        expand(str(item["path"]), base_dir)
        for item in paths
        if str(item["intent"]) in VERIFICATION_CREATED_INTENTS
    }
    owned_paths_by_target: dict[Path, list[dict[str, str | int]]] = {}
    for path_item in paths:
        owned_paths_by_target.setdefault(expand(str(path_item["path"]), base_dir), []).append(path_item)
    commands, malformed = parse_verification_lines(block.content)
    for item in malformed:
        if item.error:
            line_number = block.content_start_line + int(item["line_number"]) - 1
            errors.append(f"line {line_number}: Malformed verification command: {item['text']}")
    for item in commands:
        line_number = block.content_start_line + int(item["line_number"]) - 1
        command = command_candidate([str(item["command"]), *item["args"]])
        unsupported_constructs = unsupported_shell_constructs(str(item["raw"]))
        if unsupported_constructs:
            if warnings is not None:
                for construct in unsupported_constructs:
                    warnings.append(
                        f"Shell construct {construct} not statically validated — "
                        "unsupported shell portion skipped; path checks still run"
                    )
        if command and command not in SHELL_BUILTINS and not binary_available(command, base_dir):
            errors.append(f"line {line_number}: Binary is unavailable: {command}")
        for nested_command in shell_c_commands(str(item["command"]), list(item["args"])):
            if nested_command in SHELL_BUILTINS:
                continue
            if not binary_available(nested_command, base_dir):
                errors.append(f"line {line_number}: Binary is unavailable: {nested_command}")
        for pipeline_command in pipeline_commands(str(item["raw"])):
            if pipeline_command == command:
                continue
            if pipeline_command in SHELL_BUILTINS:
                continue
            if not binary_available(pipeline_command, base_dir):
                errors.append(f"line {line_number}: Binary is unavailable: {pipeline_command}")
        for raw, ref_kind in command_path_refs(item, warnings):
            path = expand(raw, base_dir)
            if has_glob(raw):
                if warnings is not None:
                    warnings.append("Glob pattern — existence not verified")
                declarations = owned_paths_by_target.get(path, [])
                if not declarations:
                    errors.append(f"line {line_number}: Verification path must be declared in Owned paths: {raw}")
                elif not verification_intent_compatible(declarations, ref_kind):
                    intents = ", ".join(sorted({str(declaration["intent"]) for declaration in declarations}))
                    errors.append(
                        f"line {line_number}: Verification path has incompatible Owned paths intent "
                        f"for this command context: {raw} ({intents})"
                    )
                if not path.parent.exists():
                    errors.append(f"line {line_number}: Verification path parent is missing: {raw}")
                continue
            declarations = owned_paths_by_target.get(path, [])
            if not declarations:
                errors.append(f"line {line_number}: Verification path must be declared in Owned paths: {raw}")
            elif not verification_intent_compatible(declarations, ref_kind):
                intents = ", ".join(sorted({str(declaration["intent"]) for declaration in declarations}))
                errors.append(
                    f"line {line_number}: Verification path has incompatible Owned paths intent "
                    f"for this command context: {raw} ({intents})"
                )
            path = expand(raw, base_dir)
            if ref_kind in {PATH_REF_WRITE, PATH_REF_OUTPUT_CHECK} and path in created_paths and not raw.endswith("/"):
                continue
            if ref_kind == PATH_REF_DELETE and path in created_paths:
                errors.append(f"line {line_number}: Verification path is missing: {raw}")
                continue
            if ref_kind == PATH_REF_WRITE and raw.endswith("/") and path.exists() and not path.is_dir():
                errors.append(f"line {line_number}: Verification path must be an existing directory: {raw}")
                continue
            if not path.exists():
                errors.append(f"line {line_number}: Verification path is missing: {raw}")
    return errors


def verification_intent_compatible(declarations: list[dict[str, str | int]], ref_kind: str) -> bool:
    intents = {str(declaration["intent"]) for declaration in declarations}
    if ref_kind == PATH_REF_WRITE:
        return bool(intents & VERIFICATION_WRITE_INTENTS)
    if ref_kind == PATH_REF_DELETE:
        return bool(intents & {"DELETE", "MODIFY"})
    if ref_kind == PATH_REF_OUTPUT_CHECK:
        return bool(intents & VERIFICATION_OUTPUT_CHECK_INTENTS)
    return bool(intents & VERIFICATION_READ_INTENTS)


def binary_available(command: str, base_dir: Path) -> bool:
    if "/" in command and not Path(command).is_absolute():
        resolved = (base_dir / command).resolve(strict=False)
        return resolved.exists() and os.access(resolved, os.X_OK)
    return shutil.which(command) is not None


def unsupported_shell_constructs(raw: str) -> list[str]:
    quote_aware_raw = strip_single_quoted_segments(raw)
    return [label for label, pattern in UNSUPPORTED_SHELL_PATTERNS if pattern.search(quote_aware_raw)]


def strip_single_quoted_segments(raw: str) -> str:
    """Blank shell text protected by single quotes."""
    chars = list(raw)
    index = 0
    inside_double_quotes = False
    while index < len(chars):
        character = chars[index]
        if character == "\\" and not inside_double_quotes:
            index += 2
            continue
        if character == '"':
            inside_double_quotes = not inside_double_quotes
            index += 1
            continue
        if character != "'" or inside_double_quotes:
            index += 1
            continue
        start = index
        index += 1
        while index < len(chars) and chars[index] != "'":
            index += 1
        if index >= len(chars):
            break
        for position in range(start, index + 1):
            chars[position] = " "
        index += 1
    return "".join(chars)


def has_unquoted_occurrence(raw: str, token: str) -> bool:
    return token in strip_single_quoted_segments(raw)


def shell_c_commands(command: str, args: list[str]) -> list[str]:
    if command not in {"bash", "sh"} or "-c" not in args:
        return []
    index = args.index("-c")
    if index + 1 >= len(args):
        return []
    raw = args[index + 1]
    commands = pipeline_commands(raw)
    if commands:
        return commands
    try:
        tokens = shlex.split(raw)
    except ValueError:
        return []
    candidate = command_candidate(tokens)
    return [candidate] if candidate else []


def command_candidate(tokens: list[str]) -> str | None:
    candidate = command_candidate_with_index(tokens)
    return candidate[0] if candidate else None


def command_candidate_with_index(tokens: list[str]) -> tuple[str, int] | None:
    tokens = list(tokens)
    index = 0
    while True:
        while index < len(tokens) and ENV_ASSIGNMENT_RE.match(tokens[index]):
            index += 1
        if index >= len(tokens):
            return None
        command = tokens[index]
        if command == "env":
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                option = tokens[index]
                index += 1
                if option == "--":
                    break
                option_name = option.split("=", 1)[0]
                if option_name in ENV_SPLIT_STRING_OPTIONS:
                    if "=" in option:
                        operand = option.split("=", 1)[1]
                    elif index < len(tokens):
                        operand = tokens[index]
                        index += 1
                    else:
                        continue
                    try:
                        split_tokens = shlex.split(operand)
                    except ValueError:
                        return None
                    tokens[index:index] = split_tokens
                    continue
                if option_name in ENV_OPTIONS_WITH_OPERANDS and "=" not in option and index < len(tokens):
                    index += 1
            continue
        if command == "command":
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
            if index >= len(tokens):
                return command, index - 1
            continue
        return command, index


def pipeline_commands(raw: str) -> list[str]:
    if not any(separator in raw for separator in ("|", "&", ";")):
        return []
    commands: list[str] = []
    for segment in shell_command_segments(raw):
        command = command_candidate(segment)
        if command:
            commands.append(command)
    return commands


def shell_command_segments(raw: str) -> list[list[str]]:
    lexer = shlex.shlex(raw, posix=True, punctuation_chars="|&;")
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = []
    segment: list[str] = []
    for index, token in enumerate(tokens):
        if is_command_separator(tokens, index):
            if segment:
                segments.append(segment)
            segment = []
            continue
        segment.append(token)
    if segment:
        segments.append(segment)
    return segments


def is_command_separator(tokens: list[str], index: int) -> bool:
    token = tokens[index]
    if set(token) == {"|"}:
        previous = tokens[index - 1] if index > 0 else ""
        return not re.fullmatch(r"\d*>", previous)
    if token in {"&&", "||", ";"}:
        return True
    if token != "&":
        return False
    previous = tokens[index - 1] if index > 0 else ""
    following = tokens[index + 1] if index + 1 < len(tokens) else ""
    return not (previous.endswith(">") and following.isdigit())


def has_glob(raw: str) -> bool:
    return any(character in raw for character in ("*", "?", "["))


def is_command_path_operand_candidate(raw: str) -> bool:
    candidate = clean_path(raw)
    if not candidate or candidate.startswith(("-", "$")):
        return False
    return not any(character.isspace() or character in "<>|&;()" for character in candidate)


def command_option_consumes_next(command_name: str, token: str) -> bool:
    option_name = token.split("=", 1)[0]
    if "=" in token:
        return False
    if attached_target_directory(token) is not None:
        return False
    if option_name in TARGET_DIRECTORY_OPTIONS:
        return True
    return option_name in COMMAND_OPTIONS_WITH_OPERANDS.get(command_name, set())


def read_command_option_operand_kind(command_name: str, token: str) -> str | None:
    option_name = token.split("=", 1)[0]
    if option_name in READ_COMMAND_WRITE_OPTIONS_WITH_OPERANDS.get(command_name, set()):
        return PATH_REF_WRITE
    if option_name in READ_COMMAND_READ_OPTIONS_WITH_OPERANDS.get(command_name, set()):
        return PATH_REF_READ
    if option_name in READ_COMMAND_OPTIONS_WITH_OPERANDS.get(command_name, set()):
        return "skip"
    return None


def read_command_attached_option_operand(command_name: str, token: str) -> tuple[str, str] | None:
    if not token.startswith("-") or token.startswith("--") or len(token) <= 2:
        return None
    for options, kind in (
        (READ_COMMAND_WRITE_OPTIONS_WITH_OPERANDS.get(command_name, set()), PATH_REF_WRITE),
        (READ_COMMAND_READ_OPTIONS_WITH_OPERANDS.get(command_name, set()), PATH_REF_READ),
        (READ_COMMAND_OPTIONS_WITH_OPERANDS.get(command_name, set()), "skip"),
    ):
        for option in options:
            if option.startswith("--") or len(option) != 2:
                continue
            if token.startswith(option):
                return kind, token[len(option) :]
    return None


def command_path_operand_indexes(tokens: list[str], command_name: str) -> list[int]:
    indexes: list[int] = []
    skip_next = False
    for index, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if token.startswith("<<"):
            if token in {"<<", "<<-", "<<<"}:
                skip_next = True
            continue
        if token in {">", ">>", "<"} or re.fullmatch(r"\d*>>?", token) or re.fullmatch(r"\d*<", token):
            skip_next = True
            continue
        if command_option_consumes_next(command_name, token):
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        if is_command_path_operand_candidate(token):
            indexes.append(index)
    return indexes


def command_uses_target_directory_option(tokens: list[str]) -> bool:
    for token in tokens:
        option_name = token.split("=", 1)[0]
        if option_name in TARGET_DIRECTORY_OPTIONS or attached_target_directory(token) is not None:
            return True
    return False


def attached_target_directory(token: str) -> str | None:
    if token.startswith("-t") and token != "-t" and not token.startswith("--"):
        return token[2:]
    return None


def is_inside_runtime_substitution(raw_command: str, raw_path: str) -> bool:
    path = clean_path(raw_path).rstrip(")")
    if not path:
        return False
    search_from = 0
    while True:
        position = raw_command.find(path, search_from)
        if position == -1:
            return False
        for opener in ("$(", "<(", ">("):
            open_position = raw_command.rfind(opener, 0, position)
            close_position = raw_command.find(")", position + len(path))
            if open_position != -1 and close_position != -1 and open_position < position < close_position:
                return True
        if raw_command.count("`", 0, position) % 2 == 1:
            return True
        search_from = position + len(path)


def command_paths(item: CommandEntry, warnings: list[str] | None = None) -> list[str]:
    return [path for path, _kind in command_path_refs(item, warnings)]


def command_path_refs(item: CommandEntry, warnings: list[str] | None = None) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    shell_c_payloads: set[str] = set()
    if str(item.command) in {"bash", "sh"} and "-c" in item.args:
        c_index = item.args.index("-c")
        if c_index + 1 < len(item.args):
            shell_c_payloads.add(item.args[c_index + 1])
    raw = str(item.raw) if str(item.raw) else shlex.join([str(item.command), *item.args])
    candidates.extend(shell_path_refs(raw))
    for payload in shell_c_payloads:
        candidates.extend(shell_path_refs(payload))
    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate, kind in candidates:
        cleaned = clean_path(candidate)
        if cleaned.endswith(")") and is_inside_runtime_substitution(item.raw, cleaned):
            cleaned = cleaned.rstrip(")")
        key = (cleaned, kind)
        if key not in seen:
            seen.add(key)
            deduped.append((cleaned, kind))
    return deduped


def shell_path_refs(raw: str) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    segments = shell_command_segments(raw)
    if not segments:
        try:
            tokens = shlex.split(raw)
        except ValueError:
            tokens = []
        if tokens:
            segments = [tokens]
    for segment in segments:
        candidate = command_candidate_with_index(segment)
        if not candidate:
            continue
        command, command_index = candidate
        refs.extend(token_path_refs(segment[command_index + 1 :], command))
    for payload in shell_substitution_payloads(raw):
        refs.extend(shell_path_refs(payload))
    return refs


def shell_substitution_payloads(raw: str) -> list[str]:
    text = strip_single_quoted_segments(raw)
    payloads: list[str] = []
    index = 0
    while index < len(text):
        opener = next((candidate for candidate in ("$(", "<(", ">(") if text.startswith(candidate, index)), None)
        if opener is None:
            index += 1
            continue
        payload_start = index + len(opener)
        end = matching_substitution_paren(text, payload_start)
        if end is None:
            index += len(opener)
            continue
        payload = text[payload_start:end].strip()
        if payload:
            payloads.append(payload)
        index = end + 1
    search_from = 0
    while True:
        start = text.find("`", search_from)
        if start == -1:
            break
        end = find_unescaped_backtick(text, start + 1)
        if end is None:
            break
        payload = text[start + 1 : end].replace("\\`", "`").strip()
        if payload:
            payloads.append(payload)
        search_from = end + 1
    return payloads


def find_unescaped_backtick(text: str, start: int) -> int | None:
    index = start
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "`":
            return index
        index += 1
    return None


def matching_substitution_paren(text: str, payload_start: int) -> int | None:
    depth = 1
    index = payload_start
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        opener = next((candidate for candidate in ("$(", "<(", ">(") if text.startswith(candidate, index)), None)
        if opener is not None:
            depth += 1
            index += len(opener)
            continue
        if text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def token_path_refs(tokens: list[str], command: str) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    normalized = [unquote_single_path_token(token) for token in tokens]
    command_name = Path(command).name
    skip_indexes: set[int] = set()
    grep_pattern_consumed = False
    sed_script_consumed = False
    awk_program_consumed = False
    tar_refs_added = False
    for index, token in enumerate(normalized):
        if index in skip_indexes:
            continue
        token_for_paths = token
        cleaned = clean_path(token_for_paths)
        if token.startswith("<<"):
            if token in {"<<", "<<-", "<<<"} and index + 1 < len(normalized):
                skip_indexes.add(index + 1)
            continue
        if "<<" not in token and not any(character.isspace() for character in token):
            refs.extend(redirect_path_refs(token_for_paths))
        if (token in {">", ">>"} or re.fullmatch(r"\d*>>?", token)) and index + 1 < len(normalized):
            if normalized[index + 1] == "|" and index + 2 < len(normalized):
                candidate = clean_path(normalized[index + 2])
                if not candidate.startswith("&") and (
                    is_path_reference_candidate(candidate) or is_command_path_operand_candidate(candidate)
                ):
                    refs.append((candidate, PATH_REF_WRITE))
                skip_indexes.update({index + 1, index + 2})
                continue
        if token in {">", ">>"} or re.fullmatch(r"\d*>>?", token):
            if index + 1 < len(normalized):
                candidate = clean_path(normalized[index + 1])
                if not candidate.startswith("&") and (
                    is_path_reference_candidate(candidate) or is_command_path_operand_candidate(candidate)
                ):
                    refs.append((candidate, PATH_REF_WRITE))
                skip_indexes.add(index + 1)
            continue
        if token == "<>" or re.fullmatch(r"\d*<>", token):
            if index + 1 < len(normalized):
                candidate = clean_path(normalized[index + 1])
                if is_path_reference_candidate(candidate) or is_command_path_operand_candidate(candidate):
                    refs.append((candidate, PATH_REF_WRITE))
                skip_indexes.add(index + 1)
            continue
        if token == "<" or re.fullmatch(r"\d*<", token):
            if index + 1 < len(normalized):
                candidate = clean_path(normalized[index + 1])
                if is_path_reference_candidate(candidate) or is_command_path_operand_candidate(candidate):
                    refs.append((candidate, PATH_REF_READ))
                skip_indexes.add(index + 1)
            continue
        if command_name == "env":
            option_name = token.split("=", 1)[0]
            if option_name in ENV_OPTIONS_WITH_OPERANDS and "=" not in token and index + 1 < len(normalized):
                skip_indexes.add(index + 1)
                continue
            if option_name in ENV_SPLIT_STRING_OPTIONS and index + 1 < len(normalized):
                skip_indexes.add(index + 1)
                continue
        if command_name in PYTHON_COMMANDS and token == "-m" and index + 1 < len(normalized):
            skip_indexes.add(index + 1)
            continue
        if command_name in INTERPRETER_COMMANDS and token in INTERPRETER_CODE_OPTIONS and index + 1 < len(normalized):
            skip_indexes.add(index + 1)
            continue
        if command_name == "pytest":
            option_name = token.split("=", 1)[0]
            if option_name in PYTEST_NON_PATH_OPTIONS_WITH_OPERANDS:
                if "=" not in token and index + 1 < len(normalized):
                    skip_indexes.add(index + 1)
                continue
        if command_name == "find":
            if token.startswith("-") or token in {"!", "(", ")"}:
                if token in FIND_OPTIONS_WITH_OPERANDS and index + 1 < len(normalized):
                    skip_indexes.add(index + 1)
                continue
            if is_command_path_operand_candidate(cleaned):
                refs.append((cleaned, PATH_REF_READ))
            continue
        if command_name == "tar":
            if not tar_refs_added:
                refs.extend(tar_path_refs(normalized))
                tar_refs_added = True
            continue
        if command_name == "awk":
            attached_operand = read_command_attached_option_operand(command_name, token)
            if attached_operand is not None:
                operand_kind, candidate = attached_operand
                candidate = clean_path(candidate)
                if operand_kind == PATH_REF_READ and is_command_path_operand_candidate(candidate):
                    refs.append((candidate, PATH_REF_READ))
                    awk_program_consumed = True
                continue
            operand_kind = read_command_option_operand_kind(command_name, token)
            if operand_kind is not None:
                option_name = token.split("=", 1)[0]
                if "=" in token:
                    candidate = clean_path(token.split("=", 1)[1])
                    if operand_kind == PATH_REF_READ and is_command_path_operand_candidate(candidate):
                        refs.append((candidate, PATH_REF_READ))
                        awk_program_consumed = True
                elif index + 1 < len(normalized):
                    candidate = clean_path(normalized[index + 1])
                    if operand_kind == PATH_REF_READ and is_command_path_operand_candidate(candidate):
                        refs.append((candidate, PATH_REF_READ))
                        awk_program_consumed = True
                    skip_indexes.add(index + 1)
                continue
            if token.startswith("-"):
                continue
            if is_command_path_operand_candidate(cleaned):
                if awk_program_consumed:
                    refs.append((cleaned, PATH_REF_READ))
                else:
                    awk_program_consumed = True
                continue
        if command_name in READ_OPERAND_COMMANDS:
            attached_operand = read_command_attached_option_operand(command_name, token)
            if attached_operand is not None:
                operand_kind, candidate = attached_operand
                candidate = clean_path(candidate)
                if operand_kind in {PATH_REF_READ, PATH_REF_WRITE} and is_command_path_operand_candidate(candidate):
                    refs.append((candidate, operand_kind))
                continue
            operand_kind = read_command_option_operand_kind(command_name, token)
            if operand_kind is not None:
                option_name = token.split("=", 1)[0]
                if "=" in token:
                    candidate = clean_path(token.split("=", 1)[1])
                    if operand_kind in {PATH_REF_READ, PATH_REF_WRITE} and is_command_path_operand_candidate(candidate):
                        refs.append((candidate, operand_kind))
                    if command_name == "sed" and operand_kind in {"skip", PATH_REF_READ}:
                        sed_script_consumed = True
                    if command_name == "grep" and option_name in {"-e", "--regexp", "-f", "--file"}:
                        grep_pattern_consumed = True
                elif index + 1 < len(normalized):
                    candidate = clean_path(normalized[index + 1])
                    if operand_kind in {PATH_REF_READ, PATH_REF_WRITE} and is_command_path_operand_candidate(candidate):
                        refs.append((candidate, operand_kind))
                    skip_indexes.add(index + 1)
                    if command_name == "sed" and operand_kind in {"skip", PATH_REF_READ}:
                        sed_script_consumed = True
                    if command_name == "grep" and option_name in {"-e", "--regexp", "-f", "--file"}:
                        grep_pattern_consumed = True
                continue
        if command_name in READ_WRITE_OPERAND_COMMANDS:
            attached_target = attached_target_directory(token)
            if attached_target is not None:
                candidate = clean_path(attached_target)
                if is_command_path_operand_candidate(candidate):
                    refs.append((candidate, PATH_REF_WRITE))
                continue
            option_name = token.split("=", 1)[0]
            if option_name in TARGET_DIRECTORY_OPTIONS:
                if "=" in token:
                    candidate = clean_path(token.split("=", 1)[1])
                    if is_command_path_operand_candidate(candidate):
                        refs.append((candidate, PATH_REF_WRITE))
                elif index + 1 < len(normalized):
                    candidate = clean_path(normalized[index + 1])
                    if is_command_path_operand_candidate(candidate):
                        refs.append((candidate, PATH_REF_WRITE))
                    skip_indexes.add(index + 1)
                continue
        if command_name in COMMAND_PATH_OPERAND_COMMANDS and command_option_consumes_next(command_name, token):
            if index + 1 < len(normalized):
                skip_indexes.add(index + 1)
            continue
        if "=" in token_for_paths:
            left, right = token_for_paths.split("=", 1)
            cleaned_right = clean_path(right)
            option_name = left.split("=", 1)[0]
            if is_path_reference_candidate(cleaned_right) and (
                _is_path_like(cleaned_right) or option_name in PATH_OPTIONS_WITH_OPERANDS
            ):
                kind = PATH_REF_WRITE if option_name in PATH_OPTIONS_WITH_OPERANDS else PATH_REF_READ
                refs.append((cleaned_right, kind))
            continue
        if token in PATH_OPTIONS_WITH_OPERANDS and index + 1 < len(normalized):
            candidate = clean_path(normalized[index + 1])
            if is_path_reference_candidate(candidate):
                refs.append((candidate, PATH_REF_WRITE))
            skip_indexes.add(index + 1)
            continue
        if token in NON_PATH_OPTIONS_WITH_OPERANDS and index + 1 < len(normalized):
            skip_indexes.add(index + 1)
            continue
        redirect = re.match(r"^\d*>+\s*(.+)$", token_for_paths)
        if redirect:
            cleaned_redirect = clean_path(redirect.group(1))
            if (
                not any(character.isspace() for character in token_for_paths)
                and not cleaned_redirect.startswith("&")
                and is_path_reference_candidate(cleaned_redirect)
            ):
                refs.append((cleaned_redirect, PATH_REF_WRITE))
            continue
        if not is_path_reference_candidate(cleaned):
            if command_name == "grep" and is_command_path_operand_candidate(cleaned):
                if grep_pattern_consumed:
                    refs.append((cleaned, PATH_REF_READ))
                else:
                    grep_pattern_consumed = True
                continue
            if command_name == "sed" and is_command_path_operand_candidate(cleaned):
                if sed_script_consumed:
                    refs.append((cleaned, PATH_REF_READ))
                else:
                    sed_script_consumed = True
                continue
            if command_name in READ_OPERAND_COMMANDS and is_command_path_operand_candidate(cleaned):
                refs.append((cleaned, PATH_REF_READ))
                continue
            if (
                command_name in {"test", "["}
                and index > 0
                and normalized[index - 1] in TEST_PATH_OPERATORS
                and is_command_path_operand_candidate(cleaned)
            ):
                refs.append((cleaned, PATH_REF_OUTPUT_CHECK))
                continue
            if command_name in WRITE_OPERAND_COMMANDS and is_command_path_operand_candidate(cleaned):
                refs.append((cleaned, PATH_REF_WRITE))
                continue
            if command_name in DELETE_OPERAND_COMMANDS and is_command_path_operand_candidate(cleaned):
                refs.append((cleaned, PATH_REF_DELETE))
                continue
            if command_name in READ_WRITE_OPERAND_COMMANDS and is_command_path_operand_candidate(cleaned):
                if command_name == "mv":
                    if command_uses_target_directory_option(normalized):
                        refs.append((cleaned, PATH_REF_DELETE))
                    elif index == last_path_operand_index(normalized, command_name):
                        refs.append((cleaned, PATH_REF_WRITE))
                    else:
                        refs.append((cleaned, PATH_REF_DELETE))
                elif command_uses_target_directory_option(normalized):
                    refs.append((cleaned, PATH_REF_READ))
                elif index == last_path_operand_index(normalized, command_name):
                    refs.append((cleaned, PATH_REF_WRITE))
                else:
                    refs.append((cleaned, PATH_REF_READ))
                continue
            if command_name in INTERPRETER_COMMANDS and is_command_path_operand_candidate(cleaned):
                refs.append((cleaned, PATH_REF_READ))
                continue
            if command_name == "pytest" and is_pytest_collection_operand(cleaned):
                refs.append((cleaned, PATH_REF_READ))
                continue
            if command_name == "grep" and not token.startswith("-"):
                grep_pattern_consumed = True
            continue
        if token.startswith("-"):
            continue
        if command_name in INTERPRETER_COMMANDS:
            refs.append((cleaned, PATH_REF_READ))
        elif command_name in {"test", "["} and index > 0 and normalized[index - 1] in TEST_PATH_OPERATORS:
            refs.append((cleaned, PATH_REF_OUTPUT_CHECK))
        elif command_name == "grep":
            if grep_pattern_consumed:
                refs.append((cleaned, PATH_REF_READ))
            else:
                grep_pattern_consumed = True
        elif command_name == "sed":
            if sed_script_consumed:
                refs.append((cleaned, PATH_REF_READ))
            else:
                sed_script_consumed = True
        elif command_name == "pytest":
            refs.append((cleaned, PATH_REF_READ))
        elif command_name in WRITE_OPERAND_COMMANDS:
            refs.append((cleaned, PATH_REF_WRITE))
        elif command_name in DELETE_OPERAND_COMMANDS:
            refs.append((cleaned, PATH_REF_DELETE))
        elif command_name in READ_WRITE_OPERAND_COMMANDS:
            if command_name == "mv":
                if command_uses_target_directory_option(normalized):
                    refs.append((cleaned, PATH_REF_DELETE))
                elif index == last_path_operand_index(normalized, command_name):
                    refs.append((cleaned, PATH_REF_WRITE))
                else:
                    refs.append((cleaned, PATH_REF_DELETE))
            elif command_uses_target_directory_option(normalized):
                refs.append((cleaned, PATH_REF_READ))
            elif index == last_path_operand_index(normalized, command_name):
                refs.append((cleaned, PATH_REF_WRITE))
            else:
                refs.append((cleaned, PATH_REF_READ))
        elif command_name in READ_OPERAND_COMMANDS or _is_path_like(cleaned):
            refs.append((cleaned, PATH_REF_READ))
    return refs


def tar_path_refs(tokens: list[str]) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    mode = PATH_REF_READ
    archive_operand_index: int | None = None
    skip_indexes: set[int] = set()
    for index, token in enumerate(tokens):
        if index in skip_indexes:
            continue
        if token in {"--file", "-f"} and index + 1 < len(tokens):
            archive_operand_index = index + 1
            skip_indexes.add(index + 1)
            continue
        if token.startswith("--file="):
            candidate = clean_path(token.split("=", 1)[1])
            if is_command_path_operand_candidate(candidate):
                refs.append((candidate, mode))
            continue
        if not token.startswith("-") or token == "-":
            continue
        option_letters = token.lstrip("-")
        if "c" in option_letters:
            mode = PATH_REF_WRITE
        elif "x" in option_letters or "t" in option_letters:
            mode = PATH_REF_READ
        if "f" in option_letters:
            after_f = option_letters.split("f", 1)[1]
            if after_f:
                candidate = clean_path(after_f)
                if is_command_path_operand_candidate(candidate):
                    refs.append((candidate, mode))
            elif index + 1 < len(tokens):
                archive_operand_index = index + 1
                skip_indexes.add(index + 1)
    if archive_operand_index is not None:
        candidate = clean_path(tokens[archive_operand_index])
        if is_command_path_operand_candidate(candidate):
            refs.append((candidate, mode))
    for index, token in enumerate(tokens):
        if index in skip_indexes or index == archive_operand_index or token.startswith("-"):
            continue
        candidate = clean_path(token)
        if is_command_path_operand_candidate(candidate):
            refs.append((candidate, PATH_REF_READ))
    return refs


def is_pytest_collection_operand(raw: str) -> bool:
    candidate = clean_path(raw)
    if not candidate or candidate.startswith(("-", "$", "!")) or "=" in candidate:
        return False
    if any(character.isspace() or character in "<>|&;()" for character in candidate):
        return False
    base = candidate.split("::", 1)[0]
    if not base:
        return False
    if is_path_reference_candidate(base):
        return True
    if "/" in base or base.startswith(PATH_PREFIXES):
        return True
    return re.fullmatch(r"(tests?|specs?|unit|integration|e2e)([-_A-Za-z0-9.]*)?", base) is not None


def last_path_operand_index(tokens: list[str], command_name: str) -> int | None:
    candidates = command_path_operand_indexes(tokens, command_name)
    if command_name == "mv" and len(candidates) == 1:
        return None
    return candidates[-1] if candidates else None


def kill_list_entries(sections: dict[str, SectionBlock]) -> list[str]:
    body = section(sections, "Kill List")
    if not body:
        return []
    entries: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            if current:
                entries.append(" ".join(current))
            current = [stripped[2:].strip()]
        elif current and stripped:
            current.append(stripped)
        elif current:
            entries.append(" ".join(current))
            current = []
    if current:
        entries.append(" ".join(current))
    return entries


def compact_path_text(value: str) -> str:
    return "".join(value.split())


def kill_list_contradictions(paths: list[dict[str, str | int]], sections: dict[str, SectionBlock]) -> list[str]:
    entries = kill_list_entries(sections)
    if not entries:
        return []
    compact_entries = [(entry, compact_path_text(entry)) for entry in entries]
    errors = []
    for item in paths:
        if item["intent"] not in WRITE_INTENTS:
            continue
        path_text = compact_path_text(item["path"])
        for entry, compact_entry in compact_entries:
            if path_text and path_text in compact_entry:
                errors.append(f"line {item['line']}: CONTRADICTION: {item['path']} is listed in Kill List: {entry}")
                break
    return errors


def intent_conflicts(paths: list[dict[str, str | int]], base_dir: Path) -> list[str]:
    paths_by_target: dict[Path, list[dict[str, str | int]]] = {}
    for item in paths:
        paths_by_target.setdefault(expand(str(item["path"]), base_dir), []).append(item)
    errors = []
    for target, declarations in paths_by_target.items():
        intents = {str(item["intent"]) for item in declarations}
        if not any(pair <= intents for pair in CONFLICTING_INTENT_PAIRS):
            continue
        lines = ", ".join(str(item["line"]) for item in declarations)
        errors.append(f"lines {lines}: CONTRADICTION: {target} has conflicting intents: {', '.join(sorted(intents))}")
    return errors


def timed_check(name: str, warnings: list[str], check):
    started = time.perf_counter()
    result = check()
    elapsed = time.perf_counter() - started
    if elapsed > CHECK_WARNING_SECONDS:
        warnings.append(f"Performance: {name} took {elapsed:.2f}s (threshold: {CHECK_WARNING_SECONDS:.2f}s)")
    return result


def run_checks(
    text: str,
    base_dir: Path | None = None,
    strict: bool = True,
    sections: dict[str, SectionBlock] | None = None,
) -> dict:
    base_dir = (base_dir or Path.cwd()).resolve()
    errors = []
    warnings = []
    sections = sections if sections is not None else timed_check("extract_sections", warnings, lambda: extract_sections(text))
    paths, malformed_owned_paths = timed_check("parse_owned_paths", warnings, lambda: parse_owned_paths(sections))
    # Duplicate detection preserves every block and independently scans the text.
    # Duplicate section merging itself can be O(n^2); see TROUBLESHOOTING.md.
    for name in timed_check("find_duplicate_sections", warnings, lambda: find_duplicate_sections(text)):
        errors.append(f"Duplicate section: {name}")
    missing_sections = [name for name in REQUIRED_SECTIONS if normalize_heading(name) not in sections]
    missing_section_line = len(text.splitlines()) + 1
    for name in missing_sections:
        errors.append(f"line {missing_section_line}: Missing required section: {name}")
    for item in malformed_owned_paths:
        errors.append(f"line {item['line']}: Malformed owned path line: {item['text']}")
    if strict and not paths:
        errors.append("No owned paths parsed")
    errors.extend(timed_check("readability_check", warnings, lambda: readability_check(paths, base_dir)))
    errors.extend(timed_check("writability_check", warnings, lambda: writability_check(paths, base_dir)))
    errors.extend(timed_check("command_errors", warnings, lambda: command_errors(sections, base_dir, warnings)))
    errors.extend(timed_check("kill_list_contradictions", warnings, lambda: kill_list_contradictions(paths, sections)))
    errors.extend(timed_check("intent_conflicts", warnings, lambda: intent_conflicts(paths, base_dir)))
    owned_paths_by_target: dict[Path, list[dict[str, str | int]]] = {}
    for item in paths:
        owned_paths_by_target.setdefault(expand(str(item["path"]), base_dir), []).append(item)
    errors.extend(
        timed_check(
            "referenced_path_errors",
            warnings,
            lambda: referenced_path_errors(sections, owned_paths_by_target, base_dir),
        )
    )
    output_paths = timed_check("output_paths", warnings, lambda: inline_paths_with_lines(sections, "Output Format"))
    output_block = section_block(sections, "Output Format")
    output_lines = output_block.content.splitlines() if output_block else []
    for line_number, raw in output_paths:
        line_index = line_number - (output_block.content_start_line if output_block else line_number)
        line_text = output_lines[line_index] if 0 <= line_index < len(output_lines) else ""
        if expand(raw, base_dir).is_dir() and not is_directory_output_line(line_text):
            errors.append(f"line {line_number}: Output path resolves to an existing directory: {raw}")
            continue
        declarations = owned_paths_by_target.get(expand(raw, base_dir), [])
        if any(item["intent"] in OUTPUT_VALID_INTENTS for item in declarations):
            continue
        if declarations:
            if any(item["intent"] == "READ" for item in declarations):
                errors.append(f"line {line_number}: READ-owned path cannot be used as output target.")
            else:
                errors.append(
                    f"line {line_number}: Output path must be declared with CREATE, WRITE, or APPEND in Owned paths: {raw}"
                )
        else:
            errors.append(f"line {line_number}: Output path must be declared with a write intent in Owned paths: {raw}")
    for raw in timed_check("inline_paths", warnings, lambda: inline_paths(text)):
        if raw.startswith(("~/", "$HOME/")):
            warnings.append(f"Home-relative path may resolve differently: {raw}")
    return {
        "passed": not errors,
        "errors": errors,
        "warnings": sorted(set(warnings)),
        "owned_paths": paths,
        "malformed_owned_path_lines": [str(item["text"]) for item in malformed_owned_paths],
        "missing_sections": missing_sections,
        "base_dir": str(base_dir),
        "strict": strict,
    }


def main() -> int:
    parser = GateArgumentParser(
        description="Gate 2: validate prompt filesystem claims before dispatch.",
        epilog=(
            "Examples:\n"
            "  gate-substance examples/good-prompt.txt\n"
            "  gate-substance --strict examples/good-prompt.txt\n"
            "  gate-substance --json examples/bad-missing-path.txt\n\n"
            "Gate 2 resolves relative paths from the prompt file's directory. "
            "READ, MODIFY, and DELETE targets must exist; WRITE, CREATE, and "
            "APPEND targets need an existing writable target or parent directory."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("prompt_file", help="Path to the dispatch prompt to validate")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON report")
    parser.add_argument("--strict", action="store_true", help="Require sections and at least one owned path")
    args = parser.parse_args()
    path = Path(args.prompt_file).resolve()
    text, status = read_prompt(path, args.json)
    if text is None:
        return status
    started = time.perf_counter()
    sections = extract_sections(text)
    parse_elapsed = time.perf_counter() - started
    report = run_checks(text, base_dir=path.parent, strict=args.strict, sections=sections)
    if parse_elapsed > CHECK_WARNING_SECONDS:
        report["warnings"].append(
            f"Performance: extract_sections took {parse_elapsed:.2f}s (threshold: {CHECK_WARNING_SECONDS:.2f}s)"
        )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"=== SUBSTANCE GATE: {path} ===")
        print("PASS" if report["passed"] else "BLOCKED")
        for error in report["errors"]:
            print(f"  [FAIL] {error}")
        for warning in report["warnings"]:
            print(f"  [WARN] {warning}")
    return 0 if report["passed"] else 1


def dispatch_main() -> int:
    if any(arg in ("--help", "-h") for arg in sys.argv[1:]):
        print("Usage: dispatch-gate <prompt-file>")
        return 0
    if "--version" in sys.argv[1:]:
        print(VERSION)
        return 0
    if len(sys.argv) != 2:
        print("Usage: dispatch-gate <prompt-file>")
        return 1

    prompt = Path(sys.argv[1])
    if not prompt.is_absolute():
        prompt = (Path.cwd() / prompt).resolve(strict=False)
    if not prompt.is_file():
        print(f"ERROR: prompt file not found: {sys.argv[1]}", file=sys.stderr)
        return 1

    script_dir = Path(__file__).resolve().parent
    print("=== TWO-GATE DISPATCH ===")
    sys.stdout.flush()
    form_status = os.spawnv(os.P_WAIT, sys.executable, [sys.executable, str(script_dir / "gate_form.py"), str(prompt), "--strict"])
    if form_status != 0:
        print("BLOCKED: Gate 1 (Form) failed. Role, Objective, Verification, and Output Format must be meaningful.")
        return 1
    sys.stdout.flush()
    substance_status = os.spawnv(
        os.P_WAIT, sys.executable, [sys.executable, str(script_dir / "gate_substance.py"), str(prompt), "--strict"]
    )
    if substance_status != 0:
        print("BLOCKED: Gate 2 (Substance) failed.")
        return 1
    print("=== BOTH GATES PASSED: SAFE TO DISPATCH ===")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({"error": "internal_error", "status": "ERROR"}))
        raise SystemExit(1)
