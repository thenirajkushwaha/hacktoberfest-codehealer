"""
Integration tests for heal.cli Typer application.
"""

from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner

from heal.cli import app

runner = CliRunner()


def test_cli_info():
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert "Autonomous Agents" in result.stdout
    assert "GEMINI_API_KEY" in result.stdout


def test_cli_revert_no_backup():
    with tempfile.TemporaryDirectory() as tmp_dir:
        dummy_file = Path(tmp_dir) / "no_backup.py"
        dummy_file.write_text("print('test')\n")
        result = runner.invoke(app, ["revert", str(dummy_file)])
        assert result.exit_code == 1
        assert "Rollback Failed" in result.stdout or "No backup file found" in result.stdout


def test_cli_revert_success():
    with tempfile.TemporaryDirectory() as tmp_dir:
        script = Path(tmp_dir) / "sample.py"
        script.write_text("MODIFIED = True\n")
        backup = Path(tmp_dir) / "sample.py.bak"
        backup.write_text("ORIGINAL = True\n")

        result = runner.invoke(app, ["revert", str(script)])
        assert result.exit_code == 0
        assert "Rollback Successful" in result.stdout
        assert script.read_text() == "ORIGINAL = True\n"


def test_cli_run_already_working_script():
    with tempfile.TemporaryDirectory() as tmp_dir:
        script = Path(tmp_dir) / "working.py"
        script.write_text("print('Success!')\n")

        result = runner.invoke(app, ["run", str(script)])
        assert result.exit_code == 0
        assert "Success (No Healing Required)" in result.stdout


def test_cli_run_autonomous_mock_heal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        broken_file = Path(tmp_dir) / "calc.py"
        # Script with syntax error
        broken_file.write_text("def broken()\n    pass\n")

        mock_response = "```python\ndef broken():\n    print('Healed and working!')\n\nbroken()\n```"

        with patch("heal.agent.HealingAgent.diagnose_and_patch", return_value=mock_response):
            result = runner.invoke(app, ["run", str(broken_file)])
            assert result.exit_code == 0
            assert "AUTONOMOUS RESOLUTION VERIFIED" in result.stdout
            assert "Healed and working!" in broken_file.read_text()
