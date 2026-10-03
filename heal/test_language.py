import pytest

from heal.language import detect_language, is_supported_language


def test_python_detection():
    assert detect_language("hello.py") == "python"


def test_cpp_detection():
    assert detect_language("main.cpp") == "cpp"


def test_c_detection():
    assert detect_language("main.c") == "c"


def test_java_detection():
    assert detect_language("Main.java") == "java"


def test_javascript_detection():
    assert detect_language("app.js") == "javascript"


def test_typescript_detection():
    assert detect_language("app.ts") == "typescript"


def test_go_detection():
    assert detect_language("main.go") == "go"


def test_rust_detection():
    assert detect_language("main.rs") == "rust"


def test_case_insensitive_extension():
    assert detect_language("PROGRAM.PY") == "python"


def test_supported_language():
    assert is_supported_language("hello.py") is True
    assert is_supported_language("hello.cpp") is True


def test_unsupported_language():
    assert is_supported_language("hello.xyz") is False


def test_unknown_extension():
    with pytest.raises(ValueError):
        detect_language("hello.xyz")