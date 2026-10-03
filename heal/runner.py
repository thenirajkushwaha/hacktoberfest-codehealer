"""
Subprocess execution and stream capture engine for heal.
Executes target Python scripts in isolated child processes and categorizes errors.
"""

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import re
import shutil
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
    script_path: Optional[Path]
    returncode: int
    stdout: str
    stderr: str
    success: bool
    error_category: ErrorCategory
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    error_line: Optional[int] = None
    detected_file: Optional[Path] = None
    duration: float = 0.0

    @property
    def full_output(self) -> str:
        out = []
        if self.stdout:
            out.append(f"--- STDOUT ---\n{self.stdout.strip()}")
        if self.stderr:
            out.append(f"--- STDERR ---\n{self.stderr.strip()}")
        return "\n\n".join(out)


def detect_file_and_line(text: str, cwd: Optional[Path] = None) -> tuple[Optional[Path], Optional[int]]:
    """
    Scans stderr for file paths and line numbers across multiple programming languages:
    - GCC/Clang/Rustc/Go: path/to/file.ext:14:5:
    - Python: File "path/to/file.py", line 14
    - Node.js: at ... (path/to/file.js:14:5)
    - Java: at ... (File.java:14)
    """
    base_dir = cwd or Path.cwd()

    # Pattern 1: path/file.ext:12:34: error
    colon_match = re.findall(r"([A-Za-z0-9_./\\-]+\.[a-zA-Z0-9]+):(\d+)(?::(\d+))?", text)
    if colon_match:
        for candidate_path, line_str, _ in reversed(colon_match):
            p = Path(candidate_path)
            if not p.is_absolute():
                p = base_dir / p
            if p.exists() and p.is_file():
                return p, int(line_str)

    # Pattern 2: Python File "...", line 12
    py_match = re.findall(r'File\s+"(.*?)",\s+line\s+(\d+)', text)
    if py_match:
        cand_path, line_str = py_match[-1]
        p = Path(cand_path)
        if not p.is_absolute():
            p = base_dir / p
        return p, int(line_str)

    # Pattern 3: Node.js at ... (path:12:34)
    node_match = re.findall(r'\((.*?\.[a-zA-Z0-9]+):(\d+):(\d+)\)', text)
    if node_match:
        cand_path, line_str, _ = node_match[-1]
        p = Path(cand_path)
        if not p.is_absolute():
            p = base_dir / p
        return p, int(line_str)

    return None, None


def classify_error(stderr: str, returncode: int, cwd: Optional[Path] = None) -> tuple[ErrorCategory, Optional[str], Optional[str], Optional[int], Optional[Path]]:
    """
    Analyzes stderr to identify the error category, exception class, message, line number, and offending file.
    Supports Python, Rust, Go, C/C++, JavaScript/TypeScript, and Java.
    """
    if returncode == 0:
        return ErrorCategory.NONE, None, None, None, None

    detected_file, detected_line = detect_file_and_line(stderr, cwd)

    # Check POSIX signals (especially for compiled C, C++, Rust, Go)
    if returncode in [-8, 136]:
        return ErrorCategory.RUNTIME_EXCEPTION, "FloatingPointException (SIGFPE)", "Process crashed with SIGFPE (division by zero or arithmetic overflow)", detected_line, detected_file
    elif returncode in [-11, 139]:
        return ErrorCategory.RUNTIME_EXCEPTION, "SegmentationFault (SIGSEGV)", "Process crashed with SIGSEGV (segmentation fault / null pointer)", detected_line, detected_file
    elif returncode in [-6, 134]:
        return ErrorCategory.RUNTIME_EXCEPTION, "Abort (SIGABRT)", "Process aborted (assertion failure or std::terminate)", detected_line, detected_file

    if not stderr.strip():
        return ErrorCategory.UNKNOWN_ERROR, "NonZeroExitCode", f"Process exited with code {returncode}", None, detected_file

    # Check for SyntaxError / Compiler Error (Rust, Go, GCC, Clang, TS)
    if any(keyword in stderr for keyword in ["error[E", "SyntaxError", "syntax error", "IndentationError", "expected ';'", "undeclared", "error:"]):
        syntax_match = re.search(r"((?:SyntaxError|IndentationError|error\[E\d+\]|error):\s*(.+))", stderr)
        err_type = syntax_match.group(1).split(":")[0].strip() if syntax_match else "CompilerError"
        err_msg = syntax_match.group(2).strip() if syntax_match else stderr.strip().splitlines()[0]
        return ErrorCategory.SYNTAX_ERROR, err_type, err_msg, detected_line, detected_file

    # Check for ModuleNotFoundError / Missing Package (Python, Node, Rust crate, Go module)
    if any(keyword in stderr for keyword in ["ModuleNotFoundError", "ImportError", "Cannot find module", "cannot find package", "unresolved import"]):
        err_msg = stderr.strip().splitlines()[-1]
        return ErrorCategory.MISSING_DEPENDENCY, "MissingDependencyError", err_msg, detected_line, detected_file

    # Generic Traceback / Runtime Exceptions
    exc_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Interrupt|Exit|Warning|panic)):\s*(.*)", stderr)
    if exc_match:
        err_type = exc_match.group(1).strip()
        err_msg = exc_match.group(2).strip()
        return ErrorCategory.RUNTIME_EXCEPTION, err_type, err_msg, detected_line, detected_file

    # Fallback
    last_line = stderr.strip().splitlines()[-1] if stderr.strip().splitlines() else f"Command failed with code {returncode}"
    return ErrorCategory.UNKNOWN_ERROR, "ExecutionError", last_line, detected_line, detected_file


def get_language_from_path(path: Path) -> str:
    """Infers programming language name from file extension."""
    ext_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".rs": "rust",
        ".go": "go",
        ".c": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".java": "java",
        ".sh": "bash",
        ".rb": "ruby",
    }
    return ext_map.get(path.suffix.lower(), "python")


def resolve_script_command(script_path: Path) -> List[str]:
    """Generates the runner command based on file extension."""
    ext = script_path.suffix.lower()
    if ext == ".py":
        return [sys.executable, str(script_path)]
    elif ext in [".js", ".mjs", ".cjs"]:
        return ["node", str(script_path)]
    elif ext == ".ts":
        return ["npx", "-y", "ts-node", str(script_path)]
    elif ext == ".go":
        return ["go", "run", str(script_path)]
    elif ext == ".sh":
        return ["bash", str(script_path)]
    elif ext == ".rb":
        return ["ruby", str(script_path)]
    # Fallback to direct execution
    return [str(script_path)]


def run_script(
    script_path: str | Path,
    args: Optional[List[str]] = None,
    timeout: int = 45,
    cwd: Optional[Path] = None,
) -> ExecutionResult:
    """
    Executes target script in an isolated child process using polyglot resolution.
    Captures stdout, stderr, execution time, and categorizes failures.
    """
    script_path = Path(script_path).resolve()
    if not script_path.exists():
        raise FileNotFoundError(f"Target script '{script_path}' does not exist.")

    work_dir = cwd or script_path.parent
    start_time = time.perf_counter()

    ext = script_path.suffix.lower()
    temp_bin = None
    if ext in [".cpp", ".cc", ".cxx", ".c"]:
        compiler = "g++" if ext != ".c" else "gcc"
        if not shutil.which(compiler):
            raise FileNotFoundError(f"Compiler '{compiler}' is not found on your system.")
        temp_bin = script_path.parent / f".heal_bin_{script_path.stem}_{os.getpid()}"
        compile_cmd = [compiler, "-O0", str(script_path), "-o", str(temp_bin)]
        c_proc = subprocess.run(compile_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, cwd=work_dir)
        if c_proc.returncode != 0:
            duration = time.perf_counter() - start_time
            err_cat, err_type, err_msg, line_num, detected_file = classify_error(c_proc.stderr, c_proc.returncode, cwd=work_dir)
            return ExecutionResult(
                script_path=script_path,
                returncode=c_proc.returncode,
                stdout=c_proc.stdout,
                stderr=c_proc.stderr,
                success=False,
                error_category=err_cat,
                error_type=err_type or "CompilationError",
                error_message=err_msg,
                error_line=line_num,
                detected_file=detected_file or script_path,
                duration=duration,
            )
        cmd = [str(temp_bin)]
    elif ext == ".rs":
        if not shutil.which("rustc"):
            raise FileNotFoundError("Rust compiler 'rustc' is not found on your system.")
        temp_bin = script_path.parent / f".heal_bin_{script_path.stem}_{os.getpid()}"
        c_proc = subprocess.run(["rustc", str(script_path), "-o", str(temp_bin)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, cwd=work_dir)
        if c_proc.returncode != 0:
            duration = time.perf_counter() - start_time
            err_cat, err_type, err_msg, line_num, detected_file = classify_error(c_proc.stderr, c_proc.returncode, cwd=work_dir)
            return ExecutionResult(
                script_path=script_path,
                returncode=c_proc.returncode,
                stdout=c_proc.stdout,
                stderr=c_proc.stderr,
                success=False,
                error_category=err_cat,
                error_type=err_type or "RustcCompilationError",
                error_message=err_msg,
                error_line=line_num,
                detected_file=detected_file or script_path,
                duration=duration,
            )
        cmd = [str(temp_bin)]
    else:
        cmd = resolve_script_command(script_path)

    if args:
        cmd.extend(args)

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

        err_cat, err_type, err_msg, line_num, detected_file = classify_error(stderr, returncode, cwd=work_dir)
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
            detected_file=detected_file or script_path,
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
    finally:
        if temp_bin and temp_bin.exists():
            try:
                temp_bin.unlink()
            except OSError:
                pass


def run_command_job(
    command_str: str,
    timeout: int = 60,
    cwd: Optional[Path] = None,
) -> ExecutionResult:
    """
    Executes an arbitrary shell command (e.g. 'npm test', 'cargo test', 'go test ./...').
    Captures stdout, stderr, and extracts the offending file and line.
    """
    work_dir = cwd or Path.cwd()
    start_time = time.perf_counter()

    try:
        proc = subprocess.run(
            command_str,
            shell=True,
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
                script_path=None,
                returncode=0,
                stdout=stdout,
                stderr=stderr,
                success=True,
                error_category=ErrorCategory.NONE,
                duration=duration,
            )

        combined_output = f"{stdout}\n{stderr}"
        err_cat, err_type, err_msg, line_num, detected_file = classify_error(combined_output, returncode, cwd=work_dir)

        return ExecutionResult(
            script_path=detected_file,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            success=False,
            error_category=err_cat,
            error_type=err_type,
            error_message=err_msg,
            error_line=line_num,
            detected_file=detected_file,
            duration=duration,
        )

    except subprocess.TimeoutExpired as exc:
        duration = time.perf_counter() - start_time
        stdout = exc.stdout or "" if isinstance(exc.stdout, str) else (exc.stdout.decode() if exc.stdout else "")
        stderr = exc.stderr or "" if isinstance(exc.stderr, str) else (exc.stderr.decode() if exc.stderr else "")
        stderr += f"\nCommand execution timed out after {timeout} seconds."
        return ExecutionResult(
            script_path=None,
            returncode=-1,
            stdout=stdout,
            stderr=stderr,
            success=False,
            error_category=ErrorCategory.TIMEOUT,
            error_type="TimeoutExpired",
            error_message=f"Command exceeded timeout limit ({timeout}s)",
            duration=duration,
        )
