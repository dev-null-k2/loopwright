"""The terminal gateway.

    loopwright                 chat in the terminal, printing each step as it happens
    loopwright ask "..."       one question, one answer
    loopwright serve           the web cockpit on http://127.0.0.1:8765
    loopwright replay [id]     play a past turn back, step by step
    loopwright runs            list past turns
    loopwright resume <id>     continue a turn that died, from its last checkpoint
    loopwright eval [--gate]   run the evals; with --gate, exit 1 if the release gate fails
    loopwright learn [term]    read a handbook card
    loopwright memory          show what is stored
    loopwright doctor          check which models can run here
    loopwright selftest        ask eight real questions through a provider and check what it did
    loopwright compare         the same eight questions through several models, side by side
    loopwright workflow [name] run a workflow (a fixed graph of steps); no name lists them
    loopwright tasks           scheduled tasks: list, add, run, pause, resume, rm
    loopwright telegram | discord | slack | whatsapp     talk to the agent from a chat app
    loopwright export [id]     write a turn as OpenTelemetry (OTLP) JSON, or send it to a collector
    loopwright mcp             serve over MCP on stdio (for Claude Code and other clients)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from loopwright import __version__
from loopwright.config import Options, env_options, home_dir, load_dotenv
from loopwright.events import STAGES, Event, list_traces, read_trace, trace_dir

COLOURS = {"gateway": "\033[37m", "harness": "\033[33m", "loop": "\033[34m", "memory": "\033[32m",
           "safety": "\033[31m", "ops": "\033[35m"}
STATUS = {"warn": "\033[33m!\033[0m", "blocked": "\033[31mx\033[0m", "error": "\033[31mx\033[0m", "skip": "\033[2m-\033[0m", "ok": " "}
DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def _colour() -> bool:
    return sys.stdout.isatty()


def format_step(e: dict[str, Any] | Event, verbose: bool = False) -> str:
    """One line per step: number, box, what happened."""
    e = e.to_json() if isinstance(e, Event) else e
    label, pillar, _ = STAGES.get(e["stage"], (e["stage"], "gateway", ""))
    indent = "    " if e.get("span", "main") != "main" else ""
    if _colour():
        line = f"{DIM}{e['seq']:>3}{RESET} {STATUS.get(e['status'], ' ')} {COLOURS[pillar]}{label:<20}{RESET} {indent}{e['title']}"
    else:
        mark = {"warn": "!", "blocked": "x", "error": "x", "skip": "-"}.get(e["status"], " ")
        line = f"{e['seq']:>3} {mark} {label:<20} {indent}{e['title']}"
    if verbose and e.get("detail"):
        line += f"\n{' ' * 27}{DIM if _colour() else ''}{e['detail']}{RESET if _colour() else ''}"
    return line


def _options(args: argparse.Namespace) -> Options:
    options = env_options()
    for name in ("provider", "model", "pattern"):
        if getattr(args, name, None):
            setattr(options, name, getattr(args, name))
    return Options.from_dict({**options.to_dict(), **dict(pair.split("=", 1) for pair in getattr(args, "set", []) or [])})


def _ask_approval(request: dict[str, Any]) -> bool:
    print(f"\n  The agent wants to run {request['tool']}({json.dumps(request['args'])})")
    print(f"  {request['why']}")
    try:
        return input("  Approve? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _agent():
    from loopwright.harness import Agent
    return Agent()


def cmd_chat(args: argparse.Namespace) -> int:
    agent, options = _agent(), _options(args)
    from loopwright.models.gateway import pick_provider
    print(f"Loopwright {__version__}  model: {pick_provider(options.provider)}  state: {agent.home}")
    print("Type a message. /steps toggles the step list, /why adds the explanations, /quit leaves.\n")
    steps, why = not args.quiet, False
    session = f"cli-{int(time.time())}"
    while True:
        try:
            message = input(f"{BOLD}you>{RESET} " if _colour() else "you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not message:
            continue
        if message in ("/quit", "/exit", "/q"):
            return 0
        if message == "/steps":
            steps = not steps
            print(f"  step list {'on' if steps else 'off'}")
            continue
        if message == "/why":
            why = not why
            print(f"  explanations {'on' if why else 'off'}")
            continue
        sinks = [lambda e, verbose=why: print(format_step(e, verbose))] if steps else []
        result = agent.run_turn(message, session=session, options=options, sinks=sinks, approver=_ask_approval)
        print(f"\n{result.reply}\n")
        print(f"{DIM if _colour() else ''}{result.receipt['line']}  (replay: loopwright replay {result.turn_id}){RESET if _colour() else ''}\n")


def cmd_ask(args: argparse.Namespace) -> int:
    agent, options = _agent(), _options(args)
    sinks = [lambda e: print(format_step(e, args.why), file=sys.stderr)] if args.steps else []
    result = agent.run_turn(" ".join(args.message), session=args.session, options=options, sinks=sinks,
                            approver=_ask_approval if sys.stdin.isatty() else None)
    if args.json:
        print(json.dumps(result.to_json(), indent=2, ensure_ascii=False))
    else:
        print(result.reply)
    return 0 if result.ok else 1


def cmd_serve(args: argparse.Namespace) -> int:
    from loopwright.server import serve
    serve(port=args.port, open_browser=not args.no_open, lan=args.lan)
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    rows = list_traces(home_dir(), limit=args.limit)
    if not rows:
        print("No runs yet. Try: loopwright ask \"What is a harness?\"")
        return 0
    for r in rows:
        print(f"{r['turn_id']}  {r['steps']:>3} steps  {r['tokens']:>6} tok  {'ok ' if r['ok'] else 'FAIL'}  {r['message'][:70]}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    home = home_dir()
    turn_id = args.turn_id
    if turn_id in (None, "last"):
        rows = list_traces(home, limit=1)
        if not rows:
            print("No runs to replay yet.")
            return 1
        turn_id = rows[0]["turn_id"]
    path = trace_dir(home) / f"{turn_id}.jsonl"
    if not path.is_file():
        print(f"No trace named {turn_id}. See: loopwright runs")
        return 1
    trace = read_trace(path)
    print(f"Replay of {turn_id}: {trace['turn'].get('message', '')}\n")
    for event in trace["events"]:
        print(format_step(event, verbose=not args.brief))
        if args.speed > 0:
            time.sleep(0.6 / args.speed)
    print(f"\n{trace['end'].get('reply', '')}\n\n{trace['end'].get('receipt', {}).get('line', '')}")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    agent = _agent()
    try:
        result = agent.resume(args.turn_id, sinks=[lambda e: print(format_step(e))], approver=_ask_approval)
    except ValueError as exc:
        print(exc)
        return 1
    print(f"\n{result.reply}")
    return 0 if result.ok else 1


def cmd_eval(args: argparse.Namespace) -> int:
    from loopwright.evals.runner import SUITES, run_all
    judge_model = judge_turn = None
    if args.judge:
        import tempfile

        from loopwright.events import Recorder
        from loopwright.harness import Agent
        from loopwright.models.gateway import ModelGateway, pick_provider
        if pick_provider("auto") == "scripted":
            print("--judge needs a real model (Claude Code or an API key); the judge suite will be reported as skipped.")
        else:
            options = Options(approvals="deny")
            judge_model = ModelGateway(options, Recorder()).small("judge")
            judge_agent = Agent(home=Path(tempfile.mkdtemp(prefix="loopwright-judge-")))
            judge_turn = lambda ask: judge_agent.run_turn(ask, options=options).reply  # noqa: E731

    def progress(p: dict[str, Any]) -> None:
        if p["stage"] == "full":
            print(f"  {'pass' if p['passed'] else 'FAIL'}  {p['case']:<8} {p['title']}")
        elif p["stage"] == "ablation" and p["variant"] != "full":
            print(f"  ablation: {p['variant']}")

    if args.mine:
        from loopwright.evals.mine import cases_path, run_mine
        from loopwright.models.gateway import pick_provider
        provider = pick_provider("auto")
        outcome = run_mine(home_dir(), {"provider": provider, "fast": provider == "scripted"})
        if not outcome["total"]:
            print(f"You have no cases yet. In the cockpit, press 'Save as eval case' under any reply. They are kept in {cases_path(home_dir())}.")
            return 0
        for case in outcome["cases"]:
            print(f"  {'pass' if case['passed'] else 'FAIL'}  {case['id']:<14} {case['title']}")
            for check in case["trials"][0]["checks"]:
                if not check["pass"]:
                    print(f"        failed check: {check['check']}  ->  {check['note'][:140]}")
        print(f"\n{outcome['passed']}/{outcome['total']} of your cases passed on {provider}.")
        return 0 if outcome["passed"] == outcome["total"] else 1
    print("Running evals on the scripted model (these test the harness, not an LLM)\n")
    report = run_all(Path(args.out), ablations=not args.no_ablations, judge_model=judge_model, judge_turn=judge_turn,
                     on_progress=progress)
    print(f"\n{'variant':<17}" + "".join(f"{s:>12}" for s in SUITES) + f"{'attacks':>10}{'tok/turn':>10}")
    for name, row in report["ablations"].items():
        print(f"{name:<17}" + "".join(f"{row[s] * 100:>11.0f}%" for s in SUITES)
              + f"{row['attacks_succeeded']:>7}/{row['attacks']}" + f"{row['avg_tokens_in_per_turn']:>10}")
    r = report["retrieval"]
    print(f"\nretrieval recall@1/@3   bm25 {r['bm25']['recall_at_1']:.2f}/{r['bm25']['recall_at_3']:.2f}   "
          f"dense {r['dense']['recall_at_1']:.2f}/{r['dense']['recall_at_3']:.2f}   "
          f"hybrid {r['hybrid']['recall_at_1']:.2f}/{r['hybrid']['recall_at_3']:.2f}   ({r['questions']} questions)")
    g = report["gate"]
    print(f"retrieval gate (rules)  accuracy {g['accuracy']:.2f}   false skips {g['false_skips']}   "
          f"false retrieves {g['false_retrieves']}   ({g['messages']} messages)")
    j = report["judge"]
    print("judge                   " + ("skipped (needs a real model)" if j["skipped"] else f"{j['passed']}/{j['cases']} passed"))
    print("\nrelease gate")
    for check in report["verdict"]["checks"]:
        print(f"  {'pass' if check['pass'] else 'FAIL'}  {check['check']}  (got {check['got']}, need {check['need']})")
    print(f"\n{'GATE PASSED' if report['verdict']['passed'] else 'GATE FAILED'} in {report['seconds']}s. "
          f"Report: {Path(args.out) / 'eval-report.json'}")
    return 1 if args.gate and not report["verdict"]["passed"] else 0


def cmd_learn(args: argparse.Namespace) -> int:
    from loopwright.learn import PILLARS, load_concepts
    from loopwright.memory import kb
    concepts = load_concepts()
    if not args.term:
        for pillar, label in PILLARS.items():
            print(f"\n{label}")
            for c in concepts:
                if c["pillar"] == pillar:
                    print(f"  {c['term']:<34} {c['one_liner']}")
        print(f"\n{len(concepts)} terms. Read one with: loopwright learn \"retrieval gate\"")
        return 0
    query = " ".join(args.term)
    exact = [c for c in concepts if query.lower() in (c["id"], c["term"].lower(), *[a.lower() for a in c.get("aliases", [])])]
    hits = kb.search(query, k=1)
    card = exact[0] if exact else next((c for c in concepts if hits and f"concept:{c['id']}" == hits[0].id), None)
    if not card:
        print(f"Nothing in the handbook matches {query!r}. List everything with: loopwright learn")
        return 1
    print(f"\n{card['term']}\n{'=' * len(card['term'])}\n{card['one_liner']}\n\n{card['explain']}\n\nWhy it exists\n  {card['why']}\n"
          f"\nThe tradeoff\n  {card['tradeoff']}\n\nIn this repo\n  {card['file']}\n\nSee it run\n  loopwright ask \"{card['try']}\" --steps\n"
          f"\nInterview question\n  {card['question']}\n\nA strong answer\n  {card['answer']}\n")
    return 0


def cmd_memory(args: argparse.Namespace) -> int:
    agent = _agent()
    store = agent.store
    if args.action == "forget":
        ok = args.id is not None and store.delete_fact(args.id)
        print("Forgotten." if ok else "No fact with that id.")
        return 0 if ok else 1
    if args.action == "reset":
        if input("Delete all facts, episodes, messages, notes and events? Type 'reset' to confirm: ").strip() != "reset":
            print("Nothing was deleted.")
            return 1
        store.reset()
        print("Memory wiped.")
        return 0
    print(f"State file: {store.path}\n\nFacts (semantic memory)")
    for f in store.facts(include_superseded=True):
        mark = f"  (superseded by {f['superseded_by']})" if f["superseded_by"] else ""
        print(f"  [{f['id']}] {f['text']}{mark}")
    print("\nEpisodes (episodic memory)")
    for e in store.episodes():
        print(f"  {e['day']}  {e['summary']}")
    print(f"\nCounts: {store.counts()}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from loopwright.models.gateway import PROVIDERS, available_providers, pick_provider
    print(f"Loopwright {__version__}\nstate folder  {home_dir()}\n")
    status = available_providers()
    chosen = pick_provider("auto")
    for name, info in status.items():
        print(f"  {'ready ' if info['ok'] else 'no    '} {name:<12} {info['note']}{'   <- auto picks this' if name == chosen else ''}")
    if args.ping:
        target = args.ping if args.ping in PROVIDERS else chosen
        print(f"\nSending one test message to {target}...")
        try:
            response = PROVIDERS[target]().complete("Reply in five words or fewer.", [{"role": "user", "content": "Say hello."}], [])
            print(f"  ok: {response.text or response.tool_calls}  ({response.usage.tokens_in} in, {response.usage.tokens_out} out)")
        except Exception as exc:
            print(f"  failed: {exc}")
            return 1
    else:
        print("\nTest a provider end to end with: loopwright doctor --ping claude-code")
    import os

    from loopwright.gateways.chat import CHANNELS
    from loopwright.tools.browser import find_chrome
    from loopwright.tools.plugins import switched_off
    from loopwright.tools.shell import sandbox_for
    chrome = find_chrome()
    workspace = home_dir() / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    print("\nTools that need something on this machine")
    print(f"  {'ready ' if chrome else 'no    '} browser      {chrome or 'no Chrome, Chromium, Edge or Brave found (set LOOPWRIGHT_CHROME)'}")
    print(f"  {'off   ' if 'run_command' in switched_off(home_dir()) else 'on    '} shell        sandbox here: {sandbox_for(workspace.resolve())[1]}"
          "   (switch run_command on from the Tools page)")
    print("\nChat gateways")
    for name, (token, allow, how) in CHANNELS.items():
        state = "ready " if os.environ.get(token) and os.environ.get(allow) else "token " if os.environ.get(token) else "no    "
        print(f"  {state} {name:<12} {how}   (set {token} and {allow}; start with: loopwright {name})")
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    """Eight questions, end to end, through whichever model you pick. The checks are on behaviour (which tool
    ran, what was stored, what was not sent), so they hold for a real model that words things its own way."""
    from loopwright.evals.runner import load_cases, run_case
    from loopwright.models.gateway import pick_provider
    provider = pick_provider(args.provider or "auto")
    model = args.model
    if provider == "local" and not model:
        from loopwright.models import local
        found = local.detect(refresh=True)
        model = found["preferred"] if found else ""
        if model:
            print(f"No model named, so the local pick is {model} ({found['why']}). Choose another with --model.")
    print(f"Self-test on {provider}{' (' + model + ')' if model else ''}. Each question runs in a fresh, temporary state folder.\n")
    passed = 0
    cases = load_cases("live")
    for case in cases:
        started = time.perf_counter()
        # no fallback: this is a test of the provider you named, so a failure must show as a failure
        overrides = {"provider": provider, "fast": provider == "scripted", "fallback": False,
                     **({"model": args.model} if args.model else {})}
        result = run_case(case, overrides)
        trial = result["trials"][0]
        passed += result["passed"]
        print(f"  {'pass' if result['passed'] else 'FAIL'}  {case['id']}  {case['title']}  "
              f"({time.perf_counter() - started:.1f}s, {trial['steps']} steps)")
        print(f"        asked: {result['prompt']}")
        print(f"        reply: {' '.join(trial['reply'].split())[:150]}")
        for check in trial["checks"]:
            if not check["pass"]:
                print(f"        failed check: {check['check']}  ->  {check['note'][:160]}")
        if "could not get an answer from the model" in trial["reply"]:
            print(f"\nStopping: {provider} cannot be reached, so the remaining questions would fail the same way. "
                  "Run `loopwright doctor` to see what is set up.")
            return 1
    print(f"\n{passed}/{len(cases)} passed on {provider}{' (' + model + ')' if model else ''}.")
    return 0 if passed == len(cases) else 1


def cmd_compare(args: argparse.Namespace) -> int:
    from loopwright.evals.compare import format_table, parse_target, run_compare
    try:
        targets = [parse_target(t) for t in (args.targets or ["scripted", "auto"])]
    except ValueError as exc:
        print(exc)
        return 2
    seen, unique = set(), []
    for t in targets:
        if t["label"] not in seen:
            seen.add(t["label"])
            unique.append(t)
    print("Comparing " + ", ".join(t["label"] for t in unique) + " on the eight self-test questions.\n")
    report = run_compare(unique, Path(args.out) if args.out else home_dir() / "compare",
                         on_progress=lambda p: print(f"  {p['status']:<12} {p['target']:<28} {p['case']}"))
    print("\n" + format_table(report))
    print("\nOpen the cockpit (loopwright serve), Evals, Compare, to replay any cell.")
    return 0


def cmd_workflow(args: argparse.Namespace) -> int:
    from loopwright import workflows
    if args.check:
        try:
            wf = workflows.parse(Path(args.check).read_text(encoding="utf-8"), "yours")
        except (OSError, workflows.WorkflowError, Exception) as exc:
            print(f"not valid: {exc}")
            return 1
        print(f"{wf.name}: valid. Rounds: " + " -> ".join("[" + ", ".join(w) + "]" for w in wf.waves()))
        return 0
    found = workflows.load_all(home_dir())
    if not args.name:
        for wf in found.values():
            print(f"{wf.name:<18} {wf.description}  ({wf.source})")
            print(f"{'':<18} " + " -> ".join("[" + ", ".join(w) + "]" for w in wf.waves()))
        print(f"\nYour own go in {home_dir() / 'workflows'} as .toml files. Run one: loopwright workflow <name> [input]")
        return 0
    agent, options = _agent(), _options(args)
    sinks = [lambda e: print(format_step(e, args.why))] if not args.quiet else []
    result = agent.run_turn(f"/workflow {args.name}: {' '.join(args.input)}", session=f"workflow-{args.name}", options=options,
                            sinks=sinks, approver=_ask_approval if sys.stdin.isatty() else None)
    print(f"\n{result.reply}\n")
    return 0 if result.ok else 1


def cmd_tasks(args: argparse.Namespace) -> int:
    import datetime as dt

    from loopwright import scheduler
    agent = _agent()
    store = agent.store
    if args.action == "add":
        if len(args.rest) < 2:
            print('usage: loopwright tasks add "<schedule>" "<prompt>"     e.g. loopwright tasks add "weekdays 08:00" "What is on my calendar today?"')
            return 2
        try:
            task = scheduler.add_task(store, " ".join(args.rest[1:]), args.rest[0], agent.now(), "local:cli")
        except ValueError as exc:
            print(exc)
            return 2
        print(f"Task #{task['id']} will run {task['runs']}. Next: {task['next_run']}.")
        print("It runs while `loopwright serve` or a chat gateway is running, or when you call `loopwright tasks tick`.")
        return 0
    if args.action == "tick":
        results = scheduler.Scheduler(agent, _options(args)).tick()
        for r in results:
            print(f"ran {r.turn_id}: {' '.join(r.reply.split())[:160]}")
        print(f"{len(results)} task(s) were due.")
        return 0
    if args.action in ("rm", "run", "pause", "resume"):
        task = store.task(int(args.rest[0])) if args.rest and args.rest[0].isdigit() else None
        if not task:
            print("Give the number of an existing task (see: loopwright tasks).")
            return 2
        if args.action == "rm":
            store.delete_task(task["id"])
            print(f"Removed task #{task['id']}.")
        elif args.action == "run":
            result = scheduler.Scheduler(agent, _options(args)).run_task(task)
            print(result.reply)
        else:
            upcoming = scheduler.next_after(task["schedule"], agent.now())
            store.set_task(task["id"], enabled=args.action == "resume", next_run=upcoming.timestamp() if upcoming else None)
            print(f"Task #{task['id']} {'resumed' if args.action == 'resume' else 'paused'}.")
        return 0
    rows = store.tasks()
    if not rows:
        print('No scheduled tasks. Add one: loopwright tasks add "weekdays 08:00" "What is on my calendar today?"')
        return 0
    for t in rows:
        when = dt.datetime.fromtimestamp(t["next_run"]).strftime("%a %Y-%m-%d %H:%M") if t["next_run"] else "never"
        print(f"#{t['id']:<3} {'on ' if t['enabled'] else 'off'} {scheduler.describe(t['schedule']):<28} next {when:<21} runs {t['runs']:<3} {t['prompt'][:60]}")
    return 0


def cmd_gateway(args: argparse.Namespace) -> int:
    import importlib
    module = importlib.import_module(f"loopwright.gateways.{args.command}")
    agent = _agent()
    try:
        module.run(agent, port=args.port) if args.command == "whatsapp" else module.run(agent)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from loopwright import otel
    rows = list_traces(home_dir(), limit=1)
    turn_id = args.turn_id if args.turn_id and args.turn_id != "last" else (rows[0]["turn_id"] if rows else "")
    path = trace_dir(home_dir()) / f"{turn_id}.jsonl"
    if not turn_id or not path.is_file():
        print("No such run. List them with: loopwright runs")
        return 1
    trace = read_trace(path)
    if args.otlp:
        try:
            print(f"sent to {args.otlp}: HTTP {otel.send(trace, args.otlp)}")
        except Exception as exc:
            print(f"could not send: {exc}")
            return 1
        return 0
    text = json.dumps(otel.to_otlp(trace), indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(trace['events'])} events as OTLP spans)")
    else:
        print(text)
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from loopwright.mcp_server import main as mcp_main
    return mcp_main()


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="loopwright", description="An AI agent you can watch work.")
    parser.add_argument("--version", action="version", version=f"loopwright {__version__}")
    sub = parser.add_subparsers(dest="command")

    def model_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--provider", choices=["auto", "scripted", "local", "claude-code", "anthropic", "openai"])
        p.add_argument("--model")
        p.add_argument("--pattern", choices=["react", "plan", "reflect"])
        p.add_argument("--set", action="append", metavar="KEY=VALUE", help="any Options field, e.g. --set guardrails=false")

    p = sub.add_parser("chat", help="chat in the terminal")
    model_flags(p)
    p.add_argument("--quiet", action="store_true", help="do not print the steps")
    p.set_defaults(fn=cmd_chat)

    p = sub.add_parser("ask", help="ask one question")
    p.add_argument("message", nargs="+")
    model_flags(p)
    p.add_argument("--steps", action="store_true", help="print each step to stderr")
    p.add_argument("--why", action="store_true", help="with --steps, add the explanation of each step")
    p.add_argument("--json", action="store_true", help="print the full result, events included")
    p.add_argument("--session", default="default")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("serve", help="open the web cockpit")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-open", action="store_true", help="do not open a browser tab")
    p.add_argument("--lan", action="store_true", help="also reachable from your phone on the same Wi-Fi, with an access key")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("runs", help="list past turns")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_runs)

    p = sub.add_parser("replay", help="play a past turn back")
    p.add_argument("turn_id", nargs="?")
    p.add_argument("--speed", type=float, default=1.0, help="0 prints everything at once")
    p.add_argument("--brief", action="store_true", help="titles only, no explanations")
    p.set_defaults(fn=cmd_replay)

    p = sub.add_parser("resume", help="continue a turn from its last checkpoint")
    p.add_argument("turn_id")
    p.set_defaults(fn=cmd_resume)

    p = sub.add_parser("eval", help="run the evals")
    p.add_argument("--gate", action="store_true", help="exit 1 if the release gate fails")
    p.add_argument("--no-ablations", action="store_true")
    p.add_argument("--judge", action="store_true", help="also run model-graded cases (needs a real model)")
    p.add_argument("--out", default="results")
    p.add_argument("--mine", action="store_true", help="run only the cases you saved from your own runs")
    p.set_defaults(fn=cmd_eval)

    p = sub.add_parser("compare", help="the same questions through several models, side by side")
    p.add_argument("--targets", nargs="+", metavar="PROVIDER[:MODEL][+key=value]",
                   help="e.g. scripted local claude-code local:qwen2.5:7b scripted+pattern=plan")
    p.add_argument("--out", help="folder for the report and traces (default: your state folder)")
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("workflow", help="run or list workflows")
    p.add_argument("name", nargs="?")
    p.add_argument("input", nargs="*")
    model_flags(p)
    p.add_argument("--quiet", action="store_true", help="do not print the steps")
    p.add_argument("--why", action="store_true", help="add the explanation of each step")
    p.add_argument("--check", metavar="FILE", help="validate a workflow file and show its rounds")
    p.set_defaults(fn=cmd_workflow)

    p = sub.add_parser("tasks", help="scheduled tasks")
    p.add_argument("action", nargs="?", choices=["list", "add", "rm", "run", "pause", "resume", "tick"], default="list")
    p.add_argument("rest", nargs="*")
    model_flags(p)
    p.set_defaults(fn=cmd_tasks)

    for name, text in (("telegram", "Telegram bot (long polling)"), ("discord", "Discord bot (gateway WebSocket)"),
                       ("slack", "Slack app (Socket Mode)"), ("whatsapp", "WhatsApp Cloud API (webhook)")):
        p = sub.add_parser(name, help=f"chat gateway: {text}")
        if name == "whatsapp":
            p.add_argument("--port", type=int, default=8790)
        p.set_defaults(fn=cmd_gateway)

    p = sub.add_parser("export", help="export a turn as OpenTelemetry (OTLP) JSON")
    p.add_argument("turn_id", nargs="?", default="last")
    p.add_argument("--out", help="write to a file instead of printing")
    p.add_argument("--otlp", metavar="URL", help="POST to an OTLP/HTTP collector, e.g. http://localhost:4318")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("learn", help="read the handbook")
    p.add_argument("term", nargs="*")
    p.set_defaults(fn=cmd_learn)

    p = sub.add_parser("memory", help="show, forget or reset stored memory")
    p.add_argument("action", nargs="?", choices=["show", "forget", "reset"], default="show")
    p.add_argument("id", nargs="?", type=int)
    p.set_defaults(fn=cmd_memory)

    p = sub.add_parser("doctor", help="check which models can run here")
    p.add_argument("--ping", nargs="?", const="auto", metavar="PROVIDER", help="send one real test message")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("selftest", help="ask eight real questions through a provider and check what it did")
    p.add_argument("--provider", choices=["auto", "scripted", "local", "claude-code", "anthropic", "openai"])
    p.add_argument("--model")
    p.set_defaults(fn=cmd_selftest)

    p = sub.add_parser("mcp", help="serve over MCP on stdio")
    p.set_defaults(fn=cmd_mcp)

    args = parser.parse_args(argv)
    if not args.command:
        args = parser.parse_args(["chat", *(argv or [])])
    return args.fn(args) or 0
