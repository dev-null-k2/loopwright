"""Token streaming: the reply reaches the reader while the model is still writing it."""
import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from loopwright.config import Options
from loopwright.events import Recorder
from loopwright.guardrails import redact_secrets
from loopwright.harness import Agent
from loopwright.models import anthropic_api, openai_compat
from loopwright.models._http import read_stream
from loopwright.models.base import FatalModelError, ModelResponse, TransientModelError, Usage
from loopwright.models.claude_code import ClaudeCodeProvider
from loopwright.models.streaming import HOLDBACK, ReplyFilter, Streamer
from loopwright.server import App, make_handler
from tests.test_local_models import Stub, server  # noqa: F401  (the stub local model server, as a fixture)


def shown(deltas):
    """What a reader's screen holds after these messages: per stream, appended pieces and corrections."""
    screens = {}
    for d in deltas:
        screens[d["stream"]] = d["replace"] if "replace" in d else screens.get(d["stream"], "") + d["text"]
    return screens


class Speaker:
    """A provider that streams: each step is a list of pieces (or an error to raise part-way)."""
    name, model, streams = "speaker", "speaker-1", True

    def __init__(self, *steps):
        self.steps, self.streamed = list(steps), []

    def complete(self, system, messages, tools, *, max_tokens=2048, purpose="loop", on_text=None):
        self.streamed.append(on_text is not None)
        said = []
        for piece in self.steps.pop(0):
            if isinstance(piece, Exception):
                raise piece
            said.append(piece)
            if on_text:
                on_text(piece)
        return ModelResponse(text="".join(said), provider=self.name, model=self.model, usage=Usage(10, 5))


# ---------------------------------------------------------------- the whole path

def test_a_watched_turn_streams_its_reply_and_the_pieces_add_up_to_it(agent, opts):
    deltas = []
    result = agent.run_turn("What is a harness?", options=opts, live=[deltas.append])
    kinds = result.kinds()
    assert len(deltas) > 10 and all(d["type"] == "delta" for d in deltas)
    assert list(shown(deltas).values()) == [result.reply]
    stream = next(e for e in result.events if e.kind == "model.stream")
    call = next(e for e in result.events if e.kind == "model.call" and e.data.get("stream"))
    assert stream.seq < call.seq and call.data["stream"]["event"] == stream.seq == deltas[0]["stream"]
    assert stream.stage == "reply" and stream.data["holdback"] == HOLDBACK
    assert call.data["stream"]["pieces"] > 10 and "shown" not in call.data["stream"]        # nothing was masked
    assert result.receipt["first_token_ms"] == stream.data["first_token_ms"]
    assert kinds.index("model.stream") < kinds.index("guard.output") < kinds.index("reply.send")


def test_nothing_is_streamed_when_nobody_is_watching_or_when_it_is_switched_off(agent, opts):
    unwatched = agent.run_turn("What is a harness?", options=opts)
    assert "model.stream" not in unwatched.kinds() and "first_token_ms" not in unwatched.receipt
    deltas = []
    off = agent.run_turn("What is a harness?", session="off", options=Options(**{**opts.to_dict(), "stream": False}), live=[deltas.append])
    assert deltas == [] and "model.stream" not in off.kinds() and off.reply == unwatched.reply


def test_only_the_main_loop_streams_and_a_provider_that_cannot_is_left_alone(fake_agent, tmp_path):
    agent, model = fake_agent("plain answer")                       # FakeModel takes no on_text
    deltas = []
    result = agent.run_turn("hi", options=Options(fast=True), live=[deltas.append])
    assert result.reply == "plain answer" and deltas == [] and "model.stream" not in result.kinds()
    speaker = Speaker(["one ", "two"], ["three"])
    rec = Recorder(live=[deltas.append])
    from loopwright.models.gateway import ModelGateway
    gateway = ModelGateway(Options(fast=True), rec, providers=[speaker])
    gateway.call("s", [{"role": "user", "content": "x"}], [], purpose="gate")          # a side job: its text is not the reply
    gateway.call("s", [{"role": "user", "content": "x"}], [], purpose="loop", rec=rec.child("sub:1"))      # a subagent's
    assert speaker.streamed == [False, False] and deltas == []


# ---------------------------------------------------------------- the output guard, inside the stream

@pytest.mark.parametrize("pieces, secret", [
    (["Your key is sk-", "abcdefghijkl", "mnopqrstuv", " and that is all I have to say about it."], "sk-abcdefghijklmnopqrstuv"),
    (["The card is 4111 ", "1111 ", "1111 ", "1111", " and it expires soon, so use it quickly."], "4111"),
    (["Sure. The pass", "word is hun", "ter2 and nothing else needs to be said here."], "hunter2"),
])
def test_a_secret_is_masked_inside_the_stream_before_it_is_shown(tmp_path, pieces, secret):
    deltas = []
    agent = Agent(home=tmp_path, providers=[Speaker(pieces)])
    result = agent.run_turn("tell me", options=Options(fast=True, memory=False), live=[deltas.append])
    for d in deltas:
        assert secret not in d.get("text", "") and secret not in d.get("replace", "")      # never on screen, not even for a moment
    assert "[redacted" in result.reply and secret not in result.reply
    assert list(shown(deltas).values()) == [result.reply]
    call = next(e for e in result.events if e.kind == "model.call")
    assert call.data["stream"]["shown"] == result.reply and secret in call.data["text"]     # the trace keeps both: what was written, what was shown


def test_a_pattern_longer_than_the_holdback_takes_back_what_was_shown():
    """The holdback is a window, and a secret can be wider than it: a card number read out digit by digit is
    not recognisable until the thirteenth digit, by which time the first ones are on screen. The screen is
    then corrected with a `replace`. This is the limit the handbook card owns up to."""
    deltas = []
    streamer = Streamer(Recorder(live=[deltas.append]), "p", "m", 1, guard=True)
    text = "The number is 4 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 as printed on the card, and that is everything there is."
    for word in text.split(" "):
        streamer.feed(word + " ")
    stats = streamer.finish(text)
    assert any("replace" in d for d in deltas) and stats["replaced"] >= 1
    assert list(shown(deltas).values()) == [redact_secrets(text).text] and "4 1 1 1" not in stats["shown"]
    assert stats["shown"].startswith("The number is [redacted")


def test_with_the_guard_on_the_screen_runs_behind_the_model_and_with_it_off_it_does_not():
    held, free = [], []
    guarded = Streamer(Recorder(live=[held.append]), "p", "m", 1, guard=True)
    open_ = Streamer(Recorder(live=[free.append]), "p", "m", 1, guard=False)
    text = "one two three four five six seven eight nine ten eleven twelve"
    for word in text.split(" "):
        guarded.feed(word + " ")
        open_.feed(word + " ")
    on_screen = shown(held)[guarded.seq]
    assert shown(free)[open_.seq] == text + " "
    assert text.startswith(on_screen.rstrip()) and len(text) + 1 - len(on_screen) >= HOLDBACK       # whole words, a few behind
    guarded.finish(text)
    assert shown(held)[guarded.seq] == text


def test_a_stream_that_breaks_is_retried_from_the_start_as_a_new_stream(tmp_path):
    deltas = []
    speaker = Speaker(["The answer ", "is forty", TransientModelError("connection reset")], ["The answer ", "is forty-two."])
    agent = Agent(home=tmp_path, providers=[speaker])
    result = agent.run_turn("what is it", options=Options(fast=True, memory=False, guardrails=False), live=[deltas.append])
    streams = [e.seq for e in result.events if e.kind == "model.stream"]
    assert result.reply == "The answer is forty-two." and result.kinds().count("model.retry") == 1 and len(streams) == 2
    screens = shown(deltas)
    assert screens[streams[0]] == "The answer is forty" and screens[streams[1]] == "The answer is forty-two."


# ---------------------------------------------------------------- reading each wire format

def test_server_sent_events_and_json_lines_are_both_read():
    sse = [b"event: message_start\n", b'data: {"a": 1}\n', b"\n", b": keep-alive\n", b"\n", b'data: {"b":\n', b'data: 2}\n', b"\r\n",
           b"data: [DONE]\n", b"\n", b'data: {"never": "read"}\n', b"\n"]
    assert list(read_stream(iter(sse))) == [{"a": 1}, {"b": 2}]
    assert list(read_stream(iter([b'{"message": {"content": "hi"}}\n', b'{"done": true}\n']))) == [{"message": {"content": "hi"}}, {"done": True}]
    assert list(read_stream(iter([b'data: {"last": true}']))) == [{"last": True}]                # closed without the final blank line


def test_an_anthropic_stream_is_folded_back_into_the_whole_message():
    events = [
        {"type": "message_start", "message": {"model": "claude-x", "usage": {"input_tokens": 100, "cache_read_input_tokens": 40}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "ping"},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Let me "}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "check."}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "calculator", "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"express'}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": 'ion": "2+2"}'}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 30}},
        {"type": "message_stop"},
    ]
    said, sent = [], []

    def stream(url, headers, body, timeout=120):
        sent.append(body)
        return iter(events)

    provider = anthropic_api.AnthropicProvider("claude-x", stream=stream)
    import os
    os.environ["ANTHROPIC_API_KEY"] = "test"
    try:
        response = provider.complete("s", [{"role": "user", "content": "2+2?"}], [], on_text=said.append)
    finally:
        del os.environ["ANTHROPIC_API_KEY"]
    assert sent[0]["stream"] is True and said == ["Let me ", "check."]
    assert response.text == "Let me check." and response.stop_reason == "tool_use" and response.model == "claude-x"
    assert [(c.id, c.name, c.arguments) for c in response.tool_calls] == [("toolu_1", "calculator", {"expression": "2+2"})]
    assert (response.usage.tokens_in, response.usage.tokens_out, response.usage.cached_in) == (140, 30, 40)
    with pytest.raises(TransientModelError, match="overloaded"):
        anthropic_api.fold_stream(iter([{"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}]), said.append)
    with pytest.raises(FatalModelError):
        anthropic_api.fold_stream(iter([{"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}]), said.append)
    with pytest.raises(TransientModelError, match="before the message was complete"):
        anthropic_api.fold_stream(iter(events[:5]), said.append)                              # cut off part-way


def test_an_openai_style_stream_is_folded_back_and_an_unknown_option_is_dropped():
    def piece(delta=None, finish=None, **extra):
        return {"model": "m", "choices": [{"delta": delta or {}, "finish_reason": finish}], **extra}
    chunks = [piece({"role": "assistant", "content": ""}), piece({"content": "Work"}), piece({"content": "ing."}),
              piece({"tool_calls": [{"index": 0, "id": "call_a", "function": {"name": "calc", "arguments": ""}}]}),
              piece({"tool_calls": [{"index": 0, "function": {"arguments": '{"x":'}}]}),
              piece({"tool_calls": [{"index": 1, "id": "call_b", "function": {"name": "note", "arguments": "{}"}}]}),
              piece({"tool_calls": [{"index": 0, "function": {"arguments": " 1}"}}]}),
              piece(finish="tool_calls"), {"model": "m", "choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4}}]
    said = []
    response = openai_compat.from_wire(openai_compat.fold_stream(iter(chunks), said.append), "m")
    assert said == ["Work", "ing."] and response.text == "Working."
    assert [(c.id, c.name, c.arguments) for c in response.tool_calls] == [("call_a", "calc", {"x": 1}), ("call_b", "note", {})]
    assert (response.usage.tokens_in, response.usage.tokens_out) == (9, 4)
    with pytest.raises(TransientModelError):
        openai_compat.fold_stream(iter([]), said.append)

    bodies = []

    def picky(url, headers, body, timeout=120):          # an older server: it refuses the usage option
        bodies.append(body)
        if "stream_options" in body:
            raise FatalModelError("HTTP 400: unknown field stream_options")
        return iter([piece({"content": "ok"}), piece(finish="stop")])

    data = openai_compat.stream_chat(picky, "u", {}, {"model": "m", "messages": []}, said.append)
    assert data["choices"][0]["message"]["content"] == "ok" and [("stream_options" in b) for b in bodies] == [True, False]


@pytest.mark.parametrize("model", ["native-model", "chatty-model", "plain-model"])
def test_a_local_model_streams_over_real_http_whichever_way_it_calls_tools(server, tmp_path, model):  # noqa: F811
    """The stub server streams real chunks. Two of the three models write JSON in their text; the reader must
    only ever see the answer inside it."""
    deltas = []
    agent = Agent(home=tmp_path)
    result = agent.run_turn("What's 18% of 2450?", options=Options(provider="local", model=model, fast=True, fallback=False), live=[deltas.append])
    assert result.ok and result.reply == "18% of 2450 is 441." and [t["name"] for t in result.tool_log] == ["calculator"]
    assert any(r.get("stream") for r in Stub.requests) and not result.receipt["estimated"]      # token counts came with the stream
    screens = shown(deltas)
    assert list(screens.values()) == ["18% of 2450 is 441."]
    assert all("{" not in d.get("text", "") and "tool_calls" not in d.get("text", "") for d in deltas)


# ---------------------------------------------------------------- models that answer in JSON

def feed_all(pieces):
    out = []
    reply = ReplyFilter(out.append)
    for piece in pieces:
        reply.feed(piece)
    return "".join(out)


def test_from_a_json_reply_only_the_answer_gets_through():
    whole = json.dumps({"thought": 'the "final" answer', "final": 'Line one\nShe said "hi" \\ é 😀 done'})
    for size in (1, 2, 3, 7, len(whole)):                              # every way the text can be cut, escapes included
        assert feed_all([whole[i:i + size] for i in range(0, len(whole), size)]) == 'Line one\nShe said "hi" \\ é 😀 done'
    assert feed_all(["```json\n", '{"final": "fenced', ' answer"}', "\n```"]) == "fenced answer"
    assert feed_all(['{"thought": "use a tool", "tool_calls": [{"name": "calculator", "arguments": {"final": 3}}]}']) == ""
    assert feed_all(["  Plain prose, ", "no JSON at all."]) == "  Plain prose, no JSON at all."
    assert feed_all(["```python\n", "print(1)\n```"]) == "```python\nprint(1)\n```"


FAKE_CLAUDE = r'''
import json, sys
args, prompt = sys.argv[1:], sys.stdin.read()
assert "--include-partial-messages" in args and args[args.index("--output-format") + 1] == "stream-json" and "--verbose" in args
def out(row):
    print(json.dumps(row), flush=True)
out({"type": "system", "subtype": "init"})
reply = json.dumps({"thought": "answer", "final": "It is 441.\nThat is 18% of 2450."})
for i in range(0, len(reply), 6):
    out({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": reply[i:i + 6]}}})
out({"type": "assistant", "message": {"content": [{"type": "text", "text": reply}]}})
print("a warning on stderr", file=sys.stderr)
out({"type": "result", "subtype": "success", "is_error": False, "result": reply, "total_cost_usd": 0.01,
     "usage": {"input_tokens": 50, "output_tokens": 12}})
'''


def test_claude_code_streams_through_its_partial_messages(tmp_path, monkeypatch):
    script = tmp_path / "claude"
    script.write_text(f"#!{sys.executable}\n{FAKE_CLAUDE}")
    script.chmod(0o755)
    monkeypatch.setenv("LOOPWRIGHT_CLAUDE_BIN", str(script))
    said = []
    response = ClaudeCodeProvider(home=tmp_path).complete("system", [{"role": "user", "content": "18% of 2450?"}], [], on_text=said.append)
    assert "".join(said) == "It is 441.\nThat is 18% of 2450." == response.text and len(said) > 3
    assert response.cost_usd == 0.01 and (response.usage.tokens_in, response.usage.tokens_out) == (50, 12)

    failing = tmp_path / "claude-out"
    failing.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\nprint('Please log in first', file=sys.stderr)\nsys.exit(1)\n")
    failing.chmod(0o755)
    monkeypatch.setenv("LOOPWRIGHT_CLAUDE_BIN", str(failing))
    with pytest.raises(FatalModelError, match="not signed in"):
        ClaudeCodeProvider(home=tmp_path).complete("system", [{"role": "user", "content": "hi"}], [], on_text=said.append)


# ---------------------------------------------------------------- to the browser, and to the terminal

@pytest.fixture
def cockpit(tmp_path):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(tmp_path / "home")))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def post(url, body):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    return urllib.request.urlopen(request, timeout=30)


def test_the_cockpit_gets_the_pieces_over_a_stream_and_over_polling(cockpit):
    options = {"provider": "scripted", "fast": True, "search": "demo"}
    with post(f"{cockpit}/api/chat", {"message": "What is a harness?", "options": options}) as response:
        direct = [json.loads(line[6:]) for line in response.read().decode().split("\n\n") if line.startswith("data: ")]
    job = json.load(post(f"{cockpit}/api/chat", {"message": "What is a harness?", "session": "b", "options": options, "poll": True}))["job"]
    polled, after = [], 0
    while True:
        batch = json.load(urllib.request.urlopen(f"{cockpit}/api/jobs/{job}?after={after}", timeout=30))
        polled += batch["messages"]
        after = batch["next"]
        if batch["done"]:
            break
    for messages in (direct, polled):
        deltas = [m for m in messages if m["type"] == "delta"]
        done = messages[-1]
        assert done["type"] == "done" and len(deltas) > 10 and list(shown(deltas).values()) == [done["reply"]]
        stream = next(m for m in messages if m.get("kind") == "model.stream")
        assert messages.index(stream) < messages.index(deltas[0]) and deltas[0]["stream"] == stream["seq"]
        assert done["receipt"]["first_token_ms"] is not None


def test_the_terminal_prints_the_reply_as_it_arrives(capsys):
    from loopwright.cli import _LiveText
    live = _LiveText()
    for message in [{"type": "delta", "stream": 3, "text": "Thinking "}, {"type": "delta", "stream": 3, "text": "aloud."},
                    {"type": "delta", "stream": 9, "text": "The pass"}, {"type": "delta", "stream": 9, "replace": "The [redacted password]"},
                    {"type": "delta", "stream": 9, "text": " is safe."}]:
        live(message)
    live.end_line()
    out = capsys.readouterr().out
    assert out.startswith("Thinking aloud.\n\nThe pass\n") and out.endswith("The [redacted password] is safe.\n") and "rewrote" in out
    assert live.covers("The [redacted password] is safe.") and not live.covers("Something else.")
