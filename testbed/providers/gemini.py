"""Google Gemini adapter (official `google-genai` SDK).

The SDK's automatic function calling is turned off: the testbed runs tools
itself and logs them. Each model reply's Content (including thought
signatures) is replayed verbatim on the next call.
"""

from __future__ import annotations

import itertools

import httpx
import pydantic
from google import genai
from google.genai import errors, types

from .base import (
    AssistantReply, Message, ModelResponse, ProviderAdapter, ProviderError,
    ToolCall, ToolResults, Usage, UserInput, jsonable, split_params,
)
from .errors import kind_for_status

GENERATED_ID = "testbed-call-"  # Gemini may omit call ids; ours are never sent back
_ids = itertools.count(1)


class GeminiAdapter(ProviderAdapter):
    name = "gemini"
    label = "Google Gemini"
    env_key = "GEMINI_API_KEY"
    env_aliases = ("GOOGLE_API_KEY",)
    key_url = "https://aistudio.google.com/apikey"
    sdk = "google-genai"

    def __init__(self, api_key: str, *, timeout: float = 60.0, max_retries: int = 2):
        super().__init__(api_key, timeout=timeout, max_retries=max_retries)
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout * 1000),
                retry_options=types.HttpRetryOptions(attempts=max_retries + 1),
            ),
        )

    def build_request(self, *, model, system, messages, tools, params) -> dict:
        params, _cache = split_params(params)  # Gemini caches implicitly
        config: dict = {}
        if system:
            config["system_instruction"] = system
        if tools:
            config["tools"] = [{"function_declarations": [
                {"name": spec.name, "description": spec.description,
                 "parameters_json_schema": spec.parameters}
                for spec in tools
            ]}]
        config["automatic_function_calling"] = {"disable": True}
        if "max_tokens" in params:  # same name as the other providers
            config["max_output_tokens"] = params.pop("max_tokens")
        config.update(params)
        return {"model": model, "contents": to_gemini_contents(messages), "config": config}

    async def send(self, request: dict) -> ModelResponse:
        # Validate into the SDK's types first, so a bad parameter fails clearly
        # before anything is sent. (Field names may stay snake_case on the wire:
        # the REST API accepts both proto field names and lowerCamelCase.)
        try:
            typed = {
                "model": request["model"],
                "contents": [types.Content.model_validate(item) for item in request["contents"]],
                "config": types.GenerateContentConfig.model_validate(request["config"]),
            }
        except pydantic.ValidationError as error:
            raise ProviderError("bad_request", f"Invalid Gemini request: {error}",
                                raw_request=jsonable(request), error_type="ValidationError") from error
        try:
            response = await self.client.aio.models.generate_content(**typed)
        except errors.APIError as error:
            raise ProviderError(
                kind_for_status(error.code), str(error.message or error), status_code=error.code,
                raw_request=jsonable(request), error_type=type(error).__name__,
            ) from error
        except httpx.TimeoutException as error:
            raise ProviderError("timeout", str(error), raw_request=jsonable(request),
                                error_type=type(error).__name__) from error
        except httpx.HTTPError as error:
            raise ProviderError("connection", str(error), raw_request=jsonable(request),
                                error_type=type(error).__name__) from error
        return parse_response(request, response)

    async def test_key(self) -> None:
        try:
            await self.client.aio.models.list(config={"page_size": 1})
        except errors.APIError as error:
            raise ProviderError(kind_for_status(error.code), str(error.message or error),
                                status_code=error.code, error_type=type(error).__name__) from error

    async def aclose(self) -> None:
        try:
            await self.client.aio.aclose()
        except AttributeError:
            pass


def to_gemini_contents(messages: list[Message]) -> list[dict]:
    contents: list[dict] = []

    def user_parts() -> list:
        if contents and contents[-1]["role"] == "user":
            return contents[-1]["parts"]
        contents.append({"role": "user", "parts": []})
        return contents[-1]["parts"]

    for message in messages:
        if isinstance(message, UserInput):
            user_parts().append({"text": message.text})
        elif isinstance(message, AssistantReply):
            contents.append(message.provider_blocks)
        elif isinstance(message, ToolResults):
            parts = user_parts()
            for result in message.results:
                response = {"name": result.name,
                            "response": {"error" if result.is_error else "result": result.output}}
                if not result.call_id.startswith(GENERATED_ID):
                    response["id"] = result.call_id
                parts.append({"function_response": response})
    return contents


def parse_response(request: dict, response) -> ModelResponse:
    candidate = response.candidates[0] if response.candidates else None
    content = candidate.content if candidate is not None else None
    parts = list(content.parts or []) if content is not None else []
    text = "".join(part.text for part in parts if part.text and not part.thought)
    calls = [
        ToolCall(
            id=part.function_call.id or f"{GENERATED_ID}{next(_ids)}",
            name=part.function_call.name,
            arguments=dict(part.function_call.args or {}),
        )
        for part in parts if part.function_call is not None
    ]
    finish = candidate.finish_reason if candidate is not None else None
    finish_name = getattr(finish, "name", None) or (str(finish) if finish else None)
    usage = response.usage_metadata
    prompt = (usage.prompt_token_count or 0) + (usage.tool_use_prompt_token_count or 0) if usage else 0
    thoughts = (usage.thoughts_token_count or 0) if usage else 0
    output = (usage.candidates_token_count or 0) + thoughts if usage else 0
    blocks = content.model_dump(mode="json", exclude_none=True) if parts else None
    if blocks is not None:
        blocks["role"] = "model"
    raw = response.model_dump(mode="json", exclude_none=True, exclude={"sdk_http_response"})
    if response.prompt_feedback is not None and not parts:
        finish_name = finish_name or "PROMPT_BLOCKED"
    return ModelResponse(
        text=text,
        tool_calls=calls,
        stop_reason=finish_name,
        truncated=finish_name == "MAX_TOKENS",
        usage=Usage(
            input_tokens=prompt, output_tokens=output,
            cached_input_tokens=(usage.cached_content_token_count or 0) if usage else 0,
            reasoning_tokens=thoughts,
        ),
        model=response.model_version or request.get("model"),
        request_id=response.response_id,
        raw_request=jsonable(request),
        raw_response=raw,
        provider_blocks=blocks,
    )
