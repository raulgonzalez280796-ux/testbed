"""Each adapter, end to end through its real SDK, against a mock HTTP transport.

No network and no keys: the SDK serializes our request exactly as it would
for the real API, and parses provider-shaped JSON responses. Each test runs a
two-call trial (tool call, then text) and checks what was sent back to the
provider on the second call: reasoning state replayed verbatim, tool results
in the provider's format.
"""

import json

import anthropic
import httpx
import httpx2
import openai
from google.genai import types

from testbed import providers
from testbed.providers.anthropic import AnthropicAdapter
from testbed.providers.gemini import GeminiAdapter
from testbed.providers.openai import OpenAIAdapter

from .helpers import HomeTestCase, agent

FILES = [{"name": "log.txt", "content": "hello", "access": {"A": ["read"]}}]


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bodies = []

    def __call__(self, request):
        self.bodies.append(json.loads(request.content or b"{}"))
        return self.responses.pop(0)


def install(name, base, make_client, recorder):
    class Mocked(base):
        def __init__(self, api_key, **options):
            super().__init__(api_key, **options)
            self.client = make_client(api_key, recorder)

    providers.register(name, Mocked)
    return Mocked


class AdapterHttpTests(HomeTestCase):
    def tearDown(self):
        for name in ("anthropic", "openai", "gemini"):
            providers.unregister(name)
        super().tearDown()

    async def trial(self, provider, model):
        path = self.scenario(files=FILES, agents=[agent(
            "A", provider=provider, model=model, turns=1, tools=["read_file"],
            system_prompt="Be brief.")])
        batch_dir, summary = await self.run_scenario(path, keys={provider: "test-key-not-real"})
        return batch_dir, summary

    async def test_openai_responses_round_trip(self):
        first = {
            "id": "resp_1", "object": "response", "created_at": 1, "model": "gpt-6-luna",
            "status": "completed", "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
            "output": [
                {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "ENCRYPTED"},
                {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "read_file",
                 "arguments": "{\"name\": \"log.txt\"}", "status": "completed"},
            ],
            "usage": {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 40},
                      "output_tokens": 20, "output_tokens_details": {"reasoning_tokens": 12},
                      "total_tokens": 120},
        }
        second = {
            **first, "id": "resp_2",
            "output": [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "It says hello.", "annotations": []}]}],
        }
        recorder = Recorder([httpx2.Response(200, json=first), httpx2.Response(200, json=second)])
        install("openai", OpenAIAdapter, lambda key, rec: openai.AsyncOpenAI(
            api_key=key, max_retries=0,
            http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(rec)),
        ), recorder)
        batch_dir, summary = await self.trial("openai", "gpt-6-luna")
        self.assertEqual(summary["counts"], {"completed": 1})
        one, two = recorder.bodies
        self.assertEqual(one["instructions"], "Be brief.")
        self.assertIs(one["store"], False)
        self.assertEqual(one["include"], ["reasoning.encrypted_content"])
        self.assertEqual(one["tools"][0]["name"], "read_file")
        self.assertEqual([item.get("type", item.get("role")) for item in two["input"]],
                         ["user", "reasoning", "function_call", "function_call_output"])
        self.assertEqual(two["input"][1]["encrypted_content"], "ENCRYPTED")
        self.assertEqual(two["input"][3], {"type": "function_call_output", "call_id": "call_1", "output": "hello"})
        result = self.result(batch_dir)
        self.assertEqual(result["tokens"]["per_agent"]["A"]["cached_input_tokens"], 80)
        self.assertEqual(result["tokens"]["per_agent"]["A"]["reasoning_tokens"], 24)
        turn = self.of(self.events(batch_dir), "turn_finished")[0]
        self.assertEqual(turn["text"], "It says hello.")

    async def test_gemini_round_trip_keeps_thought_signatures(self):
        first = {
            "candidates": [{"content": {"role": "model", "parts": [
                {"functionCall": {"name": "read_file", "args": {"name": "log.txt"}},
                 "thoughtSignature": "c2lnbmF0dXJl"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 10,
                              "thoughtsTokenCount": 5, "cachedContentTokenCount": 30,
                              "totalTokenCount": 115},
            "modelVersion": "gemini-3.5-flash-lite", "responseId": "r1",
        }
        second = {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "It says hello."}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 130, "candidatesTokenCount": 6, "totalTokenCount": 136},
            "modelVersion": "gemini-3.5-flash-lite", "responseId": "r2",
        }
        recorder = Recorder([httpx.Response(200, json=first), httpx.Response(200, json=second)])
        from google import genai
        install("gemini", GeminiAdapter, lambda key, rec: genai.Client(
            api_key=key, http_options=types.HttpOptions(
                httpx_async_client=httpx.AsyncClient(transport=httpx.MockTransport(rec)),
                retry_options=types.HttpRetryOptions(attempts=1)),
        ), recorder)
        batch_dir, summary = await self.trial("gemini", "gemini-3.5-flash-lite")
        self.assertEqual(summary["counts"], {"completed": 1})
        one, two = recorder.bodies
        self.assertEqual(one["systemInstruction"]["parts"][0]["text"], "Be brief.")
        declaration = one["tools"][0]["functionDeclarations"][0]
        self.assertEqual(declaration["name"], "read_file")
        # Proto field name or lowerCamelCase: the REST API accepts both.
        schema = declaration.get("parametersJsonSchema", declaration.get("parameters_json_schema"))
        self.assertEqual(schema["required"], ["name"])
        contents = two["contents"]
        self.assertEqual([c["role"] for c in contents], ["user", "model", "user"])
        self.assertEqual(contents[1]["parts"][0]["thoughtSignature"], "c2lnbmF0dXJl")
        self.assertEqual(contents[2]["parts"][0]["functionResponse"],
                         {"name": "read_file", "response": {"result": "hello"}})
        result = self.result(batch_dir)
        self.assertEqual(result["tokens"]["per_agent"]["A"]["input_tokens"], 230)
        self.assertEqual(result["tokens"]["per_agent"]["A"]["output_tokens"], 21)

    async def test_anthropic_round_trip_replays_thinking(self):
        first = {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "stop_reason": "tool_use", "stop_sequence": None,
            "content": [
                {"type": "thinking", "thinking": "", "signature": "SIG"},
                {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"name": "log.txt"}},
            ],
            "usage": {"input_tokens": 50, "output_tokens": 10, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 500},
        }
        second = {**first, "id": "msg_2", "stop_reason": "end_turn",
                  "content": [{"type": "text", "text": "It says hello."}],
                  "usage": {"input_tokens": 60, "output_tokens": 5, "cache_read_input_tokens": 500,
                            "cache_creation_input_tokens": 0}}
        recorder = Recorder([httpx2.Response(200, json=first), httpx2.Response(200, json=second)])
        install("anthropic", AnthropicAdapter, lambda key, rec: anthropic.AsyncAnthropic(
            api_key=key, max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(rec)),
        ), recorder)
        batch_dir, summary = await self.trial("anthropic", "claude-haiku-5-5")
        self.assertEqual(summary["counts"], {"completed": 1})
        one, two = recorder.bodies
        self.assertEqual(one["cache_control"], {"type": "ephemeral"})
        self.assertEqual(two["messages"][1]["content"][0], {"type": "thinking", "thinking": "", "signature": "SIG"})
        self.assertEqual(two["messages"][2]["content"][0]["type"], "tool_result")
        self.assertEqual(self.result(batch_dir)["tokens"]["per_agent"]["A"]["cached_input_tokens"], 500)

    async def test_rate_limit_errors_are_classified(self):
        recorder = Recorder([httpx2.Response(429, headers={"retry-after": "7"},
                                             json={"error": {"message": "slow down", "type": "rate_limit_error"}})])
        install("openai", OpenAIAdapter, lambda key, rec: openai.AsyncOpenAI(
            api_key=key, max_retries=0,
            http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(rec)),
        ), recorder)
        adapter = providers.adapter_class("openai")("k", max_retries=0)
        from testbed.providers.base import ProviderError, UserInput
        with self.assertRaises(ProviderError) as caught:
            await adapter.complete(model="m", system="", messages=[UserInput("hi")], tools=[], params={})
        self.assertEqual((caught.exception.kind, caught.exception.status_code, caught.exception.retry_after),
                         ("rate_limit", 429, 7.0))
