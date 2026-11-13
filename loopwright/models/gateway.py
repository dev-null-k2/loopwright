"""THE MODEL GATEWAY: one door to every model, with the reliability built in.

The loop asks for "a completion". It does not know or care which provider
answers, and it never sees a transient failure. This file is where that
insulation lives:

  routing          pick a provider: explicit choice, else the best available
  retry + backoff  a 429, 529 or timeout is retried after a growing, jittered
                   wait. Jitter matters: without it, every client that failed
                   together retries together and knocks the service over again.
  fallback         when retries run out, the next model in the chain answers
  circuit breaker  a provider that keeps failing is skipped for a cooldown
                   instead of being hammered on every call
  accounting       tokens and cost of every call, by purpose, for the receipt

Only transient errors are retried. A bad API key or a malformed request fails
the same way every time, so retrying it just burns time and money.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any

from loopwright.config import Options
from loopwright.events import Recorder
from loopwright.models import local as local_models
from loopwright.models import pricing
from loopwright.models.anthropic_api import AnthropicProvider
from loopwright.models.base import FatalModelError, Message, ModelError, ModelResponse, Provider, TransientModelError
from loopwright.models.claude_code import ClaudeCodeProvider, find_cli
from loopwright.models.local import LocalProvider
from loopwright.models.openai_compat import OpenAICompatProvider
from loopwright.models.scripted import ScriptedProvider

BREAKER_THRESHOLD = 3       # consecutive failures before a provider is skipped
BREAKER_COOLDOWN_S = 30.0
_breaker: dict[str, dict[str, float]] = {}   # provider name -> {"failures", "open_until"}

PROVIDERS = {"scripted": ScriptedProvider, "local": LocalProvider, "claude-code": ClaudeCodeProvider,
             "anthropic": AnthropicProvider, "openai": OpenAICompatProvider}


def available_providers() -> dict[str, dict[str, Any]]:
    """What can run on this machine right now, and why not when it cannot."""
    cli = find_cli()
    server = local_models.detect()
    return {
        "scripted": {"ok": True, "note": "built in: offline, deterministic, rule-based"},
        "local": {"ok": bool(server and server["models"]), "models": server["models"] if server else [],
                  "note": (f"{server['name']} at {server['url']}: " + (", ".join(server["models"][:6]) or "no models downloaded yet"))
                  if server else "no local server found. Start Ollama, LM Studio or llama.cpp to run fully offline"},
        "claude-code": {"ok": bool(cli), "note": f"uses your Claude subscription via {cli}" if cli
                        else "install Claude Code and sign in to use your subscription"},
        "anthropic": {"ok": bool(os.environ.get("ANTHROPIC_API_KEY")),
                      "note": "ANTHROPIC_API_KEY is set" if os.environ.get("ANTHROPIC_API_KEY") else "set ANTHROPIC_API_KEY"},
        "openai": {"ok": bool(os.environ.get("OPENAI_API_KEY")),
                   "note": "OPENAI_API_KEY is set" if os.environ.get("OPENAI_API_KEY") else "set OPENAI_API_KEY (and OPENAI_BASE_URL for a local server)"},
    }


def pick_provider(requested: str = "auto") -> str:
    """auto = an explicit LOOPWRIGHT_PROVIDER, else an API key, else Claude
    Code if installed, else a local model server, else the scripted model."""
    if requested and requested != "auto":
        return requested if requested in PROVIDERS else "scripted"
    env = os.environ.get("LOOPWRIGHT_PROVIDER", "")
    if env in PROVIDERS:
        return env
    status = available_providers()
    for name in ("anthropic", "openai", "claude-code", "local"):
        if status[name]["ok"]:
            return name
    return "scripted"


class _Chaos:
    """The failure lab: wraps a provider and fails its first N calls."""

    def __init__(self, inner: Provider, failures: int):
        self.inner, self.name, self.model, self.left = inner, inner.name, inner.model, failures

    def complete(self, *args: Any, **kwargs: Any) -> ModelResponse:
        if self.left > 0:
            self.left -= 1
            raise TransientModelError("529 overloaded (injected failure)")
        return self.inner.complete(*args, **kwargs)


class ModelGateway:
    def __init__(self, options: Options, rec: Recorder, home=None, providers: list[Provider] | None = None):
        self.options, self.rec = options, rec
        self.calls: list[dict[str, Any]] = []           # every model call, for the receipt
        if providers is not None:
            self.chain = providers
        else:
            name = pick_provider(options.provider)
            kwargs = {"home": home} if name == "claude-code" and home else {}
            primary: Provider = PROVIDERS[name](options.model, **kwargs)
            self.chain = [primary]
            backup = os.environ.get("LOOPWRIGHT_FALLBACK", "")
            if options.fallback:
                if backup and ":" in backup and backup.split(":")[0] in PROVIDERS:
                    self.chain.append(PROVIDERS[backup.split(":")[0]](backup.split(":", 1)[1]))
                server = local_models.detect() if name not in ("local", "scripted") else None
                if server and server["models"]:            # no network? a model on this machine still answers
                    self.chain.append(LocalProvider(server["models"][0]))
                self.chain.append(ScriptedProvider("scripted-backup"))
        if options.chaos_model_failures > 0:
            self.chain[0] = _Chaos(self.chain[0], options.chaos_model_failures)
        self.primary = self.chain[0]

    @property
    def is_scripted(self) -> bool:
        return self.primary.name == "scripted"

    @property
    def tokens(self) -> int:
        return sum(c["tokens_in"] + c["tokens_out"] for c in self.calls)

    def call(self, system: str, messages: list[Message], tools: list[dict[str, Any]], *,
             purpose: str = "loop", iteration: int = 0, src: str = "context", max_tokens: int = 2048,
             rec: Recorder | None = None) -> ModelResponse:
        rec = rec or self.rec            # a worker passes its own recorder, so its calls carry its span
        last_error: Exception | None = None
        for position, provider in enumerate(self.chain):
            if self._breaker_open(provider, position, rec):
                continue
            if position > 0:
                rec.emit("model.fallback", "model", f"Falling back to {provider.name}:{provider.model}",
                              f"The previous model could not answer ({last_error}). The next model in the chain "
                              "takes over so the user still gets a reply. A fallback is usually a cheaper or "
                              "different-vendor model, so quality may drop: that is the trade.",
                              src="model", status="warn", data={"to": f"{provider.name}:{provider.model}",
                                                                "because": str(last_error)})
            for attempt in range(self.options.retries + 1):
                started = time.perf_counter()
                try:
                    response = provider.complete(system, messages, tools, max_tokens=max_tokens, purpose=purpose)
                except TransientModelError as exc:
                    last_error = exc
                    self._breaker_fail(provider)
                    if attempt < self.options.retries:
                        wait = min(0.25 * 2 ** attempt, 4.0) * (0.5 + random.random() / 2)
                        rec.emit("model.retry", "model", f"Model call failed, retry {attempt + 1}/{self.options.retries}",
                                      f"{exc}. This kind of error is temporary, so the call is repeated after "
                                      f"{wait * 1000:.0f} ms. The wait doubles each time and is jittered so many "
                                      "clients do not retry in step.",
                                      src="model", status="warn", dur_ms=int((time.perf_counter() - started) * 1000),
                                      data={"error": str(exc), "attempt": attempt + 1, "backoff_ms": int(wait * 1000),
                                            "provider": provider.name})
                        if not self.options.fast:
                            time.sleep(wait)
                        continue
                    rec.emit("model.exhausted", "model", f"{provider.name} still failing after {attempt + 1} attempt(s)",
                                  "Retries are spent. Either the next model in the chain takes over, or the turn "
                                  "ends with an honest error.", src="model", status="error",
                                  data={"error": str(exc), "provider": provider.name})
                    break
                except FatalModelError as exc:
                    last_error = exc
                    rec.emit("model.error", "model", f"{provider.name} cannot be used: {exc}",
                                  "This error will not fix itself (a missing key, a bad request, not signed in), "
                                  "so it is not retried.", src="model", status="error",
                                  data={"error": str(exc), "provider": provider.name})
                    break
                else:
                    self._breaker_ok(provider)
                    return self._account(response, purpose, iteration, src, time.perf_counter() - started,
                                         system, messages, tools, rec)
        raise ModelError(str(last_error) if last_error else "no model provider is available")

    # ---------------------------------------------------------------- helpers
    def small(self, purpose: str):
        """A callable(prompt) -> text for narrow side jobs (the gate, memory
        extraction, the critic), or None on the scripted model, where those
        jobs fall back to rules."""
        if self.is_scripted:
            return None
        return lambda prompt: self.call("You are a precise assistant. Follow the format exactly.",
                                        [{"role": "user", "content": prompt}], [], purpose=purpose,
                                        src="gate" if purpose == "gate" else "consolidate" if purpose == "extract" else "critic",
                                        max_tokens=600).text

    def _account(self, response: ModelResponse, purpose: str, iteration: int, src: str, seconds: float,
                 system: str, messages: list[Message], tools: list[dict[str, Any]], rec: Recorder) -> ModelResponse:
        cost = response.cost_usd if response.cost_usd is not None else pricing.cost(
            response.model, response.usage.tokens_in, response.usage.tokens_out)
        row = {"purpose": purpose, "provider": response.provider, "model": response.model,
               "tokens_in": response.usage.tokens_in, "tokens_out": response.usage.tokens_out,
               "cached_in": response.usage.cached_in, "estimated": response.usage.estimated,
               "cost_usd": cost, "ms": int(seconds * 1000)}
        self.calls.append(row)
        wants = ", ".join(c.name for c in response.tool_calls)
        title = (f"Model asks for: {wants}" if wants else "Model answers") if purpose in ("loop", "subagent") \
            else f"Small model call: {purpose}"
        if iteration:
            title = f"Step {iteration}. {title}"
        detail = ("The model read the whole context and chose its next move. "
                  + (f"It wants {len(response.tool_calls)} tool call(s); it has not run anything, it has only asked."
                     if response.tool_calls else "It asked for no tools, which is how a turn normally ends.")
                  if purpose in ("loop", "subagent") else
                  "A narrow side job given to a (usually cheaper) model, separate from the main loop.")
        rec.emit("model.call", "model", title, detail, src=src, dur_ms=int(seconds * 1000), data={
            **row, "iteration": iteration, "stop_reason": response.stop_reason, "thought": response.thought,
            "text": response.text[:2000],
            "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in response.tool_calls],
            "prompt": {"system": system, "messages": messages[-12:], "tools": [t["name"] for t in tools],
                       "message_count": len(messages)},
        })
        return response

    @staticmethod
    def _breaker_fail(provider: Provider) -> None:
        state = _breaker.setdefault(provider.name + ":" + provider.model, {"failures": 0, "open_until": 0})
        state["failures"] += 1
        if state["failures"] >= BREAKER_THRESHOLD:
            state["open_until"] = time.time() + BREAKER_COOLDOWN_S

    @staticmethod
    def _breaker_ok(provider: Provider) -> None:
        _breaker.pop(provider.name + ":" + provider.model, None)

    def _breaker_open(self, provider: Provider, position: int, rec: Recorder) -> bool:
        state = _breaker.get(provider.name + ":" + provider.model)
        if self.options.fast or not state or state["open_until"] <= time.time() or position == len(self.chain) - 1:
            return False
        rec.emit("model.circuit_open", "model", f"Skipping {provider.name}: circuit open",
                      f"This provider failed {int(state['failures'])} times in a row, so calls skip it until the "
                      "cooldown passes instead of waiting on a service that is down.",
                      src="model", status="warn", data={"provider": provider.name, **state})
        return True


def reset_breakers() -> None:
    _breaker.clear()
