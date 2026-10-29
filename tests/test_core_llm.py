import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from corehelp import BASE_URL, MockTransport

from fortyshots.cassette import Cassette, CassetteMiss, canonical, request_key
from fortyshots.llm import LLMClient, NonLoopbackError, assert_loopback, image_part, parse_response


@pytest.mark.parametrize("url", ["http://10.0.0.5:8080", "http://192.168.1.2:8080", "https://example.com",
                                 "http://0.0.0.0:8080", "http://127.0.0.1.evil.com:8080", "http://[2001:db8::1]:80"])
def test_non_loopback_base_url_raises(url):
    with pytest.raises(NonLoopbackError):
        LLMClient(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080", "http://localhost:8080/", "http://[::1]:8080"])
def test_loopback_accepted(url):
    assert assert_loopback(url) == url.rstrip("/")


def test_chat_roundtrip_and_typed_result():
    client = LLMClient(BASE_URL, transport=MockTransport())
    res = client.chat([{"role": "user", "content": "Reply with the single word READY."}], reasoning_strength="low")
    assert res.content == "READY" and res.reasoning and res.finish_reason == "stop"
    assert res.timings["prompt_n"] >= 1 and res.tokens()["completion"] > 0
    assert res.event_timings().keys() == {"prompt_n", "prompt_per_second", "predicted_n", "predicted_per_second",
                                          "cache_n"}


def test_request_fields_follow_llama_server_contract():
    client = LLMClient(BASE_URL)
    req = client.build_request([{"role": "user", "content": "x"}], tools=[{"type": "function", "function": {
        "name": "f", "parameters": {"type": "object", "properties": {}}}}], tool_choice="auto",
        schema=("S", {"type": "object"}), reasoning_strength="high", max_tokens=99, temperature=0.5, seed=3)
    assert req["chat_template_kwargs"] == {"reasoning_strength": "high"}
    assert req["response_format"] == {"type": "json_schema", "json_schema": {"name": "S", "schema": {"type": "object"},
                                                                             "strict": True}}
    assert req["max_tokens"] == 99 and req["temperature"] == 0.5 and req["parallel_tool_calls"] is True


def test_truncated_detection():
    res = parse_response({"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content":
                                                                              "thinking..."}}]})
    assert res.truncated
    res = parse_response({"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
        {"id": "x", "type": "function", "function": {"name": "f", "arguments": "{bad json"}}]}}]})
    assert not res.truncated and res.tool_calls[0].parse_error


def test_cassette_record_then_replay_is_byte_identical(tmp_path):
    path = tmp_path / "cassette.jsonl"
    msgs = [{"role": "user", "content": [{"type": "text", "text": "How many red dots?"}, image_part(b"\x89PNGfake")]}]
    rec = LLMClient(BASE_URL, cassette=Cassette(path, "record"), transport=MockTransport())
    first = rec.chat(msgs, reasoning_strength="low")
    line = json.loads(path.read_text().splitlines()[0])
    assert "base64" not in path.read_text() and line["request_meta"]["images"][0].startswith("sha256:")
    rep = LLMClient(BASE_URL, cassette=Cassette(path, "replay"), transport=MockTransport(fail_chat=True))
    again = rep.chat(msgs, reasoning_strength="low")
    assert json.dumps(again.raw, sort_keys=True) == json.dumps(first.raw, sort_keys=True)
    assert again.from_cassette
    with pytest.raises(CassetteMiss):
        rep.chat([{"role": "user", "content": "never recorded"}])


def test_canonical_hashes_images_not_text():
    a = {"messages": [{"role": "user", "content": [image_part(b"one")]}]}
    b = {"messages": [{"role": "user", "content": [image_part(b"two")]}]}
    assert request_key(a) != request_key(b)
    assert "sha256:" in json.dumps(canonical(a))
