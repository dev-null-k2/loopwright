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
2. Wait for the set-up to finish (a few minutes the first time). The terminal then prints two links:

   ```
   Cockpit, for your phone    https://<name>-8765.app.github.dev/?key=<key>
   Wake this codespace        https://<name>.github.dev
   ```

3. To use Claude Code, type `claude` in that terminal once and sign in with your Claude account. The sign-in
   is Anthropic's own and stays in the codespace. Until you do, the built-in scripted model answers.

### Use it from a phone

Bookmark both links on the phone.

- **Cockpit**: your agent. The first visit asks you to sign in to GitHub, because the port is private to you,
  and then stores the access key in a cookie.
- **Wake**: when the cockpit link shows an error, the codespace is asleep. Open the wake link, wait about a
  minute for the editor to appear, then open the cockpit link again.

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

The dev container's script was run with a made-up codespace name, and the server's behaviour behind a forwarder
(a rewritten Host header, forwarding headers, its own public origin) is covered by tests. Polling was run in a
browser at phone size with the page and the agent on different origins, including an approval. What was checked
on a real codespace is listed in the changelog entry for the release that added this page.
