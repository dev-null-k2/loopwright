# Chat gateways: Telegram, Discord, Slack, WhatsApp

Talk to the same agent from a chat app. A gateway is a thin adapter: it turns "a message arrived" into
`agent.run_turn(...)` and sends the reply back. Memory, tools, guardrails and traces are exactly what they are in
the terminal and the cockpit. The shared part is [`gateways/chat.py`](../loopwright/gateways/chat.py), about 130
lines; each app is under a hundred on top of it. No dependencies: Discord and Slack use the WebSocket client in
[`ws.py`](../loopwright/ws.py).

`loopwright doctor` shows which gateways are configured. Put the variables in a `.env` file in the folder you run
from, or export them.

## What every gateway does

| | |
|---|---|
| **Allowlist** | A bot can be found by anyone. Only the ids in `<APP>_ALLOWED` are answered. Everyone else is told their own id, so you can add yourself: start the gateway with no allowlist, message the bot, copy the id it shows. An empty allowlist answers nobody. |
| **One session per chat** | Each chat keeps its own conversation. `/new` starts a fresh one. |
| **Approvals in words** | There is no button, so the agent asks: *"I'd like to run send_message with ... Reply yes to allow it."* Your next message is the answer. Anything but yes, or no reply in two minutes, is a no. |
| **`/steps`** | Toggles a short list of what the agent did under each reply. The full trace is always saved: `loopwright replay last`, or Activity in the cockpit. |
| **Scheduled tasks** | A task created from a chat ("every weekday at 8am, ...") is delivered back to that chat. The gateway runs the scheduler's clock while it is up. |
| **`/workflow <name>`** | Runs a workflow, the same as anywhere else. |

## Telegram

Long polling against the Bot API. Nothing needs to be reachable from the internet.

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. `export TELEGRAM_BOT_TOKEN=...` then `loopwright telegram`.
3. Message your bot. It replies with your chat id. Stop the gateway.
4. `export TELEGRAM_ALLOWED=<that id>` and start it again.

## Discord

A WebSocket to Discord's gateway for incoming messages, REST for replies. Nothing needs to be reachable.

1. [discord.com/developers](https://discord.com/developers/applications): New Application, then **Bot**. Copy the
   token and switch on **Message Content Intent**.
2. Under OAuth2, generate an invite URL with the `bot` scope and add it to a server, or just open a DM with it.
3. `export DISCORD_BOT_TOKEN=...` then `loopwright discord`. Message it to see your user id.
4. `export DISCORD_ALLOWED=<your user id>` and start it again.

It answers direct messages, and in servers only when mentioned.

## Slack

Socket Mode: Slack pushes events over a WebSocket the gateway opens. Nothing needs to be reachable.

1. [api.slack.com/apps](https://api.slack.com/apps): Create New App. Turn on **Socket Mode** and create an
   app-level token with `connections:write` (it starts `xapp-`).
2. OAuth scopes for the bot: `chat:write`, `im:history`, `app_mentions:read`. Under Event Subscriptions, subscribe
   to the bot events `message.im` and `app_mention`. Under App Home, allow messages. Install to your workspace and
   copy the bot token (it starts `xoxb-`).
3. `export SLACK_APP_TOKEN=xapp-... SLACK_BOT_TOKEN=xoxb-...` then `loopwright slack`. Message the app to see your
   member id.
4. `export SLACK_ALLOWED=<your member id>` and start it again.

Slack requires each event to be acknowledged within three seconds; the gateway does that at once and sends the
answer separately when the turn finishes.

## WhatsApp

The WhatsApp Cloud API delivers messages to a webhook you host, so this one does need a public URL. For personal
use a tunnel is enough (`cloudflared tunnel --url http://localhost:8790`, or ngrok).

1. [developers.facebook.com](https://developers.facebook.com/): create an app with the WhatsApp product. Note the
   **phone number id**, an **access token**, and the app's **secret** (Settings, Basic).
2. Set the variables and start the gateway:
   ```bash
   export WHATSAPP_TOKEN=...  WHATSAPP_PHONE_ID=...  WHATSAPP_APP_SECRET=...
   export WHATSAPP_VERIFY_TOKEN=any-string-you-choose
   export WHATSAPP_ALLOWED=15551234567          # your number, digits only, with country code
   loopwright whatsapp --port 8790
   ```
3. In the WhatsApp product's Configuration, set the webhook URL to `https://<your tunnel>/webhook`, paste the same
   verify token, and subscribe to `messages`.

Every delivery is signed by Meta with the app secret (`X-Hub-Signature-256`). The gateway recomputes the HMAC and
drops anything that does not match. That check is the only thing between a public URL and your agent, so the
gateway refuses to start without the secret.

## What is and is not tested

Each gateway's protocol handling is tested against a stand-in for the service: a fake Bot API for Telegram, a
local WebSocket server that plays Discord's gateway and Slack's Socket Mode, and real signed and unsigned HTTP
requests for the WhatsApp webhook ([`tests/test_automation.py`](../tests/test_automation.py)). None of them has
been run against the live service in CI, because that needs accounts and tokens. If one misbehaves against the
real thing, the fix is almost certainly in its one small file.

## Safety notes

- A message from an allowlisted chat is treated as you. Keep the allowlist to yourself.
- Messages pass through the chat platform. Do not use a chat gateway for anything you would not send over that
  platform.
- The taint rule still applies: after the agent reads a web page in a turn, it cannot send or schedule anything
  until the next turn, whichever app you are in.
- Tokens are read from the environment and never written to the state folder or a trace.
