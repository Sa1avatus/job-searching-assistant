#!/bin/sh
set -eu

virtual_display="${DISPLAY:-:99}"
export DISPLAY="${virtual_display}"

Xvfb "${virtual_display}" -screen 0 1440x900x24 -ac -nolisten tcp \
  >/tmp/job-assistant-xvfb.log 2>&1 &
sleep 1

x11vnc -display "${virtual_display}" -forever -shared -nopw -localhost -rfbport 5900 \
  >/tmp/job-assistant-x11vnc.log 2>&1 &

websockify --web=/usr/share/novnc 0.0.0.0:7900 localhost:5900 \
  >/tmp/job-assistant-websockify.log 2>&1 &

alembic upgrade head
exec uvicorn app.api.main:app --host 0.0.0.0 --port 8000
