# Native Omarchy theme picker

The picker is a user-owned Quickshell bar-widget plugin with a `qs.Ui.KeyboardPanel` popup. It uses Omarchy's shared buttons, dropdowns, text field, fonts, colors, panel motion, focus handling and translation catalogs. It is not a separate application window.

## Components

- `omarchy-plugin/Picker.qml`: topbar palette icon, catalog/search, frame preview, focused/unfocused state, palette/scope controls and job status.
- `omarchy-plugin/Model.js`: bounded JSON parsing, name filtering and local-only preview URLs. Remote names/errors are displayed exclusively through `Text.PlainText`; shared tooltips contain only plugin-owned translated strings.
- `e16toomarchy/picker.py`: catalog/cache, detached jobs, conversion, native compilation, installation, activation and restoration.
- `e16toomarchy/picker_install.py`: independent runtime, shell plugin installation and bar enablement through Omarchy's commands.

## User flow

The catalog is fetched when the popup first opens and cached for 24 hours. Refresh is explicit. Selecting a theme starts one asynchronous download/conversion, followed by actual frame rendering with the same Cairo renderer used by the native decoration plugin. Focused/unfocused views are cached locally. The Apply button is enabled once the selected theme/mode has a valid preview.

The worker holds a per-user `flock`. The launcher passes its locked file description to a detached child before returning, preventing a second window, another monitor, or another click from starting a conflicting activation. Atomic JSON state updates keep the popup informed while it is open. Closing the popup or unloading its QML does not kill the child. Opening the picker checks for interrupted workers and recovers stale busy state.

A full application installs a managed theme with a collision-resistant identity and delegates palette/wallpaper activation to `omarchy theme set`. Frames-only application leaves those unchanged. Both use a generated, trusted Lua integration to request the local decoration plugin and avoid duplicate hyprbars. The downloaded E16 configuration is never loaded as Lua or executed.

The picker's first configuration edit is backed up and a loader is appended once. Subsequent applications atomically update only the picker's own integration. Hyprland is reloaded and its configuration errors checked. A failed application restores the previous integration and, if changed, the previous theme. Restore retains a palette independently selected through another tool.

## Paths

The normal XDG locations are used for picker files:

| Path | Contents |
| --- | --- |
| `~/.config/omarchy/plugins/schneipp.e16-themes/` | Native bar-widget code and translations |
| `~/.local/share/e16toomarchy/` | Independent Python environment, converter/renderer source, local build, backend entry point |
| `~/.cache/e16toomarchy/catalog.json` | Bounded catalog cache |
| `~/.cache/e16toomarchy/themes/` | Converted themes and frame previews |
| `~/.local/state/e16toomarchy/status.json` | Atomic progress/result state |
| `~/.local/state/e16toomarchy/worker.log` | Current worker's diagnostic output |
| `~/.local/state/e16toomarchy/active.json` | Managed selection and previous appearance |
| `~/.local/state/e16toomarchy/integration.lua` | Picker-generated Hyprland configuration |
| `~/.config/omarchy/themes/e16-*/` | Installed managed themes |

Omarchy's theme commands use its standard HOME-based theme paths; use the standard Omarchy layout. The picker does not alter packaged files under `/usr/share/omarchy`.

`hyprland.lua.e16-backup-*` and `shell.json.e16-backup-*` preserve the configuration before the picker's respective edits. The plugin can be updated by rerunning `e16toomarchy install-picker` from an updated source checkout. Its installed runtime does not depend on keeping that checkout or its virtual environment.

## Recovery

A failure is displayed in the popup; invalid or unsupported theme formats do not become active. Cached unsupported image warnings remain in `conversion.json`. A build failure names the missing tool/header or incompatible compositor. The current native adapter supports Hyprland 0.56.2 only; system dependencies must already be installed.

Use **Restore previous look** to disable the generated integration. The harmless loader remains in the main config. Then disable the bar widget if desired:

```sh
omarchy plugin disable schneipp.e16-themes
```

For a stale job after an interrupted process, reopen the popup. Its status check verifies the lock rather than trusting a saved PID. See `~/.local/state/e16toomarchy/status.json` for the result and `worker.log` for diagnostics.

## Validation

Python tests cover catalog origin/path validation, markup-shaped names, redirect restrictions, collision-resistant cache identities, Lua string escaping, concurrent-job exclusion, stale-state recovery, backed-up/idempotent integration, activation rollback, frames-only behavior, display-name normalization, restoration across scope changes and plain-text UI sinks. JavaScript tests cover bounds, markup-shaped errors and rejection of remote/traversing image URLs.

The native popup was loaded and exercised in a separate nested Hyprland 0.56.2 session. Actual catalog downloads, previews, full application, frames-only application, continuation after closing, configuration validation and Restore were checked in an isolated HOME. Theme staging used Omarchy's headless test mode to avoid retinting applications outside that session.
