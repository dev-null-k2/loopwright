"""OPENTELEMETRY EXPORT: send a turn's trace to the tools the industry uses.

Loopwright's own trace is a JSONL file, which is all this project needs. In
production, traces go to an observability backend (Langfuse, Arize Phoenix,
Grafana Tempo, Jaeger, Datadog, Honeycomb...), and nearly all of them accept
the same wire format: OpenTelemetry's OTLP. This file converts a turn into
OTLP/JSON, using OpenTelemetry's "GenAI" attribute names so a backend knows
which span is a model call and which is a tool call.

    one turn        -> one trace
    the turn        -> a root span          invoke_agent loopwright
    a model call    -> a child span         chat <model>       (tokens, provider)
    a tool call     -> a child span         execute_tool <name>
    everything else -> span events on the root (the gate decision, a guardrail
                       finding, an approval, a retry...)

    loopwright export last --out trace.otlp.json
    loopwright export last --otlp http://localhost:4318        # any OTLP/HTTP collector

Set OTEL_EXPORTER_OTLP_ENDPOINT and every turn is exported as it finishes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
import urllib.request
from typing import Any

from loopwright import __version__

MODEL_KINDS = {"model.call"}
TOOL_KINDS = {"tool.run", "tool.error"}


def _attr(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _id(text: str, length: int) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:length]


def to_otlp(trace: dict[str, Any]) -> dict[str, Any]:
    turn, end, events = trace["turn"], trace.get("end", {}), trace["events"]
    try:
        start = dt.datetime.fromisoformat(turn.get("started", "")).timestamp()
    except ValueError:
        start = 0.0
    receipt = end.get("receipt") or {}
    total_ms = max([receipt.get("duration_ms", 0), *[e["t_ms"] for e in events]] or [0])
    began = start - total_ms / 1000            # `started` is stamped when the trace is written, at the end
    nano = lambda ms: str(int((began + ms / 1000) * 1e9))  # noqa: E731
    trace_id, root_id = _id(turn["turn_id"], 32), _id(turn["turn_id"] + "root", 16)
    spans, root_events = [], []
    for e in events:
        data = e.get("data", {})
        if e["kind"] in MODEL_KINDS | TOOL_KINDS:
            dur = e.get("dur_ms") or 0
            if e["kind"] in MODEL_KINDS:
                name = f"chat {data.get('model', '')}".strip()
                attrs = [_attr("gen_ai.operation.name", "chat"), _attr("gen_ai.provider.name", data.get("provider", "")),
                         _attr("gen_ai.system", data.get("provider", "")), _attr("gen_ai.request.model", data.get("model", "")),
                         _attr("gen_ai.usage.input_tokens", data.get("tokens_in", 0)),
                         _attr("gen_ai.usage.output_tokens", data.get("tokens_out", 0)),
                         _attr("loopwright.purpose", data.get("purpose", ""))]
            else:
                name = f"execute_tool {data.get('tool', '')}"
                attrs = [_attr("gen_ai.operation.name", "execute_tool"), _attr("gen_ai.tool.name", data.get("tool", "")),
                         _attr("gen_ai.tool.call.arguments", json.dumps(data.get("args", {}))[:1000])]
            attrs += [_attr("loopwright.stage", e["stage"]), _attr("loopwright.span", e.get("span", "main"))]
            spans.append({"traceId": trace_id, "spanId": _id(f"{turn['turn_id']}{e['seq']}", 16), "parentSpanId": root_id,
                          "name": name, "kind": 3 if e["kind"] in MODEL_KINDS else 1,
                          "startTimeUnixNano": nano(max(e["t_ms"] - dur, 0)), "endTimeUnixNano": nano(e["t_ms"]),
                          "attributes": attrs, "status": {"code": 2 if e["status"] == "error" else 1}})
        else:
            root_events.append({"timeUnixNano": nano(e["t_ms"]), "name": e["kind"],
                                "attributes": [_attr("title", e["title"]), _attr("status", e["status"]),
                                               _attr("loopwright.stage", e["stage"])]})
    spans.insert(0, {
        "traceId": trace_id, "spanId": root_id, "name": "invoke_agent loopwright", "kind": 2,
        "startTimeUnixNano": nano(0), "endTimeUnixNano": nano(total_ms), "events": root_events,
        "status": {"code": 1 if end.get("ok", True) else 2},
        "attributes": [_attr("gen_ai.operation.name", "invoke_agent"), _attr("gen_ai.agent.name", "loopwright"),
                       _attr("gen_ai.conversation.id", turn.get("session", "")),
                       _attr("gen_ai.request.model", turn.get("model", "")),
                       _attr("gen_ai.usage.input_tokens", receipt.get("tokens_in", 0)),
                       _attr("gen_ai.usage.output_tokens", receipt.get("tokens_out", 0)),
                       _attr("loopwright.turn_id", turn["turn_id"]), _attr("loopwright.tool_calls", receipt.get("tool_calls", 0)),
                       _attr("input.value", turn.get("message", "")[:2000]), _attr("output.value", str(end.get("reply", ""))[:2000])]})
    return {"resourceSpans": [{
        "resource": {"attributes": [_attr("service.name", "loopwright"), _attr("service.version", __version__)]},
        "scopeSpans": [{"scope": {"name": "loopwright", "version": __version__}, "spans": spans}]}]}


def send(trace: dict[str, Any], endpoint: str, timeout: float = 5) -> int:
    """POST one trace to an OTLP/HTTP collector. Returns the HTTP status."""
    url = endpoint.rstrip("/")
    url = url if url.endswith("/v1/traces") else url + "/v1/traces"
    headers = {"content-type": "application/json"}
    for pair in os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "").split(","):
        if "=" in pair:
            key, value = pair.split("=", 1)
            headers[key.strip()] = value.strip()
    request = urllib.request.Request(url, data=json.dumps(to_otlp(trace)).encode(), headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


def export_in_background(trace: dict[str, Any]) -> bool:
    """Called at the end of every turn. Does nothing unless an endpoint is configured."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if not endpoint:
        return False

    def work() -> None:
        try:
            send(trace, endpoint)
        except Exception:        # observability must never break the thing it observes
            pass
    threading.Thread(target=work, daemon=True).start()
    return True
