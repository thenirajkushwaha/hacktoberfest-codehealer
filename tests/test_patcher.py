"""
Unit tests for heal.patcher file safety, AST validation, and diff generation.
"""

from pathlib import Path
import tempfile
import pytest

from heal.patcher import (
    apply_patch,
    create_backup,
    create_diff_renderable,
    extract_code,
    generate_diff,
    get_backup_path,
    restore_backup,
    PatchError,
)


def test_extract_code_markdown_fence():
    sample_response = (
        "Here is the corrected script:\n\n"
        "```python\n"
        "def add(a, b):\n"
        "    return a + b\n\n"
        "print(add(2, 3))\n"
        "```\n"
        "Hope this helps!"
    )
    code = extract_code(sample_response)
    assert "def add(a, b):" in code
    assert "Hope this helps!" not in code
    assert code.startswith("def add")


def test_extract_code_syntax_error():
    broken_response = (
        "```python\n"
        "def broken(\n"
        "    return 42\n"
        "```"
    )
    with pytest.raises(PatchError):
        extract_code(broken_response)


def test_extract_code_empty_error():
    with pytest.raises(PatchError):
        extract_code("")


def test_backup_and_restore_cycle():
    with tempfile.TemporaryDirectory() as tmp_dir:
        target = Path(tmp_dir) / "test_target.py"
        target.write_text("ORIGINAL_CONTENT = 1\n", encoding="utf-8")

        # Create backup
        bak = create_backup(target)
        assert bak.exists()
        assert bak.read_text(encoding="utf-8") == "ORIGINAL_CONTENT = 1\n"

        # Apply a modification
        apply_patch(target, "MODIFIED_CONTENT = 2\n")
        assert target.read_text(encoding="utf-8") == "MODIFIED_CONTENT = 2\n"

        # Rollback
        restored, msg = restore_backup(target)
        assert restored is True
        assert target.read_text(encoding="utf-8") == "ORIGINAL_CONTENT = 1\n"


def test_diff_generation():
    old = "x = 1\ny = 2\n"
    new = "x = 1\ny = 3\n"
    diff = generate_diff(old, new, "test.py")
    assert "-y = 2" in diff
    assert "+y = 3" in diff

    # Ensure rich renderable creates without error
    renderable = create_diff_renderable(old, new, "test.py")
    assert renderable is not None
