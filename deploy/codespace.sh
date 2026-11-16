#!/usr/bin/env bash
# Loopwright inside a GitHub Codespace. The dev container runs these by itself:
#
#   setup   once, when the codespace is created: install Loopwright, Claude Code and a browser
#   start   every time the codespace starts: run the cockpit in the background
#   watch   when the editor opens: print the links, then follow the request log
#   links   print the links again
#
# The cockpit's port is forwarded privately, so opening it takes your GitHub sign-in and the access key.
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

STATE="$HOME/.loopwright"
LOG="$STATE/server.log"
NAME="${CODESPACE_NAME:-}"
ADDRESS="https://${NAME}-8765.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"

links() {
  local key
  key="$(cat "$STATE/access_key" 2>/dev/null || true)"
  echo
  echo "  Cockpit, for your phone    $ADDRESS/?key=$key"
  echo "  Wake this codespace        https://${NAME}.github.dev"
  echo
  echo "  Bookmark both on the phone. The cockpit link works while the codespace is running;"
  echo "  the second link starts it again after it has gone to sleep (give it a minute)."
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
    if ! command -v chromium >/dev/null 2>&1 && ! command -v google-chrome >/dev/null 2>&1; then
      (sudo apt-get update -qq && sudo apt-get install -y -qq --no-install-recommends chromium) >/dev/null 2>&1 \
        || echo "No browser was installed, so the browser agent is unavailable here."
    fi
    ;;
  start)
    [ -n "$NAME" ] || { echo "This is for a GitHub Codespace. Elsewhere: loopwright serve --tunnel"; exit 1; }
    mkdir -p "$STATE"
    pkill -f "loopwright serve" 2>/dev/null
    # --page none: the port is private to you, so no outside page is let in; you open the cockpit at its own address
    setsid nohup loopwright serve --no-open --no-qr --keep-key --log-requests --page none --public-url "$ADDRESS" \
      >> "$LOG" 2>&1 < /dev/null &
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
    python3 - "$ADDRESS/?key=$(cat "$STATE/access_key" 2>/dev/null)" "$(tput cols 2>/dev/null || echo 0)" <<'PY' 2>/dev/null
import sys
from loopwright.qr import matrix, render
link, columns = sys.argv[1], int(sys.argv[2])
if len(matrix(link)) + 6 <= columns:
    print(render(link) + "\n  Scan with the phone's camera to open the cockpit there.\n")
PY
    echo "  Requests to the cockpit appear below. While this terminal shows activity, the codespace stays awake."
    echo
    tail -n 3 -F "$LOG" 2>/dev/null
    ;;
  links)
    links
    ;;
  *)
    echo "usage: bash deploy/codespace.sh setup|start|watch|links"; exit 2
    ;;
esac
