"""
Subprocess execution and stream capture engine for heal.
Executes target Python scripts in isolated child processes and categorizes errors.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import List, Optional


class ErrorCategory(str, Enum):
    NONE = "NONE"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    RUNTIME_EXCEPTION = "RUNTIME_EXCEPTION"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
    TIMEOUT = "TIMEOUT"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


@dataclass
class ExecutionResult:
    script_path: Path
    returncode: int
    stdout: str
    stderr: str
    success: bool
    error_category: ErrorCategory
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    error_line: Optional[int] = None
    duration: float = 0.0

    @property
    def full_output(self) -> str:
        out = []
        if self.stdout:
            out.append(f"--- STDOUT ---\n{self.stdout.strip()}")
        if self.stderr:
            out.append(f"--- STDERR ---\n{self.stderr.strip()}")
        return "\n\n".join(out)


def classify_error(stderr: str, returncode: int) -> tuple[ErrorCategory, Optional[str], Optional[str], Optional[int]]:
    """
    Analyzes stderr to identify the error category, exception class, message, and line number.
    """
    if returncode == 0:
        return ErrorCategory.NONE, None, None, None

    if not stderr.strip():
        return ErrorCategory.UNKNOWN_ERROR, "NonZeroExitCode", f"Process exited with code {returncode}", None

    # Check for SyntaxError / IndentationError
    syntax_match = re.search(r"((?:SyntaxError|IndentationError|TabError):\s*(.+))", stderr)
    if syntax_match:
        line_match = re.search(r'File\s+".*?",\s+line\s+(\d+)', stderr)
        line_num = int(line_match.group(1)) if line_match else None
        err_type = syntax_match.group(1).split(":")[0].strip()
        err_msg = syntax_match.group(2).strip()
        return ErrorCategory.SYNTAX_ERROR, err_type, err_msg, line_num

    # Check for ModuleNotFoundError / ImportError
    import_match = re.search(r"((?:ModuleNotFoundError|ImportError):\s*(.+))", stderr)
    if import_match:
        err_type = import_match.group(1).split(":")[0].strip()
        err_msg = import_match.group(2).strip()
        line_match = re.findall(r'File\s+".*?",\s+line\s+(\d+)', stderr)
        line_num = int(line_match[-1]) if line_match else None
        return ErrorCategory.MISSING_DEPENDENCY, err_type, err_msg, line_num

    # Generic Traceback / Runtime Exceptions
    # Matches any standard Python exception at the end of traceback
    exc_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Interrupt|Exit|Warning)):\s*(.*)", stderr)
    if exc_match:
        err_type = exc_match.group(1).strip()
        err_msg = exc_match.group(2).strip()
        # Find the last line number in the traceback (closest to crash point)
        lines = re.findall(r'File\s+".*?",\s+line\s+(\d+)', stderr)
        line_num = int(lines[-1]) if lines else None
        return ErrorCategory.RUNTIME_EXCEPTION, err_type, err_msg, line_num

    # Fallback when traceback doesn't match standard regex
    last_line = stderr.strip().splitlines()[-1] if stderr.strip().splitlines() else "Unknown failure"
    return ErrorCategory.UNKNOWN_ERROR, "RuntimeError", last_line, None


def run_script(
    script_path: str | Path,
    args: Optional[List[str]] = None,
    timeout: int = 45,
    cwd: Optional[Path] = None,
) -> ExecutionResult:
    """
    Executes target script in an isolated child process using Python subprocess.run.
    Captures stdout, stderr, execution time, and categorizes failures.
    """
    script_path = Path(script_path).resolve()
    if not script_path.exists():
        raise FileNotFoundError(f"Target script '{script_path}' does not exist.")

    cmd = [sys.executable, str(script_path)]
    if args:
        cmd.extend(args)

    work_dir = cwd or script_path.parent
    start_time = time.perf_counter()

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            cwd=work_dir,
        )
        duration = time.perf_counter() - start_time
        stdout = proc.stdout
        stderr = proc.stderr
        returncode = proc.returncode

        if returncode == 0:
            return ExecutionResult(
                script_path=script_path,
                returncode=0,
                stdout=stdout,
                stderr=stderr,
                success=True,
                error_category=ErrorCategory.NONE,
                duration=duration,
            )

        err_cat, err_type, err_msg, line_num = classify_error(stderr, returncode)
        return ExecutionResult(
            script_path=script_path,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            success=False,
            error_category=err_cat,
            error_type=err_type,
            error_message=err_msg,
            error_line=line_num,
            duration=duration,
        )

    except subprocess.TimeoutExpired as exc:
        duration = time.perf_counter() - start_time
        stdout = exc.stdout or "" if isinstance(exc.stdout, str) else (exc.stdout.decode() if exc.stdout else "")
        stderr = exc.stderr or "" if isinstance(exc.stderr, str) else (exc.stderr.decode() if exc.stderr else "")
        stderr += f"\nProcess timed out after {timeout} seconds."
        return ExecutionResult(
            script_path=script_path,
            returncode=-1,
            stdout=stdout,
            stderr=stderr,
            success=False,
            error_category=ErrorCategory.TIMEOUT,
            error_type="TimeoutExpired",
            error_message=f"Script execution exceeded timeout limit ({timeout}s)",
            duration=duration,
        )
