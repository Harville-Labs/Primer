"""A tool call and its result, as the model's server receives them.

Checked in the wire format rather than as Haystack messages, because the
rule that matters is the server's: every result names the call it answers,
and a result whose call is not immediately before it is rejected outright.
"""

from __future__ import annotations

from haystack.dataclasses import ToolCall
from primer_chat.generation import ToolExchange, _exchanged


def wire(*exchanges: ToolExchange) -> list[dict]:
    return [message.to_openai_dict_format() for message in _exchanged(exchanges)]


def test_each_result_follows_the_call_it_answers() -> None:
    first = ToolCall(tool_name="search", arguments={"query": "heron"}, id="call-1")
    second = ToolCall(tool_name="search", arguments={"query": "egret"}, id="call-2")

    messages = wire(ToolExchange(first, "herons wade"), ToolExchange(second, "egrets too"))

    assert [message["role"] for message in messages] == ["assistant", "tool"] * 2
    assert messages[0]["tool_calls"][0]["id"] == "call-1"
    assert messages[1]["tool_call_id"] == "call-1"
    assert messages[1]["content"] == "herons wade"
    assert messages[3]["tool_call_id"] == "call-2"


def test_a_refusal_is_sent_as_the_result_of_its_call() -> None:
    """The model has to read why it got nothing, or it writes as if it had."""
    call = ToolCall(tool_name="search", arguments={}, id="call-1")

    (_, result) = wire(ToolExchange(call, "The user declined.", failed=True))

    assert result == {"role": "tool", "tool_call_id": "call-1", "content": "The user declined."}
