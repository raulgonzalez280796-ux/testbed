"""A scripted provider for offline tests. Never registered outside tests.

The script for an agent is picked by its model name. The step to play is the
number of assistant replies already in the history, so concurrent trials that
share one adapter each follow their own script.

Step forms:
  "text"                                   a text reply
  {"tool": name, "args": {...}}            one tool call
  {"text": "...", "tools": [{...}, ...]}   text plus several tool calls
  {"error": "auth" | "rate_limit" | ...}   the call fails
  {"empty": True}                          a reply with no content
"""

from __future__ import annotations

import asyncio
import itertools
import random

from testbed import providers
from testbed.providers.base import (
    AssistantReply, ModelResponse, ProviderAdapter, ProviderError, ToolCall, Usage,
)

SCRIPTS: dict[str, list] = {}
REQUESTS: list[dict] = []
FAILURES: dict[tuple, int] = {}  # (model, step) -> failures so far, for {"error", "times"} steps
_ids = itertools.count(1)


class FakeAdapter(ProviderAdapter):
    name = "fake"
    label = "Fake"
    env_key = ""
    key_url = ""
    sdk = "none"
    latency = 0.0
    tokens_per_call = 100
    rate_limit_probability = 0.0  # scale tests: random 429s

    def build_request(self, *, model, system, messages, tools, params) -> dict:
        return {
            "model": model, "system": system, "messages": list(messages),
            "tools": [tool.name for tool in tools], "params": dict(params),
        }

    async def send(self, request: dict) -> ModelResponse:
        REQUESTS.append(request)
        if self.latency:
            await asyncio.sleep(self.latency)
        if self.rate_limit_probability and random.random() < self.rate_limit_probability:
            raise ProviderError("rate_limit", "fake 429", status_code=429, retry_after=0.2,
                                raw_request={"model": request["model"]}, error_type="FakeError")
        script = SCRIPTS.get(request["model"], [])
        step_index = sum(isinstance(m, AssistantReply) for m in request["messages"])
        step = script[step_index] if step_index < len(script) else "done"
        logged = {"model": request["model"], "step": step_index}
        if isinstance(step, dict) and "error" in step:
            key = (request["model"], step_index)
            FAILURES[key] = FAILURES.get(key, 0) + 1
            if "times" not in step or FAILURES[key] <= step["times"]:
                raise ProviderError(step["error"], step.get("message", "fake failure"),
                                    status_code=step.get("status"), retry_after=step.get("retry_after"),
                                    raw_request=logged, error_type="FakeError")
            step = step.get("then", "done")
        if isinstance(step, dict) and step.get("empty"):
            return ModelResponse("", [], "end_turn", False, Usage(10, 0), request["model"],
                                 None, logged, {"empty": True}, provider_blocks=[])
        if isinstance(step, str):
            text, calls = step, []
        else:
            text = step.get("text", "")
            calls = step.get("tools") or ([{"tool": step["tool"], "args": step.get("args", {})}]
                                          if "tool" in step else [])
        tool_calls = [ToolCall(f"call_{next(_ids)}", c["tool"], c.get("args", {})) for c in calls]
        half = self.tokens_per_call // 2
        return ModelResponse(
            text=text, tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn", truncated=False,
            usage=Usage(input_tokens=half, output_tokens=self.tokens_per_call - half),
            model=request["model"], request_id=None, raw_request=logged,
            raw_response={"text": text, "tools": calls},
            provider_blocks=[{"text": text, "tools": calls}],
        )

    async def test_key(self) -> None:
        return None


def install() -> None:
    providers.register("fake", FakeAdapter)
    SCRIPTS.clear()
    REQUESTS.clear()
    FAILURES.clear()
    FakeAdapter.latency = 0.0
    FakeAdapter.tokens_per_call = 100
    FakeAdapter.rate_limit_probability = 0.0
