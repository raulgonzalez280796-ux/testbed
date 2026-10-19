"""Anthropic Messages API adapter (official `anthropic` SDK)."""

from __future__ import annotations

import anthropic

from .base import (
    AssistantReply, Message, ModelResponse, ProviderAdapter, ProviderError,
    ToolCall, ToolResults, ToolSpec, Usage, UserInput, jsonable, split_params,
)

DEFAULT_MAX_TOKENS = 16_000


class AnthropicAdapter(ProviderAdapter):
    name = "anthropic"
    label = "Anthropic"
    env_key = "ANTHROPIC_API_KEY"
    key_url = "https://platform.claude.com/settings/keys"
    sdk = "anthropic"

    def __init__(self, api_key: str, *, timeout: float = 60.0, max_retries: int = 2):
        super().__init__(api_key, timeout=timeout, max_retries=max_retries)
        self.client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries,
        )

    def build_request(self, *, model, system, messages, tools, params) -> dict:
        params, cache = split_params(params)
        request: dict = {"model": model, "max_tokens": params.pop("max_tokens", DEFAULT_MAX_TOKENS)}
        if system:
            request["system"] = system
        if tools:
            request["tools"] = [
                {"name": spec.name, "description": spec.description, "input_schema": spec.parameters}
                for spec in tools
            ]
        request["messages"] = to_anthropic_messages(messages)
        if cache:
            # Caches the longest stable prefix (tools, system, history) automatically.
            request["cache_control"] = {"type": "ephemeral"}
        request.update(params)  # researcher's params win, e.g. output_config or temperature
        return request

    async def send(self, request: dict) -> ModelResponse:
        try:
            response = await self.client.messages.create(**request)
        except anthropic.APIError as error:
            raise classify(error, request) from error
        return parse_response(request, response)

    async def test_key(self) -> None:
        try:
            await self.client.models.list(limit=1)
        except anthropic.APIError as error:
            raise classify(error, {}) from error

    async def aclose(self) -> None:
        await self.client.close()


def to_anthropic_messages(messages: list[Message]) -> list[dict]:
    """Neutral history -> Messages API `messages`.

    Tool results and the next user text share one user message, with the
    tool_result blocks first, as the API requires after a tool_use turn.
    """
    converted: list[dict] = []

    def user_blocks() -> list:
        if converted and converted[-1]["role"] == "user":
            return converted[-1]["content"]
        converted.append({"role": "user", "content": []})
        return converted[-1]["content"]

    for message in messages:
        if isinstance(message, UserInput):
            user_blocks().append({"type": "text", "text": message.text})
        elif isinstance(message, ToolResults):
            blocks = user_blocks()
            for result in message.results:
                blocks.append({
                    "type": "tool_result",
                    "tool_use_id": result.call_id,
                    "content": result.output,
                    "is_error": result.is_error,
                })
        elif isinstance(message, AssistantReply):
            converted.append({"role": "assistant", "content": list(message.provider_blocks)})
    return converted


def parse_response(request: dict, response) -> ModelResponse:
    blocks = []
    for block in response.content:
        data = block.model_dump(mode="json", exclude_none=True)
        if data.get("type") == "text" and not data.get("text"):
            continue  # the API rejects empty text blocks on replay
        blocks.append(data)
    text = "".join(block.text for block in response.content if block.type == "text")
    calls = [
        ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {}))
        for block in response.content if block.type == "tool_use"
    ]
    usage = response.usage
    cached = usage.cache_read_input_tokens or 0
    written = usage.cache_creation_input_tokens or 0
    return ModelResponse(
        text=text,
        tool_calls=calls,
        stop_reason=response.stop_reason,
        truncated=response.stop_reason in {"max_tokens", "model_context_window_exceeded"},
        usage=Usage(
            input_tokens=(usage.input_tokens or 0) + cached + written,
            output_tokens=usage.output_tokens or 0,
            cached_input_tokens=cached,
            cache_write_tokens=written,
        ),
        model=response.model,
        request_id=getattr(response, "_request_id", None),
        raw_request=jsonable(request),
        raw_response=jsonable(response),
        provider_blocks=blocks,
    )


def classify(error: anthropic.APIError, request: dict) -> ProviderError:
    status = getattr(error, "status_code", None)
    if isinstance(error, anthropic.RateLimitError):
        kind = "rate_limit"
    elif isinstance(error, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        kind = "auth"
    elif isinstance(error, anthropic.NotFoundError):
        kind = "not_found"
    elif isinstance(error, anthropic.APITimeoutError):
        kind = "timeout"
    elif isinstance(error, anthropic.APIConnectionError):
        kind = "connection"
    elif isinstance(error, anthropic.APIStatusError) and status and status >= 500:
        kind = "server"
    elif isinstance(error, anthropic.APIStatusError) and status and status < 500:
        kind = "bad_request"
    else:
        kind = "other"
    retry_after = None
    response = getattr(error, "response", None)
    if response is not None:
        try:
            retry_after = float(response.headers.get("retry-after"))
        except (TypeError, ValueError):
            retry_after = None
    return ProviderError(
        kind, str(getattr(error, "message", error)), status_code=status,
        retry_after=retry_after, raw_request=jsonable(request) if request else None,
        error_type=type(error).__name__,
    )
