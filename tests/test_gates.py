from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import prompt_parser
from prompt_parser import (
    parse_output_format,
    parse_owned_paths,
    parse_sections,
    parse_verification,
    parse_workflow,
    section_has_content,
)


ROOT = Path(__file__).resolve().parents[1]


def test_source_tree_contains_required_runtime_files() -> None:
    for name in ("prompt_parser.py", "dispatch-gate.sh"):
        assert (ROOT / name).is_file(), f"missing source-tree runtime file: {name}"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)


def valid_prompt_text(output_format: str | None = "- Write Markdown to `./examples/generated-report.md`.\n") -> str:
    output_section = "Output Format:\n"
    if output_format is not None:
        output_section += output_format
    return (
        "Role: Precision gate logic engineer\n\n"
        "Objective: Write a complete validation report.\n\n"
        "Owned paths:\n"
        "- ./examples/good-prompt.txt (READ)\n"
        "- ./examples/generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Read the prompt.\n"
        "2. Create the report.\n\n"
        "Verification:\n"
        "- Run: test -f ./examples/good-prompt.txt\n\n"
        f"{output_section}\n"
        "Self-Challenge:\n"
        "- Did every referenced path resolve correctly?\n"
    )


def test_prompt_parser_sections_handle_wrapped_inline_and_empty_content() -> None:
    sections = parse_sections(
        "Role: Precision parser engineer\n\n"
        "Output\n"
        "Format:\n"
        "- Write report to `./reports/final.md`.\n\n"
        "Verification:\n"
        "   \n"
        "Self-Challenge:\n"
        "- Check parser behavior.\n"
    )
    assert sections["role"] == "Precision parser engineer"
    assert sections["output format"] == "- Write report to `./reports/final.md`."
    assert section_has_content(sections["verification"]) is False
    assert section_has_content("   \n\t  ") is False


def test_prompt_parser_sections_handle_compact_adjacent_headings() -> None:
    sections = parse_sections(
        "Role: expert\n"
        "Objective:\n"
        "validate\n"
        "Owned Paths:\n"
        "  - /tmp/x (CREATE)\n"
    )
    assert sections["role"] == "expert"
    assert sections["objective"] == "validate"
    assert sections["owned paths"] == "  - /tmp/x (CREATE)"


def test_prompt_parser_keeps_plain_colon_labels_inside_output_format() -> None:
    sections = parse_sections(
        "Output Format:\n"
        "Details:\n"
        "- Write Markdown to `./undeclared.md`.\n"
    )
    assert sections["output format"] == "Details:\n- Write Markdown to `./undeclared.md`."
    assert "details" not in sections


def test_prompt_parser_keeps_unknown_markdown_subheading_inside_known_section() -> None:
    sections = parse_sections(
        "# Output Format\n"
        "## Details\n"
        "- Write Markdown to `./report.md`.\n"
    )
    assert sections["output format"] == "## Details\n- Write Markdown to `./report.md`."
    assert "details" not in sections


def test_prompt_parser_extracts_owned_paths_verification_and_workflow() -> None:
    owned = parse_owned_paths(
        "- ./src/app.py (READ)\n"
        "- /tmp/generated-report.md (CREATE)\n"
        "- ./bad/path READ\n"
    )
    verification = parse_verification(
        "- Run: bash -c 'echo hello'\n"
        "- Run: pytest tests | tee ./reports/out.log > /tmp/pytest.log\n"
        "- Execute: pytest tests\n"
    )
    workflow = parse_workflow("1. Read inputs.\n2. Write the report.\n   Include notes.\n")
    assert owned == [
        {"path": "./src/app.py", "intent": "READ", "line_number": 1},
        {"path": "/tmp/generated-report.md", "intent": "CREATE", "line_number": 2},
    ]
    assert verification[0]["command"] == "bash"
    assert verification[0]["args"] == ["-c", "echo hello"]
    assert verification[1]["command"] == "pytest"
    assert "|" in verification[1]["args"]
    assert ">" in verification[1]["args"]
    assert len(verification) == 2
    assert workflow == ["Read inputs.", "Write the report. Include notes."]


def test_prompt_parser_skips_shlex_for_plain_lines(monkeypatch) -> None:
    def fail_split(*_args, **_kwargs):
        raise AssertionError("shlex.split should not run for plain lines")

    monkeypatch.setattr(prompt_parser.shlex, "split", fail_split)
    commands = prompt_parser.parse_verification_commands("- Run: pytest tests -q\n")
    output_paths = prompt_parser.parse_output_paths_with_lines("- Save as ./reports/plain.md\n")
    assert commands[0].args == ["tests", "-q"]
    assert output_paths == [(1, "./reports/plain.md")]


def test_move_intent_is_not_parsed() -> None:
    owned = parse_owned_paths("- ./old.md -> ./new.md (MOVE)\n")
    assert owned == []


def test_prompt_parser_output_format_extracts_all_path_styles() -> None:
    paths = parse_output_format(
        "- Write summary to `report.md`.\n"
        "- Write extensionless summary to `report`.\n"
        "- Write nested summary to `reports/final.md`.\n"
        "- Write to `./reports/final report.md`.\n"
        "- Write parent report to `../parent-report.md`.\n"
        "- Write home report to `~/home-report.md`.\n"
        "- Write env report to `$HOME/env-report.md`.\n"
        "- Save as ./reports/plain.md\n"
        "- Output to /tmp/absolute-report.txt\n"
        "- Also produce `./wrapped path/\n"
        "  final.md`.\n"
    )
    assert paths == [
        "report.md",
        "report",
        "reports/final.md",
        "./reports/final report.md",
        "../parent-report.md",
        "~/home-report.md",
        "$HOME/env-report.md",
        "./wrapped path/final.md",
        "./reports/plain.md",
        "/tmp/absolute-report.txt",
    ]


def test_prompt_parser_output_format_extracts_unbackticked_slash_relative_paths() -> None:
    paths = parse_output_format(
        "- Write Markdown to reports/final.md.\n"
        "- Output JSON to results/out.json,\n"
        "- Save as artifacts/nested/summary.txt)\n"
    )
    assert paths == ["reports/final.md", "results/out.json", "artifacts/nested/summary.txt"]


def test_prompt_parser_extracts_equals_and_attached_redirect_paths() -> None:
    paths = parse_output_format(
        "- Run tool --flag=./path --home=\"~/test\" --tmp=/tmp/test "
        "2>./stderr.log 2>&1 cat<./stdin.txt cat>>./append.log\n"
    )
    assert paths == ["./path", "~/test", "/tmp/test", "./stderr.log", "./stdin.txt", "./append.log"]


def test_prompt_parser_extracts_compact_redirect_paths_in_runtime_substitution() -> None:
    assert prompt_parser.extract_inline_paths("- Run: echo $(cat<./missing.txt)") == [(1, "./missing.txt")]
    assert prompt_parser.extract_inline_paths("- Run: echo `cat<./missing.txt`") == [(1, "./missing.txt")]


def test_substance_command_paths_extracts_equals_and_attached_redirects() -> None:
    substance = load_script("gate_substance.py")
    item = prompt_parser.CommandEntry(
        "tool",
        [
            "--flag=./path",
            '--home="~/test"',
            "--tmp=/tmp/test",
            "2>./stderr.log",
            "2>&1",
            "cat<./stdin.txt",
            "cat>>./append.log",
        ],
        "",
        1,
    )
    assert sorted(substance.command_paths(item)) == sorted(
        ["./path", "~/test", "/tmp/test", "./stderr.log", "./stdin.txt", "./append.log"]
    )


def test_substance_command_paths_extracts_separated_redirects() -> None:
    substance = load_script("gate_substance.py")
    commands, malformed = prompt_parser.parse_verification_lines(
        "- Run: echo ok > report.xml\n"
        "- Run: echo ok >> append.log\n"
        "- Run: echo ok 2> stderr.log\n"
        "- Run: cat < input.txt\n"
        "- Run: cat << report.xml\n"
        "- Run: cat <<report.xml\n"
        "- Run: bash -c 'cat <<inner.txt'\n"
    )

    assert malformed == []
    assert [substance.command_paths(command) for command in commands] == [
        ["report.xml"],
        ["append.log"],
        ["stderr.log"],
        ["input.txt"],
        [],
        [],
        [],
    ]


def test_verification_backslash_continuation_preserves_redirect_path() -> None:
    substance = load_script("gate_substance.py")
    commands, malformed = prompt_parser.parse_verification_lines(
        "- Run: echo ok \\\n"
        "  > /tmp/hidden\n"
    )

    assert malformed == []
    assert len(commands) == 1
    assert substance.command_paths(commands[0]) == ["/tmp/hidden"]


def test_escaped_trailing_backslash_is_not_a_line_continuation() -> None:
    commands, malformed = prompt_parser.parse_verification_lines(
        "- Run: echo ok \\\\\n"
        "- Run: test -f ./declared.txt\n"
    )

    assert malformed == []
    assert len(commands) == 2
    assert commands[0].raw == "echo ok \\\\"


def test_single_quoted_backslash_is_not_continuation() -> None:
    substance = load_script("gate_substance.py")
    commands, malformed = prompt_parser.parse_verification_lines(
        "- Run: echo 'literal \\\n"
        "  > /tmp/not_redirect'\n"
    )

    assert malformed == []
    assert len(commands) == 1
    assert substance.command_paths(commands[0]) == []


def test_backslash_continuation_verification_redirect_requires_declaration(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: echo ok \\\n"
        "  > /tmp/hidden\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "Verification path must be declared in Owned paths: /tmp/hidden" in "\n".join(report["errors"])


def test_single_quoted_backslash_redirect_text_passes_strict_gate(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: echo 'literal \\\n"
        "  > /tmp/not_redirect'\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 0, report
    assert "/tmp/not_redirect" not in "\n".join(report["errors"])


def test_verification_heredoc_bodies_are_not_parsed_as_commands() -> None:
    for opener in ("<<'PY'", "<<PY", '<<"PY"'):
        commands, malformed = prompt_parser.parse_verification_lines(
            f"- Run: python3 - {opener}\n"
            "import pathlib\n"
            "pathlib.Path('undeclared').read_text()\n"
            "PY\n"
            "- Run: test -f ./declared.txt\n"
        )

        assert malformed == []
        assert [command.command for command in commands] == ["python3", "test"]
        assert commands[0].raw == f"python3 - {opener}"
        assert commands[0].args == ["-", "<<PY"]


def test_heredoc_body_paths_do_not_trigger_verification_path_errors(tmp_path: Path) -> None:
    (tmp_path / "declared.txt").write_text("ok\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./declared.txt (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: python3 - <<'PY'\n"
        "import pathlib\n"
        "pathlib.Path('undeclared').read_text()\n"
        "PY\n"
        "- Run: test -f ./declared.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json")
    assert result.returncode == 0, result.stdout + result.stderr


def test_substance_command_paths_extracts_extensionless_redirects() -> None:
    substance = load_script("gate_substance.py")
    commands, malformed = prompt_parser.parse_verification_lines(
        "- Run: echo ok > report\n"
        "- Run: echo ok >> append\n"
        "- Run: echo ok 2> stderr\n"
        "- Run: cat < input\n"
        "- Run: echo ok 2>stderr-attached\n"
        "- Run: cat << report\n"
        "- Run: cat <<report\n"
    )

    assert malformed == []
    assert [substance.command_paths(command) for command in commands] == [
        ["report"],
        ["append"],
        ["stderr"],
        ["input"],
        ["stderr-attached"],
        [],
        [],
    ]


def test_substance_command_paths_ignore_quoted_literal_nested_paths() -> None:
    substance = load_script("gate_substance.py")
    commands, malformed = prompt_parser.parse_verification_lines("- Run: printf '$(cat ./not-real.txt)'\n")

    assert malformed == []
    assert commands
    assert substance.command_paths(commands[0]) == []


def test_substance_command_paths_extract_single_quoted_path() -> None:
    substance = load_script("gate_substance.py")
    commands, malformed = prompt_parser.parse_verification_lines("- Run: printf './quoted-missing.txt'\n")

    assert malformed == []
    assert commands
    assert substance.command_paths(commands[0]) == ["./quoted-missing.txt"]


def test_substance_gate_blocks_missing_single_quoted_verification_path(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: test -f './missing-single.txt'\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "Verification path is missing: ./missing-single.txt" in "\n".join(report["errors"])


def test_substance_command_paths_extract_double_quoted_path() -> None:
    substance = load_script("gate_substance.py")
    commands, malformed = prompt_parser.parse_verification_lines('- Run: test -f "./quoted-missing.txt"\n')

    assert malformed == []
    assert commands
    assert substance.command_paths(commands[0]) == ["./quoted-missing.txt"]


def test_substance_command_paths_extract_slash_relative_operands() -> None:
    substance = load_script("gate_substance.py")
    commands, malformed = prompt_parser.parse_verification_lines(
        "- Run: python3 scripts/missing-check.py\n"
        "- Run: pytest 'tests/missing_test_file.py'\n"
        "- Run: sh \"scripts/missing-check.sh\"\n"
        "- Run: bash -c 'python3 scripts/nested-missing.py'\n"
    )

    assert malformed == []
    assert [substance.command_paths(command) for command in commands] == [
        ["scripts/missing-check.py"],
        ["tests/missing_test_file.py"],
        ["scripts/missing-check.sh"],
        ["scripts/nested-missing.py"],
    ]


def test_substance_command_paths_extract_bare_filename_operands() -> None:
    substance = load_script("gate_substance.py")
    commands, malformed = prompt_parser.parse_verification_lines(
        "- Run: test -f MISSING.md\n"
        "- Run: pytest --junitxml=report.xml\n"
        "- Run: cp missing-input.txt generated-output.txt\n"
        "- Run: touch touched.txt\n"
        "- Run: tee tee-output.txt\n"
        "- Run: mkdir generated-dir.txt\n"
        "- Run: rm obsolete.txt\n"
        "- Run: install source.txt installed-output.txt\n"
        "- Run: mv move-source.txt move-output.txt\n"
        "- Run: ls listed.txt\n"
        "- Run: stat stated.txt\n"
        "- Run: file typed.txt\n"
        "- Run: awk -f missing.awk input.txt\n"
        "- Run: find missing -type f\n"
        "- Run: tar -cf out.tar input\n"
    )

    assert malformed == []
    assert [substance.command_paths(command) for command in commands] == [
        ["MISSING.md"],
        ["report.xml"],
        ["missing-input.txt", "generated-output.txt"],
        ["touched.txt"],
        ["tee-output.txt"],
        ["generated-dir.txt"],
        ["obsolete.txt"],
        ["source.txt", "installed-output.txt"],
        ["move-source.txt", "move-output.txt"],
        ["listed.txt"],
        ["stated.txt"],
        ["typed.txt"],
        ["missing.awk", "input.txt"],
        ["missing"],
        ["out.tar", "input"],
    ]


def test_substance_command_paths_extract_extensionless_file_command_operands() -> None:
    substance = load_script("gate_substance.py")
    commands, malformed = prompt_parser.parse_verification_lines(
        "- Run: cat missing\n"
        "- Run: ls missing\n"
        "- Run: stat missing\n"
        "- Run: test -f missing\n"
        "- Run: mkdir generated-dir\n"
        "- Run: touch stamp\n"
        "- Run: rm obsolete\n"
        "- Run: mv source outdir\n"
        "- Run: install source bin\n"
        "- Run: cp source outdir/\n"
        "- Run: cp -t outdir source\n"
        "- Run: cp --target-directory=outdir source\n"
        "- Run: tee transcript\n"
    )

    assert malformed == []
    assert [substance.command_paths(command) for command in commands] == [
        ["missing"],
        ["missing"],
        ["missing"],
        ["missing"],
        ["generated-dir"],
        ["stamp"],
        ["obsolete"],
        ["source", "outdir"],
        ["source", "bin"],
        ["source", "outdir/"],
        ["outdir", "source"],
        ["outdir", "source"],
        ["transcript"],
    ]


def test_substance_command_paths_extract_extensionless_interpreter_operands() -> None:
    substance = load_script("gate_substance.py")
    commands, malformed = prompt_parser.parse_verification_lines(
        "- Run: bash missing-script\n"
        "- Run: sh script\n"
        "- Run: python3 tool\n"
        "- Run: python runner\n"
        "- Run: perl checker\n"
        "- Run: ruby task\n"
        "- Run: node cli\n"
        "- Run: bash -c 'echo ok'\n"
        "- Run: python3 -m http.server\n"
        "- Run: node -e 'console.log(1)'\n"
    )

    assert malformed == []
    assert [substance.command_paths(command) for command in commands] == [
        ["missing-script"],
        ["script"],
        ["tool"],
        ["runner"],
        ["checker"],
        ["task"],
        ["cli"],
        [],
        [],
        [],
    ]


def test_substance_command_path_refs_keep_same_path_read_and_write_roles() -> None:
    substance = load_script("gate_substance.py")
    commands, malformed = prompt_parser.parse_verification_lines("- Run: cp file.txt file.txt\n")

    assert malformed == []
    assert substance.command_path_refs(commands[0]) == [
        ("file.txt", substance.PATH_REF_READ),
        ("file.txt", substance.PATH_REF_WRITE),
    ]


def test_substance_command_path_refs_classify_awk_find_tar_and_mv_operands() -> None:
    substance = load_script("gate_substance.py")
    commands, malformed = prompt_parser.parse_verification_lines(
        "- Run: awk -f missing.awk input.txt\n"
        "- Run: awk '{print}' input.txt\n"
        "- Run: find missing -type f\n"
        "- Run: tar -cf out.tar input\n"
        "- Run: mv source outdir\n"
    )

    assert malformed == []
    assert [substance.command_path_refs(command) for command in commands] == [
        [("missing.awk", substance.PATH_REF_READ), ("input.txt", substance.PATH_REF_READ)],
        [("input.txt", substance.PATH_REF_READ)],
        [("missing", substance.PATH_REF_READ)],
        [("out.tar", substance.PATH_REF_WRITE), ("input", substance.PATH_REF_READ)],
        [("source", substance.PATH_REF_DELETE), ("outdir", substance.PATH_REF_WRITE)],
    ]


def test_substance_command_paths_extract_attached_target_directory_option() -> None:
    substance = load_script("gate_substance.py")
    commands, malformed = prompt_parser.parse_verification_lines(
        "- Run: cp -toutdir source\n"
        "- Run: cp -t./outdir/ source\n"
    )

    assert malformed == []
    assert [substance.command_path_refs(command) for command in commands] == [
        [("outdir", substance.PATH_REF_WRITE), ("source", substance.PATH_REF_READ)],
        [("./outdir/", substance.PATH_REF_WRITE), ("source", substance.PATH_REF_READ)],
    ]


def test_substance_command_paths_ignore_python_modules_and_dotted_option_values() -> None:
    substance = load_script("gate_substance.py")
    commands, malformed = prompt_parser.parse_verification_lines(
        "- Run: python3 -m http.server --help\n"
        "- Run: tool --format json.lines\n"
        "- Run: pytest --junitxml=report.xml\n"
    )

    assert malformed == []
    assert [substance.command_paths(command) for command in commands] == [
        [],
        [],
        ["report.xml"],
    ]


def test_substance_shell_builtins_do_not_include_cat() -> None:
    substance = load_script("gate_substance.py")
    assert "cat" not in substance.SHELL_BUILTINS


def test_substance_gate_blocks_missing_nested_bash_c_binary(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    source = tmp_path / "source.txt"
    source.write_text("ok\n")
    prompt.write_text(
        "Role: Verification command engineer\n\n"
        "Objective: Validate nested shell command binaries.\n\n"
        "Owned paths:\n"
        "- ./source.txt (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Check the nested command.\n"
        "2. Write the report.\n\n"
        "Verification:\n"
        "- Run: bash -c 'missing_nested_binary_for_gate'\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n\n"
        "Self-Challenge:\n"
        "- Did nested command validation run?\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert any("Binary is unavailable: missing_nested_binary_for_gate" in error for error in report["errors"])


def test_substance_gate_skips_declared_glob_existence_with_warning(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    source = tmp_path / "source.txt"
    source.write_text("ok\n")
    prompt.write_text(
        "Role: Verification command engineer\n\n"
        "Objective: Validate glob command paths without literal path failures.\n\n"
        "Owned paths:\n"
        "- ./source.txt (READ)\n"
        "- ./*.txt (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Check matching files.\n"
        "2. Write the report.\n\n"
        "Verification:\n"
        "- Run: ls ./*.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n\n"
        "Self-Challenge:\n"
        "- Did glob validation avoid literal path checks?\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 0, report
    assert "Glob pattern — existence not verified" in report["warnings"]


def test_substance_gate_requires_glob_owned_path_declaration(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    source = tmp_path / "source.txt"
    source.write_text("ok\n")
    prompt.write_text(
        "Role: Verification command engineer\n\n"
        "Objective: Validate glob command paths require explicit ownership.\n\n"
        "Owned paths:\n"
        "- ./source.txt (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Check matching files.\n"
        "2. Write the report.\n\n"
        "Verification:\n"
        "- Run: cat ./*.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n\n"
        "Self-Challenge:\n"
        "- Did glob validation require ownership?\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1, report
    assert "Glob pattern — existence not verified" in report["warnings"]
    assert "Verification path must be declared in Owned paths: ./*.txt" in "\n".join(report["errors"])


def test_form_gate_blocks_below_threshold(tmp_path: Path) -> None:
    prompt = tmp_path / "thin-prompt.txt"
    prompt.write_text("Role: Packaging engineer\n")
    result = run(sys.executable, "gate_form.py", str(prompt))
    assert result.returncode == 1
    assert "requires 7/9" in result.stdout


def test_substance_gate_blocks_duplicate_sections(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        valid_prompt_text()
        + "\nOutput Format:\n"
        + "- Write another report to `./examples/undeclared.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--strict")
    assert result.returncode == 1
    assert "Duplicate section: output format" in result.stdout


def test_section_headers_inside_fenced_code_blocks_are_not_duplicates(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("ok\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Role: Precision gate logic engineer\n\n"
        "Objective: Write a complete validation report.\n\n"
        "Owned paths:\n"
        "- ./source.txt (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Read the source.\n"
        "2. Write the report.\n\n"
        "Verification:\n"
        "- Run: test -f ./source.txt\n\n"
        "Output Format:\n"
        "- Include this literal fenced example:\n"
        "```\n"
        "Role: Example user-facing template\n"
        "Objective: Example objective text\n"
        "```\n"
        "- Write Markdown to `./generated-report.md`.\n\n"
        "Self-Challenge:\n"
        "- Did fenced headings stay literal?\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 0, report
    assert not any("Duplicate section" in error for error in report["errors"])


def test_section_headers_inside_tilde_fenced_code_blocks_are_not_duplicates(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("ok\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Role: Precision gate logic engineer\n\n"
        "Objective: Write a complete validation report.\n\n"
        "Owned paths:\n"
        "- ./source.txt (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Read the source.\n"
        "2. Write the report.\n\n"
        "Verification:\n"
        "- Run: test -f ./source.txt\n\n"
        "Output Format:\n"
        "- Include this literal fenced example:\n"
        "~~~\n"
        "Role: Example user-facing template\n"
        "Objective: Example objective text\n"
        "~~~\n"
        "- Write Markdown to `./generated-report.md`.\n\n"
        "Self-Challenge:\n"
        "- Did fenced headings stay literal?\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 0, report
    assert not any("Duplicate section" in error for error in report["errors"])


def test_output_paths_inside_tilde_fenced_code_blocks_are_ignored(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("ok\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Role: Precision gate logic engineer\n\n"
        "Objective: Write a complete validation report.\n\n"
        "Owned paths:\n"
        "- ./source.txt (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Read the source.\n"
        "2. Write the report.\n\n"
        "Verification:\n"
        "- Run: test -f ./source.txt\n\n"
        "Output Format:\n"
        "- Literal example only:\n"
        "~~~\n"
        "- Write Markdown to `./not-real-output.md`.\n"
        "~~~\n"
        "- Write Markdown to `./generated-report.md`.\n\n"
        "Self-Challenge:\n"
        "- Did fenced output paths stay literal?\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 0, report
    assert "./not-real-output.md" not in "\n".join(report["errors"])


def test_boundaries_section_is_optional_for_form_gate(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Role: Packaging engineer\n\n"
        "Objective: Write a complete validation report.\n\n"
        "Owned paths:\n"
        "- ./examples/good-prompt.txt (READ)\n"
        "- ./examples/generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Read the prompt.\n"
        "2. Create the report.\n\n"
        "Verification:\n"
        "- Run: test -f ./examples/good-prompt.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./examples/generated-report.md`.\n\n"
        "Self-Challenge:\n"
        "- Did every referenced path resolve correctly?\n"
    )
    result = run(sys.executable, "gate_form.py", str(prompt))
    assert result.returncode == 0, result.stdout + result.stderr


def test_good_prompt_passes_form_gate() -> None:
    result = run(sys.executable, "gate_form.py", "examples/good-prompt.txt")
    assert result.returncode == 0, result.stdout + result.stderr


def test_good_prompt_passes_substance_gate() -> None:
    result = run(sys.executable, "gate_substance.py", "examples/good-prompt.txt")
    assert result.returncode == 0, result.stdout + result.stderr


def test_gates_report_version() -> None:
    for gate in ("gate_form.py", "gate_substance.py"):
        result = run(sys.executable, gate, "--version")
        assert result.returncode == 0
        assert result.stdout.strip() == "two-gate-dispatch 1.0.0"


def test_form_gate_blocks_binary_input_without_traceback(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.bin"
    prompt.write_bytes(b"\xff\x00not text")
    result = run(sys.executable, "gate_form.py", str(prompt), "--json", "--strict")
    assert result.returncode == 1
    assert "Traceback" not in result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "error": "binary_or_undecodable_input",
        "status": "ERROR",
    }


def test_form_gate_rejects_files_over_5mb(tmp_path: Path) -> None:
    prompt = tmp_path / "large-prompt.txt"
    prompt.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    result = run(sys.executable, "gate_form.py", str(prompt), "--json")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert report["error"] == "file_too_large"
    assert report["status"] == "ERROR"
    assert report["max_bytes"] == 5 * 1024 * 1024


def test_substance_gate_rejects_files_over_5mb(tmp_path: Path) -> None:
    prompt = tmp_path / "large-prompt.txt"
    prompt.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert report["error"] == "file_too_large"
    assert report["status"] == "ERROR"
    assert report["max_bytes"] == 5 * 1024 * 1024


def test_json_mode_covers_argument_errors_and_missing_files() -> None:
    for gate in ("gate_form.py", "gate_substance.py"):
        missing_arg = run(sys.executable, gate, "--json")
        assert missing_arg.returncode == 2
        assert json.loads(missing_arg.stdout)["status"] == "ERROR"
        assert missing_arg.stderr == ""

        missing_file = run(sys.executable, gate, "does-not-exist.txt", "--json")
        assert missing_file.returncode == 1
        assert json.loads(missing_file.stdout)["status"] == "ERROR"
        assert missing_file.stderr == ""


def test_form_gate_does_not_count_empty_headings(tmp_path: Path) -> None:
    prompt = tmp_path / "headings-only.txt"
    prompt.write_text(
        "Role:\n"
        "Objective:\n"
        "Owned paths:\n"
        "Workflow:\n"
        "Verification:\n"
        "Output Format:\n"
        "Self-Challenge:\n"
    )
    result = run(sys.executable, "gate_form.py", str(prompt), "--json")
    report = json.loads(result.stdout)
    checks = {check["name"]: check["passed"] for check in report["checks"]}
    assert result.returncode == 1
    assert not report["passed"]
    assert checks["role"] is False
    assert checks["objective"] is False
    assert checks["owned_paths"] is False
    assert checks["self_challenge"] is False


def test_form_gate_empty_heading_stops_at_unknown_heading(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Role:\n"
        "Boundaries:\n"
        "- Do not edit unrelated files.\n\n"
        "Objective: Write a complete validation report.\n\n"
        "Owned paths:\n"
        "- ./examples/good-prompt.txt (READ)\n"
        "- ./examples/generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Read the prompt.\n"
        "2. Create the report.\n\n"
        "Verification:\n"
        "- Run: test -f ./examples/good-prompt.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./examples/generated-report.md`.\n\n"
        "Self-Challenge:\n"
        "- Did every referenced path resolve correctly?\n"
    )
    result = run(sys.executable, "gate_form.py", str(prompt), "--json")
    report = json.loads(result.stdout)
    checks = {check["name"]: check["passed"] for check in report["checks"]}
    assert result.returncode == 0
    assert checks["role"] is False
    assert report["missing_mandatory"] == ["role"]


def test_form_gate_blocks_missing_mandatory_even_when_score_is_high(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./examples/good-prompt.txt (READ)\n"
        "- ./examples/generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Read the prompt.\n"
        "2. Create the report.\n\n"
        "Objective: Write a complete validation report.\n\n"
        "Verification:\n"
        "- Run: test -f ./examples/good-prompt.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./examples/generated-report.md`.\n\n"
        "Self-Challenge:\n"
        "- Did every referenced path resolve correctly?\n"
    )
    result = run(sys.executable, "gate_form.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert report["score"] >= report["required"]
    assert report["strict"] is True
    assert report["missing_mandatory"] == ["role"]


def test_form_gate_blocks_placeholder_mandatory_content_even_when_score_is_high(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Role: placeholder\n\n"
        "Objective: placeholder\n\n"
        "Owned paths:\n"
        "- ./examples/good-prompt.txt (READ)\n"
        "- ./examples/generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Read the prompt.\n"
        "2. Create the report.\n\n"
        "Verification:\n\n"
        "Output Format:\n"
        "- Write Markdown to `./examples/generated-report.md`.\n\n"
        "Self-Challenge:\n"
        "- Did every referenced path resolve correctly?\n"
    )
    result = run(sys.executable, "gate_form.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert report["missing_mandatory"] == ["role", "objective", "verification"]


def test_form_gate_blocks_short_role_content_in_strict_mode(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(valid_prompt_text().replace("Role: Precision gate logic engineer", "Role: CLI dev"))
    result = run(sys.executable, "gate_form.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert report["missing_mandatory"] == ["role"]


def test_wrapper_blocks_empty_output_format_even_when_score_is_high(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(valid_prompt_text(output_format=""))
    result = run("bash", "dispatch-gate.sh", str(prompt))
    assert result.returncode == 1
    assert "BLOCKED: Gate 1 (Form) failed." in result.stdout
    assert "mandatory section lacks meaningful content: output_format" in result.stdout


def test_form_gate_only_exact_required_headings_count(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Role: Packaging engineer with enough detail.\n\n"
        "Objective: Write a complete validation report.\n\n"
        "Owned paths:\n"
        "- ./examples/good-prompt.txt (READ)\n"
        "- ./examples/generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Read the prompt.\n"
        "2. Create the report.\n\n"
        "Verification:\n"
        "- Run: test -f ./examples/good-prompt.txt\n\n"
        "Report: final response\n\n"
        "Final Response: final response\n\n"
        "Self-Challenge:\n"
        "- Did every referenced path resolve correctly?\n"
    )
    result = run(sys.executable, "gate_form.py", str(prompt), "--json")
    report = json.loads(result.stdout)
    checks = {check["name"]: check["passed"] for check in report["checks"]}
    assert result.returncode == 0
    assert checks["output_format"] is False


def test_substance_gate_fails_when_no_owned_paths_parse_in_strict_mode(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "\n"
        "Verification:\n"
        "- Run: test -d ./examples\n"
        "\n"
        "Output Format:\n"
        "- Report in final response.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "No owned paths parsed" in report["errors"]


def test_substance_gate_reports_malformed_owned_path_lines(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("content")
    prompt = tmp_path / "prompt.txt"
    bad_line = "- ./examples/good-prompt.txt READ"
    prompt.write_text(
        "Owned paths:\n"
        f"- {source} (READ)\n"
        f"{bad_line}\n"
        "\n"
        "Verification:\n"
        "- Run: test -d ./examples\n"
        "\n"
        "Output Format:\n"
        "- Report in final response.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert bad_line in report["malformed_owned_path_lines"]
    assert f"line 3: Malformed owned path line: {bad_line}" in report["errors"]


def test_substance_gate_reports_missing_required_sections(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        f"- {source} (READ)\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert report["missing_sections"] == ["Verification", "Output Format"]
    assert "line 3: Missing required section: Verification" in report["errors"]
    assert "line 3: Missing required section: Output Format" in report["errors"]


def test_contradictory_output_parent_is_blocked() -> None:
    result = run(sys.executable, "gate_substance.py", "examples/bad-contradiction.txt")
    assert result.returncode == 1
    assert "has no writable parent" in result.stdout


def test_read_owned_path_cannot_be_used_as_output(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (READ)\n\n"
        "Verification:\n"
        "- Run: test -f ./input.txt\n\n"
        "Output Format:\n"
        "- Overwrite `./input.txt`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "line 8: READ-owned path cannot be used as output target." in report["errors"]


def test_read_owned_path_output_conflict_uses_resolved_paths(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (READ)\n\n"
        "Verification:\n"
        "- Run: test -f ./input.txt\n\n"
        "Output Format:\n"
        f"- Overwrite `{source}`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "READ-owned path cannot be used as output target." in "\n".join(report["errors"])


def test_delete_owned_path_cannot_be_used_as_output(tmp_path: Path) -> None:
    target = tmp_path / "obsolete-report.md"
    target.write_text("old\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./obsolete-report.md (DELETE)\n\n"
        "Verification:\n"
        "- Run: test -f ./obsolete-report.md\n\n"
        "Output Format:\n"
        "- Delete output at `./obsolete-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert (
        "line 8: Output path must be declared with CREATE, WRITE, or APPEND in Owned paths: ./obsolete-report.md"
        in report["errors"]
    )


def test_output_path_must_be_declared_in_owned_paths(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (READ)\n\n"
        "Verification:\n"
        "- Run: test -f ./input.txt\n\n"
        "Output Format:\n"
        "- Write `./report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "line 8: Output path must be declared with a write intent in Owned paths: ./report.md" in report["errors"]


def test_nested_output_format_label_does_not_hide_undeclared_path(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths: - ./declared.md (CREATE)\n"
        "Verification: - Run: test -d .\n"
        "Output Format:\n"
        "Details:\n"
        "- Write Markdown to `./undeclared.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "line 5: Output path must be declared with a write intent in Owned paths: ./undeclared.md" in report["errors"]


def test_markdown_subheading_inside_output_format_does_not_hide_undeclared_path(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths: - ./declared.md (CREATE)\n"
        "Verification: - Run: test -d .\n"
        "Output Format:\n"
        "## Files\n"
        "- Write Markdown to `./undeclared.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "line 5: Output path must be declared with a write intent in Owned paths: ./undeclared.md" in report["errors"]


def test_markdown_subheading_inside_verification_does_not_hide_missing_path(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "## Extra Checks\n"
        "- Run: test -f ./missing-required.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "line 6: Verification path is missing: ./missing-required.txt" in report["errors"]


def test_inline_section_diagnostics_use_heading_line(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths: - ./bad.txt (MOVE)\n"
        "Verification: - Run: test -f ./missing.txt\n"
        "Output Format: - Write `./undeclared.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "line 1: Malformed owned path line: - ./bad.txt (MOVE)" in report["errors"]
    assert "line 2: Verification path is missing: ./missing.txt" in report["errors"]
    assert "line 3: Output path must be declared with a write intent in Owned paths: ./undeclared.md" in report["errors"]


def test_backticked_bare_output_path_must_be_declared_in_owned_paths(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (READ)\n\n"
        "Verification:\n"
        "- Run: test -f ./input.txt\n\n"
        "Output Format:\n"
        "- Write `report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "line 8: Output path must be declared with a write intent in Owned paths: report.md" in report["errors"]


def test_backticked_extensionless_output_path_must_be_declared_in_owned_paths(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (READ)\n\n"
        "Verification:\n"
        "- Run: test -f ./input.txt\n\n"
        "Output Format:\n"
        "- Write `report`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "line 8: Output path must be declared with a write intent in Owned paths: report" in report["errors"]


def test_parent_relative_output_path_must_be_declared(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (READ)\n\n"
        "Verification:\n"
        "- Run: test -f ./input.txt\n\n"
        "Output Format:\n"
        "- Write `../undeclared.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "Output path must be declared with a write intent in Owned paths: ../undeclared.md" in "\n".join(report["errors"])


def test_declared_home_relative_output_path_is_allowed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ~/declared.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: test -d .\n\n"
        "Output Format:\n"
        "- Write `~/declared.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_unbackticked_slash_relative_output_paths_must_be_declared(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (READ)\n\n"
        "Verification:\n"
        "- Run: test -f ./input.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to reports/final.md.\n"
        "- Output JSON to results/out.json,\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    errors = "\n".join(report["errors"])
    assert "Output path must be declared with a write intent in Owned paths: reports/final.md" in errors
    assert "Output path must be declared with a write intent in Owned paths: results/out.json" in errors


def test_unbackticked_output_path_is_validated_against_owned_paths(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (READ)\n"
        "- ./report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: test -f ./input.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to ./report.md\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_file_output_to_existing_directory_is_blocked(tmp_path: Path) -> None:
    outdir = tmp_path / "outdir"
    outdir.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./outdir (CREATE)\n\n"
        "Verification:\n"
        "- Run: test -d .\n\n"
        "Output Format:\n"
        "- Write Markdown to ./outdir\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "line 8: Output path resolves to an existing directory: ./outdir" in report["errors"]


def test_file_output_to_existing_directory_is_not_bypassed_by_directory_word(tmp_path: Path) -> None:
    outdir = tmp_path / "outdir"
    outdir.mkdir()
    for output_line in ("- Write Markdown directory to ./outdir\n", "- Write Markdown directory output to ./outdir\n"):
        prompt = tmp_path / "prompt.txt"
        prompt.write_text(
            "Owned paths:\n"
            "- ./outdir (CREATE)\n\n"
            "Verification:\n"
            "- Run: test -d .\n\n"
            "Output Format:\n"
            f"{output_line}"
        )
        result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
        report = json.loads(result.stdout)
        assert result.returncode == 1
        assert "line 8: Output path resolves to an existing directory: ./outdir" in report["errors"]


def test_multiline_backticked_output_path_is_validated(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("content")
    reports = tmp_path / "reports"
    reports.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (READ)\n"
        "- ./reports/generated report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: test -f ./input.txt\n\n"
        "Output Format:\n"
        "- Write report to `./reports/generated\n"
        "  report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_read_path_is_blocked() -> None:
    result = run(sys.executable, "gate_substance.py", "examples/bad-missing-path.txt")
    assert result.returncode == 1
    assert "missing for READ" in result.stdout


def test_missing_verification_path_is_blocked(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Verification:\n"
        "- Run: test -f ./project/not-present.md\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt))
    assert result.returncode == 1
    assert "Verification path is missing" in result.stdout


def test_missing_bare_filename_verification_path_is_blocked(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: test -f MISSING.md\n"
        "- Run: pytest --junitxml=report.xml\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    errors = "\n".join(report["errors"])
    assert "Verification path is missing: MISSING.md" in errors
    assert "Verification path is missing: report.xml" in errors


def test_existing_bare_filename_verification_path_must_be_declared(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: test -f README.md\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "line 5: Verification path must be declared in Owned paths: README.md" in report["errors"]


def test_existing_slash_verification_path_must_be_declared(tmp_path: Path) -> None:
    (tmp_path / "existing.txt").write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: test -f ./existing.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "line 5: Verification path must be declared in Owned paths: ./existing.txt" in report["errors"]


def test_quoted_existing_verification_path_must_be_declared(tmp_path: Path) -> None:
    (tmp_path / "quoted.txt").write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: cat './quoted.txt'\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "line 5: Verification path must be declared in Owned paths: ./quoted.txt" in report["errors"]


def test_python_module_and_dotted_option_values_are_not_verification_paths(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: python3 -m http.server --help\n"
        "- Run: python3 -m pytest --format json.lines\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 0, report


def test_created_verification_path_is_not_required_before_dispatch(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Role: Precision gate logic engineer\n\n"
        "Objective: Write a complete validation report.\n\n"
        "Owned paths:\n"
        "- ./reports/result.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Create the report.\n\n"
        "Verification:\n"
        "- Run: test -f ./reports/result.md\n\n"
        "Output Format:\n"
        "- Write Markdown to `./reports/result.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_read_verification_command_rejects_create_intent(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (CREATE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: cat ./input.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert (
        "line 6: Verification path has incompatible Owned paths intent "
        "for this command context: ./input.txt (CREATE)"
    ) in report["errors"]


def test_write_verification_target_accepts_create_intent(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./report.xml (CREATE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: pytest --junitxml=report.xml\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 0, report


def test_home_relative_backticked_path_warns(tmp_path: Path) -> None:
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "good-prompt.txt").write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./examples/good-prompt.txt (READ)\n\n"
        "Workflow:\n"
        "1. Informational: compare the local note path `~/worker-specific/report.md`.\n\n"
        "Verification:\n"
        "- Run: test -f ./examples/good-prompt.txt\n\n"
        "Output Format:\n"
        "- Report in final response.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt))
    assert result.returncode == 0
    assert "Home-relative path may resolve differently" in result.stdout


def test_workflow_read_path_must_be_declared_in_owned_paths(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Read ./missing-input.txt before writing the report.\n\n"
        "Verification:\n"
        "- Run: test -d .\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert (
        "line 5: Referenced path must be declared in Owned paths or marked informational: ./missing-input.txt"
        in report["errors"]
    )


def test_workflow_bare_filename_reference_must_be_declared(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Read README.md before writing the report.\n\n"
        "Verification:\n"
        "- Run: test -d .\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "line 5: Referenced path must be declared in Owned paths or marked informational: README.md" in report["errors"]


def test_workflow_read_path_requires_compatible_owned_intent(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (CREATE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Read ./input.txt before writing the report.\n\n"
        "Verification:\n"
        "- Run: test -d .\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert (
        "line 6: Referenced path has incompatible Owned paths intent for this context: ./input.txt (CREATE)"
        in report["errors"]
    )


def test_informational_workflow_path_is_not_required_in_owned_paths(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Workflow:\n"
        "1. Informational: compare against ./not-owned-example.txt if it exists.\n\n"
        "Verification:\n"
        "- Run: test -d .\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 0, report


def test_relative_paths_are_anchored_to_prompt_directory_from_any_cwd(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompt dir"
    prompt_dir.mkdir()
    (prompt_dir / "local input.txt").write_text("content")
    prompt = prompt_dir / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./local input.txt (READ)\n"
        "- ./out.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: test -f './local input.txt'\n\n"
        "Output Format:\n"
        "- Write report to `./out.md`.\n"
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "gate_substance.py"), str(prompt), "--json"],
        cwd="/",
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["base_dir"] == str(prompt_dir.resolve())


def test_relative_verification_binary_is_checked_from_prompt_directory(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompt-dir"
    prompt_dir.mkdir()
    check_script = prompt_dir / "check.sh"
    check_script.write_text("#!/bin/sh\nexit 0\n")
    check_script.chmod(0o755)
    prompt = prompt_dir / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./check.sh (READ)\n"
        "- ./out.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: ./check.sh\n\n"
        "Output Format:\n"
        "- Write report to `./out.md`.\n"
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "gate_substance.py"), str(prompt), "--json"],
        cwd="/",
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any("Binary is unavailable: ./check.sh" in error for error in report["errors"])


def test_quoted_verification_path_with_spaces_parses(tmp_path: Path) -> None:
    spaced_dir = tmp_path / "my dir"
    spaced_dir.mkdir()
    target = spaced_dir / "file.py"
    target.write_text("print('ok')\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./my dir/file.py (READ)\n\n"
        "Verification:\n"
        "- Run: test -f \"./my dir/file.py\"\n\n"
        "Output Format:\n"
        "- Report in final response.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json")
    assert result.returncode == 0, result.stdout + result.stderr


def test_nested_shell_verification_paths_are_blocked_for_both_quote_styles(tmp_path: Path) -> None:
    for command in (
        "bash -c 'test -f ./hidden.txt'",
        'bash -c "test -f ./hidden.txt"',
    ):
        prompt = tmp_path / "prompt.txt"
        prompt.write_text(
            "Owned paths:\n"
            "- ./generated-report.md (CREATE)\n\n"
            "Verification:\n"
            f"- Run: {command}\n\n"
            "Output Format:\n"
            "- Write Markdown to `./generated-report.md`.\n"
        )
        result = run(sys.executable, "gate_substance.py", str(prompt))
        assert result.returncode == 1
        assert "Verification path is missing: ./hidden.txt" in result.stdout


def test_nested_shell_bare_filename_paths_are_blocked_for_both_quote_styles(tmp_path: Path) -> None:
    for command, expected in (
        ("bash -c 'test -f MISSING.md'", "MISSING.md"),
        ('bash -c "test -f MISSING.md"', "MISSING.md"),
        ("bash -c 'cat README.md'", "README.md"),
        ('bash -c "cat README.md"', "README.md"),
    ):
        prompt = tmp_path / "prompt.txt"
        prompt.write_text(
            "Owned paths:\n"
            "- ./generated-report.md (CREATE)\n\n"
            "Verification:\n"
            f"- Run: {command}\n\n"
            "Output Format:\n"
            "- Write Markdown to `./generated-report.md`.\n"
        )
        result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
        report = json.loads(result.stdout)
        errors = "\n".join(report["errors"])
        assert result.returncode == 1, command
        assert f"Verification path must be declared in Owned paths: {expected}" in errors
        assert f"Verification path is missing: {expected}" in errors


def test_separated_shell_redirects_require_compatible_owned_paths(tmp_path: Path) -> None:
    (tmp_path / "input.txt").write_text("input\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./report.xml (CREATE)\n"
        "- ./append.log (APPEND)\n"
        "- ./stderr.log (WRITE)\n"
        "- ./input.txt (READ)\n\n"
        "Verification:\n"
        "- Run: echo ok > report.xml\n"
        "- Run: echo ok >> append.log\n"
        "- Run: echo ok 2> stderr.log\n"
        "- Run: cat < input.txt\n"
        "- Run: cat << report.xml\n\n"
        "Output Format:\n"
        "- Write Markdown to `./report.xml`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_separated_shell_redirect_intent_mismatches_are_blocked(tmp_path: Path) -> None:
    (tmp_path / "report.xml").write_text("old\n")
    (tmp_path / "append.log").write_text("old\n")
    (tmp_path / "input.txt").write_text("input\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./report.xml (READ)\n"
        "- ./append.log (READ)\n"
        "- ./input.txt (CREATE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: echo ok > report.xml\n"
        "- Run: echo ok >> append.log\n"
        "- Run: cat < input.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path has incompatible Owned paths intent for this command context: report.xml (READ)" in errors
    assert "Verification path has incompatible Owned paths intent for this command context: append.log (READ)" in errors
    assert "Verification path has incompatible Owned paths intent for this command context: input.txt (CREATE)" in errors


def test_extensionless_shell_redirects_require_declarations_and_matching_intents(tmp_path: Path) -> None:
    (tmp_path / "report").write_text("old\n")
    (tmp_path / "input").write_text("input\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./report (READ)\n"
        "- ./input (CREATE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: echo ok > report\n"
        "- Run: cat < input\n"
        "- Run: echo ok 2> stderr\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path has incompatible Owned paths intent for this command context: report (READ)" in errors
    assert "Verification path has incompatible Owned paths intent for this command context: input (CREATE)" in errors
    assert "Verification path must be declared in Owned paths: stderr" in errors


def test_extensionless_shell_redirects_pass_with_compatible_owned_paths(tmp_path: Path) -> None:
    (tmp_path / "input").write_text("input\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./report (CREATE)\n"
        "- ./append (APPEND)\n"
        "- ./stderr (WRITE)\n"
        "- ./input (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: echo ok > report\n"
        "- Run: echo ok >> append\n"
        "- Run: echo ok 2> stderr\n"
        "- Run: cat < input\n"
        "- Run: cat << report\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_common_file_command_operands_require_matching_intents(tmp_path: Path) -> None:
    (tmp_path / "missing-input.txt").write_text("input\n")
    (tmp_path / "source.txt").write_text("input\n")
    (tmp_path / "move-source.txt").write_text("input\n")
    (tmp_path / "listed.txt").write_text("input\n")
    (tmp_path / "stated.txt").write_text("input\n")
    (tmp_path / "typed.txt").write_text("input\n")
    (tmp_path / "obsolete.txt").write_text("old\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./missing-input.txt (READ)\n"
        "- ./generated-output.txt (CREATE)\n"
        "- ./source.txt (READ)\n"
        "- ./installed-output.txt (WRITE)\n"
        "- ./move-source.txt (MODIFY)\n"
        "- ./move-output.txt (WRITE)\n"
        "- ./listed.txt (READ)\n"
        "- ./stated.txt (READ)\n"
        "- ./typed.txt (READ)\n"
        "- ./touched.txt (CREATE)\n"
        "- ./tee-output.txt (APPEND)\n"
        "- ./generated-dir.txt (CREATE)\n"
        "- ./obsolete.txt (DELETE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: cp missing-input.txt generated-output.txt\n"
        "- Run: install source.txt installed-output.txt\n"
        "- Run: touch touched.txt\n"
        "- Run: tee tee-output.txt\n"
        "- Run: mkdir generated-dir.txt\n"
        "- Run: rm obsolete.txt\n"
        "- Run: mv move-source.txt move-output.txt\n"
        "- Run: ls listed.txt\n"
        "- Run: stat stated.txt\n"
        "- Run: file typed.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_extensionless_file_command_operands_require_matching_intents(tmp_path: Path) -> None:
    (tmp_path / "source").write_text("input\n")
    (tmp_path / "move-source").write_text("input\n")
    (tmp_path / "obsolete").write_text("old\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-dir (CREATE)\n"
        "- ./stamp (CREATE)\n"
        "- ./obsolete (DELETE)\n"
        "- ./source (READ)\n"
        "- ./bin (WRITE)\n"
        "- ./move-source (MODIFY)\n"
        "- ./outdir (WRITE)\n"
        "- ./transcript (APPEND)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: mkdir generated-dir\n"
        "- Run: touch stamp\n"
        "- Run: rm obsolete\n"
        "- Run: install source bin\n"
        "- Run: mv move-source outdir\n"
        "- Run: tee transcript\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_extensionless_file_command_operands_missing_and_intent_errors_are_blocked(tmp_path: Path) -> None:
    (tmp_path / "source").write_text("input\n")
    (tmp_path / "obsolete").write_text("old\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./source (CREATE)\n"
        "- ./outdir (READ)\n"
        "- ./obsolete (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: cp source outdir\n"
        "- Run: mkdir generated-dir\n"
        "- Run: touch stamp\n"
        "- Run: rm obsolete\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path has incompatible Owned paths intent for this command context: source (CREATE)" in errors
    assert "Verification path has incompatible Owned paths intent for this command context: outdir (READ)" in errors
    assert "Verification path must be declared in Owned paths: generated-dir" in errors
    assert "Verification path must be declared in Owned paths: stamp" in errors
    assert "Verification path has incompatible Owned paths intent for this command context: obsolete (READ)" in errors


def test_extensionless_read_only_operands_missing_and_intent_errors_are_blocked(tmp_path: Path) -> None:
    (tmp_path / "existing").write_text("input\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./existing (CREATE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: cat missing\n"
        "- Run: ls missing\n"
        "- Run: stat missing\n"
        "- Run: test -f missing\n"
        "- Run: cat existing\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path must be declared in Owned paths: missing" in errors
    assert "Verification path is missing: missing" in errors
    assert "Verification path has incompatible Owned paths intent for this command context: existing (CREATE)" in errors


def test_grep_pattern_is_not_treated_as_extensionless_file(tmp_path: Path) -> None:
    (tmp_path / "input").write_text("content\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: grep content input\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_grep_extensionless_file_operand_still_requires_declaration(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: grep content missinginput\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path must be declared in Owned paths: missinginput" in errors
    assert "Verification path must be declared in Owned paths: content" not in errors


def test_read_command_option_operands_are_not_treated_as_files(tmp_path: Path) -> None:
    for name in ("input", "log", "file"):
        (tmp_path / name).write_text("one\ntwo\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input (READ)\n"
        "- ./log (READ)\n"
        "- ./file (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: head -n 1 input\n"
        "- Run: tail -n 20 log\n"
        "- Run: sed -n 1,5p file\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_read_command_option_file_operands_still_require_declaration(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: head -n 1 missinginput\n"
        "- Run: sed -n 1,5p missingfile\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path must be declared in Owned paths: missinginput" in errors
    assert "Verification path must be declared in Owned paths: missingfile" in errors
    assert "Verification path must be declared in Owned paths: 1" not in errors
    assert "Verification path must be declared in Owned paths: 1,5p" not in errors


def test_sort_output_option_uses_write_intent_and_reads_input(tmp_path: Path) -> None:
    (tmp_path / "input").write_text("b\na\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input (READ)\n"
        "- ./sorted (CREATE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: sort -o sorted input\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_sort_output_option_rejects_read_intent_for_output(tmp_path: Path) -> None:
    (tmp_path / "input").write_text("b\na\n")
    (tmp_path / "sorted").write_text("old\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input (READ)\n"
        "- ./sorted (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: sort -o sorted input\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path has incompatible Owned paths intent for this command context: sorted (READ)" in errors


def test_mv_source_requires_write_compatible_intent(tmp_path: Path) -> None:
    (tmp_path / "move-source").write_text("input\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./move-source (READ)\n"
        "- ./outdir (WRITE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: mv move-source outdir\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path has incompatible Owned paths intent for this command context: move-source (READ)" in errors


def test_mv_source_passes_with_modify_intent(tmp_path: Path) -> None:
    (tmp_path / "move-source").write_text("input\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./move-source (MODIFY)\n"
        "- ./outdir (WRITE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: mv move-source outdir\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_mv_source_must_exist_even_with_create_intent(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./source (CREATE)\n"
        "- ./outdir (CREATE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: mv source outdir\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path has incompatible Owned paths intent for this command context: source (CREATE)" in errors
    assert "Verification path is missing: source" in errors


def test_mv_source_passes_with_modify_intent_and_create_destination(tmp_path: Path) -> None:
    (tmp_path / "source").write_text("input\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./source (MODIFY)\n"
        "- ./outdir (CREATE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: mv source outdir\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_noclobber_override_redirect_requires_declared_output(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: echo ok >| tee\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path must be declared in Owned paths: tee" in errors


def test_noclobber_override_redirect_passes_with_write_intent(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./tee (CREATE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: echo ok >| tee\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_read_write_redirect_requires_write_compatible_intent(tmp_path: Path) -> None:
    (tmp_path / "state").write_text("old\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./state (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: true <> state\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path has incompatible Owned paths intent for this command context: state (READ)" in errors


def test_read_write_redirect_passes_with_modify_intent(tmp_path: Path) -> None:
    (tmp_path / "state").write_text("old\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./state (MODIFY)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: true <> state\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_attached_sort_output_option_requires_declared_output(tmp_path: Path) -> None:
    (tmp_path / "input").write_text("b\na\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: sort -osorted input\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path must be declared in Owned paths: sorted" in errors


def test_attached_sort_output_option_passes_with_write_intent(tmp_path: Path) -> None:
    (tmp_path / "input").write_text("b\na\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input (READ)\n"
        "- ./sorted (CREATE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: sort -osorted input\n"
        "- Run: sort --output=sorted input\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_cp_and_mv_trailing_slash_destinations_require_write_intent(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("input\n")
    (tmp_path / "move-source").write_text("input\n")
    (tmp_path / "outdir").mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./source.txt (READ)\n"
        "- ./move-source (MODIFY)\n"
        "- ./outdir (WRITE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: cp source.txt outdir/\n"
        "- Run: mv move-source ./outdir/\n"
        "- Run: cp -t outdir source.txt\n"
        "- Run: cp -toutdir source.txt\n"
        "- Run: cp -t./outdir/ source.txt\n"
        "- Run: cp --target-directory=./outdir source.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_cp_trailing_slash_create_destination_must_exist_as_directory(tmp_path: Path) -> None:
    (tmp_path / "source").write_text("input\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./source (READ)\n"
        "- ./outdir (CREATE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: cp source outdir/\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "Verification path is missing: outdir/" in "\n".join(report["errors"])


def test_same_path_copy_requires_read_and_write_owned_intents(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("input\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./file.txt (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: cp file.txt file.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path has incompatible Owned paths intent for this command context: file.txt (READ)" in errors


def test_common_file_command_missing_and_intent_errors_are_blocked(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("input\n")
    (tmp_path / "generated-output.txt").write_text("old\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./source.txt (CREATE)\n"
        "- ./generated-output.txt (READ)\n"
        "- ./obsolete.txt (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: cp source.txt generated-output.txt\n"
        "- Run: cp missing-input.txt missing-output.txt\n"
        "- Run: ls missing-listed.txt\n"
        "- Run: rm obsolete.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path has incompatible Owned paths intent for this command context: source.txt (CREATE)" in errors
    assert "Verification path has incompatible Owned paths intent for this command context: generated-output.txt (READ)" in errors
    assert "Verification path must be declared in Owned paths: missing-input.txt" in errors
    assert "Verification path is missing: missing-input.txt" in errors
    assert "Verification path must be declared in Owned paths: missing-output.txt" in errors
    assert "Verification path is missing: missing-output.txt" in errors
    assert "Verification path must be declared in Owned paths: missing-listed.txt" in errors
    assert "Verification path is missing: missing-listed.txt" in errors
    assert "Verification path has incompatible Owned paths intent for this command context: obsolete.txt (READ)" in errors


def test_awk_find_tar_operands_require_matching_intents(tmp_path: Path) -> None:
    for name in ("script.awk", "input.txt", "search-root", "tar-input"):
        path = tmp_path / name
        if name == "search-root":
            path.mkdir()
        else:
            path.write_text("input\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./script.awk (READ)\n"
        "- ./input.txt (READ)\n"
        "- ./search-root (READ)\n"
        "- ./out.tar (CREATE)\n"
        "- ./tar-input (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: awk -f script.awk input.txt\n"
        "- Run: find search-root -type f\n"
        "- Run: tar -cf out.tar tar-input\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_awk_find_tar_missing_and_intent_errors_are_blocked(tmp_path: Path) -> None:
    (tmp_path / "script.awk").write_text("{print}\n")
    (tmp_path / "tar-input").write_text("input\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./script.awk (CREATE)\n"
        "- ./out.tar (READ)\n"
        "- ./tar-input (CREATE)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: awk -f script.awk missing-input.txt\n"
        "- Run: find missing-root -type f\n"
        "- Run: tar -cf out.tar tar-input\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path has incompatible Owned paths intent for this command context: script.awk (CREATE)" in errors
    assert "Verification path must be declared in Owned paths: missing-input.txt" in errors
    assert "Verification path must be declared in Owned paths: missing-root" in errors
    assert "Verification path has incompatible Owned paths intent for this command context: out.tar (READ)" in errors
    assert "Verification path has incompatible Owned paths intent for this command context: tar-input (CREATE)" in errors


def test_shell_separator_operands_use_each_segment_command_context(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("input\n")
    (tmp_path / "input.txt").write_text("input\n")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./source.txt (READ)\n"
        "- ./input.txt (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: test -f ./source.txt && cat MISSING.md\n"
        "- Run: printf ok | tee report.xml\n"
        "- Run: test -f ./input.txt; cp input.txt output.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path must be declared in Owned paths: MISSING.md" in errors
    assert "Verification path is missing: MISSING.md" in errors
    assert "Verification path must be declared in Owned paths: report.xml" in errors
    assert "Verification path must be declared in Owned paths: output.txt" in errors


def test_pytest_bare_collection_operands_are_validated(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: pytest tests -q\n"
        "- Run: pytest test_gates.py::case -q\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    errors = "\n".join(report["errors"])
    assert result.returncode == 1
    assert "Verification path must be declared in Owned paths: tests" in errors
    assert "Verification path is missing: tests" in errors
    assert "Verification path must be declared in Owned paths: test_gates.py::case" in errors
    assert "Verification path is missing: test_gates.py::case" in errors


def test_declared_existing_pytest_collection_and_option_values_pass(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./tests (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: pytest tests -q\n"
        "- Run: pytest -k smoke -m 'not slow' --tb short -q\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )

    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    assert result.returncode == 0, result.stdout + result.stderr


def test_substitution_bare_filename_operands_are_validated(tmp_path: Path) -> None:
    for command, warning in (
        ("echo $(cat MISSING.md)", "$() command substitution"),
        ("echo `cat MISSING.md`", "backtick substitution"),
        ("echo <(cat MISSING.md)", "<() process substitution"),
    ):
        prompt = tmp_path / "prompt.txt"
        prompt.write_text(
            "Owned paths:\n"
            "- ./generated-report.md (CREATE)\n\n"
            "Verification:\n"
            f"- Run: {command}\n\n"
            "Output Format:\n"
            "- Write Markdown to `./generated-report.md`.\n"
        )

        result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
        report = json.loads(result.stdout)
        errors = "\n".join(report["errors"])
        assert result.returncode == 1, command
        assert any(warning in item for item in report["warnings"])
        assert "Verification path must be declared in Owned paths: MISSING.md" in errors
        assert "Verification path is missing: MISSING.md" in errors


def test_nested_substitution_bare_filename_operands_are_validated(tmp_path: Path) -> None:
    for command, warning in (
        ("echo $(echo $(cat MISSING.md))", "$() command substitution"),
        ("echo $(cat $(cat LIST.md))", "$() command substitution"),
        ("echo `echo \\`cat MISSING.md\\``", "backtick substitution"),
    ):
        prompt = tmp_path / "prompt.txt"
        prompt.write_text(
            "Owned paths:\n"
            "- ./generated-report.md (CREATE)\n\n"
            "Verification:\n"
            f"- Run: {command}\n\n"
            "Output Format:\n"
            "- Write Markdown to `./generated-report.md`.\n"
        )

        result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
        report = json.loads(result.stdout)
        errors = "\n".join(report["errors"])
        assert result.returncode == 1, command
        assert any(warning in item for item in report["warnings"])
        assert "Verification path must be declared in Owned paths" in errors
        assert "Verification path is missing" in errors


def test_slash_relative_verification_operands_are_blocked(tmp_path: Path) -> None:
    for command, expected in (
        ("python3 scripts/missing-check.py", "scripts/missing-check.py"),
        ("pytest tests/missing_test_file.py", "tests/missing_test_file.py"),
        ("sh 'scripts/missing-check.sh'", "scripts/missing-check.sh"),
        ("bash -c 'python3 scripts/nested-missing.py'", "scripts/nested-missing.py"),
    ):
        prompt = tmp_path / "prompt.txt"
        prompt.write_text(
            "Owned paths:\n"
            "- ./generated-report.md (CREATE)\n\n"
            "Verification:\n"
            f"- Run: {command}\n\n"
            "Output Format:\n"
            "- Write Markdown to `./generated-report.md`.\n"
        )
        result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
        report = json.loads(result.stdout)
        assert result.returncode == 1, command
        assert f"Verification path is missing: {expected}" in "\n".join(report["errors"])


def test_extensionless_interpreter_operands_are_blocked(tmp_path: Path) -> None:
    for command, expected in (
        ("bash missing-script", "missing-script"),
        ("sh script", "script"),
        ("python3 tool", "tool"),
        ("perl checker", "checker"),
        ("ruby task", "task"),
        ("node cli", "cli"),
    ):
        prompt = tmp_path / "prompt.txt"
        prompt.write_text(
            "Owned paths:\n"
            "- ./generated-report.md (CREATE)\n\n"
            "Verification:\n"
            f"- Run: {command}\n\n"
            "Output Format:\n"
            "- Write Markdown to `./generated-report.md`.\n"
        )
        result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
        report = json.loads(result.stdout)
        assert result.returncode == 1, command
        assert f"Verification path must be declared in Owned paths: {expected}" in "\n".join(report["errors"])
        assert f"Verification path is missing: {expected}" in "\n".join(report["errors"])


def test_same_resolved_path_with_read_and_create_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (READ)\n"
        f"- {source} (CREATE)\n\n"
        "Verification:\n"
        "- Run: test -f ./input.txt\n\n"
        "Output Format:\n"
        "- Report in final response.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "conflicting intents: CREATE, READ" in "\n".join(report["errors"])


def test_same_resolved_path_with_read_and_modify_is_allowed(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (READ)\n"
        f"- {source} (MODIFY)\n\n"
        "Verification:\n"
        "- Run: test -f ./input.txt\n\n"
        "Output Format:\n"
        "- Report in final response.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json")
    assert result.returncode == 0, result.stdout + result.stderr


def test_performance_warning_helpers_report_checks_over_five_seconds() -> None:
    substance = load_script("gate_substance.py")
    form = load_script("gate_form.py")
    warnings: list[str] = []
    original_counter = substance.time.perf_counter
    try:
        substance.time.perf_counter = iter((1.0, 6.1)).__next__
        assert substance.timed_check("slow_check", warnings, lambda: "done") == "done"
    finally:
        substance.time.perf_counter = original_counter
    assert warnings == ["Performance: slow_check took 5.10s (threshold: 5.00s)"]
    assert form.performance_warning("slow_check", 5.1) == "Performance: slow_check took 5.10s (threshold: 5.00s)"


def test_substance_gate_caches_section_parse_during_run_checks(tmp_path: Path) -> None:
    substance = load_script("gate_substance.py")
    calls = 0
    original = substance.extract_sections

    def counted_extract_sections(text: str):
        nonlocal calls
        calls += 1
        return original(text)

    substance.extract_sections = counted_extract_sections
    report = substance.run_checks(valid_prompt_text(), base_dir=ROOT)
    assert report["passed"], report["errors"]
    assert calls == 1


def test_malformed_verification_quoting_is_blocked(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (READ)\n\n"
        "Verification:\n"
        "- Run: test -f \"./input.txt\n\n"
        "Output Format:\n"
        "- Report in final response.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "Malformed verification command" in "\n".join(report["errors"])


def test_modify_and_delete_targets_must_exist(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./missing-modify.txt (MODIFY)\n"
        "- ./missing-delete.txt (DELETE)\n\n"
        "Verification:\n"
        "- Run: test -d .\n\n"
        "Output Format:\n"
        "- Report in final response.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    errors = "\n".join(report["errors"])
    assert "is missing for MODIFY" in errors
    assert "is missing for DELETE" in errors


def test_write_and_modify_targets_must_be_writable(tmp_path: Path) -> None:
    write_target = tmp_path / "readonly-write.txt"
    modify_target = tmp_path / "readonly-modify.txt"
    write_target.write_text("locked\n")
    modify_target.write_text("locked\n")
    write_target.chmod(0o444)
    modify_target.chmod(0o444)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./readonly-write.txt (WRITE)\n"
        "- ./readonly-modify.txt (MODIFY)\n\n"
        "Verification:\n"
        "- Run: test -f ./readonly-write.txt\n\n"
        "Output Format:\n"
        "- Report in final response.\n"
    )
    try:
        result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    finally:
        write_target.chmod(0o644)
        modify_target.chmod(0o644)
    report = json.loads(result.stdout)
    assert result.returncode == 1
    errors = "\n".join(report["errors"])
    assert "readonly-write.txt exists but is not writable for WRITE" in errors
    assert "readonly-modify.txt exists but is not writable for MODIFY" in errors


def test_wrapped_kill_list_entry_blocks_write_path(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./blocked path.txt (WRITE)\n\n"
        "Kill List:\n"
        "- Do not modify ./blocked\n"
        "  path.txt during this task.\n\n"
        "Verification:\n"
        "- Run: test -d .\n\n"
        "Output Format:\n"
        "- Report in final response.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert any("Kill List" in error for error in report["errors"])


def test_known_verification_binary_unavailable_is_blocked(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Verification:\n"
        "- Run: bash ./examples/good-prompt.txt\n"
    )
    result = subprocess.run(
        [sys.executable, "gate_substance.py", str(prompt)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": ""},
    )
    assert result.returncode == 1
    assert "Binary is unavailable: bash" in result.stdout


def test_pipeline_verification_binary_unavailable_is_blocked(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./examples/good-prompt.txt (READ)\n\n"
        "Verification:\n"
        "- Run: echo hello | definitely_missing_bin_zz\n\n"
        "Output Format:\n"
        "- Report in final response.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt))
    assert result.returncode == 1
    assert "Binary is unavailable: definitely_missing_bin_zz" in result.stdout


def test_and_separator_verification_binary_unavailable_is_blocked(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Verification:\n"
        "- Run: echo hello && definitely_missing_bin_zz\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt))
    assert result.returncode == 1
    assert "Binary is unavailable: definitely_missing_bin_zz" in result.stdout


def test_semicolon_separator_verification_binary_unavailable_is_blocked(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Verification:\n"
        "- Run: echo hello; definitely_missing_bin_zz\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt))
    assert result.returncode == 1
    assert "Binary is unavailable: definitely_missing_bin_zz" in result.stdout


def test_background_verification_construct_warns_but_keeps_binary_check(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    source = tmp_path / "source.txt"
    source.write_text("ok\n")
    prompt.write_text(
        "Owned paths:\n"
        "- ./source.txt (READ)\n\n"
        "Verification:\n"
        "- Run: definitely_missing_bin_zz & test -f ./source.txt\n\n"
        "Output Format:\n"
        "- Report in final response.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json")
    report = json.loads(result.stdout)
    assert result.returncode == 1, report
    assert (
        "Shell construct & (background) not statically validated — "
        "unsupported shell portion skipped; path checks still run"
        in report["warnings"]
    )
    assert any("Binary is unavailable: definitely_missing_bin_zz" in error for error in report["errors"])


def test_unsupported_shell_construct_detector_is_quote_aware() -> None:
    substance = load_script("gate_substance.py")
    assert substance.unsupported_shell_constructs("cmd &") == ["& (background)"]
    assert substance.unsupported_shell_constructs("cmd $(nested)") == ["$() command substitution"]
    assert substance.unsupported_shell_constructs('cmd "$(nested)"') == ["$() command substitution"]
    assert substance.unsupported_shell_constructs("cmd `nested`") == ["backtick substitution"]
    assert substance.unsupported_shell_constructs("diff <(one) <(two)") == ["<() process substitution"]
    assert substance.unsupported_shell_constructs("diff >(one)") == [">() process substitution"]
    assert substance.unsupported_shell_constructs("cmd || fallback") == ["|| (OR operator)"]
    assert substance.unsupported_shell_constructs("test '$(not-substitution)'") == []
    assert substance.unsupported_shell_constructs("printf '`not-substitution`'") == []


def test_quoted_shell_metacharacters_do_not_warn(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: test '$(not-substitution)'\n\n"
        "- Run: printf '||'\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 0, report
    assert not any("Shell construct" in warning for warning in report["warnings"])


def test_prompt_relative_slash_command_binary_resolves_without_dot_prefix(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    check = scripts_dir / "check.sh"
    check.write_text("#!/bin/sh\nexit 0\n")
    check.chmod(0o755)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: scripts/check.sh\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 0, report
    assert not any("Binary is unavailable: scripts/check.sh" in error for error in report["errors"])


def test_unsupported_construct_does_not_skip_pipeline_binary_validation(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (READ)\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: echo $(cat ./input.txt) | definitely_missing_bin_zz\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1, report
    assert (
        "Shell construct $() command substitution not statically validated — "
        "unsupported shell portion skipped; path checks still run"
        in report["warnings"]
    )
    assert any("Binary is unavailable: definitely_missing_bin_zz" in error for error in report["errors"])


def test_command_substitution_still_runs_path_checks_without_false_missing_path(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("content")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./input.txt (READ)\n\n"
        "Verification:\n"
        "- Run: echo $(cat ./input.txt)\n"
        "- Run: diff <(cat ./input.txt) <(cat ./input.txt)\n\n"
        "Output Format:\n"
        "- Report in final response.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 0, report
    assert (
        "Shell construct $() command substitution not statically validated — "
        "unsupported shell portion skipped; path checks still run"
        in report["warnings"]
    )
    assert (
        "Shell construct <() process substitution not statically validated — "
        "unsupported shell portion skipped; path checks still run"
        in report["warnings"]
    )
    assert "Verification path is missing" not in "\n".join(report["errors"])


def test_command_substitution_blocks_missing_inner_path(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: echo $(cat ./missing.txt)\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert (
        "Shell construct $() command substitution not statically validated — "
        "unsupported shell portion skipped; path checks still run"
        in report["warnings"]
    )
    assert "Verification path is missing: ./missing.txt" in "\n".join(report["errors"])


def test_double_quoted_command_substitution_blocks_missing_inner_path(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        '- Run: echo "$(cat ./missing.txt)"\n\n'
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert (
        "Shell construct $() command substitution not statically validated — "
        "unsupported shell portion skipped; path checks still run"
        in report["warnings"]
    )
    assert "Verification path is missing: ./missing.txt" in "\n".join(report["errors"])


def test_output_process_substitution_blocks_missing_inner_path(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: diff >(cat ./missing.txt) ./generated-report.md\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert (
        "Shell construct >() process substitution not statically validated — "
        "unsupported shell portion skipped; path checks still run"
        in report["warnings"]
    )
    assert "Verification path is missing: ./missing.txt" in "\n".join(report["errors"])


def test_compact_input_redirect_in_command_substitution_blocks_missing_path(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: echo $(cat<./missing.txt)\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "Verification path is missing: ./missing.txt" in "\n".join(report["errors"])


def test_unsupported_shell_construct_still_blocks_missing_verification_path(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./generated-report.md (CREATE)\n\n"
        "Verification:\n"
        "- Run: echo $(date) ./definitely-missing.txt\n\n"
        "Output Format:\n"
        "- Write Markdown to `./generated-report.md`.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json", "--strict")
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert (
        "Shell construct $() command substitution not statically validated — "
        "unsupported shell portion skipped; path checks still run"
        in report["warnings"]
    )
    assert "Verification path is missing: ./definitely-missing.txt" in "\n".join(report["errors"])


def test_or_verification_construct_warns_but_keeps_binary_check(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        "- ./missing.txt (CREATE)\n\n"
        "Verification:\n"
        "- Run: test -f ./missing.txt || definitely_missing_bin_zz\n\n"
        "Output Format:\n"
        "- Report in final response.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt), "--json")
    report = json.loads(result.stdout)
    assert result.returncode == 1, report
    assert (
        "Shell construct || (OR operator) not statically validated — "
        "unsupported shell portion skipped; path checks still run"
        in report["warnings"]
    )
    assert "Verification path is missing: ./missing.txt" not in "\n".join(report["errors"])
    assert any("Binary is unavailable: definitely_missing_bin_zz" in error for error in report["errors"])


def test_verification_environment_assignment_is_not_checked_as_binary(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Owned paths:\n"
        f"- {ROOT / 'examples/good-prompt.txt'} (READ)\n\n"
        "Verification:\n"
        f"- Run: FOO=bar test -f {ROOT / 'examples/good-prompt.txt'}\n\n"
        "Output Format:\n"
        "- Report in final response.\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt))
    assert result.returncode == 0, result.stdout
    assert "Binary is unavailable: FOO=bar" not in result.stdout


def test_env_wrapper_verification_binary_unavailable_is_blocked(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(
        "Verification:\n"
        "- Run: env FOO=bar definitely_missing_tgd\n"
    )
    result = run(sys.executable, "gate_substance.py", str(prompt))
    assert result.returncode == 1
    assert "Binary is unavailable: definitely_missing_tgd" in result.stdout


def test_env_wrapper_options_are_skipped_before_command(tmp_path: Path) -> None:
    for command in (
        "env -i true",
        "env -- true",
        "env -u FOO true",
        "env --chdir /tmp true",
        "env -C /tmp true",
        "env --block-signal PIPE true",
        "env --default-signal PIPE true",
        "env --ignore-signal PIPE true",
        "env --split-string 'true'",
        "env -S 'true'",
    ):
        prompt = tmp_path / "prompt.txt"
        prompt.write_text(
            "Owned paths:\n"
            "- ./generated-report.md (CREATE)\n\n"
            "Verification:\n"
            f"- Run: {command}\n\n"
            "Output Format:\n"
            "- Write Markdown to `./generated-report.md`.\n"
        )
        result = run(sys.executable, "gate_substance.py", str(prompt))
        assert result.returncode == 0, result.stdout
        assert "Binary is unavailable" not in result.stdout


def test_env_wrapper_option_operands_are_not_checked_as_binaries(tmp_path: Path) -> None:
    for command, skipped_operand in (
        ("env -u FOO definitely_missing_tgd", "FOO"),
        ("env --chdir /tmp definitely_missing_tgd", "/tmp"),
        ("env -C /tmp definitely_missing_tgd", "/tmp"),
    ):
        prompt = tmp_path / "prompt.txt"
        prompt.write_text(
            "Verification:\n"
            f"- Run: {command}\n"
        )
        result = run(sys.executable, "gate_substance.py", str(prompt))
        assert result.returncode == 1
        assert f"Binary is unavailable: {skipped_operand}" not in result.stdout
        assert "Binary is unavailable: definitely_missing_tgd" in result.stdout


def test_env_wrapper_split_string_command_is_checked_as_binary(tmp_path: Path) -> None:
    for command in (
        "env -S 'definitely_missing_tgd'",
        "env --split-string 'definitely_missing_tgd arg1'",
    ):
        prompt = tmp_path / "prompt.txt"
        prompt.write_text(
            "Verification:\n"
            f"- Run: {command}\n"
        )
        result = run(sys.executable, "gate_substance.py", str(prompt))
        assert result.returncode == 1
        assert "Binary is unavailable: definitely_missing_tgd" in result.stdout


def test_command_wrapper_verification_binary_unavailable_is_blocked(tmp_path: Path) -> None:
    for command in (
        "command -p definitely_missing_tgd",
        "env FOO=bar command -p definitely_missing_tgd",
    ):
        prompt = tmp_path / "prompt.txt"
        prompt.write_text(
            "Verification:\n"
            f"- Run: {command}\n"
        )
        result = run(sys.executable, "gate_substance.py", str(prompt))
        assert result.returncode == 1
        assert "Binary is unavailable: definitely_missing_tgd" in result.stdout
        assert "Binary is unavailable: command" not in result.stdout


def test_dispatch_main_rejects_extra_args() -> None:
    result = run(
        sys.executable,
        "-c",
        (
            "import sys, gate_substance; "
            "sys.argv = ['dispatch-gate', 'examples/good-prompt.txt', 'extra']; "
            "raise SystemExit(gate_substance.dispatch_main())"
        ),
    )
    assert result.returncode == 1
    assert result.stdout.strip() == "Usage: dispatch-gate <prompt-file>"


def test_wrapper_runs_both_gates() -> None:
    result = run("bash", "dispatch-gate.sh", "examples/good-prompt.txt")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BOTH GATES PASSED" in result.stdout


def test_dispatch_main_prints_banner_before_child_gate_output() -> None:
    result = run(
        sys.executable,
        "-c",
        (
            "import sys, gate_substance; "
            "sys.argv = ['dispatch-gate', 'examples/good-prompt.txt']; "
            "raise SystemExit(gate_substance.dispatch_main())"
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0] == "=== TWO-GATE DISPATCH ==="


def test_installed_dispatch_gate_version_works_without_pythonpath(tmp_path: Path) -> None:
    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv_dir)], check=True)
    python = venv_dir / "bin" / "python"
    dispatch_gate = venv_dir / "bin" / "dispatch-gate"
    install = subprocess.run(
        [sys.executable, "-m", "pip", "--python", str(python), "install", "--no-deps", str(ROOT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [str(dispatch_gate), "--version"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "two-gate-dispatch 1.0.0"


def test_wrapper_help_version_and_invalid_args() -> None:
    help_result = run("bash", "dispatch-gate.sh", "--help")
    assert help_result.returncode == 0
    assert help_result.stdout.strip() == "Usage: dispatch-gate <prompt-file>"

    version_result = run("bash", "dispatch-gate.sh", "--version")
    assert version_result.returncode == 0
    assert version_result.stdout.strip() == "two-gate-dispatch 1.0.0"

    unknown = run("bash", "dispatch-gate.sh", "--unknown")
    assert unknown.returncode == 1
    assert "prompt file not found: --unknown" in unknown.stderr

    extra = run("bash", "dispatch-gate.sh", "examples/good-prompt.txt", "extra")
    assert extra.returncode == 1
    assert "Usage: dispatch-gate <prompt-file>" in extra.stderr


def test_documented_example_fixtures_have_expected_gate_results() -> None:
    expected_substance = {
        "examples/good-prompt.txt": 0,
        "examples/bad-missing-path.txt": 1,
        "examples/bad-contradiction.txt": 1,
        "examples/realworld-read-output-conflict.txt": 1,
        "examples/realworld-missing-verification-path.txt": 1,
    }
    for prompt, substance_status in expected_substance.items():
        form = run(sys.executable, "gate_form.py", prompt, "--json")
        substance = run(sys.executable, "gate_substance.py", prompt, "--json")
        assert form.returncode == 0, form.stdout + form.stderr
        assert json.loads(form.stdout)["score"] == 9
        assert substance.returncode == substance_status, substance.stdout + substance.stderr


def test_clean_install_exposes_dispatch_console_script(tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns("build", "*.egg-info", "__pycache__", ".pytest_cache"),
    )
    prefix = tmp_path / "install"
    bin_dir = prefix / "bin"
    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--prefix",
            str(prefix),
            "--no-deps",
            "--no-build-isolation",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    scripts = {
        name: next(prefix.rglob(name))
        for name in ("two-gate-form", "two-gate-substance", "two-gate-dispatch")
    }
    bin_dir = scripts["two-gate-dispatch"].parent
    site_packages = next(prefix.rglob("two_gate_dispatch_cli.py")).parent
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "PYTHONPATH": f"{site_packages}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
    }
    for script, script_path in scripts.items():
        result = subprocess.run(
            [str(script_path), "--version"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "two-gate-dispatch 1.0.0"


def test_docs_marked_prompt_examples_are_executable() -> None:
    docs = (ROOT / "examples" / "EXAMPLES-COMPARISON.md").read_text()
    examples = re.findall(r"<!-- gate-example:(pass|block) -->\s*```text\n(.*?)\n```", docs, re.S)
    assert examples

    for index, (expected, prompt_text) in enumerate(examples):
        prompt = ROOT / f".doc-example-{index}.txt"
        prompt.write_text(prompt_text)
        try:
            form = run(sys.executable, "gate_form.py", str(prompt), "--strict")
            assert form.returncode == 0, form.stdout + form.stderr

            substance = run(sys.executable, "gate_substance.py", str(prompt), "--strict")
            if expected == "pass":
                assert substance.returncode == 0, substance.stdout + substance.stderr
            else:
                assert substance.returncode == 1, substance.stdout + substance.stderr
                assert "FAIL" in substance.stdout
        finally:
            prompt.unlink(missing_ok=True)
