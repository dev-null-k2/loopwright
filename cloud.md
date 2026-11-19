# Running it in the cloud

For when your own computer is off. The agent has to run somewhere that is on; this page puts it on a machine in
the cloud, so a phone needs nothing but a link.

| Where | Cost | Always on | What you do |
|---|---|---|---|
| **GitHub Codespaces** (below) | free for 60 hours a month on a personal account | no: it sleeps when idle and you wake it | one click to create it, one sign-in to Claude |
| Any Linux server you have | whatever the server costs | yes | `pip install`, then `loopwright serve --public-url … --keep-key` behind your HTTPS address |

## GitHub Codespaces

A codespace is a small Linux machine GitHub runs for you, tied to a repository. This repository carries a dev
container (`.devcontainer/devcontainer.json`) that sets everything up by itself: Loopwright, Claude Code, a
browser for the browser agent, and the cockpit started on every boot.

### Create it

1. On GitHub, open your copy of this repository, then **Code**, **Codespaces**, **Create codespace on main**.
2. Wait for the set-up to finish (about three minutes the first time). If the editor asks whether you trust the
   authors of the files in this folder, say yes: it is your own repository, and the terminal needs it. The
   terminal then prints two links, and the cockpit link again as a QR code if the terminal is wide enough:

   ```
   Cockpit, for your phone    https://<name>-8765.app.github.dev/?key=<key>
   Wake this codespace        https://<name>.github.dev
   ```

3. To use Claude Code, type `claude` in that terminal once and sign in with your Claude account. The sign-in
   is Anthropic's own and stays in the codespace. Until you do, the built-in scripted model answers.

### Use it from a phone

There are two ways to reach the cockpit, and you choose with one command in the codespace's terminal.

**Private (the default).** The port answers only to you, signed in to GitHub. Bookmark both links on the phone:

- **Cockpit**: your agent. The first visit asks you to sign in to GitHub, and then stores the access key in a
  cookie.
- **Wake**: when the cockpit link shows an error, the codespace is asleep. Open the wake link, wait about a
  minute for the editor to appear, then open the cockpit link again.

**One link, any device.** `bash deploy/codespace.sh public` opens the port to the internet, guarded by the
access key alone, and prints a single link to the public cockpit page:

```
Your agent, on any device    https://dev-null-k2.github.io/loopwright/#connect=…&key=…&wake=…
```

Bookmark it, or scan the QR code. On any device it opens straight into your agent, with no GitHub sign-in,
while the codespace is awake. When it is asleep the page says so and shows a **Wake it** button: that opens the
codespace's own page in another tab (GitHub asks you to sign in if that device has not), and the cockpit tab
connects by itself as soon as the agent answers. After the first visit the browser remembers the connection, so
the plain public address is enough.

What you give up: GitHub's sign-in no longer stands in front of the cockpit. Anyone who obtains that link can
use your agent, with its tools and your Claude subscription, until you run `bash deploy/codespace.sh private`
or delete `~/.loopwright/access_key` and restart. Treat the link as a password.

A free codespace cannot be woken without a GitHub sign-in, so no free set-up removes that tap. A machine that is
always on does: see *Any Linux server* below.

### What to expect

- **It sleeps.** GitHub stops a codespace after 30 minutes without activity (you can raise that to 4 hours in
  your GitHub settings, under Codespaces). Requests to the cockpit are written to the editor's terminal, and
  GitHub counts terminal output as activity, so it should stay awake while you are using it. Scheduled tasks and
  chat gateways only run while it is awake.
- **Free hours.** A personal account includes 120 core-hours a month, which is 60 hours on the default 2-core
  machine. A sleeping codespace uses none.
- **It is deleted if unused.** GitHub deletes a codespace that has been stopped for 30 days, and with it the
  agent's memory and the Claude sign-in. Use it once a month, or export what matters.
- **Who can reach it.** Only you: GitHub's sign-in guards the port, and Loopwright's own key guards the cockpit
  behind it. The key is kept in `~/.loopwright/access_key` and stays the same across restarts.
- **Turns survive a dropped connection.** Away from your own network the page does not hold one long stream
  open; the turn runs as a job on the server and the page collects its steps with short requests, picking up
  where it left off after the phone locks or changes network.

`bash deploy/codespace.sh links` prints the links again. `~/.loopwright/server.log` is the cockpit's log.

## Any Linux server

On a machine with a public HTTPS address of its own (a reverse proxy with a certificate, or a tunnel with a
fixed name):

```bash
pip install git+https://github.com/dev-null-k2/loopwright
curl -fsSL https://claude.ai/install.sh | bash && claude        # Claude Code, then sign in once
loopwright serve --no-open --keep-key --public-url https://agent.example.com
```

`--keep-key` keeps one access key across restarts, so the printed link never changes. Run it under your service
manager of choice. The security notes in [security.md](security.md) apply: the key is all that stands between
the internet and your agent.

## What has and has not been tested

Checked on a real codespace (2-core, created from this repository): the dev container built and set itself up in
about three minutes; the cockpit answered at its forwarded address and refused requests without the key; a turn
ran from a phone-sized browser and was collected by polling; the browser agent ran in the codespace's own
Chromium after an approval given from the page; the request log masked the key.

The wake button and the page connecting by itself were run in a browser at phone size against a stand-in agent
that was started after the tap.

The public mode was then switched on for that codespace: the public cockpit page, opened with the one link at
phone size, connected to it and ran a turn.

Not checked: Claude Code signed in there (that takes your account), waking a sleeping codespace from a phone,
and whether the request log really keeps it awake past 30 minutes of use.
