import datetime as dt

import pytest

from loopwright.config import Options
from loopwright.harness import Agent
from loopwright.models.base import ModelResponse, ToolCall, Usage
from loopwright.models.gateway import reset_breakers

NOW = dt.datetime(2026, 11, 12, 18, 0)        # a Thursday


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Every test gets its own state folder and no real model credentials."""
    monkeypatch.setenv("LOOPWRIGHT_HOME", str(tmp_path / "home"))
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LOOPWRIGHT_PROVIDER", "TAVILY_API_KEY", "BRAVE_API_KEY", "LOOPWRIGHT_SEARCH"):
        monkeypatch.delenv(key, raising=False)
    reset_breakers()


@pytest.fixture
def opts():
    return Options(provider="scripted", fast=True, approvals="approve", search="demo")


@pytest.fixture
def agent(tmp_path):
    return Agent(home=tmp_path / "agent", now=lambda: NOW)


class FakeModel:
    """A provider that plays back a fixed list of responses, so a test can make
    the 'model' do exactly the thing the harness has to handle."""
    name, model = "fake", "fake-1"

    def __init__(self, *steps):
        self.steps, self.seen = list(steps), []

    def complete(self, system, messages, tools, *, max_tokens=2048, purpose="loop"):
        self.seen.append({"system": system, "messages": [dict(m) for m in messages], "tools": [t["name"] for t in tools]})
        step = self.steps.pop(0) if self.steps else "done"
        if isinstance(step, Exception):
            raise step
        if isinstance(step, str):
            return ModelResponse(text=step, provider=self.name, model=self.model, usage=Usage(10, 5))
        calls = [ToolCall(f"c{len(self.seen)}_{i}", name, args) for i, (name, args) in enumerate(step)]
        return ModelResponse(tool_calls=calls, provider=self.name, model=self.model, stop_reason="tool_use", usage=Usage(10, 5))


@pytest.fixture
def fake_agent(tmp_path):
    def make(*steps):
        model = FakeModel(*steps)
        return Agent(home=tmp_path / "fake", now=lambda: NOW, providers=[model]), model
    return make
