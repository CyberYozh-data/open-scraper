#!/bin/sh
# Container entrypoint. Headful browsers need an X display; on a headless server
# we provide a virtual one via Xvfb. Launch mode is per-request
# (ScrapeRequest.headless), so a container whose default is headless must still
# be able to serve a headful request — start Xvfb unconditionally. It is one
# idle ~40MB process per container and does not scale with browser count;
# headless Chromium simply ignores DISPLAY.
set -e

if [ -z "${DISPLAY}" ]; then
    Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 &
    export DISPLAY=:99
    # Wait briefly for the display socket so the first browser launch doesn't race it.
    i=0
    while [ ! -e /tmp/.X11-unix/X99 ] && [ "$i" -lt 25 ]; do
        i=$((i + 1))
        sleep 0.2
    done
    # Don't fail the container: headless requests (the common case) ignore
    # DISPLAY and must still be served. But say so loudly — otherwise the only
    # symptom is every headful request failing at browser launch with no clue
    # that Xvfb is the cause.
    if [ ! -e /tmp/.X11-unix/X99 ]; then
        echo "WARNING: Xvfb display :99 did not appear after 5s; headful requests will fail (headless is unaffected)" >&2
    fi
fi

exec "$@"
