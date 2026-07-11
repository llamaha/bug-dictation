# bug-dictation

Dictate bugs you find while playtesting your game and turn them into a clean,
numbered prompt for a coding agent — fully local.

**Pipeline:** mic → [Whisper](https://github.com/openai/whisper) (GPU) → raw
transcript → local [ollama](https://ollama.com) model (`gemma4:latest`) that
fixes transcription errors and rewrites it as actionable bug reports, using
your game's `CLAUDE.md` as context.

## Usage

```bash
./dictate.sh                      # record (Enter to stop) → transcribe → format
./dictate.sh --raw                # transcribe only, skip the LLM
./dictate.sh --text "the cube ..."  # format text directly (no mic)
./dictate.sh --audio note.wav     # transcribe an existing file
```

Output is printed, saved to `./bugs/bugs_<timestamp>.md`, and copied to the
clipboard.

## Hotkey mode (dictate from anywhere)

Instead of opening a terminal each time, a daemon owns the mic and the full
pipeline; a global hotkey toggles it:

```
hotkey → 🎙️ talk → hotkey → 📋 paste
```

The daemon is **on-demand**: it is not started at login (so it holds no VRAM
while you're not using it). The first hotkey press boots it — recording starts
immediately while Whisper loads in the background (recording doesn't need the
model, only transcription does), so a cold start still feels instant. After
`DICTATE_IDLE_TIMEOUT` seconds idle (default 600) it exits on its own and frees
the ~10 GB of GPU memory. The next press starts it again.

**Pieces:**

- `dictate_daemon.py` — owns the mic and the pipeline; listens on a Unix socket
  (`$XDG_RUNTIME_DIR/bug-dictation.sock`). A toggle from IDLE starts recording;
  a toggle from RECORDING stops, transcribes, formats, saves, and copies.
  Desktop notifications announce each step. Auto-exits when idle.
- `dictate_meter.py` — a small frameless overlay that pops up at the bottom of
  the screen while recording and shows a live, scrolling meter of your mic
  level, so you can see it's hearing you (green → yellow → red as you get
  louder). The daemon launches it on record start and closes it on stop. It
  reads its own capture stream, so it never interferes with the recording.
  Disable it by setting `DICTATE_METER=0` in the service environment.
- `dictate_ctl.py [toggle|start|stop|status]` — tiny stdlib client the hotkey
  runs; sends one command and exits (`toggle` is the default). If the daemon
  isn't running, `toggle`/`start` boots it on demand; `status` reports
  `stopped` without starting anything.
- `bug-dictation.service` — a systemd **user** service used to launch the
  daemon on demand. Installed but **not enabled**, so it never autostarts.

**Install the service (once):**

```bash
cp bug-dictation.service ~/.config/systemd/user/
systemctl --user daemon-reload
# Do NOT `enable` it — leave it disabled so it only runs on demand.
journalctl --user -u bug-dictation -f      # watch it / debug
```

**Bind the hotkey (KDE Plasma, one-time):**

System Settings → *Shortcuts* → *Custom Shortcuts* → *Edit ▸ New ▸ Global
Shortcut ▸ Command/URL*. Set:

- **Trigger:** your key, e.g. `Meta+Shift+D`
- **Action / Command:** `/home/adam/repos/bug-dictation/dictate_ctl.py toggle`

Apply. Now press the key to start dictating, press again to stop — the report
lands on your clipboard.

**Manual control** (no hotkey needed):

```bash
./dictate_ctl.py toggle                      # start / stop (boots the daemon if down)
./dictate_ctl.py status                      # stopped | idle | recording | busy
systemctl --user start bug-dictation         # pre-warm without recording
systemctl --user stop  bug-dictation         # shut down now, free VRAM
```

**VRAM knobs** (set in `bug-dictation.service` under `[Service]`, e.g.
`Environment=DICTATE_IDLE_TIMEOUT=300`):

| Env var | Default | Meaning |
|---------|---------|---------|
| `DICTATE_IDLE_TIMEOUT` | `600` | Seconds idle before the daemon exits and frees Whisper's VRAM. `0` = stay resident. |
| `OLLAMA_KEEP_ALIVE` | ollama's own (`5m`) | How long ollama keeps gemma in VRAM after formatting. `0` unloads it immediately; `10m` keeps it warm. |
| `DICTATE_METER` | `1` | `0` disables the live mic-level overlay. |

### Useful flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--whisper-model` | `large-v3` | `tiny`/`base`/`small`/`medium`/`large-v3`/`turbo` |
| `--ollama-model`  | `gemma4:latest` | any model in `ollama list` |
| `--context`       | `./context/` | game-context file or dir fed to the LLM |
| `--no-clip`       | off | don't copy to clipboard |

All flags also have env-var equivalents (`WHISPER_MODEL`, `OLLAMA_MODEL`,
`OLLAMA_URL`, `GAME_CONTEXT`).

## Setup (already done)

```bash
python3 -m venv venv
venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cu128  # Blackwell / RTX 5090
venv/bin/pip install -r requirements.txt
```

The first run downloads the Whisper model weights (cached in `~/.cache/whisper`).

## Notes

- Default is `large-v3` (maximum accuracy) — plenty fast on the RTX 5090.
  Use `--whisper-model turbo` if you ever want it even faster.
- The LLM reorders issues highest→lowest priority and tags each with a
  `[critical]`/`[high]`/`[medium]`/`[low]` severity.
- Requires `ollama serve` running with `gemma4:latest` pulled.
- Clipboard uses `xclip` (X11) or `wl-copy` (Wayland) if present.
