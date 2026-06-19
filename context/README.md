# Chameleon

A minimal **Bevy 0.18 + OpenXR** starter template for **Meta Quest**, with the
VR plumbing already solved so you can jump straight to building your game:

- OpenXR session setup, passthrough (mixed reality), and `LOCAL_FLOOR` tracking
- Head + controller tracking, controller action bindings
- A worldspace **egui** UI rendered to a 3D panel, driven by a **laser pointer**
- Recenter (thumbstick-click and Meta-button long-press)
- **avian3d** physics
- A working **example game**: a glass menu (mixed-reality toggle, gravity slider)
  and a cube you grab with the trigger and throw.

The engine layer lives in `src/vr/` (reuse it as-is); the example game lives in
`src/game/` (**replace it with your own**).

---

## Quick start

```bash
# One-time tooling setup (see below), then with a Quest connected over USB:
./deploy.sh
```

`./deploy.sh` builds a release APK, installs it, and launches it on the headset.

---

## Prerequisites

### 1. Rust + the Android target

Install Rust via [rustup](https://rustup.rs/):

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup target add aarch64-linux-android
```

### 2. cargo-apk

```bash
cargo install cargo-apk
```

### 3. Android SDK + NDK

You do **not** need Android Studio — just a JDK plus the Android **command-line
tools** (which provide the SDK) and the **NDK**. (If you happen to have Android
Studio, its SDK Manager installs the same packages and you can skip to setting the
environment variables.)

These package versions match this template's `Cargo.toml`
(`target_sdk_version = 32`); bump them together if you change it.

**a) Install a JDK (17 or newer)** — needed by `sdkmanager`, `keytool`, `apksigner`.

- **Linux:** `sudo apt install openjdk-17-jdk` (or your distro's equivalent)
- **macOS:** `brew install --cask temurin`
- **Windows:** install [Temurin JDK](https://adoptium.net/) (MSI)

**b) Get the Android command-line tools** and place them at
`<sdk>/cmdline-tools/latest`:

- **macOS:** `brew install --cask android-commandlinetools` (sets this up for you;
  SDK at `/opt/homebrew/share/android-commandlinetools`)
- **Linux / Windows:** download "Command line tools only" from
  <https://developer.android.com/studio#command-tools>, then unzip so the path is
  `<sdk>/cmdline-tools/latest/bin` (e.g. `~/Android/Sdk/cmdline-tools/latest`).

**c) Install the SDK packages** with `sdkmanager` (on `<sdk>/cmdline-tools/latest/bin`):

```bash
sdkmanager "platform-tools" "platforms;android-32" "build-tools;34.0.0" "ndk;27.0.12077973"
sdkmanager --licenses        # accept the licenses
```

**d) Set environment variables** (point at your SDK; add to your shell profile):

- **Linux / macOS** (`~/.bashrc` / `~/.zshrc`):
  ```bash
  export ANDROID_HOME="$HOME/Android/Sdk"                 # macOS Homebrew: /opt/homebrew/share/android-commandlinetools
  export ANDROID_NDK_HOME="$ANDROID_HOME/ndk/27.0.12077973"
  export PATH="$ANDROID_HOME/platform-tools:$PATH"        # for adb
  ```
- **Windows** (PowerShell, persisted with `setx`):
  ```powershell
  setx ANDROID_HOME "$env:LOCALAPPDATA\Android\Sdk"
  setx ANDROID_NDK_HOME "$env:LOCALAPPDATA\Android\Sdk\ndk\27.0.12077973"
  # add %ANDROID_HOME%\platform-tools to your PATH (System Properties → Environment Variables)
  ```

Verify: `adb version`, `cargo apk --version`, `echo $ANDROID_HOME`.

### 4. Quest in developer mode

1. Create a Meta developer account and enable **Developer Mode** for your headset
   (via the Meta Horizon phone app: *Menu → Devices → Headset → Developer Mode*).
2. Connect the Quest by USB and **accept the "Allow USB debugging" prompt** inside
   the headset. Confirm with `adb devices` (you should see the device).
   - Wi-Fi alternative: *Settings → Developer → Wireless debugging*, then
     `adb connect <quest-ip>:5555`.

---

## Deploy

```bash
./deploy.sh              # release build → install → launch
./deploy.sh --debug      # faster build, worse VR performance
./deploy.sh --clean      # cargo clean first
./deploy.sh --uninstall  # uninstall first (wipes on-device settings)
./deploy.sh --no-launch  # install only
```

Release builds are signed with cargo-apk's **debug keystore** automatically — no
keystore to manage for local development. (Publishing to the Meta Store needs a
stable signing key and the Meta CLI; that's intentionally out of scope here.)

### Viewing logs

Rust `println!`/`info!` output comes through the **`RustStdoutStderr`** logcat tag:

```bash
adb logcat --pid=$(adb shell pidof com.example.chameleon)
# or:
adb logcat | grep --line-buffered RustStdoutStderr
```

---

## What's in the box

```
src/
  vr/      ← the reusable engine. Keep it.
  game/    ← the example game. Replace it.
```

See **CLAUDE.md** for an architecture tour: the module layout, the worldspace-UI
+ laser interaction model, the recenter/`SceneOrigin` pattern, the mixed-reality
toggle, and the critical `bevy_render_patched` note (a Quest-3 driver workaround
without which the app crashes after ~7.5 minutes).

## Building your game

1. Rename the package: `name` in `Cargo.toml`, and `package` /
   `apk_name` / `label` under `[package.metadata.android]` (and the
   `PACKAGE_NAME`/`APK_NAME` in `deploy.sh`).
2. Replace the contents of `src/game/` — keep `GamePlugin` as the entry the
   engine calls, but swap `state.rs` / `grab.rs` / `menu.rs` for your own
   systems, resources and UI.
3. Leave `src/vr/` alone (until you need to extend the engine itself).

## The `bevy_render_patched` workaround

This template ships a patched copy of `bevy_render` (`bevy_render_patched/`, wired
in via `[patch.crates-io]` in `Cargo.toml`) that works around an Adreno-740
(Quest 3) driver bug: a per-frame render pass on the wgpu Surface swap-chain leaks
one `sync_file` fence fd per frame, exhausting the fd limit and **crashing the app
after ~7.5 minutes**. The patch skips Surface texture acquisition on Android (safe
for XR-only apps — the window Surface is never shown).

This is the same fix as **[bevyengine/bevy#23276](https://github.com/bevyengine/bevy/pull/23276)**,
which has been upstreamed. Once **Bevy 0.19** is released and `bevy_oxr` bumps to
0.19, you can **delete `bevy_render_patched/` and the `[patch.crates-io]` block**
and rely on upstream. Until then we're on Bevy 0.18 (the OpenXR stack requires
`bevy_oxr`, which is still 0.18), so the workaround stays.

## License

MIT.
