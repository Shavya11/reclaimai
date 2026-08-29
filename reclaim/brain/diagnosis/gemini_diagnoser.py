"""Layer 2, on Gemini. Same contract, different vendor.

The prompt, the closed tool schema, the validation and the cache all come from
`llm_diagnoser` — importing them is the point. Two copies of a rule about money
is one copy that goes stale, and the enum the model must choose from is the
thing standing between a hallucination and a payment.

Gemini forces a call differently to Anthropic: `tool_config` with mode ANY and
`allowed_function_names` is the equivalent of `tool_choice={"type": "tool"}`.
The guarantee is identical — the model cannot answer in prose, only fill in the
schema — and a filled-in schema that fails Pydantic is still an UNKNOWN, not a
crash.
"""

import logging
import random
import threading
import time

from ...config import settings
from ...models import Diagnosis
from .llm_diagnoser import (
    DIAGNOSIS_TOOL,
    MAX_TOKENS,
    CachedDiagnoser,
    _validate,
    build_context,
    prompt_for,
    tool_for,
)

log = logging.getLogger(__name__)

TOOL_NAME = DIAGNOSIS_TOOL["name"]

# The free tier is metered per minute as well as per day, and the per-minute
# cap is the one a batch trips: 120 records through a semaphore of 8 arrive as
# a burst, not a stream. Pacing to just under the documented 15/min costs a
# batch about two minutes and turns a guaranteed 429 into none.
#
# This matters more than it looks. A 429 degrades to UNKNOWN, which is
# indistinguishable in the scoreboard from the model honestly declining to
# guess — a run that was silently rate-limited reads as a run where layer 2
# had nothing to say. Retrying, and logging when we give up, is what keeps
# "layer 2 was throttled" from being reported as "layer 2 found nothing".
MIN_INTERVAL_S = 4.2
MAX_RETRIES = 3


class GeminiDiagnoser(CachedDiagnoser):
    def __init__(self, client=None, model: str | None = None) -> None:
        super().__init__(client, model or settings.gemini_model)
        # Pace only a client we built ourselves. An injected one is a test
        # double, and there is no quota to respect on the other side of it -
        # pacing there would buy nothing and add 4s to every assertion.
        self._throttled = client is None
        if self._client is None and settings.has_gemini:
            from google import genai

            self._client = genai.Client(api_key=settings.gemini_api_key)

    _pace_lock = threading.Lock()
    _last_call = 0.0

    @classmethod
    def _pace(cls) -> None:
        """One in flight per interval, across every instance and thread."""
        with cls._pace_lock:
            wait = cls._last_call + MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            cls._last_call = time.monotonic()

    def _ask(self, record, signal=None) -> Diagnosis | None:
        from google.genai import types

        self.calls += 1
        tool = tool_for(record)
        config = types.GenerateContentConfig(
            system_instruction=prompt_for(record),
            max_output_tokens=MAX_TOKENS,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=TOOL_NAME,
                            description=tool["description"],
                            parameters_json_schema=tool["input_schema"],
                        )
                    ]
                )
            ],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="ANY", allowed_function_names=[TOOL_NAME]
                )
            ),
        )
        response = self._generate(_prompt(record, signal), config)
        if response is None:
            return None
        for call in response.function_calls or []:
            if call.name == TOOL_NAME:
                return _validate(dict(call.args or {}))
        return None


    def _generate(self, contents, config):
        """Bounded retry on 429 only. Every other failure belongs to the caller's
        fallback chain — retrying a 400 just spends quota to fail again."""
        for attempt in range(MAX_RETRIES):
            if self._throttled:
                self._pace()
            try:
                return self._client.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
            except Exception as exc:  # noqa: BLE001
                if "429" not in str(exc) and "RESOURCE_EXHAUSTED" not in str(exc):
                    raise
                if attempt == MAX_RETRIES - 1:
                    log.warning(
                        "gemini rate limit not cleared after %d attempts (%s); "
                        "this record becomes UNKNOWN through throttling, not "
                        "through the model declining to guess",
                        MAX_RETRIES, self.model,
                    )
                    return None
                time.sleep(_retry_delay(str(exc), attempt))
        return None


def _retry_delay(message: str, attempt: int) -> float:
    """Honour the server's own retryDelay when it sends one; back off if not."""
    import re

    m = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+(?:\.\d+)?)", message)
    if m:
        return min(float(m.group(1)) + 1.0, 45.0)
    return min(2.0 ** attempt * 5.0, 45.0) + random.uniform(0, 1.5)


def _prompt(record, signal) -> str:
    import json

    return json.dumps(build_context(record, signal), indent=2)
