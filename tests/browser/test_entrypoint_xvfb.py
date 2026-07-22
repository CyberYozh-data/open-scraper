"""The container entrypoint starts Xvfb so headful requests have a display.

A restarted container reuses its writable layer, so the X lock and socket from
the previous run survive into the new one. Xvfb then refuses to start ("Server
is already active") and every headful request fails at browser launch. These
tests pin that the entrypoint recovers from those leftovers.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e  # forks real processes and touches /tmp

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "scripts" / "docker-entrypoint.sh"

# Displays this container does not serve requests on, so the tests never
# disturb the live one. They do not share a display either: an X server removes
# its socket as it exits, so one still shutting down would delete a socket a
# later test had just placed at the same path.
TEST_DISPLAY = 77
SPARE_DISPLAY = 78


def _lock_path(display: int) -> Path:
    return Path(f"/tmp/.X{display}-lock")


def _socket_path(display: int) -> Path:
    return Path(f"/tmp/.X11-unix/X{display}")


def _display_is_live(display: int) -> bool:
    """True only when something actually accepts X connections.

    A leftover socket file still exists on disk, so an existence check cannot
    tell a served display from a dead one — connecting can.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(2)
        sock.connect(str(_socket_path(display)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _run_entrypoint(
    display: int | str,
    *command: str,
    inherited_display: str | None = None,
    shadow_xvfb_with: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
    env["XVFB_DISPLAY"] = str(display)
    if inherited_display is not None:
        env["DISPLAY"] = inherited_display
    if shadow_xvfb_with is not None:
        # Shadow only Xvfb. Emptying PATH would take mkfifo/timeout/head with
        # it and break the entrypoint for reasons unrelated to the display.
        env["PATH"] = f"{shadow_xvfb_with}:{env.get('PATH', '')}"
    # Capture through files, not pipes. Xvfb inherits the entrypoint's stderr
    # and outlives it by design, so it holds a pipe open long after the command
    # has exited and subprocess would wait for it until the timeout.
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "stdout"
        err_path = Path(tmp) / "stderr"
        with out_path.open("w") as out, err_path.open("w") as err:
            done = subprocess.run(
                ["/bin/sh", str(ENTRYPOINT), *command],
                env=env,
                stdout=out,
                stderr=err,
                timeout=60,
                check=False,
            )
        return subprocess.CompletedProcess(
            done.args, done.returncode, out_path.read_text(), err_path.read_text()
        )


_ECHO_DISPLAY = ("sh", "-c", 'printf "DISPLAY=[%s]" "${DISPLAY-}"')


@pytest.fixture
def throwaway_display():
    """Leave no live Xvfb, lock or socket behind for the next test.

    The entrypoint execs its command, so Xvfb outlives it and is reparented to
    PID 1 — which, being the app rather than an init, never reaps it. Killing it
    therefore leaves a zombie entry per run. That costs a PID slot and nothing
    else, and it is also why `pgrep` alone cannot tell a live Xvfb from a dead
    one — the check below connects to the socket instead.
    """
    allocated = []

    def _allocate(display: int = TEST_DISPLAY) -> int:
        if os.environ.get("XVFB_DISPLAY") == str(display):
            pytest.skip(f"this container serves :{display}; the teardown would tear it down")
        # Only an X server creates this directory, so it is absent until one runs.
        _socket_path(display).parent.mkdir(parents=True, exist_ok=True)
        allocated.append(display)
        return display

    yield _allocate

    for display in allocated:
        subprocess.run(["pkill", "-f", f"Xvfb :{display}"], check=False)
        _lock_path(display).unlink(missing_ok=True)
        _socket_path(display).unlink(missing_ok=True)


def _leave_previous_run_debris(display: int) -> None:
    """Exactly what a restarted container inherits, and both halves matter.

    The lock is what blocks Xvfb, but only in the shape a restart produces: X
    stores the owning PID as `%10d` and refuses the display when that PID is
    still alive. A restarted container numbers its PIDs from scratch, so the
    number recorded by the previous run is live again — often the very Xvfb now
    trying to start. A lock naming a dead PID is taken over cleanly, and a
    malformed one is ignored outright, so neither reproduces the bug.

    The socket is what makes the failure silent: it satisfies an existence
    check instantly, so the entrypoint stops waiting and reports success.
    """
    _lock_path(display).write_text("%10d\n" % os.getpid())
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(_socket_path(display)))
    stale.close()


@pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not installed")
def test_entrypoint_serves_a_display_despite_a_previous_runs_leftovers(throwaway_display):
    display = throwaway_display()
    _leave_previous_run_debris(display)
    assert not _display_is_live(display), "precondition: the leftover socket is dead"

    result = _run_entrypoint(display, *_ECHO_DISPLAY)

    assert result.returncode == 0, f"entrypoint failed: {result.stderr!r}"
    assert _display_is_live(display), (
        f"entrypoint left a dead display behind; stderr={result.stderr!r}"
    )
    # The exported value has to be a display specifier, not a bare number: the
    # runner's guard only checks that DISPLAY is non-empty, so a malformed one
    # passes it and fails later inside Playwright instead.
    assert f"DISPLAY=[:{display}]" in result.stdout, f"stdout={result.stdout!r}"


def test_entrypoint_does_not_advertise_a_display_it_could_not_start(tmp_path):
    """A DISPLAY pointing at nothing defeats the runner's fail-fast guard.

    ``src/browser/runner.py`` refuses a headful launch when DISPLAY is unset and
    names Xvfb in the message. Exporting a display that never came up walks the
    request straight past that guard into Playwright's opaque "Missing X server"
    crash — the very symptom this fix exists to prevent. Headless traffic is
    unaffected by a missing display, so the container must still come up.
    """
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "Xvfb"
    stub.write_text("#!/bin/sh\nexit 1\n")
    stub.chmod(0o755)

    result = _run_entrypoint(TEST_DISPLAY, *_ECHO_DISPLAY, shadow_xvfb_with=stub_dir)

    assert result.returncode == 0, "a display that won't start must not stop the container"
    assert "WARNING" in result.stderr, f"failure must be loud; stderr={result.stderr!r}"
    assert "DISPLAY=[]" in result.stdout, f"stdout={result.stdout!r}"


def test_entrypoint_leaves_an_externally_supplied_display_alone(throwaway_display):
    """DISPLAY already set means an operator pointed us at a real X server.

    Starting an Xvfb of our own alongside it would be wrong, and so would
    touching the lock and socket that server owns.
    """
    display = throwaway_display(SPARE_DISPLAY)
    _leave_previous_run_debris(display)  # stands in for the operator's server

    result = _run_entrypoint(display, *_ECHO_DISPLAY, inherited_display=f":{display}")

    assert result.returncode == 0
    assert f"DISPLAY=[:{display}]" in result.stdout, f"stdout={result.stdout!r}"
    assert _lock_path(display).exists(), "deleted a lock this entrypoint does not own"
    assert _socket_path(display).exists(), "deleted a socket this entrypoint does not own"
