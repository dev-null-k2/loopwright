# On a phone

Loopwright is a web page, so there is nothing to install on the phone. Which link you open depends on what you
want.

| You want | Open | The agent runs |
|---|---|---|
| To look around and replay recorded runs | [dev-null-k2.github.io/loopwright](https://dev-null-k2.github.io/loopwright/) | nowhere: it is a recording |
| To use your own agent, from anywhere | the link `loopwright serve --tunnel` prints (it opens that same public page, connected) | on your computer |
| To use your own agent, at home | the link `loopwright serve --lan` prints | on your computer |
| To use your own agent with your computer off | a GitHub Codespace: see [cloud.md](cloud.md) | on a machine in the cloud |

A phone cannot run Claude Code or a multi-gigabyte local model in a browser tab, and GitHub Pages can only serve
files. So for a live agent the model always runs on a computer of yours (with Claude Code, or Ollama and a local
model, or an API key), and the phone is the screen.

## The public demo

The link above is the cockpit replaying recorded runs of the built-in scripted model. Every lesson, the
architecture, the step cards, the evals and the handbook work. On its own it cannot answer a new question,
because nothing is running behind it. The **Connect** button at the top changes that.

## Your own agent on the public page

The public page can be the screen for Loopwright on your computer. On the computer:

```bash
brew install cloudflared      # once; any OS: see Cloudflare's downloads page
loopwright serve --tunnel
```

It starts a tunnel (a public HTTPS address that forwards to the cockpit on your computer) and prints a link and
a QR code:

```
On your phone       https://dev-null-k2.github.io/loopwright/#connect=https://calm-river-stone.trycloudflare.com&key=aea16e4e3b09c787
```

Scan the code with the phone's camera, or send yourself the link. The page opens with **Live** at the top, and
from then on everything on it is your agent: pick Claude Code or a local model in the model menu, ask, approve
actions, read memory, run lessons. The phone's browser remembers the connection, so next time the plain public
link is enough, for as long as the same tunnel is running.

- **Claude Code signs in on the computer**, once, by running `claude` there. The page has no Claude sign-in and
  cannot have one: Anthropic does not allow other sites to offer Claude account login. Your subscription is used
  by Claude Code on your computer, exactly as when you sit at it.
- The quick tunnel's address is new every time you start it, and so is the key. Open the new link each time, or
  see *A link that stays the same* below.
- The address and the key travel after the `#` in the link. Browsers do not send that part to the site hosting
  the page, so GitHub never sees them. The page keeps them in this browser's storage until you press
  **Live**, then **Disconnect**.
- The computer has to be on and awake, with `loopwright serve --tunnel` running.
- Anyone who has the link can use your agent, with its memory and its tools, until you stop the server. Share
  it as you would a password.

If the page says your computer did not answer, it tells you which of three things happened: the key is out of
date (Loopwright was restarted), nothing answered (Loopwright or the tunnel is not running), or the browser
refused to call an address on your home network (use `--tunnel`, or the computer's own link).

### A link that stays the same

Use a tunnel with a fixed address (an ngrok static domain, Tailscale Funnel, a named Cloudflare tunnel), start
it yourself pointing at port 8765, and tell Loopwright its address and a key of your choosing:

```bash
LOOPWRIGHT_KEY=a-long-secret-you-choose loopwright serve --public-url https://your-name.example.ts.net
```

The printed link then never changes, and neither does the connection your phone remembers.

### Your own copy of the page

The server lets in exactly one outside page: the project's public copy. If you publish your own (a fork's GitHub
Pages), say so, and the printed link will open yours instead:

```bash
loopwright serve --tunnel --page https://your-name.github.io/loopwright/
```

### What has and has not been tested

The connection itself is covered by tests and by a browser run at phone size with the page on one origin and the
agent on another: connecting by link and by pasting, a live turn with its step cards, an approval, the memory
view, a wrong key, and a computer that is off. The tunnel start-up was tested with a stand-in for `cloudflared`.
It has not yet been run through a real Cloudflare tunnel or on a real phone.

Cloudflare documents that quick tunnels do not pass Server-Sent Events, the kind of stream the cockpit normally
uses for a turn. So whenever the page is not talking to a server on its own network, it does not stream: the
turn runs as a job on the computer and the page collects its steps with short requests. That also means a turn
is not lost when the phone locks its screen.

## Your own agent, on the same Wi-Fi

No tunnel and no public page: the computer serves the cockpit straight to the phone.

```bash
loopwright serve --lan
```

It prints a link and a QR code:

```
On your phone       http://192.168.1.23:8765/?key=3f9a1c07be5d2a41
```

Open that on the phone. The first visit stores the key in a cookie; after that the plain address works. Pick the
model in the top bar as usual: Claude Code, a local model, or the scripted one.

- The key is new every time the server starts (unless you set `LOOPWRIGHT_KEY`).
- Without `--lan`, `--tunnel` or `--public-url` the server only answers this computer, and there is no key to
  present.
- If the phone cannot connect, the computer's firewall is usually blocking the port: allow incoming connections
  for Python when macOS asks, or open port 8765.

## A chat app instead

`loopwright telegram` (or `discord`, `slack`, `whatsapp`) and message the bot from the phone. No network setup,
approvals are asked in words, and `/steps` shows what the agent did. See [gateways.md](gateways.md).

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
