# Chameleon

A Bevy 0.18 + OpenXR starter template for Meta Quest. **The engine layer
(`src/vr/`) is reusable plumbing — keep it. The example game (`src/game/`) is
meant to be replaced.**

## Version control

This repo uses **git**, hosted on GitHub at
`git@github.com:llamaha/chameleon.git`.

Daily workflow:

```bash
git status
git add <file>...
git commit -m "msg"
git push
```

(We previously experimented with Atomic VCS + a Patchwave remote. That's
parked for now — the on-disk state, the git history, and the GitHub
remote are the only sources of truth. The earlier Patchwave repo at
`adam/chameleon` is left in place to come back to later.)

## Build & Deploy

- **Build + install + launch on a connected Quest:** `./deploy.sh`
  - Use this to verify changes on the headset.
  - Release build, signed with cargo-apk's auto debug keystore (no keystore to
    manage). Flags: `--debug`, `--clean`, `--uninstall`, `--no-launch`.
- **Compile-check only (host):** `cargo check` — fast, catches most errors
  without an Android build.
- Prerequisites: rustup + `aarch64-linux-android` target, `cargo-apk`, Android
  SDK (platform-tools/`adb`, build-tools) and NDK. See README.md.

## Architecture: `vr/` (engine) vs `game/` (your game)

```
src/
├── main.rs / lib.rs     desktop / Android(#[bevy_main]) entry → vr::run_app()
├── vr/                  ── ENGINE — reusable, leave alone ──
│   ├── mod.rs           run_app(), VrAppPlugin, OpenXR extensions, camera tweaks,
│   │                    the WorldspaceUiPass schedule, and ALL external-crate
│   │                    imports (re-exported so submodules just `use super::*`)
│   ├── session.rs       SceneOrigin + PlayerReference (world-space origin helpers)
│   ├── components.rs     marker components + engine resources (Hand, action
│   │                    markers, laser/panel markers, VrConfig, MenuSpawned, …)
│   ├── constants.rs     UI texture/panel sizes, hand radius
│   ├── hands.rs         controller-tracked hand spheres + per-frame velocity
│   ├── input.rs         head tracker, controller actions, laser pointers, the
│   │                    laser→egui pointer bridge, spawn_vr_panel/rounded_rect_mesh
│   ├── setup.rs         scene/floor/light, mixed-reality toggle, worldspace-UI
│   │                    camera, recenter family
│   └── ui.rs            the "glass" egui theme + cleanup_state_ui
└── game/                ── EXAMPLE — replace with your game ──
    ├── mod.rs           GamePlugin (the entry vr::run_app adds)
    ├── state.rs         GameState (Menu/Sandbox) + persisted Settings
    ├── grab.rs          the grab-and-throw cube (avian3d dynamic body)
    └── menu.rs          the glass menu + panel/laser spawning
```

`run_app()` (in `vr/mod.rs`) builds the `App` with the XR plugins + physics, then
adds `VrAppPlugin` (engine) and `crate::game::GamePlugin` (your game). To start a
new game, replace `src/game/` and keep `GamePlugin` as the entry point.

### Module import convention

`vr/mod.rs` imports every external crate (bevy, bevy_egui, bevy_mod_openxr,
avian3d, …) and re-exports each submodule, so every `vr/*.rs` only needs
`use super::*`. `game/mod.rs` does the same (`use crate::vr::*` plus its own
crate imports). Add a new engine file: drop `src/vr/foo.rs`, add `mod foo; pub use
foo::*;` to `vr/mod.rs`, and `use super::*` inside it.

## Key patterns

- **Worldspace UI + laser interaction:** egui renders to a texture
  (`setup_worldspace_ui`) shown on a `WorldspaceUiPanel` quad (`spawn_vr_panel`,
  with rounded see-through corners via `rounded_rect_mesh`). `calculate_laser_panel_hits`
  ray-casts the controller onto the panel; `update_vr_egui_pointer` /
  `send_vr_egui_click` translate that into egui pointer + click events (trigger =
  click). Draw your UI in a system added to the **`WorldspaceUiPass`** schedule,
  querying `&mut EguiContext` `With<WorldspaceUiCamera>`. Call `apply_glass_style`
  + `paint_glass_backdrop` + `paint_glass_border` at the top for the themed look.
- **SceneOrigin / recenter:** all menus/content are placed relative to
  `SceneOrigin` (the player's pose, captured on first tracking and on recenter),
  so they appear in front of the player wherever they are. `local_to_world` (UI,
  with look-up/down height offset) vs `local_to_world_gameplay` (no offset).
  Recenter triggers: thumbstick-click (`check_recenter_button`) and Meta-button
  long-press (`handle_reference_space_change` → `apply_system_recenter`). Both
  reset `MenuSpawned` so the menu re-places at the new origin.
- **Mixed reality:** `VrConfig.mixed_reality` drives `apply_mixed_reality_mode`
  (passthrough + transparent clear + hidden floor, vs dark background + visible
  floor). Auto-detect downgrades a Quest 2 to virtual-environment on first frame.
- **State cleanup:** tag transient UI entities `StateUI`; `cleanup_state_ui` (on
  state exit) despawns them. The recenter systems exclude lasers from despawn.
- **Settings:** `game::Settings::load()/save()` — tiny JSON file (per-platform
  path). Mirror this for your own persisted state.
- **Events:** use `MessageWriter`/`MessageReader` + `#[derive(Message)]` (not
  Bevy's `Event*`).
- **Bevy quirks:** 16 system-params max (split if needed); pseudo-random via
  `SystemTime::now().subsec_nanos()` (no `rand`).

## CRITICAL: `bevy_render_patched`

`Cargo.toml` has `[patch.crates-io] bevy_render = { path = "bevy_render_patched" }`.
This is an **Adreno-740 (Quest 3) driver workaround**: a per-frame render pass on
the wgpu Surface swap-chain leaks one `sync_file` fence fd per frame, which
exhausts the fd limit and **crashes the app after ~7.5 minutes**. The patched
`bevy_render` skips Surface texture acquisition on Android (safe for XR-only apps;
the window Surface is never shown). **Do not remove it on Bevy 0.18.** When you
move to Bevy 0.19 you can drop it — the fix was upstreamed as
[bevyengine/bevy#23276](https://github.com/bevyengine/bevy/pull/23276).

## Why Bevy 0.18 (not 0.19)

The OpenXR stack is the `awtterpip/bevy_oxr` fork (`bevy_mod_openxr` /
`bevy_mod_xr` / `bevy_xr_utils`), which is still on Bevy 0.18 — no XR without it,
so we can't move to 0.19 yet. The git deps are **pinned to a known-good commit**
(the fork's `main` has already moved to a newer, API-incompatible tip needing
openxr 0.21). To upgrade later: bump the `rev`, reconcile the API, bump `openxr`,
and drop `bevy_render_patched`. Watch the fork for a 0.19 branch.

## Testing on Quest via ADB (without wearing the headset)

Cover the proximity sensor (cloth between the lenses) so the app keeps running.

```bash
adb shell am force-stop com.example.chameleon
adb logcat -c
adb shell am start -n com.example.chameleon/android.app.NativeActivity
# Rust logs come through the RustStdoutStderr tag (NOT a package-named tag):
adb logcat --pid=$(adb shell pidof com.example.chameleon)
```
