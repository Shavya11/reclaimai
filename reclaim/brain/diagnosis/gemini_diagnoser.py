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

from ...config import settings
from ...models import Diagnosis
from .llm_diagnoser import (
    DIAGNOSIS_TOOL,
    MAX_TOKENS,
    SYSTEM_PROMPT,
    CachedDiagnoser,
    _validate,
    build_context,
)

TOOL_NAME = DIAGNOSIS_TOOL["name"]


class GeminiDiagnoser(CachedDiagnoser):
    def __init__(self, client=None, model: str | None = None) -> None:
        super().__init__(client, model or settings.gemini_model)
        if self._client is None and settings.has_gemini:
            from google import genai

            self._client = genai.Client(api_key=settings.gemini_api_key)

    def _ask(self, record, signal) -> Diagnosis | None:
        from google.genai import types

        self.calls += 1
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=MAX_TOKENS,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=TOOL_NAME,
                            description=DIAGNOSIS_TOOL["description"],
                            parameters_json_schema=DIAGNOSIS_TOOL["input_schema"],
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
        response = self._client.models.generate_content(
            model=self.model,
            contents=_prompt(record, signal),
            config=config,
        )
        for call in response.function_calls or []:
            if call.name == TOOL_NAME:
                return _validate(dict(call.args or {}))
        return None


def _prompt(record, signal) -> str:
    import json

    return json.dumps(build_context(record, signal), indent=2)
