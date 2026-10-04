#!/usr/bin/env bash
# AQUAHOLICS OCS operator display: start the vehicle backend and open the display.
# Then connect each vehicle on its card (port, baud, Connect). The backend remembers the
# connections and reconnects them by itself at the next start.
#
#   ./start.sh            open in the default browser
#   ./start.sh --kiosk    full-screen kiosk window (Chrome/Chromium), for runs
#
# Stop with Ctrl+C. The OCS (../main.py, RoboCommand side) is started separately.
set -euo pipefail
cd "$(dirname "$0")"

URL="http://127.0.0.1:5080/"
KIOSK=0
[[ "${1:-}" == "--kiosk" ]] && KIOSK=1

# Build the display the first time, and again when its sources changed.
if [[ ! -f frontend/dist/index.html ]] || [[ -n "$(find frontend/src frontend/index.html -newer frontend/dist/index.html -print -quit)" ]]; then
  echo "Building the operator display..."
  (cd frontend && { [[ -d node_modules ]] || npm install; } && npm run build)
fi

# Open the browser once the backend answers.
(
  for _ in $(seq 120); do
    if curl -s -o /dev/null "$URL"; then
      if (( KIOSK )); then
        for b in google-chrome chromium chromium-browser; do
          if command -v "$b" >/dev/null; then exec "$b" --kiosk --app="$URL" >/dev/null 2>&1; fi
        done
      fi
      exec xdg-open "$URL" >/dev/null 2>&1
    fi
    sleep 0.5
  done
  echo "The backend did not answer on $URL" >&2
) &

cd backend/OcsBackend
exec dotnet run
