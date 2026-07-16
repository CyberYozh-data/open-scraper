#!/bin/sh
# Container entrypoint. Headful browsers (HEADLESS=false) need an X display; on a
# headless server we provide a virtual one via Xvfb. Headless runs (the default)
# skip it entirely — no Xvfb process, no cost. Only the worker actually launches
# browsers, but starting an idle Xvfb elsewhere is harmless.
set -e

if [ "${HEADLESS}" = "false" ] && [ -z "${DISPLAY}" ]; then
    Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 &
    export DISPLAY=:99
    # Wait briefly for the display socket so the first browser launch doesn't race it.
    i=0
    while [ ! -e /tmp/.X11-unix/X99 ] && [ "$i" -lt 25 ]; do
        i=$((i + 1))
        sleep 0.2
    done
fi

exec "$@"
