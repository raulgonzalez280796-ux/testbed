"""Provider-neutral message types and the adapter interface.

An agent keeps one history of these neutral messages. Its adapter turns them
into that provider's request format on every call. Assistant replies keep the
provider's native content (`provider_blocks`) so it can be replayed verbatim:
reasoning models need their thinking/reasoning items back unchanged.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema of the arguments object


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    call_id: str
    name: str
    output: str
    is_error: bool = False


@dataclass
class UserInput:
    text: str


@dataclass
class AssistantReply:
    text: str
    tool_calls: list[ToolCall]
    provider_blocks: Any  # native content, replayed verbatim by the same adapter


@dataclass
class ToolResults:
    results: list[ToolResult]


Message = UserInput | AssistantReply | ToolResults


@dataclass
class Usage:
    input_tokens: int = 0         # all input, including cached
    output_tokens: int = 0        # including reasoning
    cached_input_tokens: int = 0  # read from the provider's prompt cache
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, other: Usage) -> None:
        for name in self.__dataclass_fields__:
            setattr(self, name, getattr(self, name) + getattr(other, name))

    def as_dict(self) -> dict:
        return {**{name: getattr(self, name) for name in self.__dataclass_fields__}, "total": self.total}


@dataclass
class ModelResponse:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str | None
    truncated: bool  # cut off by the output limit; tool calls must not run
    usage: Usage
    model: str | None
    request_id: str | None
    raw_request: dict
    raw_response: dict
    provider_blocks: Any = None

    @property
    def is_empty(self) -> bool:
        return not self.provider_blocks


class ProviderError(Exception):
    """A failed model call, classified so the engine never mistakes it for behavior."""

    def __init__(
        self,
        kind: str,  # rate_limit | auth | bad_request | not_found | server | connection | timeout | other
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
        raw_request: dict | None = None,
        error_type: str | None = None,
    ):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after
        self.raw_request = raw_request
        self.error_type = error_type or kind

    @property
    def retryable(self) -> bool:
        return self.kind in {"rate_limit", "server", "connection", "timeout"}


@dataclass
class ProviderInfo:
    name: str
    label: str
    env_key: str
    key_url: str
    sdk: str
    models: list[str] = field(default_factory=list)


class ProviderAdapter(ABC):
    """One per provider. Adding a provider = one subclass + one registry entry."""

    name: ClassVar[str]
    label: ClassVar[str]
    env_key: ClassVar[str]
    key_url: ClassVar[str]
    sdk: ClassVar[str]  # package name, recorded in manifests

    def __init__(self, api_key: str, *, timeout: float = 60.0, max_retries: int = 2):
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries

    @abstractmethod
    def build_request(
        self, *, model: str, system: str, messages: list[Message],
        tools: list[ToolSpec], params: dict,
    ) -> dict:
        """The exact keyword arguments passed to the SDK call."""

    @abstractmethod
    async def send(self, request: dict) -> ModelResponse:
        """Call the SDK with `request`; raise ProviderError on failure."""

    async def complete(
        self, *, model: str, system: str, messages: list[Message],
        tools: list[ToolSpec], params: dict,
    ) -> ModelResponse:
        request = self.build_request(
            model=model, system=system, messages=messages, tools=tools, params=params,
        )
        return await self.send(request)

    @abstractmethod
    async def test_key(self) -> None:
        """Make one tiny request; raise ProviderError if the key does not work."""

    async def aclose(self) -> None:
        return None


def jsonable(value: Any) -> Any:
    """Plain-JSON copy of SDK request or response data, for the log only (from v1/agents.py)."""
    return json.loads(json.dumps(
        value,
        default=lambda item: (
            item.model_dump(mode="json") if hasattr(item, "model_dump") else repr(item)
        ),
    ))


def split_params(params: dict) -> tuple[dict, bool]:
    """Separate the testbed's own key ("cache") from provider parameters."""
    params = dict(params)
    cache = bool(params.pop("cache", True))
    return params, cache


def tool_output_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(output)
