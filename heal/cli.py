"""
heal CLI Interface & User Experience.
Styled with Rich for interactive feedback, spinners, diffs, and banners.
"""

import os
from pathlib import Path
import sys
from typing import List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
import typer

from heal import __version__
from heal.agent import HealingAgent, MissingAPIKeyError, HealingAgentException
from heal.patcher import (
    apply_patch,
    create_backup,
    create_diff_renderable,
    extract_code,
    get_backup_path,
    restore_backup,
    PatchError,
)
from heal.runner import ErrorCategory, ExecutionResult, run_script
from heal.voice import VoiceNarrator

app = typer.Typer(
    name="heal",
    help="heal - Autonomous Self-Healing CLI Agent powered by Gemma & Gemini API",
    add_completion=False,
)
console = Console()


def print_banner(script_name: str, has_image: bool, voice_active: bool, max_retries: int, model: str):
    """Renders the top branding header with active telemetry configuration."""
    title = Text.assemble(
        ("⚡ HEAL ", "bold bright_white on deep_sky_blue4"),
        (f" v{__version__} ", "bold white on grey23"),
        (" — Autonomous Self-Healing Agent", "bold bright_cyan"),
    )
    
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold yellow")
    table.add_column(style="white")
    table.add_column(style="bold yellow")
    table.add_column(style="white")
    
    table.add_row("Target File:", script_name, "Max Retries:", str(max_retries))
    table.add_row("Model Backend:", model, "Voice Telemetry:", "[green]ENABLED[/green]" if voice_active else "[dim]DISABLED[/dim]")
    table.add_row("Diagnostic Mode:", "[magenta]Multimodal (Image + Code)[/magenta]" if has_image else "[cyan]Source Code + Traceback[/cyan]", "", "")

    console.print(Panel(
        Align.center(table),
        title=title,
        border_style="bright_blue",
        padding=(1, 2),
    ))


@app.command()
def run(
    script: Path = typer.Argument(
        ...,
        help="Path to the target Python script to execute and heal.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    image: Optional[Path] = typer.Option(
        None,
        "--image",
        "-i",
        help="Path to visual error screenshot (terminal crash, UI failure, or corrupted plot).",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
    voice: bool = typer.Option(
        False,
        "--voice",
        "-v",
        help="Enable real-time vocal telemetry powered by ElevenLabs.",
    ),
    max_retries: int = typer.Option(
        3,
        "--max-retries",
        "-m",
        min=1,
        max=10,
        help="Maximum autonomous self-healing attempts before deterministic rollback.",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Override AI model endpoint (e.g. gemma-2-27b-it, gemma-2-9b-it, gemini-2.5-flash).",
    ),
):
    """
    Executes a Python script in a closed-loop autonomous repair cycle:
    Execute -> Intercept Crash -> Reason -> Patch -> Re-evaluate -> Verify.
    """
    narrator = VoiceNarrator(enabled=voice)
    agent = HealingAgent(model=model)
    resolved_model = agent.resolve_model(has_image=(image is not None))

    print_banner(
        script_name=script.name,
        has_image=image is not None,
        voice_active=narrator.is_active,
        max_retries=max_retries,
        model=resolved_model,
    )

    if voice and not narrator.is_active:
        console.print(
            "[yellow]Note: --voice requested, but ELEVENLABS_API_KEY is not set. "
            "Proceeding in text-only mode.[/yellow]\n"
        )

    # 1. Initial Execution
    console.print(f"[bold cyan]▶ Executing script:[/bold cyan] {script.name}...")
    initial_res = run_script(script)

    if initial_res.success:
        console.print(
            Panel(
                f"[bold green]✔ Script executed successfully with returncode 0![/bold green]\n"
                f"[dim]Duration: {initial_res.duration:.2f}s[/dim]\n\n"
                f"{initial_res.stdout.strip() if initial_res.stdout else '(No standard output)'}",
                title="[bold green]Success (No Healing Required)[/bold green]",
                border_style="green",
            )
        )
        return

    # Crash intercepted!
    crash_desc = f"{initial_res.error_type or 'Exception'}"
    if initial_res.error_line:
        crash_desc += f" on line {initial_res.error_line}"
    if initial_res.error_message:
        crash_desc += f": {initial_res.error_message}"

    console.print(
        Panel(
            Text(initial_res.stderr.strip() or f"Process failed with exit code {initial_res.returncode}", style="bright_red"),
            title=f"[bold red]💥 Intercepted Crash: {initial_res.error_category.value} ({crash_desc})[/bold red]",
            subtitle="Autonomous Healing Activated",
            border_style="red",
        )
    )

    narrator.narrate_crash(
        error_type=initial_res.error_type or "Crash",
        line_num=initial_res.error_line,
        message=initial_res.error_message,
    )

    # 2. Create pristine baseline backup before touching code
    backup_file = create_backup(script)
    console.print(f"[dim]Created pre-healing safety snapshot: {backup_file.name}[/dim]\n")

    current_code = script.read_text(encoding="utf-8")
    last_res = initial_res
    previous_attempt_errors: List[str] = []

    # 3. Iterative Autonomous Self-Correction Loop
    for attempt in range(1, max_retries + 1):
        console.print(Rule(f"[bold yellow]Repair Cycle: Attempt {attempt} of {max_retries}[/bold yellow]"))
        narrator.narrate_patch(attempt, max_retries)

        # Call Reasoning Core
        try:
            with console.status(
                f"[bold cyan]Gemma ({resolved_model}) analyzing code and synthesizing patch...[/bold cyan]",
                spinner="dots",
            ):
                response_text = agent.diagnose_and_patch(
                    script_path=script,
                    script_code=current_code,
                    stderr=last_res.stderr,
                    stdout=last_res.stdout,
                    returncode=last_res.returncode,
                    error_category=last_res.error_category.value,
                    attempt=attempt,
                    max_retries=max_retries,
                    image_path=image,
                    previous_attempts=previous_attempt_errors,
                )

            # Extract clean validated Python source
            new_code = extract_code(response_text)

        except MissingAPIKeyError as e:
            console.print(Panel(f"[bold red]{e}[/bold red]", title="Configuration Error", border_style="red"))
            sys.exit(1)
        except (HealingAgentException, PatchError) as e:
            console.print(f"[bold red]✖ Synthesis failed on attempt {attempt}:[/bold red] {e}")
            previous_attempt_errors.append(str(e))
            continue

        # Render rich syntax diff
        diff_panel = create_diff_renderable(current_code, new_code, filename=script.name)
        console.print(diff_panel)

        # Atomically apply patch to target script
        apply_patch(script, new_code)
        console.print(f"[bold green]✔ Patch atomically applied to[/bold green] [cyan]{script.name}[/cyan]. Re-evaluating...")

        # Re-evaluate patched script
        eval_res = run_script(script)
        if eval_res.success:
            # Verified Resolution!
            console.print("\n")
            console.print(
                Panel(
                    Align.center(
                        Text.assemble(
                            ("✨ AUTONOMOUS RESOLUTION VERIFIED ✨\n\n", "bold bright_green"),
                            (f"Script '{script.name}' executed cleanly with Exit Code 0\n", "bold white"),
                            (f"Total Cycles: {attempt} | Execution Time: {eval_res.duration:.2f}s | Safety Backup: {backup_file.name}\n\n", "dim"),
                            ("STDOUT OUTPUT:\n", "bold cyan"),
                            (eval_res.stdout.strip() if eval_res.stdout else "(Script exited silently with 0)", "green"),
                        )
                    ),
                    title="[bold green]Success[/bold green]",
                    border_style="bright_green",
                    padding=(1, 2),
                )
            )
            narrator.narrate_success(attempt)
            return

        # Patched code still failed: record and retry
        console.print(
            f"[yellow]⚠ Patched execution returned code {eval_res.returncode} "
            f"({eval_res.error_type or 'Error'} on line {eval_res.error_line or '?'}).[/yellow]"
        )
        current_code = new_code
        last_res = eval_res
        previous_attempt_errors.append(
            f"Attempt #{attempt} failed with {eval_res.error_type}: {eval_res.error_message or eval_res.stderr.strip()[:100]}"
        )

    # 4. Deterministic Rollback if retry budget exhausted
    console.print("\n")
    console.print(Rule("[bold red]Retry Budget Exhausted[/bold red]"))
    restored, msg = restore_backup(script)
    if restored:
        console.print(f"[bold yellow]Deterministic Rollback:[/bold yellow] {msg}")
    else:
        console.print(f"[bold red]Rollback warning:[/bold red] {msg}")

    narrator.narrate_failure()
    console.print(
        Panel(
            f"[bold red]Self-healing agent was unable to resolve all errors in {max_retries} attempts.[/bold red]\n"
            f"Target file has been deterministically restored to its original state.",
            title="[bold red]Self-Healing Unsuccessful[/bold red]",
            border_style="red",
        )
    )
    sys.exit(1)


@app.command()
def revert(
    script: Path = typer.Argument(
        ...,
        help="Path to the target Python script to restore from its .bak file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
):
    """
    Restores the target Python script to its pristine state using its backup (.bak).
    """
    restored, msg = restore_backup(script)
    if restored:
        console.print(
            Panel(
                f"[bold green]✔ {msg}[/bold green]",
                title="[bold green]Rollback Successful[/bold green]",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel(
                f"[bold red]✖ {msg}[/bold red]\n"
                f"Ensure a '{script.name}.bak' exists in {script.parent}.",
                title="[bold red]Rollback Failed[/bold red]",
                border_style="red",
            )
        )
        sys.exit(1)


@app.command()
def info():
    """
    Displays environment configuration, API key status, active models, and hackathon tracks.
    """
    title = Text.assemble(
        ("⚡ HEAL Telemetry & Environment Info ", "bold bright_white on deep_sky_blue4"),
        (f" v{__version__} ", "bold white on grey23"),
    )

    gemini_key = os.environ.get("GEMINI_API_KEY")
    eleven_key = os.environ.get("ELEVENLABS_API_KEY")
    
    masked_gemini = f"...{gemini_key[-4:]}" if gemini_key and len(gemini_key) > 4 else ("[red]Not Set[/red]")
    masked_eleven = f"...{eleven_key[-4:]}" if eleven_key and len(eleven_key) > 4 else ("[yellow]Not Set (Optional)[/yellow]")

    table = Table(title="Hackathon Tracks & Config Status", border_style="cyan")
    table.add_column("Category", style="bold cyan")
    table.add_column("Status / Value", style="white")

    table.add_row("Main Track", "Autonomous Agents (Execute -> Catch -> Reason -> Patch -> Verify)")
    table.add_row("Gemma Challenge", "Direct Gemma integration via google-genai + Multimodal reasoning")
    table.add_row("ElevenLabs Challenge", "Low-latency voice telemetry integration (--voice flag)")
    table.add_row("DigitalOcean / Deploy", "Docker & Render ready configurations included")
    table.add_row("GEMINI_API_KEY", masked_gemini if gemini_key else "[red]Missing (Required for AI)[/red]")
    table.add_row("ELEVENLABS_API_KEY", masked_eleven)
    table.add_row("Default Text Model", os.environ.get("HEAL_TEXT_MODEL", "gemma-2-27b-it"))
    table.add_row("Default Multimodal Model", os.environ.get("HEAL_MULTIMODAL_MODEL", "gemini-2.5-flash"))

    console.print(Panel(table, title=title, border_style="bright_blue"))


def main():
    try:
        app()
    except Exception as e:
        console.print(f"[bold red]Unexpected CLI error:[/bold red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
