"""
Voice Narration Module for heal using ElevenLabs API.
Provides low-latency spoken telemetry for autonomous debugging events.
Silently degrades to no-op text mode if ELEVENLABS_API_KEY is not configured.
"""

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Default voice: Rachel ("21m00Tcm4TlvDq8ikWAM")
DEFAULT_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
DEFAULT_MODEL_ID = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")


class VoiceNarrator:
    def __init__(self, enabled: bool = False, api_key: Optional[str] = None):
        self.enabled = enabled
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        self._client = None
        self._available = False

        if self.enabled:
            if not self.api_key:
                logger.debug("Voice narration requested but ELEVENLABS_API_KEY is not set.")
                self._available = False
            else:
                try:
                    from elevenlabs.client import ElevenLabs
                    self._client = ElevenLabs(api_key=self.api_key)
                    self._available = True
                except Exception as e:
                    logger.debug(f"Failed to initialize ElevenLabs client: {e}")
                    self._available = False

    @property
    def is_active(self) -> bool:
        return self.enabled and self._available

    def speak(self, text: str, block: bool = False) -> None:
        """
        Synthesizes and plays vocal telemetry.
        If block is False, runs in a background daemon thread to keep terminal UI responsive.
        """
        if not self.is_active or not text.strip():
            return

        def _play_worker():
            try:
                from elevenlabs import play

                # Request TTS audio stream
                audio_stream = self._client.text_to_speech.convert(
                    voice_id=DEFAULT_VOICE_ID,
                    text=text,
                    model_id=DEFAULT_MODEL_ID,
                    output_format="mp3_44100_128",
                )
                play(audio_stream)
            except Exception as e:
                # Never crash the main CLI workflow on audio failure
                logger.debug(f"Voice narration playback failed: {e}")

        if block:
            _play_worker()
        else:
            thread = threading.Thread(target=_play_worker, daemon=True)
            thread.start()

    def narrate_crash(self, error_type: str, line_num: Optional[int], message: Optional[str]):
        """Vocally announces the detected exception."""
        line_info = f"on line {line_num}" if line_num else ""
        summary = f"{error_type} detected {line_info}."
        if message:
            # Keep spoken message punchy
            clean_msg = message.splitlines()[0][:80]
            summary += f" Reason: {clean_msg}."
        summary += " Initiating autonomous diagnosis."
        self.speak(summary)

    def narrate_patch(self, attempt: int, total_attempts: int):
        """Vocally announces code synthesis."""
        self.speak(f"Synthesizing repair attempt {attempt} of {total_attempts} with Gemma.")

    def narrate_success(self, attempts: int):
        """Vocally announces resolved execution."""
        if attempts == 1:
            self.speak("Self healing verified. Script executed with exit code zero.", block=True)
        else:
            self.speak(f"Self healing verified after {attempts} attempts. Script successfully executed.", block=True)

    def narrate_failure(self):
        """Vocally announces exhaustion of retry budget."""
        self.speak("Autonomous repair budget exhausted. Reverting changes to original backup.", block=True)
