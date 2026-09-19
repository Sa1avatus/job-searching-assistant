#!/bin/bash
# Start the virtual display stack in dependency order and only then the browser HTTP server.
#
# Everything downstream (x11vnc, the login window Playwright opens, noVNC) needs a display that is
# already accepting connections. A fixed `sleep` raced on a slow start, so each step now waits for
# the thing it depends on and fails loudly instead of letting the worker come up half-working.
set -eu

virtual_display="${DISPLAY:-:99}"
export DISPLAY="${virtual_display}"
display_number="${virtual_display#:}"

wait_for() {
  # wait_for <description> <timeout-seconds> <command...>
  description="$1"; timeout="$2"; shift 2
  elapsed=0
  until "$@" >/dev/null 2>&1; do
    elapsed=$((elapsed + 1))
    if [ "$elapsed" -ge $((timeout * 10)) ]; then
      echo "browser-worker: timed out waiting for ${description}" >&2
      return 1
    fi
    sleep 0.1
  done
}

rm -f "/tmp/.X${display_number}-lock" "/tmp/.X11-unix/X${display_number}"

Xvfb "${virtual_display}" -screen 0 1440x900x24 -ac -nolisten tcp \
  >/tmp/job-assistant-xvfb.log 2>&1 &
xvfb_pid=$!

# Without a display nothing else can work: fail so the container restarts instead of lingering.
wait_for "the X display ${virtual_display}" 30 xdpyinfo -display "${virtual_display}" || exit 1
kill -0 "${xvfb_pid}" 2>/dev/null || { echo "browser-worker: Xvfb exited" >&2; exit 1; }

x11vnc -display "${virtual_display}" -forever -shared -nopw -localhost -rfbport 5900 \
  >/tmp/job-assistant-x11vnc.log 2>&1 &
# The viewer is a convenience: warn, but keep the worker (and Playwright) usable without it.
wait_for "x11vnc on port 5900" 30 bash -c 'exec 3<>/dev/tcp/127.0.0.1/5900' || true

websockify --web=/usr/share/novnc 0.0.0.0:7900 localhost:5900 \
  >/tmp/job-assistant-websockify.log 2>&1 &

exec python -m app.workers.browser_http_server
