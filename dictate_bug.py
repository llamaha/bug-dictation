#!/usr/bin/env python3
"""
dictate_bug.py — Dictate bugs you find while playtesting and turn them into a
clean, numbered prompt for a coding agent.

Pipeline:
  1. Record from your microphone (press Enter to stop).
  2. Transcribe locally with OpenAI Whisper on the GPU (RTX 5090 / CUDA).
  3. Send the raw transcript + game context to a local ollama model
     (default: gemma4:latest), which fixes transcription errors and rewrites
     it as a numbered list of actionable bug reports.
  4. Print the result, save it to ./bugs/, and copy it to the clipboard.

Usage:
  python dictate_bug.py                 # record -> transcribe -> format
  python dictate_bug.py --text "..."    # skip recording, format given text
  python dictate_bug.py --audio f.wav   # transcribe an existing audio file
  python dictate_bug.py --raw           # just transcribe, skip the LLM step

See `python dictate_bug.py --help` for all options.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent

# ---- Defaults (override via CLI flags or env vars) -------------------------
DEFAULT_WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3")
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:latest")
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
# Game context fed to the LLM so it understands the codebase / terminology.
# Points at the local ./context/ folder (copied from the game repo); the LLM
# receives every file in it. A single file path also works.
DEFAULT_CONTEXT = os.environ.get("GAME_CONTEXT", str(HERE / "context"))
SAMPLE_RATE = 16000  # Whisper expects 16 kHz mono
BUGS_DIR = HERE / "bugs"


# ---- 1. Recording ----------------------------------------------------------
def record_until_enter() -> "object":
    """Record mic audio until the user presses Enter. Returns a float32 numpy array."""
    import numpy as np
    import sounddevice as sd

    q: "queue.Queue" = queue.Queue()

    def callback(indata, frames, time_info, status):  # noqa: ANN001
        if status:
            print(f"  (audio: {status})", file=sys.stderr)
        q.put(indata.copy())

    print("\n🎙️  Recording — speak now. Press \033[1mEnter\033[0m to stop.")
    chunks = []
    stop = threading.Event()

    def wait_for_enter():
        try:
            input()
        except EOFError:
            pass
        stop.set()

    threading.Thread(target=wait_for_enter, daemon=True).start()

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        callback=callback):
        while not stop.is_set():
            try:
                chunks.append(q.get(timeout=0.1))
            except queue.Empty:
                continue
    # Drain anything still queued.
    while not q.empty():
        chunks.append(q.get())

    if not chunks:
        print("No audio captured.", file=sys.stderr)
        sys.exit(1)

    audio = np.concatenate(chunks, axis=0).flatten()
    secs = len(audio) / SAMPLE_RATE
    print(f"⏹️  Captured {secs:.1f}s of audio.")
    return audio


# ---- 2. Transcription -------------------------------------------------------
def transcribe(audio_or_path, model_name: str) -> str:
    """Transcribe a numpy array or an audio file path with Whisper."""
    import whisper

    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        device = "cpu"

    if device == "cpu":
        print("⚠️  CUDA not available — falling back to CPU (slow).", file=sys.stderr)
    print(f"🧠 Loading Whisper '{model_name}' on {device} …")
    model = whisper.load_model(model_name, device=device)

    print("✍️  Transcribing (text appears below as it's decoded) …\n")
    # verbose=True streams each segment to the terminal as it's recognised.
    result = model.transcribe(audio_or_path, fp16=(device == "cuda"), verbose=True)
    text = (result.get("text") or "").strip()
    if not text:
        print("Whisper returned no text.", file=sys.stderr)
        sys.exit(1)
    return text


# ---- 3. LLM cleanup via ollama ---------------------------------------------
def load_context(context_path: str) -> str:
    """Load context from a single file or every file in a directory."""
    p = Path(context_path)
    if not p.exists():
        print(f"⚠️  Game-context path not found: {p} (continuing without it).",
              file=sys.stderr)
        return ""
    if p.is_dir():
        files = sorted(f for f in p.rglob("*")
                       if f.is_file() and f.suffix.lower() in {".md", ".txt"})
        if not files:
            print(f"⚠️  No .md/.txt files in {p} (continuing without context).",
                  file=sys.stderr)
            return ""
        parts = []
        for f in files:
            rel = f.relative_to(p)
            parts.append(f"## {rel}\n\n{f.read_text(encoding='utf-8', errors='replace')}")
        print(f"📄 Game context: {len(files)} file(s) from {p}")
        return "\n\n".join(parts)
    return p.read_text(encoding="utf-8", errors="replace")


def build_prompt(transcript: str, context: str) -> str:
    ctx_block = (
        f"# Game context (for your understanding only — do not summarise it back)\n\n"
        f"{context}\n\n---\n\n"
        if context else ""
    )
    return f"""You are a meticulous bug-report editor for a video game project.

{ctx_block}A developer dictated the following notes out loud while playtesting,
and it was transcribed by speech-to-text. The transcript may contain
mis-heard words, filler ("um", "like"), run-on sentences, and homophone
errors. Use the game context above to correct obviously mis-transcribed
technical terms (file names, systems, mechanics).

Raw transcript:
\"\"\"
{transcript}
\"\"\"

Rewrite this as a clean, numbered list of distinct, actionable bug reports /
tasks that could be handed directly to a coding agent. Rules:
- One concrete issue per numbered item; split run-on thoughts apart.
- Keep the developer's intent; do NOT invent bugs that were not mentioned.
- Be concise and specific. Use the project's real terminology where clear.
- ORDER the items from highest priority to lowest. Judge priority by user
  impact and severity: crashes, data loss, and broken core gameplay rank
  highest; minor visual/polish issues rank lowest. Number 1 is the most
  important issue.
- Prefix each item with a "[severity]" tag — one of [critical], [high],
  [medium], or [low].
- If something is genuinely ambiguous, keep it but append "(unclear)".
- ALWAYS use an ordered markdown list ("1.", "2.", …), even if there is only
  one item. If the transcript contains no actionable bug, output the single
  line: "1. [low] No actionable bug found in transcript."
- Output ONLY the numbered markdown list — no preamble, no closing remarks,
  and no HTML.
"""


def call_ollama(prompt: str, model: str, url: str) -> str:
    endpoint = url.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"temperature": 0.2},
    }
    print(f"🤖 Formatting with ollama '{model}' …\n")
    try:
        resp = requests.post(endpoint, json=payload, stream=True, timeout=600)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        print(f"Could not reach ollama at {url}. Is it running? (`ollama serve`)",
              file=sys.stderr)
        sys.exit(1)

    out = []
    for line in resp.iter_lines():
        if not line:
            continue
        data = json.loads(line)
        if "response" in data:
            piece = data["response"]
            out.append(piece)
            print(piece, end="", flush=True)
        if data.get("done"):
            break
    print()
    return "".join(out).strip()


# ---- Output helpers ---------------------------------------------------------
def copy_to_clipboard(text: str) -> "str | None":
    """Copy text to the system clipboard. Returns the tool used, or None.

    The clipboard helper (xclip on X11, wl-copy on Wayland) must keep running
    in the background to *own* the selection. We detach it into its own session
    with stdout/stderr to /dev/null so it survives after this script — and any
    surrounding shell pipeline — exits; otherwise the clipboard reverts."""
    # Prefer the tool matching the session; fall back to whatever's installed.
    on_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    candidates = [("wl-copy", ["wl-copy"]),
                  ("xclip", ["xclip", "-selection", "clipboard"])]
    if not on_wayland:
        candidates.reverse()  # try xclip first on X11

    data = text.encode()
    for tool, argv in candidates:
        if not shutil.which(tool):
            continue
        try:
            p = subprocess.Popen(
                argv, stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            p.communicate(data)  # writes input, closes stdin, waits for fork
            if p.returncode == 0:
                return tool
        except Exception:
            continue
    return None


def save_output(raw: str, formatted: str) -> Path:
    BUGS_DIR.mkdir(exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = BUGS_DIR / f"bugs_{stamp}.md"
    body = (
        f"# Dictated bug report — {stamp}\n\n"
        f"{formatted}\n\n"
        f"## Raw transcript\n\n{raw}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


# ---- Main -------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Dictate bugs -> clean numbered prompt.")
    ap.add_argument("--text", help="Skip recording/transcription; format this text.")
    ap.add_argument("--audio", help="Transcribe this audio file instead of recording.")
    ap.add_argument("--raw", action="store_true",
                    help="Only transcribe; skip the LLM formatting step.")
    ap.add_argument("--whisper-model", default=DEFAULT_WHISPER_MODEL,
                    help=f"Whisper model (default: {DEFAULT_WHISPER_MODEL}). "
                         "e.g. tiny, base, small, medium, large-v3, turbo")
    ap.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL,
                    help=f"ollama model (default: {DEFAULT_OLLAMA_MODEL})")
    ap.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    ap.add_argument("--context", default=DEFAULT_CONTEXT,
                    help="Path to a game-context file or directory fed to the LLM.")
    ap.add_argument("--no-clip", action="store_true", help="Don't copy to clipboard.")
    args = ap.parse_args()

    # Get the raw transcript.
    if args.text:
        transcript = args.text.strip()
    else:
        source = args.audio if args.audio else record_until_enter()
        transcript = transcribe(source, args.whisper_model)

    print("\n\033[1m── Raw transcript ──────────────────────────\033[0m")
    print(transcript)
    print("\033[1m────────────────────────────────────────────\033[0m")

    if args.raw:
        return

    context = load_context(args.context)
    prompt = build_prompt(transcript, context)
    formatted = call_ollama(prompt, args.ollama_model, args.ollama_url)

    path = save_output(transcript, formatted)
    print(f"\n💾 Saved to {path}")
    if not args.no_clip:
        tool = copy_to_clipboard(formatted)
        if tool:
            print(f"📋 Copied to clipboard (via {tool}) — paste it straight in.")
        else:
            print("(No clipboard tool found — install xclip or wl-clipboard.)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)
