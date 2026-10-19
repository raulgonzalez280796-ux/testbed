"""OpenAI Responses API adapter (official `openai` SDK).

Stateless on purpose: store=False, so every request carries the full history
and the logged request is the whole stimulus. Reasoning items come back
encrypted and are replayed verbatim on the next call.
"""

from __future__ import annotations

import json

import openai

from .base import (
    AssistantReply, Message, ModelResponse, ProviderAdapter, ProviderError,
    ToolCall, ToolResults, Usage, UserInput, jsonable, split_params,
)
from .errors import classify_stainless


class OpenAIAdapter(ProviderAdapter):
    name = "openai"
    label = "OpenAI"
    env_key = "OPENAI_API_KEY"
    key_url = "https://platform.openai.com/api-keys"
    sdk = "openai"

    def __init__(self, api_key: str, *, timeout: float = 60.0, max_retries: int = 2):
        super().__init__(api_key, timeout=timeout, max_retries=max_retries)
        self.client = openai.AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)

    def build_request(self, *, model, system, messages, tools, params) -> dict:
        params, _cache = split_params(params)  # OpenAI caches prompt prefixes automatically
        request: dict = {"model": model}
        if system:
            request["instructions"] = system
        request["input"] = to_openai_input(messages)
        if tools:
            request["tools"] = [
                {"type": "function", "name": spec.name, "description": spec.description,
                 "parameters": spec.parameters, "strict": False}
                for spec in tools
            ]
        request["store"] = False
        request["include"] = ["reasoning.encrypted_content"]
        if "max_tokens" in params:  # same name as the other providers
            request["max_output_tokens"] = params.pop("max_tokens")
        request.update(params)
        return request

    async def send(self, request: dict) -> ModelResponse:
        try:
            response = await self.client.responses.create(**request)
        except openai.APIError as error:
            raise classify_stainless(error, openai, request) from error
        return parse_response(request, response)

    async def test_key(self) -> None:
        try:
            await self.client.models.list()
        except openai.APIError as error:
            raise classify_stainless(error, openai, {}) from error

    async def aclose(self) -> None:
        await self.client.close()


def to_openai_input(messages: list[Message]) -> list[dict]:
    items: list[dict] = []
    for message in messages:
        if isinstance(message, UserInput):
            items.append({"role": "user", "content": [{"type": "input_text", "text": message.text}]})
        elif isinstance(message, AssistantReply):
            items.extend(message.provider_blocks)
        elif isinstance(message, ToolResults):
            # The Responses API has no error flag for tool output; the message says what failed.
            items.extend(
                {"type": "function_call_output", "call_id": result.call_id, "output": result.output}
                for result in message.results
            )
    return items


def parse_arguments(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_invalid_json": raw}  # fails argument validation -> an error result to the model
    return value if isinstance(value, dict) else {"_not_an_object": value}


def parse_response(request: dict, response) -> ModelResponse:
    items = [item.model_dump(mode="json", exclude_none=True) for item in response.output]
    calls = [
        ToolCall(id=item.call_id, name=item.name, arguments=parse_arguments(item.arguments))
        for item in response.output if item.type == "function_call"
    ]
    incomplete = getattr(response.incomplete_details, "reason", None) if response.incomplete_details else None
    usage = response.usage
    cached = (usage.input_tokens_details.cached_tokens or 0) if usage and usage.input_tokens_details else 0
    reasoning = (usage.output_tokens_details.reasoning_tokens or 0) if usage and usage.output_tokens_details else 0
    return ModelResponse(
        text=response.output_text,
        tool_calls=calls,
        stop_reason=incomplete or ("tool_calls" if calls else response.status),
        truncated=incomplete == "max_output_tokens",
        usage=Usage(
            input_tokens=(usage.input_tokens or 0) if usage else 0,
            output_tokens=(usage.output_tokens or 0) if usage else 0,
            cached_input_tokens=cached,
            reasoning_tokens=reasoning,
        ),
        model=response.model,
        request_id=getattr(response, "_request_id", None),
        raw_request=jsonable(request),
        raw_response=jsonable(response),
        provider_blocks=items,
    )
