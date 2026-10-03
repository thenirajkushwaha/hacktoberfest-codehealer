from pathlib import Path


LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".java": "java",
    ".js": "javascript",
    ".ts": "typescript",
    ".go": "go",
    ".rs": "rust",
}


def detect_language(file_path: str) -> str:
    """
    Detect the programming language from the file extension.

    Example:
        detect_language("main.py") -> "python"
        detect_language("main.cpp") -> "cpp"
    """

    extension = Path(file_path).suffix.lower()

    language = LANGUAGE_EXTENSIONS.get(extension)

    if language is None:
        raise ValueError(
            f"Unsupported programming language: {extension or 'no extension'}"
        )

    return language


def is_supported_language(file_path: str) -> bool:
    """Return True if HEAL supports the file extension."""

    extension = Path(file_path).suffix.lower()

    return extension in LANGUAGE_EXTENSIONS