#!/usr/bin/env bash
# Loopwright inside a GitHub Codespace. The dev container runs these by itself:
#
#   setup   once, when the codespace is created: install Loopwright, Claude Code and a browser
#   start   every time the codespace starts: run the cockpit in the background
#   watch   when the editor opens: print the links, then follow the request log
#   links   print the links again
#   public  one link that works on any device: opens the port to the internet, guarded by the access key alone
#   private back to the default: the port answers only to you, signed in to GitHub
#
# Private (the default): opening the cockpit takes your GitHub sign-in and the access key.
# Public: the public cockpit page becomes the one link. It carries the key, needs no GitHub sign-in while
# the codespace is awake, and offers a Wake button when it is asleep.
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

STATE="$HOME/.loopwright"
LOG="$STATE/server.log"
NAME="${CODESPACE_NAME:-}"
ADDRESS="https://${NAME}-8765.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
WAKE="https://${NAME}.github.dev"
PAGE="${LOOPWRIGHT_PAGE:-https://dev-null-k2.github.io/loopwright/}"
MODE="$(cat "$STATE/port" 2>/dev/null || echo private)"

one_link() { echo "${PAGE}#connect=${ADDRESS}&key=$(cat "$STATE/access_key" 2>/dev/null)&wake=${WAKE}"; }

# GitHub resets a forwarded port to private whenever the codespace starts, so this is done on every start.
open_port() {
  command -v gh >/dev/null 2>&1 || (sudo apt-get update -qq && sudo apt-get install -y -qq gh) >/dev/null 2>&1
  for _ in $(seq 1 30); do
    gh codespace ports visibility 8765:public -c "$NAME" >/dev/null 2>&1 && return 0
    sleep 2
  done
  echo "$(date +%H:%M:%S)  could not make port 8765 public (is gh installed and signed in?)" >> "$LOG"
  return 1
}

links() {
  local key
  key="$(cat "$STATE/access_key" 2>/dev/null || true)"
  echo
  if [ "$MODE" = public ]; then
    echo "  Your agent, on any device    $(one_link)"
    echo
    echo "  One link: bookmark it. It needs no sign-in while the codespace is awake, and shows a"
    echo "  Wake button when it is asleep. Anyone who has this link can use your agent."
  else
    echo "  Cockpit, for your phone    $ADDRESS/?key=$key"
    echo "  Wake this codespace        $WAKE"
    echo
    echo "  Bookmark both on the phone. The cockpit link works while the codespace is running;"
    echo "  the second link starts it again after it has gone to sleep (give it a minute)."
    echo "  For one link that works on any device without a GitHub sign-in: bash deploy/codespace.sh public"
  fi
  if ! command -v claude >/dev/null 2>&1; then
    echo "  Claude Code is not installed here: curl -fsSL https://claude.ai/install.sh | bash"
  elif [ ! -e "$HOME/.claude/.credentials.json" ] && [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}${ANTHROPIC_API_KEY:-}" ]; then
    echo "  To use Claude Code: run  claude  in a terminal here once and sign in. Until then the"
    echo "  built-in scripted model answers."
  fi
  echo
}

case "${1:-}" in
  setup)
    python3 -m pip install --quiet --user -e . || { echo "Loopwright did not install"; exit 1; }
    command -v claude >/dev/null 2>&1 || curl -fsSL https://claude.ai/install.sh | bash \
      || echo "Claude Code did not install. Later: curl -fsSL https://claude.ai/install.sh | bash"
    command -v gh >/dev/null 2>&1 || (sudo apt-get update -qq && sudo apt-get install -y -qq gh) >/dev/null 2>&1 || true
    if ! command -v chromium >/dev/null 2>&1 && ! command -v google-chrome >/dev/null 2>&1; then
      (sudo apt-get update -qq && sudo apt-get install -y -qq --no-install-recommends chromium) >/dev/null 2>&1 \
        || echo "No browser was installed, so the browser agent is unavailable here."
    fi
    ;;
  start)
    [ -n "$NAME" ] || { echo "This is for a GitHub Codespace. Elsewhere: loopwright serve --tunnel"; exit 1; }
    mkdir -p "$STATE"
    pkill -f "loopwright serve" 2>/dev/null
    if [ "$MODE" = public ]; then
      # the public cockpit page is the way in: it is let in with the key, and told how to wake this codespace
      setsid nohup loopwright serve --no-open --no-qr --keep-key --log-requests --public-url "$ADDRESS" --wake-url "$WAKE" \
        >> "$LOG" 2>&1 < /dev/null &
      (open_port &) >/dev/null 2>&1
    else
      # --page none: the port is private to you, so no outside page is let in; you open the cockpit at its own address
      setsid nohup loopwright serve --no-open --no-qr --keep-key --log-requests --page none --public-url "$ADDRESS" \
        >> "$LOG" 2>&1 < /dev/null &
    fi
    for _ in $(seq 1 40); do
      curl -fs http://127.0.0.1:8765/api/health >/dev/null 2>&1 && break
      sleep 0.5
    done
    curl -fs http://127.0.0.1:8765/api/health >/dev/null 2>&1 || { echo "The cockpit did not start. See $LOG"; tail -5 "$LOG"; exit 1; }
    links
    ;;
  watch)
    links
    # the cockpit link as a QR code, when the terminal is wide enough to draw it unbroken
    python3 - "$([ "$MODE" = public ] && one_link || echo "$ADDRESS/?key=$(cat "$STATE/access_key" 2>/dev/null)")" "$(tput cols 2>/dev/null || echo 0)" <<'PY' 2>/dev/null
import sys
from loopwright.qr import matrix, render
link, columns = sys.argv[1], int(sys.argv[2])
if len(matrix(link)) + 6 <= columns:
    print(render(link) + "\n  Scan with a phone's camera to open your agent there.\n")
PY
    echo "  Requests to the cockpit appear below. While this terminal shows activity, the codespace stays awake."
    echo
    tail -n 3 -F "$LOG" 2>/dev/null
    ;;
  links)
    links
    ;;
  public|private)
    mkdir -p "$STATE"
    echo "$1" > "$STATE/port"
    if [ "$1" = private ]; then gh codespace ports visibility 8765:private -c "$NAME" >/dev/null 2>&1; fi
    exec bash "$0" start
    ;;
  *)
    echo "usage: bash deploy/codespace.sh setup|start|watch|links|public|private"; exit 2
    ;;
esac
