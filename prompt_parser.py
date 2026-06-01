"""Section-aware prompt parsers for two-gate-dispatch."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
import shlex


PATH_PREFIXES = ("./", "../", "~/", "$HOME/", "/")
OWNED_INTENTS = {"READ", "CREATE", "WRITE", "MODIFY", "DELETE", "APPEND"}
OUTPUT_VERBS = ("write", "save", "output", "overwrite", "report", "produce", "create")
INLINE_PATH_VERBS = (
    "append",
    "check",
    "compare",
    "create",
    "edit",
    "inspect",
    "load",
    "open",
    "output",
    "produce",
    "read",
    "review",
    "save",
    "source",
    "update",
    "use",
    "verify",
    "write",
)
SHLEX_META_RE = re.compile(r"['\"`$;]")
TOP_LEVEL_HEADINGS = {
    "boundaries",
    "kill list",
    "role",
    "objective",
    "owned paths",
    "workflow",
    "verification",
    "output format",
    "self-challenge",
    "self challenge",
    "knowledge context",
}


@dataclass(frozen=True)
class SectionBlock:
    """A normalized section with source line boundaries."""

    name: str
    content: str
    start_line: int
    end_line: int
    first_content_line: int

    @property
    def heading_line(self) -> int:
        """Compatibility alias for older gate integrations."""
        return self.start_line

    @property
    def content_start_line(self) -> int:
        """Return the source line containing the first section content."""
        return self.first_content_line


@dataclass(frozen=True)
class PathEntry:
    """An owned path declaration parsed from an Owned paths section."""

    path: str
    intent: str
    line_number: int

    def __getitem__(self, key: str) -> str | int:
        return getattr(self, key)


@dataclass(frozen=True)
class MalformedEntry:
    """A malformed structured line with section-relative line metadata."""

    text: str
    line_number: int
    error: str = ""

    def __getitem__(self, key: str) -> str | int:
        return getattr(self, key)


@dataclass(frozen=True)
class CommandEntry:
    """A verification command parsed from a Run bullet."""

    command: str
    args: list[str]
    raw: str
    line_number: int

    def __getitem__(self, key: str) -> object:
        return getattr(self, key)


def normalize_heading(raw: str) -> str:
    """Normalize a heading to lowercase words separated by one space."""
    heading = raw.strip()
    while heading.startswith("#"):
        heading = heading[1:].lstrip()
    if heading.endswith(":"):
        heading = heading[:-1]
    return " ".join(heading.strip().lower().split())


def _split_heading(line: str, *, allow_unknown: bool = False) -> tuple[str, str] | None:
    stripped = line.strip()
    is_markdown_heading = stripped.startswith("#")
    while stripped.startswith("#"):
        stripped = stripped[1:].lstrip()
    if stripped.startswith(("-", "*")):
        return None
    if ":" in stripped:
        before, after = stripped.split(":", 1)
    elif is_markdown_heading:
        before, after = stripped, ""
    else:
        return None
    name = normalize_heading(before)
    if not name or not name.replace(" ", "").replace("-", "").isalpha():
        return None
    if len(name) > 60:
        return None
    if not allow_unknown and name not in TOP_LEVEL_HEADINGS:
        return None
    return name, after.strip()


def _is_wrapped_heading_prefix(line: str) -> bool:
    stripped = line.strip()
    while stripped.startswith("#"):
        stripped = stripped[1:].lstrip()
    if not stripped or ":" in stripped or len(stripped) > 40:
        return False
    return stripped.replace(" ", "").replace("-", "").isalpha()


def _heading_at(lines: list[str], index: int) -> tuple[str, str, int] | None:
    heading = _split_heading(lines[index])
    if heading:
        return heading[0], heading[1], 1
    if (
        index + 1 >= len(lines)
        or (index > 0 and lines[index - 1].strip())
        or not _is_wrapped_heading_prefix(lines[index])
    ):
        return None
    next_heading = _split_heading(lines[index + 1], allow_unknown=True)
    if not next_heading:
        return None
    name = normalize_heading(f"{lines[index].strip()} {next_heading[0]}")
    if name not in TOP_LEVEL_HEADINGS:
        return None
    return name, next_heading[1], 2


def _is_fenced_code_boundary(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def strip_fenced_code_blocks(text: str) -> str:
    """Remove fenced code block contents while preserving line numbers."""
    stripped_lines: list[str] = []
    inside_fenced_code = False
    for line in text.splitlines():
        if _is_fenced_code_boundary(line):
            inside_fenced_code = not inside_fenced_code
            stripped_lines.append("")
            continue
        stripped_lines.append("" if inside_fenced_code else line)
    return "\n".join(stripped_lines)


def extract_sections(text: str) -> dict[str, SectionBlock]:
    """Parse normalized top-level sections with content and source boundaries."""
    lines = text.splitlines()
    blocks: list[SectionBlock] = []
    current_name: str | None = None
    current_start = 0
    current_content_start = 0
    current_content: list[str] = []
    index = 0
    inside_fenced_code = False

    while index < len(lines):
        if _is_fenced_code_boundary(lines[index]):
            if current_name is not None:
                current_content.append(lines[index])
            inside_fenced_code = not inside_fenced_code
            index += 1
            continue

        heading = None if inside_fenced_code else _heading_at(lines, index)
        if heading:
            if current_name is not None:
                blocks.append(
                    SectionBlock(
                        current_name,
                        "\n".join(current_content).strip("\n"),
                        current_start,
                        index,
                        current_content_start,
                    )
                )
            current_name, inline_content, consumed = heading
            current_start = index + 1
            current_content_start = index + consumed if inline_content else index + consumed + 1
            current_content = [inline_content] if inline_content else []
            index += consumed
            continue
        if current_name is not None:
            current_content.append(lines[index])
        index += 1

    if current_name is not None:
        blocks.append(
            SectionBlock(
                current_name,
                "\n".join(current_content).strip("\n"),
                current_start,
                len(lines),
                current_content_start,
            )
        )

    sections: dict[str, SectionBlock] = {}
    for block in blocks:
        if block.name not in sections:
            sections[block.name] = block
            continue
        previous = sections[block.name]
        content = "\n".join(part for part in (previous.content, block.content) if part).strip("\n")
        sections[block.name] = SectionBlock(
            previous.name,
            content,
            previous.start_line,
            block.end_line,
            previous.first_content_line,
        )
    return sections


@lru_cache(maxsize=16)
def parse_sections(text: str) -> dict[str, str]:
    """Compatibility wrapper returning normalized section content."""
    return {name: block.content for name, block in extract_sections(text).items()}


def _shell_split_fast(raw: str, *, comments: bool = False, posix: bool = True) -> list[str]:
    if not raw.strip():
        return []
    if not SHLEX_META_RE.search(raw):
        return raw.split()
    return shlex.split(raw, comments=comments, posix=posix)


def _ends_with_line_continuation(line: str) -> bool:
    if not line.endswith("\\"):
        return False
    trailing = len(line) - len(line.rstrip("\\"))
    if trailing % 2 == 1 and _inside_single_quotes(line[:-trailing]):
        return False
    return trailing % 2 == 1


def _inside_single_quotes(raw: str) -> bool:
    inside_single_quotes = False
    inside_double_quotes = False
    index = 0
    while index < len(raw):
        character = raw[index]
        if character == "\\" and not inside_single_quotes:
            index += 2
            continue
        if character == "'" and not inside_double_quotes:
            inside_single_quotes = not inside_single_quotes
        elif character == '"' and not inside_single_quotes:
            inside_double_quotes = not inside_double_quotes
        index += 1
    return inside_single_quotes


def _heredoc_terminators(raw: str) -> list[str]:
    terminators: list[str] = []
    for match in re.finditer(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_-]*)\1", raw):
        terminators.append(match.group(2))
    return terminators


def _verification_logical_lines(section_text: str) -> list[tuple[int, str]]:
    logical_lines: list[tuple[int, str]] = []
    lines = section_text.splitlines()
    index = 0
    while index < len(lines):
        line_number = index + 1
        line = lines[index]
        parts = [line]
        while index + 1 < len(lines):
            if _ends_with_line_continuation(parts[-1]):
                parts[-1] = parts[-1][:-1]
                separator = ""
            elif _inside_single_quotes("".join(parts)):
                separator = "\n"
            else:
                break
            index += 1
            parts.append(separator + lines[index])
        logical = "".join(parts)
        logical_lines.append((line_number, logical))

        body = _strip_bullet(logical)
        label, sep, command_text = body.partition(":")
        terminators = (
            _heredoc_terminators(command_text.strip())
            if sep and normalize_heading(label) == "run"
            else []
        )
        if terminators and any(line.strip() in terminators for line in lines[index + 1 :]):
            while terminators and index + 1 < len(lines):
                index += 1
                if lines[index].strip() in terminators:
                    terminators.remove(lines[index].strip())
        index += 1
    return logical_lines


def parse_section_blocks(text: str) -> list[SectionBlock]:
    """Return all section blocks in source order, preserving duplicates."""
    lines = text.splitlines()
    blocks: list[SectionBlock] = []
    current_name: str | None = None
    current_start = 0
    current_content_start = 0
    current_content: list[str] = []
    index = 0
    inside_fenced_code = False

    while index < len(lines):
        if _is_fenced_code_boundary(lines[index]):
            if current_name is not None:
                current_content.append(lines[index])
            inside_fenced_code = not inside_fenced_code
            index += 1
            continue

        heading = None if inside_fenced_code else _heading_at(lines, index)
        if heading:
            if current_name is not None:
                blocks.append(
                    SectionBlock(
                        current_name,
                        "\n".join(current_content).strip("\n"),
                        current_start,
                        index,
                        current_content_start,
                    )
                )
            current_name, inline_content, consumed = heading
            current_start = index + 1
            current_content_start = index + consumed if inline_content else index + consumed + 1
            current_content = [inline_content] if inline_content else []
            index += consumed
            continue
        if current_name is not None:
            current_content.append(lines[index])
        index += 1

    if current_name is not None:
        blocks.append(
            SectionBlock(
                current_name,
                "\n".join(current_content).strip("\n"),
                current_start,
                len(lines),
                current_content_start,
            )
        )
    return blocks


def find_duplicate_sections(text: str) -> list[str]:
    """Return normalized section headings that appear more than once."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for block in parse_section_blocks(text):
        if block.name in seen and block.name not in duplicates:
            duplicates.append(block.name)
        seen.add(block.name)
    return duplicates


def _strip_bullet(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith(("-", "*")):
        return stripped[1:].strip()
    return stripped


def _clean_path(raw: str) -> str:
    value = raw.strip().strip("`'\"").rstrip(".,;:)")
    value = re.sub(r"/\s*\n\s*", "/", value)
    return re.sub(r"\s*\n\s*", " ", value)


def _unquote_single_path_token(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1]
    return raw


def _is_path_like(value: str) -> bool:
    candidate = _clean_path(value)
    if candidate.startswith(PATH_PREFIXES):
        return True
    if "/" not in candidate or "://" in candidate:
        return False
    if candidate.startswith(("-", "$")) or candidate.endswith("/"):
        return False
    return not any(character.isspace() or character in "<>|&;()" for character in candidate)


def _is_bare_filename(value: str) -> bool:
    candidate = _clean_path(value)
    if candidate.startswith(("-", "$")):
        return False
    return "/" not in candidate and re.fullmatch(r"[^.\s][^/\s]*\.[A-Za-z0-9][A-Za-z0-9_-]*", candidate) is not None


def _redirect_path_candidates(raw: str) -> list[str]:
    candidates: list[str] = []
    for match in re.finditer(r"[<>]+([^<>]*)", raw):
        candidate = _clean_path(match.group(1))
        if raw.startswith("$(") and candidate.endswith(")"):
            candidate = candidate.rstrip(")")
        if _is_path_like(candidate):
            candidates.append(candidate)
    return candidates


def _is_backticked_output_path(value: str) -> bool:
    candidate = _clean_path(value)
    if not candidate or candidate.startswith(("-", "$")):
        return False
    if _is_path_like(candidate):
        return True
    return re.fullmatch(r"[^/\s<>|&;()]+", candidate) is not None


def parse_owned_path_lines(section_text: str) -> tuple[list[PathEntry], list[MalformedEntry]]:
    """Parse '- path (INTENT)' owned-path lines and malformed bullets."""
    paths: list[PathEntry] = []
    malformed: list[MalformedEntry] = []
    for line_number, line in enumerate(section_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        body = _strip_bullet(stripped)
        if body == stripped:
            continue
        intent_start = body.rfind("(")
        intent_end = body.rfind(")")
        if intent_start > 0 and intent_end == len(body) - 1:
            raw_path = body[:intent_start].strip()
            intent = body[intent_start + 1 : intent_end].strip().upper()
            if raw_path and intent in OWNED_INTENTS:
                paths.append(PathEntry(_clean_path(raw_path), intent, line_number))
                continue
        malformed.append(MalformedEntry(stripped, line_number))
    return paths, malformed


def parse_verification_commands(section_text: str) -> list[CommandEntry]:
    """Parse verification '- Run: ...' bullets into shell-tokenized commands."""
    commands: list[CommandEntry] = []
    for line_number, line in _verification_logical_lines(section_text):
        body = _strip_bullet(line)
        label, sep, command_text = body.partition(":")
        if sep and normalize_heading(label) == "run":
            raw = command_text.strip()
            try:
                tokens = _shell_split_fast(raw)
            except ValueError:
                continue
            if tokens:
                commands.append(CommandEntry(tokens[0], tokens[1:], raw, line_number))
    return commands


def parse_verification_lines(section_text: str) -> tuple[list[CommandEntry], list[MalformedEntry]]:
    """Parse verification commands and report malformed command bullets."""
    commands: list[CommandEntry] = []
    malformed: list[MalformedEntry] = []
    for line_number, line in _verification_logical_lines(section_text):
        stripped = line.strip()
        if not stripped:
            continue
        body = _strip_bullet(stripped)
        label, sep, command_text = body.partition(":")
        if sep and normalize_heading(label) == "run":
            raw = command_text.strip()
            try:
                tokens = _shell_split_fast(raw)
            except ValueError as exc:
                malformed.append(MalformedEntry(stripped, line_number, str(exc)))
                continue
            if tokens:
                commands.append(CommandEntry(tokens[0], tokens[1:], raw, line_number))
            continue
        if stripped.startswith(("-", "*")) and sep:
            malformed.append(MalformedEntry(stripped, line_number))
    return commands, malformed


def _extract_backtick_paths_with_lines(text: str, *, output_context: bool = False) -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []
    index = 0
    while index < len(text):
        start = text.find("`", index)
        if start == -1:
            break
        end = text.find("`", start + 1)
        if end == -1:
            break
        candidate = _clean_path(text[start + 1 : end])
        if _is_path_like(candidate) or (output_context and _is_backticked_output_path(candidate)):
            results.append((text.count("\n", 0, start) + 1, candidate))
        index = end + 1
    return results


def _token_paths_with_lines(text: str) -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        try:
            tokens = _shell_split_fast(line, comments=False, posix=True)
        except ValueError:
            continue
        for raw in tokens:
            raw = _unquote_single_path_token(raw)
            for candidate in _redirect_path_candidates(raw):
                results.append((line_number, candidate))
            if "`" in raw:
                continue
            candidate = _clean_path(raw)
            if _is_path_like(candidate):
                results.append((line_number, candidate))
            if "=" in raw:
                _left, right = raw.split("=", 1)
                candidate = _clean_path(right)
                if _is_path_like(candidate):
                    results.append((line_number, candidate))
            redirect = re.match(r"^\d*>+\s*(.+)$", raw)
            if redirect:
                candidate = _clean_path(redirect.group(1))
                if not candidate.startswith("&") and _is_path_like(candidate):
                    results.append((line_number, candidate))
    return results


def _verb_paths_with_lines(text: str, verbs: tuple[str, ...] = OUTPUT_VERBS) -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if "`" in line:
            continue
        words = line.strip().split()
        saw_output_verb = False
        for word in words:
            if word.strip(":").lower() in verbs:
                saw_output_verb = True
                continue
            candidate = _clean_path(word)
            if saw_output_verb and (_is_path_like(candidate) or _is_bare_filename(candidate)):
                results.append((line_number, candidate))
    return results


def parse_output_paths(section_text: str) -> list[str]:
    """Extract output target paths from backticks and unquoted path tokens."""
    return [path for _line, path in parse_output_paths_with_lines(section_text)]


def parse_output_paths_with_lines(section_text: str) -> list[tuple[int, str]]:
    """Extract output target paths with section-relative line numbers."""
    visible_text = strip_fenced_code_blocks(section_text)
    ordered = _extract_backtick_paths_with_lines(visible_text, output_context=True)
    ordered.extend(_verb_paths_with_lines(visible_text))
    ordered.extend(_token_paths_with_lines(visible_text))
    results: list[tuple[int, str]] = []
    seen: set[str] = set()
    for line_number, path in ordered:
        if path in seen:
            continue
        seen.add(path)
        results.append((line_number, path))
    return results


def parse_workflow_steps(section_text: str) -> list[str]:
    """Parse ordered workflow steps, including wrapped continuation lines."""
    steps: list[str] = []
    current: list[str] = []
    for line in section_text.splitlines():
        stripped = line.strip()
        marker, sep, rest = stripped.partition(".")
        alt_marker, alt_sep, alt_rest = stripped.partition(")")
        if sep and marker.isdigit() and rest.strip():
            if current:
                steps.append(" ".join(current).strip())
            current = [rest.strip()]
        elif alt_sep and alt_marker.isdigit() and alt_rest.strip():
            if current:
                steps.append(" ".join(current).strip())
            current = [alt_rest.strip()]
        elif current and stripped:
            current.append(stripped)
        elif current:
            steps.append(" ".join(current).strip())
            current = []
    if current:
        steps.append(" ".join(current).strip())
    return steps


def has_meaningful_content(section_text: str, min_chars: int = 20) -> bool:
    """Return true when non-whitespace content reaches min_chars."""
    return len("".join(section_text.split())) >= min_chars


def extract_inline_paths(text: str) -> list[tuple[int, str]]:
    """Extract inline path references from any prompt text with line numbers."""
    visible_text = strip_fenced_code_blocks(text)
    ordered = _extract_backtick_paths_with_lines(visible_text)
    ordered.extend(_verb_paths_with_lines(visible_text, verbs=INLINE_PATH_VERBS))
    ordered.extend(_token_paths_with_lines(visible_text))
    results: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for item in ordered:
        if item in seen:
            continue
        seen.add(item)
        results.append(item)
    return results


def duplicate_section_names(text: str) -> list[str]:
    """Compatibility alias for find_duplicate_sections()."""
    return find_duplicate_sections(text)


def parse_owned_paths(section_text: str) -> list[dict[str, str | int]]:
    """Compatibility wrapper returning valid owned-path records."""
    paths, _malformed = parse_owned_path_lines(section_text)
    return [
        {"path": item.path, "intent": item.intent, "line_number": item.line_number}
        for item in paths
    ]


def parse_verification(section_text: str) -> list[dict[str, object]]:
    """Compatibility wrapper returning verification command records."""
    return [
        {
            "command": item.command,
            "args": item.args,
            "raw": item.raw,
            "line_number": item.line_number,
        }
        for item in parse_verification_commands(section_text)
    ]


def parse_output_format(section_text: str) -> list[str]:
    """Compatibility alias for parse_output_paths()."""
    return parse_output_paths(section_text)


def parse_output_format_with_lines(section_text: str) -> list[dict[str, str | int]]:
    """Compatibility wrapper returning dict records for older callers."""
    return [{"path": path, "line_number": line} for line, path in parse_output_paths_with_lines(section_text)]


def parse_workflow(section_text: str) -> list[str]:
    """Compatibility alias for parse_workflow_steps()."""
    return parse_workflow_steps(section_text)


def section_has_content(text: str, min_chars: int = 20) -> bool:
    """Compatibility alias for has_meaningful_content()."""
    return has_meaningful_content(text, min_chars=min_chars)
