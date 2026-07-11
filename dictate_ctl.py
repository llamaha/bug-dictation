#!/usr/bin/env python3
"""
dictate_ctl.py — tiny control client for dictate_daemon.py.

Bound to a global hotkey. Sends a single command over the daemon's Unix socket
and exits. Uses only the standard library so it starts in a few milliseconds —
the hotkey should feel instant.

Usage:  dictate_ctl.py [toggle|start|stop|status]   (default: toggle)
"""

import os
import socket
import subprocess
import sys
import time

SOCKET_PATH = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "bug-dictation.sock"
)
SERVICE = "bug-dictation.service"


def notify(title: str, body: str = "") -> None:
    try:
        subprocess.run(["notify-send", "-a", "bug-dictation", title, body],
                       check=False)
    except FileNotFoundError:
        pass


def send(cmd: str) -> "str | None":
    """Send one command to the daemon. Returns the reply, or None if the socket
    isn't reachable (daemon not running)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(SOCKET_PATH)
            s.sendall(cmd.encode())
            return s.recv(4096).decode("utf-8", "replace").strip()
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        return None


def start_daemon() -> bool:
    """Start the daemon on demand and wait for its socket. The daemon binds the
    socket before the (slow) model load, so this returns quickly — recording
    can begin while Whisper loads in the background."""
    try:
        subprocess.run(["systemctl", "--user", "start", SERVICE], check=False)
    except FileNotFoundError:
        return False
    for _ in range(100):  # up to ~10s for the socket to appear
        if send("status") is not None:
            return True
        time.sleep(0.1)
    return False


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "toggle"

    reply = send(cmd)
    if reply is None:
        # Daemon not running — a plain "status" shouldn't boot it.
        if cmd == "status":
            print("stopped")
            return 0
        notify("bug-dictation starting…", "Booting the dictation daemon.")
        if not start_daemon():
            notify("bug-dictation failed to start",
                   "Check: journalctl --user -u bug-dictation")
            print("could not start daemon", file=sys.stderr)
            return 1
        reply = send(cmd)
        if reply is None:
            print("daemon not reachable after start", file=sys.stderr)
            return 1

    print(reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
