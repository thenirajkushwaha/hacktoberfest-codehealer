"""
Unit tests for heal.runner subprocess execution engine.
"""

from pathlib import Path
import tempfile
import pytest

from heal.runner import run_script, ErrorCategory


def test_run_script_success():
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write('print("Hello from test!")\n')
        f.flush()
        script_path = Path(f.name)

    try:
        res = run_script(script_path)
        assert res.success is True
        assert res.returncode == 0
        assert res.error_category == ErrorCategory.NONE
        assert "Hello from test!" in res.stdout
    finally:
        script_path.unlink(missing_ok=True)


def test_run_script_syntax_error():
    syntax_file = Path("tests/broken_syntax.py")
    res = run_script(syntax_file)
    assert res.success is False
    assert res.returncode != 0
    assert res.error_category == ErrorCategory.SYNTAX_ERROR
    assert res.error_type == "SyntaxError"
    assert res.error_line == 8


def test_run_script_runtime_exception():
    runtime_file = Path("tests/broken_runtime.py")
    res = run_script(runtime_file)
    assert res.success is False
    assert res.returncode != 0
    assert res.error_category == ErrorCategory.RUNTIME_EXCEPTION
    assert res.error_type == "IndexError"
    assert res.error_line == 12


def test_run_script_missing_file():
    with pytest.raises(FileNotFoundError):
        run_script("non_existent_file_xyz_123.py")


def test_run_script_timeout():
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write("import time\ntime.sleep(5)\n")
        f.flush()
        script_path = Path(f.name)

    try:
        res = run_script(script_path, timeout=1)
        assert res.success is False
        assert res.error_category == ErrorCategory.TIMEOUT
        assert res.error_type == "TimeoutExpired"
    finally:
        script_path.unlink(missing_ok=True)
