"""
File safety, patcher, syntax validator, and rollback engine for heal.
Handles atomic file modifications, AST syntax verification, and Rich visual diffs.
"""

import ast
import difflib
import os
from pathlib import Path
import re
import shutil
import time
from typing import Optional, Tuple

from rich.console import RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text


class PatchError(Exception):
    """Raised when patch extraction or validation fails."""
    pass


def get_backup_path(script_path: Path) -> Path:
    """Returns the primary canonical backup path: <script>.py.bak"""
    return script_path.with_name(f"{script_path.name}.bak")


def create_backup(script_path: Path, preserve_existing: bool = True) -> Path:
    """
    Creates an automatic backup file (<script>.py.bak) before modifying source code.
    If preserve_existing is True and a .bak already exists (e.g. from step 1 of retries),
    keeps the pristine original backup and also writes a timestamped snapshot.
    """
    script_path = Path(script_path).resolve()
    if not script_path.exists():
        raise FileNotFoundError(f"Target script '{script_path}' not found.")

    canonical_bak = get_backup_path(script_path)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    timestamped_bak = script_path.with_name(f"{script_path.name}.bak.{timestamp}")

    # Always ensure timestamped snapshot
    shutil.copy2(script_path, timestamped_bak)

    # If canonical .bak does not exist, or we aren't preserving it, save original
    if not canonical_bak.exists() or not preserve_existing:
        shutil.copy2(script_path, canonical_bak)

    return canonical_bak


def restore_backup(script_path: Path) -> Tuple[bool, Optional[str]]:
    """
    Rolls back the target script to its pristine .bak version.
    Returns (True, message) if restored, (False, error_message) otherwise.
    """
    script_path = Path(script_path).resolve()
    canonical_bak = get_backup_path(script_path)

    if canonical_bak.exists():
        shutil.copy2(canonical_bak, script_path)
        return True, f"Successfully restored {script_path.name} from {canonical_bak.name}"

    # Check for any timestamped backup files
    timestamped = sorted(script_path.parent.glob(f"{script_path.name}.bak.*"))
    if timestamped:
        latest = timestamped[-1]
        shutil.copy2(latest, script_path)
        return True, f"Restored {script_path.name} from latest backup {latest.name}"

    return False, f"No backup file found for {script_path.name}"


def extract_code(raw_response: str, filename: Optional[str] = None) -> str:
    """
    Extracts clean source code from Gemma's response.
    Expects markdown code blocks (```cpp ... ```, ```python ... ```, etc.).
    Validates Python syntax with ast.parse if target is a Python file.
    """
    if not raw_response or not raw_response.strip():
        raise PatchError("Model returned an empty response.")

    text = raw_response.strip()

    # Pattern for code fences: ```<lang>\n...\n``` or generic ```\n...\n```
    code_block_pattern = re.compile(
        r"```(?:[a-zA-Z0-9_+-]+)?\r?\n(.*?)```",
        re.DOTALL | re.IGNORECASE,
    )

    matches = code_block_pattern.findall(text)
    if matches:
        candidate = max(matches, key=len).strip()
    else:
        lines = text.splitlines()
        filtered = [l for l in lines if not l.strip().startswith("```")]
        candidate = "\n".join(filtered).strip()

    if not candidate:
        raise PatchError("Extracted code snippet is empty.")

    # Validate syntax if target is a Python file
    is_python = filename.endswith(".py") if filename else True
    if is_python:
        try:
            ast.parse(candidate)
        except SyntaxError as e:
            if filename and filename.endswith(".py"):
                raise PatchError(
                    f"Generated patch contains invalid Python syntax at line {e.lineno}: {e.msg}"
                ) from e
    else:
        # For non-Python languages (C++, Rust, Go, JS), check balanced braces
        open_braces = candidate.count("{")
        close_braces = candidate.count("}")
        if abs(open_braces - close_braces) > 2:
            raise PatchError("Extracted code appears truncated (unbalanced curly braces).")

    return candidate


def apply_patch(script_path: Path, new_code: str) -> None:
    """
    Atomically writes the validated patch to the target script file.
    Uses a temporary file in the same directory to prevent partial writes.
    """
    script_path = Path(script_path).resolve()
    temp_file = script_path.with_name(f".{script_path.name}.tmp.{os.getpid()}")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(new_code)
            if not new_code.endswith("\n"):
                f.write("\n")
        # Atomic rename
        os.replace(temp_file, script_path)
    finally:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass


def generate_diff(old_code: str, new_code: str, filename: str = "script.py") -> str:
    """Generates standard unified diff as a string."""
    old_lines = old_code.splitlines(keepends=True)
    new_lines = new_code.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{filename} (before)",
        tofile=f"b/{filename} (repaired)",
    )
    return "".join(diff)


def create_diff_renderable(old_code: str, new_code: str, filename: str = "script.py") -> RenderableType:
    """
    Builds a beautifully styled Rich table / panel highlighting additions and removals.
    """
    old_lines = old_code.splitlines()
    new_lines = new_code.splitlines()

    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    table = Table(
        title=f"Autonomous Patch Diff: [bold cyan]{filename}[/bold cyan]",
        box=None,
        show_header=True,
        header_style="bold bright_white on grey23",
        expand=True,
        pad_edge=False,
    )
    table.add_column("Old", justify="right", style="dim", width=6)
    table.add_column("New", justify="right", style="dim", width=6)
    table.add_column("Op", justify="center", width=3)
    table.add_column("Code", style="none", ratio=1)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            # For equal blocks, we show context around changes (up to 2 lines)
            count = i2 - i1
            if count <= 5:
                for idx in range(count):
                    old_num = str(i1 + idx + 1)
                    new_num = str(j1 + idx + 1)
                    line_text = Text(old_lines[i1 + idx], style="dim")
                    table.add_row(old_num, new_num, " ", line_text)
            else:
                # Show first line
                table.add_row(str(i1 + 1), str(j1 + 1), " ", Text(old_lines[i1], style="dim"))
                # Ellipsis
                table.add_row("...", "...", " ", Text(f"    ... {count - 2} unchanged lines ...", style="dim italic"))
                # Show last line
                table.add_row(str(i2), str(j2), " ", Text(old_lines[i2 - 1], style="dim"))
        elif tag == "delete":
            for idx in range(i1, i2):
                table.add_row(
                    str(idx + 1),
                    "",
                    Text("-", style="bold red"),
                    Text(old_lines[idx], style="red on #2d1010"),
                )
        elif tag == "insert":
            for idx in range(j1, j2):
                table.add_row(
                    "",
                    str(idx + 1),
                    Text("+", style="bold green"),
                    Text(new_lines[idx], style="green on #102d15"),
                )
        elif tag == "replace":
            for idx in range(i1, i2):
                table.add_row(
                    str(idx + 1),
                    "",
                    Text("-", style="bold red"),
                    Text(old_lines[idx], style="red on #2d1010"),
                )
            for idx in range(j1, j2):
                table.add_row(
                    "",
                    str(idx + 1),
                    Text("+", style="bold green"),
                    Text(new_lines[idx], style="green on #102d15"),
                )

    return Panel(table, border_style="cyan", padding=(0, 1))
