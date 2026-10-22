"""The turn loop: schedule, interaction modes, model calls, tools and stop rules.

Nothing here is specific to any scenario. Agents never share histories; in
conversation mode an agent's input also carries the other agents' final texts
since its own last turn.
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field
from typing import Any, Callable

from .keys import redact
from .logger import EventLogger
from .providers.base import (
    AssistantReply, Message, ModelResponse, ProviderAdapter, ProviderError,
    ToolResult, ToolResults, Usage, UserInput,
)
from .scenario import AgentSpec, Scenario
from .tools.decorator import Tool, ToolContext, ToolError, TrialControl, file_target
from .tools.workspace import Workspace, WorkspaceError


class TrialStopped(Exception):
    def __init__(self, status: str, stop_reason: str, error: dict | None = None):
        super().__init__(stop_reason)
        self.status = status
        self.stop_reason = stop_reason
        self.error = error


@dataclass
class AgentRuntime:
    spec: AgentSpec
    adapter: ProviderAdapter
    tools: dict[str, Tool]
    system: str
    turn_one: str
    continuation: str
    history: list[Message] = field(default_factory=list)
    turns_taken: int = 0
    finished: bool = False
    usage: Usage = field(default_factory=Usage)
    cursor: int = 0  # conversation mode: transcript entries already delivered
    last_call_tokens: int = 0

    @property
    def done(self) -> bool:
        return self.finished or self.turns_taken >= self.spec.turns


@dataclass
class SharedMessage:
    sender: str
    round: int
    text: str


class Engine:
    def __init__(
        self,
        scenario: Scenario,
        agents: dict[str, AgentRuntime],
        workspace: Workspace,
        variables: dict[str, Any],
        rng: random.Random,
        log: EventLogger,
        *,
        keys: dict[str, str] | None = None,
        charge: Callable[[Usage], None] | None = None,  # batch budget; may raise TrialStopped
        precheck: Callable[[int], None] | None = None,  # batch budget, before each call
        cancelled: Callable[[], bool] | None = None,
        limiter=None,  # optional rate limiter with `async with limiter.slot(provider, tokens)`
    ):
        self.scenario = scenario
        self.agents = agents
        self.workspace = workspace
        self.variables = variables
        self.rng = rng
        self.log = log
        self.keys = keys or {}
        self.charge_batch = charge
        self.precheck = precheck
        self.cancelled = cancelled or (lambda: False)
        self.limiter = limiter
        self.state: dict = {}
        self.rng_jitter = random.Random()  # retry jitter only; never touches the trial's seeded rng
        self.control = TrialControl()
        self.transcript: list[SharedMessage] = []
        self.usage = Usage()
        self.rounds_completed = 0

    async def run(self) -> str:
        """Run until a stop rule fires. Returns the stop reason; raises TrialStopped otherwise."""
        for round_number in range(1, self.scenario.rounds + 1):
            if all(agent.done for agent in self.agents.values()):
                return "all_agents_done"
            order = list(self.scenario.turn_order)
            if self.scenario.schedule.mode == "random_each_round":
                self.rng.shuffle(order)
            self.log.emit("round_started", round=round_number, order=order)
            for agent_id in order:
                agent = self.agents[agent_id]
                if agent.done:
                    continue
                if self.cancelled():
                    raise TrialStopped("cancelled", "cancelled")
                await self.run_turn(agent, round_number)
                if self.control.stop_reason:
                    return self.control.stop_reason
            self.rounds_completed = round_number
        return "round_limit"

    # -- one turn ---------------------------------------------------------------

    def turn_input(self, agent: AgentRuntime, turn: int) -> str:
        prompt = agent.turn_one if turn == 1 else agent.continuation
        parts = []
        if self.scenario.interaction == "conversation":
            new = [m for m in self.transcript[agent.cursor:] if m.sender != agent.spec.id]
            agent.cursor = len(self.transcript)
            if new:
                parts.append("\n\n".join(
                    self.scenario.conversation_format.format(sender=m.sender, text=m.text, round=m.round)
                    for m in new
                ))
        parts.append(prompt)
        return "\n\n".join(part for part in parts if part.strip())

    async def run_turn(self, agent: AgentRuntime, round_number: int) -> None:
        agent.turns_taken += 1
        turn = agent.turns_taken
        agent_id = agent.spec.id
        text = self.turn_input(agent, turn)
        if not text.strip():
            # Only possible in conversation mode with no new messages and no continuation prompt.
            self.log.emit("turn_skipped", agent_id=agent_id, round=round_number, turn=turn,
                          reason="no input")
            return
        self.log.emit("turn_started", agent_id=agent_id, round=round_number, turn=turn, prompt=text)
        agent.history.append(UserInput(text))
        texts: list[str] = []
        stop_reason = None
        calls = 0
        cap_hit = False
        limit = agent.spec.max_model_calls_per_turn
        for call_index in range(1, limit + 1):
            response = await self.call_model(agent, round_number, turn, call_index)
            calls += 1
            stop_reason = response.stop_reason
            if response.is_empty:
                self.log.emit("empty_reply", agent_id=agent_id, round=round_number, turn=turn,
                              call=call_index, stop_reason=response.stop_reason)
            else:
                agent.history.append(AssistantReply(
                    text=response.text, tool_calls=response.tool_calls,
                    provider_blocks=response.provider_blocks,
                ))
            if response.text:
                texts.append(response.text)
            self.log.emit(
                "agent_reply", agent_id=agent_id, round=round_number, turn=turn, call=call_index,
                text=response.text,
                tool_calls=[{"id": c.id, "name": c.name, "arguments": c.arguments}
                            for c in response.tool_calls],
                stop_reason=response.stop_reason, truncated=response.truncated,
                usage=response.usage.as_dict(),
            )
            self.charge(agent, response)
            if not response.tool_calls:
                break
            results = await self.run_tools(agent, response, round_number, turn)
            agent.history.append(ToolResults(results))
            if self.control.stop_reason or agent_id in self.control.finished_agents:
                break
            if call_index == limit:
                cap_hit = True  # the agent sees these tool results at the start of its next turn
        if agent_id in self.control.finished_agents:
            agent.finished = True
        final_text = "\n\n".join(texts)
        if self.scenario.interaction == "conversation" and final_text.strip():
            self.transcript.append(SharedMessage(agent_id, round_number, final_text))
        self.log.emit(
            "turn_finished", agent_id=agent_id, round=round_number, turn=turn, text=final_text,
            stop_reason=stop_reason, model_calls=calls, cap_hit=cap_hit,
            finished=agent.finished,
        )

    def charge(self, agent: AgentRuntime, response: ModelResponse) -> None:
        agent.last_call_tokens = response.usage.total
        agent.usage.add(response.usage)
        self.usage.add(response.usage)
        cap = self.scenario.budget.max_trial_tokens
        if cap is not None and self.usage.total >= cap:
            self.log.emit("token_cap_reached", tokens=self.usage.total, cap=cap)
            raise TrialStopped("completed", "token_cap")
        if self.charge_batch is not None:
            self.charge_batch(response.usage)

    async def call_model(self, agent: AgentRuntime, round_number: int, turn: int, call: int) -> ModelResponse:
        spec = agent.spec
        request_args = dict(
            model=spec.model, system=agent.system, messages=agent.history,
            tools=[t.spec() for t in agent.tools.values()], params=spec.params,
        )
        if self.precheck is not None:
            # Histories only grow, so the next call costs a bit more than the last one.
            self.precheck(int(agent.last_call_tokens * 1.2))
        request = agent.adapter.build_request(**request_args)
        loop = asyncio.get_running_loop()
        started = loop.time()
        limits = self.scenario.limits
        attempt = 0
        while True:
            try:
                response = await self.send(spec.provider, agent.adapter, request)
                break
            except ProviderError as error:
                message = redact(error.message, self.keys)
                if error.retryable and attempt < limits.retry_attempts:
                    attempt += 1
                    delay = error.retry_after or min(
                        limits.retry_max_delay_seconds, 2 ** attempt + self.rng_jitter.random())
                    if self.limiter is not None and error.kind == "rate_limit":
                        await self.limiter.rate_limited(spec.provider, error.retry_after)
                    self.log.emit(
                        "rate_limited" if error.kind == "rate_limit" else "call_retry",
                        agent_id=spec.id, round=round_number, turn=turn, call=call,
                        provider=spec.provider, attempt=attempt, delay_seconds=round(delay, 2),
                        error_kind=error.kind, status_code=error.status_code, message=message,
                    )
                    await asyncio.sleep(delay)
                    continue
                self.log.emit(
                    "model_call_failed", agent_id=spec.id, round=round_number, turn=turn, call=call,
                    provider=spec.provider, duration_seconds=round(loop.time() - started, 3),
                    attempts=attempt + 1, request=error.raw_request, error_kind=error.kind,
                    error_type=error.error_type, status_code=error.status_code, message=message,
                )
                raise TrialStopped("infra_error", "infra_error", {
                    "kind": error.kind, "type": error.error_type,
                    "status_code": error.status_code, "message": message,
                }) from error
        self.log.emit(
            "model_call", agent_id=spec.id, round=round_number, turn=turn, call=call,
            provider=spec.provider, duration_seconds=round(loop.time() - started, 3),
            request_id=response.request_id, model=response.model,
            attempts=attempt + 1,
            request=response.raw_request, response=response.raw_response,
        )
        return response

    async def send(self, provider: str, adapter: ProviderAdapter, request: dict) -> ModelResponse:
        if self.limiter is None:
            return await adapter.send(request)
        async with self.limiter.slot(provider, request):
            response = await adapter.send(request)
        await self.limiter.success(provider)
        return response

    async def run_tools(self, agent: AgentRuntime, response: ModelResponse, round_number: int, turn: int) -> list[ToolResult]:
        results = []
        limits = self.scenario.limits
        for call in response.tool_calls:
            base = dict(agent_id=agent.spec.id, round=round_number, turn=turn,
                        call_id=call.id, tool=call.name)
            if response.truncated:
                output = "Not run: the response was cut off before the tool call was complete."
                self.log.emit("tool_skipped", **base, arguments=call.arguments, reason="truncated")
                results.append(ToolResult(call.id, call.name, output, True))
                continue
            tool = agent.tools.get(call.name)
            if tool is not None:
                # Which file and action the call touches, so checks need not know tool names.
                target, op = file_target(tool, call.arguments)
                if op:
                    base["op"] = op
                if target:
                    base["file"] = target
            self.log.emit("tool_started", **base, arguments=call.arguments)
            if tool is None:
                output = f"Unknown tool {call.name!r}."
                self.log.emit("tool_failed", **base, error_type="UnknownTool", error=output)
                results.append(ToolResult(call.id, call.name, output, True))
                continue
            if len(json.dumps(call.arguments, ensure_ascii=False).encode()) > limits.max_tool_input_bytes:
                output = "Input size limit exceeded"
                self.log.emit("tool_failed", **base, error_type="InputTooLarge", error=output)
                results.append(ToolResult(call.id, call.name, output, True))
                continue
            ctx = ToolContext(
                agent_id=agent.spec.id, files=self.workspace,
                state=self.state, variables=self.variables, rng=self.rng,
                round=round_number, turn=turn,
                _note=lambda message, _base=base, **data: self.log.emit(
                    "tool_note", **_base, message=message, data=data),
                _control=self.control,
            )
            try:
                output = await tool.invoke(ctx, call.arguments, limits.tool_timeout_seconds)
            except (ToolError, WorkspaceError) as error:  # messages written for the model
                output = str(error)
                self.log.emit("tool_failed", **base, error_type=type(error).__name__, error=output)
                results.append(ToolResult(call.id, call.name, output, True))
                continue
            except asyncio.TimeoutError:
                output = f"Tool timed out after {limits.tool_timeout_seconds:g} s."
                self.log.emit("tool_failed", **base, error_type="TimeoutError", error=output)
                results.append(ToolResult(call.id, call.name, output, True))
                continue
            except Exception as error:
                output = redact(f"{type(error).__name__}: {error}", self.keys)
                self.log.emit("tool_failed", **base, error_type=type(error).__name__, error=output)
                results.append(ToolResult(call.id, call.name, output, True))
                continue
            self.log.emit("tool_completed", **base, result=output)
            results.append(ToolResult(call.id, call.name, output, False))
        return results
