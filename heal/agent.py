"""
Multimodal Reasoning Core for heal.
Leverages Gemma and Gemini models via the official google-genai SDK
to diagnose Python exceptions, terminal traces, and visual artifacts.
"""

import os
from pathlib import Path
from typing import List, Optional
from PIL import Image

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None  # Handled with friendly error message


class HealingAgentException(Exception):
    """Base exception for agent reasoning errors."""
    pass


class MissingAPIKeyError(HealingAgentException):
    """Raised when GEMINI_API_KEY is not configured."""
    pass


# Default models: Gemma 2 27B for pure code text, Gemini 2.5 Flash for multimodal reasoning
DEFAULT_TEXT_MODEL = os.environ.get("HEAL_TEXT_MODEL", "gemma-2-27b-it")
DEFAULT_MULTIMODAL_MODEL = os.environ.get("HEAL_MULTIMODAL_MODEL", "gemini-2.5-flash")


class HealingAgent:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.custom_model = model or os.environ.get("GEMINI_MODEL") or os.environ.get("HEAL_MODEL")
        self._client = None

    def _get_client(self) -> genai.Client:
        if self._client is not None:
            return self._client

        if not self.api_key:
            raise MissingAPIKeyError(
                "GEMINI_API_KEY is not set. Please set it via environment variable:\n"
                "  export GEMINI_API_KEY='your-api-key'\n"
                "or place it in a .env file."
            )

        if genai is None:
            raise HealingAgentException(
                "The 'google-genai' package is not installed. Run 'pip install google-genai'."
            )

        try:
            self._client = genai.Client(api_key=self.api_key)
            return self._client
        except Exception as e:
            raise HealingAgentException(f"Failed to initialize google-genai client: {e}") from e

    def resolve_model(self, has_image: bool = False) -> str:
        """Determines the appropriate model based on input modalities and overrides."""
        if self.custom_model:
            return self.custom_model
        if has_image:
            return DEFAULT_MULTIMODAL_MODEL
        return DEFAULT_TEXT_MODEL

    def diagnose_and_patch(
        self,
        script_path: Path,
        script_code: str,
        stderr: str,
        stdout: str,
        returncode: int,
        error_category: str,
        attempt: int = 1,
        max_retries: int = 3,
        image_path: Optional[Path] = None,
        previous_attempts: Optional[List[str]] = None,
    ) -> str:
        """
        Submits code, runtime traceback, and optional visual evidence to Gemma/Gemini.
        Enforces strict markdown code block formatting for deterministic code extraction.
        """
        client = self._get_client()
        has_image = image_path is not None and image_path.exists()
        model_name = self.resolve_model(has_image=has_image)

        # Build prompt instructions
        prompt_parts = []

        system_prompt = (
            "You are an elite autonomous debugging and self-healing systems engineer. "
            "Your task is to fix broken Python scripts so they execute with exit code 0. "
            "\nSTRICT OUTPUT REQUIREMENTS:\n"
            "1. Output ONLY the complete, corrected Python script inside a single ```python ... ``` markdown block.\n"
            "2. Do NOT write conversational preamble, introductory text, explanations, or commentary outside the code block.\n"
            "3. Ensure the repaired code preserves the original logic, fixes all runtime/syntax errors, and handles edge cases."
        )

        user_content = [
            f"### Target File: {script_path.name}\n",
            f"### Execution Attempt: {attempt} of {max_retries}\n",
            f"### Error Category: {error_category}\n",
            f"### Process Exit Code: {returncode}\n\n",
            "### Current Python Source Code:\n",
            f"```python\n{script_code}\n```\n\n",
            "### Captured STDERR & Crash Traceback:\n",
            f"```text\n{stderr.strip() if stderr.strip() else '(No stderr output)'}\n```\n\n",
        ]

        if stdout.strip():
            user_content.append(
                f"### Captured STDOUT:\n```text\n{stdout.strip()}\n```\n\n"
            )

        if previous_attempts:
            user_content.append("### Previous Failed Repair Attempts:\n")
            for idx, prev_err in enumerate(previous_attempts, 1):
                user_content.append(f"- Attempt #{idx} failed with: {prev_err}\n")
            user_content.append("\nAvoid repeating the mistakes made in earlier repair attempts.\n\n")

        if has_image:
            user_content.append(
                "### Multimodal Visual Diagnostic:\n"
                "A screenshot/image of the terminal crash, UI failure, or corrupted output "
                "is attached. Inspect the visual artifacts closely to diagnose layout issues, "
                "rendering bugs, or visual exception details.\n\n"
            )

        user_content.append(
            "Synthesize and output the entire corrected Python script now inside a ```python``` block:"
        )

        combined_text = "".join(user_content)

        # Prepare contents for google-genai
        contents = []
        if has_image:
            try:
                img = Image.open(image_path)
                contents.append(img)
            except Exception as e:
                raise HealingAgentException(f"Failed to open diagnostic image '{image_path}': {e}") from e

        contents.append(f"{system_prompt}\n\n{combined_text}")

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
            )
            if not response or not response.text:
                raise HealingAgentException("Received empty response from the AI model.")
            return response.text
        except Exception as e:
            if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
                raise HealingAgentException(
                    f"API Quota exceeded or rate limited while querying {model_name}: {e}"
                ) from e
            elif "NOT_FOUND" in str(e) or "404" in str(e):
                raise HealingAgentException(
                    f"Model '{model_name}' was not found. Try setting --model to an available model."
                ) from e
            raise HealingAgentException(f"AI generation failed ({model_name}): {e}") from e
