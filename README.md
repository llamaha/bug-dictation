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
