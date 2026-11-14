# On a phone

Loopwright is a web page, so there is nothing to install on the phone. Which link you open depends on what you
want.

| You want | Open | The agent runs |
|---|---|---|
| To look around and replay recorded runs | [dev-null-k2.github.io/loopwright](https://dev-null-k2.github.io/loopwright/) | nowhere: it is a recording |
| To use your own agent, at home | the link `loopwright serve --lan` prints | on your computer |
| To use your own agent, from anywhere | a tunnel to that same server, or a chat app | on your computer |

A phone cannot run Claude Code or a multi-gigabyte local model in a browser tab, and GitHub Pages can only serve
files. So for a live agent the model always runs on a computer of yours (with Claude Code, or Ollama and a local
model, or an API key), and the phone is the screen.

## The public demo

The link above is the cockpit replaying recorded runs of the built-in scripted model. Every lesson, the
architecture, the step cards, the evals and the handbook work. It cannot answer a new question, because nothing
is running behind it.

## Your own agent, on the same Wi-Fi

On the computer:

```bash
loopwright serve --lan
```

It prints a second line:

```
On your phone       http://192.168.1.23:8765/?key=3f9a1c07be
```

Open that on the phone. The first visit stores the key in a cookie; after that the plain address works. Pick the
model in the top bar as usual: Claude Code, a local model, or the scripted one.

- The key is new every time the server starts.
- Without `--lan` the server only answers this computer, and there is no key to present.
- Anyone who has the link can use your agent, with its memory and its tools, until you stop the server. Share it
  as you would a password.
- If the phone cannot connect, the computer's firewall is usually blocking the port: allow incoming connections
  for Python when macOS asks, or open port 8765.

## Your own agent, from anywhere

Two ways, both with the agent still running on your computer.

**A chat app.** `loopwright telegram` (or `discord`, `slack`, `whatsapp`) and message the bot from the phone. No
network setup, approvals are asked in words, and `/steps` shows what the agent did. See
[gateways.md](gateways.md).

**A tunnel to the cockpit.** Start `loopwright serve --lan`, then point any tunnel at the port and add the key:

```bash
cloudflared tunnel --url http://localhost:8765
# open  https://<the address it prints>/?key=<the key loopwright printed>
```

The tunnel's address is public, so the key is the only thing protecting the agent. Stop both when you are done.

## How the page adapts

One pane at a time, chosen with the switch under the top bar: **Chat**, **Architecture**, **Steps**. Chat is
where you will spend most of the time: every reply carries a card for each thing the agent did, and tapping a
card opens that step. The architecture is drawn at a readable size and pans to follow the active box; turn the
phone sideways to see all of it at once. Approvals appear in the chat as a card with two buttons.

## Running everything on Android

Loopwright is plain Python with no dependencies, so it should run in Termux (`pkg install python git`, then the
usual clone and `pip install -e .`) with the scripted model, or pointed at a model server on another machine with
`LOOPWRIGHT_LOCAL_URL`. That has not been tested, and the browser agent and the shell sandbox will not be
available there.
