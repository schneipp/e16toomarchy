# E16 → Omarchy

Bring classic Enlightenment E16 themes to Omarchy, including their **original bitmap window decorations**.

The Python converter exports a color palette, wallpapers, and an inert description of E16's frame parts. The C++ Hyprland plugin renders those original assets around real windows: textured titlebars, custom buttons, active/inactive/hover/pressed states, transparent shapes, and resize borders. An offline preview tool uses the same rendering code as the plugin.

## Install the converter

Requires Python 3.11+ and Pillow.

```sh
python -m venv .venv
.venv/bin/python -m pip install -e .
# Or: uv venv && uv pip install -e .
```

```sh
.venv/bin/e16toomarchy list --filter metal
.venv/bin/e16toomarchy convert BrushedMetal
.venv/bin/e16toomarchy convert Aqua --mode dark
```

Sources may be a [catalog theme name](https://themes.effx.us/packages/e16/), HTTP(S) URL, `.etheme`/tar archive, or extracted theme directory:

```sh
.venv/bin/e16toomarchy convert ~/Downloads/AlienConsole.etheme --name alien-console
.venv/bin/e16toomarchy convert /path/to/extracted/theme --border DEFAULT -o ./converted
```

Output defaults to `converted/<theme-name>/`. Existing output is never overwritten; choose a new name or parent directory to regenerate. `--mode auto|dark|light` controls the app palette. `--no-wallpapers` creates a solid background. `--no-decorations` exports only colors and wallpapers. Local conversion works offline.

## Build the decorations

The plugin targets **Hyprland 0.56.2** and its Lua configuration/API. It must be built against the **exact ABI of the running compositor**. Rebuild after updating Hyprland. Other versions may need source changes; older Hyprland APIs are not supported by this plugin.

On Arch/Omarchy, the build dependencies are a C++23 compiler, make, pkg-config, Hyprland development headers, Cairo, Pango, and json-c. Header packaging depends on how Hyprland was installed; `pkg-config --modversion hyprland` should match `Hyprland --version`.

```sh
make                 # Plugin + offline preview
# make preview       # Only Cairo/Pango/json-c headers needed; no Hyprland
```

Inspect the actual recreated frame before activating it:

```sh
build/e16-frame-preview converted/brushedmetal/e16/decoration.json /tmp/frame.png
build/e16-frame-preview converted/brushedmetal/e16/decoration.json /tmp/inactive.png 720 380 inactive
build/e16-frame-preview converted/brushedmetal/e16/decoration.json /tmp/pressed.png 720 380 pressed:2 "A real E16 frame"
```

The numeric state index is the part's index in `decoration.json`.

## Activate on Omarchy

Copy the generated theme into your user themes folder, checking that the destination does not already exist:

```sh
mkdir -p ~/.config/omarchy/themes
cp -r converted/brushedmetal ~/.config/omarchy/themes/
omarchy theme set brushedmetal
```

Load the locally built plugin:

```sh
hyprctl plugin load "$PWD/build/e16-decorations.so"
```

By default it follows Omarchy's current theme in `$XDG_STATE_HOME/omarchy/current/theme` (normally `~/.local/state/omarchy/current/theme`), with support for the older `~/.config/omarchy/current/theme` location. Frame changes are checked every half second. Selecting a theme without E16 decoration data removes the E16 frames. Invalid replacements retain the last working frame.

For a temporary frame selection without installing a theme:

```sh
hyprctl eval 'hl.plugin.e16.set_theme("/absolute/path/to/converted/brushedmetal/e16/decoration.json")'
hyprctl eval 'hl.plugin.e16.reload()'
hyprctl eval 'hl.plugin.e16.set_theme("auto")'
```

To load it at login, add this to a user Hyprland Lua file loaded by your configuration, using the absolute path to your build:

```lua
hl.plugin.load("/absolute/path/to/e16toomarchy/build/e16-decorations.so")
hl.config({
  general = { border_size = 0 },
  decoration = { rounding = 0 },
})
```

Zero native border size/rounding lets the original E16 frame define the outline. If another titlebar plugin such as Omarchy's hyprbars is enabled, disable its titlebars while using this plugin to avoid duplicate frames. Hyprland group bars can also compete for decoration space. Restart/rebuild requirements are documented in [the plugin notes](docs/decorations.md).

After editing Hyprland configuration, run `hyprctl reload` and `hyprctl configerrors`.

Unload at any time:

```sh
hyprctl plugin unload "$PWD/build/e16-decorations.so"
```

Neither conversion nor compilation edits your desktop configuration or activates a theme.

## Buttons and geometry

- Original E16 1024-unit anchors, inclusive bottom-right coordinates, and references to other frame parts.
- Nine-slice bitmap scaling, tiled fills, source alpha and magenta shape masks.
- Focused/unfocused, hover/pressed, and available sticky bitmap states.
- Original text colors, justification, shadow/outline effects, live window titles, and ellipsis.
- Close, maximize/restore, titlebar dragging, directional/corner resizing, and pinning of floating windows.
- Minimize into `special:e16-minimized`. Show/hide those windows with `hyprctl eval 'hl.plugin.e16.show_minimized()'`; your normal workspace bindings can move them back.

Custom E16 shell actions are never executed. Menu/shade actions without an implemented Hyprland equivalent retain their artwork and emit conversion warnings. The plugin decorates regular mapped windows, respects `decorate` and X11 border hints, and hides its frame in true fullscreen.

The frame shape is transparent and its bitmap hit testing respects transparent pixels. This does **not** reshape an application's own rectangular client surface. Text currently uses a Pango font fallback (`Sans Bold 10`); legacy X11 fonts and bundled fonts are not installed. Directional tile rules fall back to two-axis tiling with a warning. Complex C preprocessor expressions and macros, per-client E16 border matching, native E16 menus/widgets, sound, shading, wallpaper tiling, and per-desktop wallpaper assignments are not recreated.

## Generated files

| File | Purpose |
| --- | --- |
| `colors.toml` | Named Omarchy colors plus numbered ANSI aliases |
| `backgrounds/` | Source wallpapers converted to PNG, or solid fallback |
| `preview.png` | Palette swatches |
| `e16/decoration.json` | Frame geometry, states, text and allowlisted actions |
| `e16/assets/` | Original decoration bitmaps converted to PNG |
| `conversion.json` | Source, conversion warnings and limitations |

Source artwork retains its original license. This repository contains converter/renderer code and synthetic test artwork generators; it does not bundle third-party theme archives. Check a theme's license before redistributing its generated artwork.

Theme configuration is read as data. No preprocessor, scripts, or source commands are run. Downloads/extraction are limited to 256 MiB and 20,000 files. Traversal, external links, hard links, and devices are rejected; internal archive symlink aliases are skipped. Extracted input directories must not contain symlinks. The renderer rejects asset traversal, cyclic anchors, invalid versions and oversized geometry.

## Tests

```sh
make test
# Or Python converter/export tests without building the native renderer:
.venv/bin/python -m unittest discover -s tests -v
```

Ten tests cover archive safety, palette readability, wallpaper export, original image states, shape masks, shared renderer pixels, anchor geometry, hit testing, malformed manifests and action filtering. GitHub Actions builds and tests the converter and offline renderer; plugin compilation requires matching local Hyprland headers.

Validated with real BrushedMetal, AlienConsole, and Aqua archives. Live tests in an isolated Hyprland 0.56.2 session verified frame rendering, maximize, titlebar dragging, border resize, close/cancelled close, minimize, automatic theme replacement, invalid-theme recovery and clean unload. See [development notes](docs/decorations.md) for architecture and compatibility.

## License

MIT for this project's code. Theme artwork is separately licensed by its authors.
