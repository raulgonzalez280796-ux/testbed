"""Anthropic adapter: request building and response parsing, with no API calls."""

import unittest
from pathlib import Path

from anthropic.types import Message, TextBlock, ThinkingBlock, ToolUseBlock, Usage

from testbed.providers.anthropic import AnthropicAdapter, parse_response, to_anthropic_messages
from testbed.providers.base import AssistantReply, ToolCall, ToolResult, ToolResults, ToolSpec, UserInput
from testbed.tools.loader import resolve_agent_tools

REPO = Path(__file__).resolve().parents[1]


def message(content, stop_reason="tool_use", **usage):
    return Message(
        id="msg_1", type="message", role="assistant", model="claude-haiku-5-5",
        content=content, stop_reason=stop_reason, stop_sequence=None,
        usage=Usage(input_tokens=usage.get("input", 10), output_tokens=usage.get("output", 5),
                    cache_read_input_tokens=usage.get("cached", 0),
                    cache_creation_input_tokens=usage.get("written", 0)),
    )


class AnthropicAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = AnthropicAdapter("sk-ant-test-not-a-key")

    def test_history_conversion_replays_blocks_and_merges_tool_results(self):
        blocks = [
            {"type": "thinking", "thinking": "", "signature": "sig123"},
            {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"name": "a.txt"}},
        ]
        history = [
            UserInput("Turn 1."),
            AssistantReply("", [ToolCall("toolu_1", "read_file", {"name": "a.txt"})], blocks),
            ToolResults([ToolResult("toolu_1", "read_file", "contents", False)]),
            UserInput("Turn 2."),
        ]
        converted = to_anthropic_messages(history)
        self.assertEqual(converted, [
            {"role": "user", "content": [{"type": "text", "text": "Turn 1."}]},
            {"role": "assistant", "content": blocks},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "contents", "is_error": False},
                {"type": "text", "text": "Turn 2."},
            ]},
        ])

    def test_request_defaults_and_params(self):
        tools = [ToolSpec("list_files", "List files.", {"type": "object", "properties": {}})]
        request = self.adapter.build_request(
            model="claude-opus-5-5", system="Sys.", messages=[UserInput("Hi")],
            tools=tools, params={"max_tokens": 1024},
        )
        self.assertEqual(request["max_tokens"], 1024)
        self.assertEqual(request["system"], "Sys.")
        self.assertEqual(request["cache_control"], {"type": "ephemeral"})
        self.assertEqual(request["tools"][0]["input_schema"], {"type": "object", "properties": {}})
        self.assertNotIn("temperature", request)
        request = self.adapter.build_request(
            model="m", system="", messages=[UserInput("Hi")], tools=[],
            params={"cache": False, "tool_choice": {"type": "auto"}, "output_config": {"effort": "low"}},
        )
        self.assertNotIn("cache_control", request)
        self.assertNotIn("system", request)
        self.assertNotIn("tools", request)
        self.assertEqual(request["tool_choice"], {"type": "auto"})
        self.assertEqual(request["output_config"], {"effort": "low"})
        self.assertEqual(request["max_tokens"], 16000)

    def test_parse_response(self):
        response = message(
            [ThinkingBlock(type="thinking", thinking="", signature="sig"),
             TextBlock(type="text", text=""),
             TextBlock(type="text", text="Reading."),
             ToolUseBlock(type="tool_use", id="toolu_9", name="read_file", input={"name": "x"})],
            input=100, output=20, cached=900, written=50,
        )
        parsed = parse_response({"model": "m"}, response)
        self.assertEqual(parsed.text, "Reading.")
        self.assertEqual([(c.id, c.name, c.arguments) for c in parsed.tool_calls],
                         [("toolu_9", "read_file", {"name": "x"})])
        self.assertEqual([b["type"] for b in parsed.provider_blocks], ["thinking", "text", "tool_use"])
        self.assertEqual(parsed.provider_blocks[0], {"type": "thinking", "thinking": "", "signature": "sig"})
        self.assertEqual(parsed.usage.input_tokens, 1050)
        self.assertEqual(parsed.usage.cached_input_tokens, 900)
        self.assertEqual(parsed.usage.total, 1070)
        self.assertFalse(parsed.truncated)
        self.assertEqual(parsed.raw_response["id"], "msg_1")

    def test_truncated_response(self):
        parsed = parse_response({}, message([TextBlock(type="text", text="Cut")], stop_reason="max_tokens"))
        self.assertTrue(parsed.truncated)

    def test_v1_tools_send_v1_schemas(self):
        tools = resolve_agent_tools(["v1_tools:list_files", "v1_tools:append_log"], REPO / "tools", "A")
        self.assertEqual(tools["list_files"].parameters, {"properties": {}, "required": [], "type": "object"})
        self.assertEqual(tools["append_log"].parameters, {
            "properties": {"text": {"description": "text", "title": "Text", "type": "string"}},
            "required": ["text"], "type": "object",
        })
        self.assertEqual(tools["append_log"].description, "Append text followed by a newline to log.txt.")


if __name__ == "__main__":
    unittest.main()
