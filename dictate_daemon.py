#!/usr/bin/env python3
"""
dictate_daemon.py — resident, GPU-warm companion to dictate_bug.py.

Loads the Whisper model once and keeps it in VRAM, then waits on a Unix socket
for control commands. A global hotkey (bound to `dictate_ctl.py toggle`) drives
it as a toggle:

    toggle  (while IDLE)      → start recording the mic
    toggle  (while RECORDING) → stop, transcribe, format via ollama, save, copy
    toggle  (while BUSY)      → ignored (processing the previous take)

Desktop notifications announce each state change. The result is saved to ./bugs/
and copied to the clipboard, exactly like the CLI.

Run it under systemd --user (see bug-dictation.service) so it starts at login
and stays warm. For a quick manual test:  ./venv/bin/python dictate_daemon.py
"""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from enum import Enum

import dictate_bug as db

HERE = os.path.dirname(os.path.abspath(__file__))
SOCKET_PATH = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "bug-dictation.sock"
)
METER_SCRIPT = os.path.join(HERE, "dictate_meter.py")
SHOW_METER = os.environ.get("DICTATE_METER", "1") != "0"

# Config mirrors the CLI defaults (override via the same env vars).
WHISPER_MODEL = db.DEFAULT_WHISPER_MODEL
OLLAMA_MODEL = db.DEFAULT_OLLAMA_MODEL
OLLAMA_URL = db.DEFAULT_OLLAMA_URL
CONTEXT_PATH = db.DEFAULT_CONTEXT
SAMPLE_RATE = db.SAMPLE_RATE

# Auto-shutdown: exit (freeing the Whisper VRAM) after this many seconds with
# no activity while IDLE. 0 disables the timeout (stay resident forever).
IDLE_TIMEOUT = int(os.environ.get("DICTATE_IDLE_TIMEOUT", "600"))
# How long ollama keeps its model in VRAM after formatting. "" leaves ollama's
# own default (5m); "0" unloads immediately; "10m" etc. to keep it warm longer.
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "") or None


class State(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    BUSY = "busy"


def notify(title: str, body: str = "") -> None:
    """Best-effort desktop notification. The synchronous hint makes KDE collapse
    successive notifications into a single, updating popup."""
    try:
        subprocess.run(
            ["notify-send", "-a", "bug-dictation",
             "-h", "string:x-canonical-private-synchronous:bugdict",
             title, body],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


class Daemon:
    def __init__(self) -> None:
        self.state = State.IDLE
        self.lock = threading.Lock()
        # Recording state (only touched while holding the lock or from the
        # audio callback while a stream is open).
        self._stream = None
        self._chunks: list = []
        self._meter = None  # Popen of the level-meter overlay
        self.model = None  # loaded by warm_up() in a background thread
        self.model_ready = threading.Event()
        self.last_activity = time.monotonic()

    # ---- model ----------------------------------------------------------
    def warm_up(self) -> None:
        """Load the model in the background so the socket can accept commands
        immediately — recording doesn't need the model, only transcription
        does, so we can start capturing while this loads."""
        self.model = db.load_whisper_model(WHISPER_MODEL)
        self.model_ready.set()
        print("✅ Model warm.", flush=True)

    # ---- recording ------------------------------------------------------
    def _start_recording(self) -> None:
        import sounddevice as sd

        self._chunks = []

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                print(f"  (audio: {status})", file=sys.stderr)
            self._chunks.append(indata.copy())

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback
        )
        self._stream.start()
        self._start_meter()
        self.state = State.RECORDING
        print("🎙️  Recording…", flush=True)
        notify("🎙️ Recording…", "Press the hotkey again to stop.")

    def _start_meter(self) -> None:
        if not SHOW_METER or not os.path.exists(METER_SCRIPT):
            return
        try:
            self._meter = subprocess.Popen(
                [sys.executable, METER_SCRIPT],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f"  (meter failed to launch: {exc})", file=sys.stderr)
            self._meter = None

    def _stop_meter(self) -> None:
        meter, self._meter = self._meter, None
        if meter is None:
            return
        try:
            meter.terminate()
            meter.wait(timeout=2)
        except Exception:
            try:
                meter.kill()
            except Exception:
                pass

    def _stop_recording(self):
        """Close the stream and return the captured float32 mono array (or None)."""
        import numpy as np

        self._stop_meter()
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        chunks, self._chunks = self._chunks, []
        if not chunks:
            return None
        return np.concatenate(chunks, axis=0).flatten()

    # ---- processing (runs in a worker thread) --------------------------
    def _process(self, audio) -> None:
        try:
            secs = len(audio) / SAMPLE_RATE
            if not self.model_ready.is_set():
                print("⏳ Waiting for the model to finish loading…", flush=True)
                notify("⏳ Loading model…", "First run this session — one moment.")
                self.model_ready.wait()
            print(f"⏹️  Captured {secs:.1f}s. Transcribing…", flush=True)
            notify("✍️ Transcribing…", f"{secs:.0f}s of audio")
            transcript = db.transcribe_audio(self.model, audio, verbose=True)
            if not transcript:
                notify("⚠️ Nothing transcribed", "No speech recognised.")
                return

            print(f"── transcript ──\n{transcript}", flush=True)
            notify("🤖 Formatting…", "Cleaning up with the local LLM.")
            context = db.load_context(CONTEXT_PATH)
            prompt = db.build_prompt(transcript, context)
            formatted = db.call_ollama(prompt, OLLAMA_MODEL, OLLAMA_URL,
                                       keep_alive=OLLAMA_KEEP_ALIVE)

            path = db.save_output(transcript, formatted)
            tool = db.copy_to_clipboard(formatted)
            n_items = sum(
                1 for ln in formatted.splitlines()
                if re.match(r"\s*\d+\.\s", ln)
            )
            clip = "copied to clipboard" if tool else "saved (no clipboard tool)"
            print(f"💾 Saved to {path}", flush=True)
            notify("📋 Bug report ready",
                   f"{n_items} item(s) — {clip}. Paste it in.")
        except Exception as exc:  # keep the daemon alive no matter what
            print(f"‼️  Error while processing: {exc}", file=sys.stderr, flush=True)
            notify("‼️ Dictation failed", str(exc))
        finally:
            with self.lock:
                self.state = State.IDLE
                self.last_activity = time.monotonic()

    # ---- command dispatch ----------------------------------------------
    def handle(self, cmd: str) -> str:
        cmd = cmd.strip().lower()
        with self.lock:
            self.last_activity = time.monotonic()
            if cmd == "status":
                return self.state.value
            if cmd in ("toggle", "start", "stop"):
                if self.state is State.BUSY:
                    return "busy"
                if self.state is State.IDLE:
                    if cmd == "stop":
                        return "idle"  # nothing to stop
                    self._start_recording()
                    return "recording"
                if self.state is State.RECORDING:
                    if cmd == "start":
                        return "recording"  # already recording
                    audio = self._stop_recording()
                    if audio is None:
                        self.state = State.IDLE
                        notify("⚠️ No audio", "Nothing was captured.")
                        return "idle"
                    self.state = State.BUSY
                    threading.Thread(
                        target=self._process, args=(audio,), daemon=True
                    ).start()
                    return "processing"
            return f"unknown command: {cmd!r}"

    # ---- socket server --------------------------------------------------
    def serve(self) -> None:
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o600)
        srv.listen(8)
        srv.settimeout(5.0)  # so the accept loop can check the idle timeout
        print(f"🔌 Listening on {SOCKET_PATH}", flush=True)
        if IDLE_TIMEOUT:
            print(f"⏲️  Will exit after {IDLE_TIMEOUT}s idle (frees VRAM).",
                  flush=True)
        try:
            while True:
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    if self._idle_expired():
                        print("💤 Idle timeout — shutting down to free VRAM.",
                              flush=True)
                        return
                    continue
                with conn:
                    try:
                        data = conn.recv(4096).decode("utf-8", "replace")
                    except OSError:
                        continue
                    if not data:
                        continue
                    reply = self.handle(data)
                    try:
                        conn.sendall((reply + "\n").encode())
                    except OSError:
                        pass
        finally:
            srv.close()
            if os.path.exists(SOCKET_PATH):
                os.unlink(SOCKET_PATH)

    def _idle_expired(self) -> bool:
        if not IDLE_TIMEOUT:
            return False
        with self.lock:
            if self.state is not State.IDLE:
                return False  # never quit mid-recording or mid-processing
            return (time.monotonic() - self.last_activity) >= IDLE_TIMEOUT


def _hard_exit(*_) -> None:
    """Unlink the socket and terminate immediately. os._exit avoids racing the
    CUDA/torch teardown in the background loader thread (which can otherwise
    abort with 'terminate called'); the OS reclaims the GPU memory on exit."""
    try:
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
    finally:
        os._exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _hard_exit)
    signal.signal(signal.SIGINT, _hard_exit)
    daemon = Daemon()
    # Load the model in the background so the socket is ready immediately and
    # a cold hotkey press can start recording without waiting for the GPU load.
    threading.Thread(target=daemon.warm_up, daemon=True).start()
    notify("bug-dictation starting…", "Warming up — hotkey is armed.")
    daemon.serve()   # returns on idle timeout
    _hard_exit()


if __name__ == "__main__":
    main()
