# Decoration renderer

The plugin is an E16 border interpreter, rather than a recoloring of Hyprland's built-in border. The converter stores literal E16 geometry and converts the referenced image states to PNG. Both the plugin and the offline preview load that same manifest through `plugin/frame.hpp`.

## Data flow

1. `e16toomarchy/decorations.py` reads nested `__BORDER` / `__BORDER_PART`, image and text classes, including literal local includes. The source's order and zero-based anchor references are retained.
2. `e16/decoration.json` declares frame extents, image states, edge margins, text styles, geometry, and a restricted action vocabulary. Its `available_borders` field lists alternate border styles available for `--border`.
3. `e16::Frame` resolves E16's 1024-unit anchors recursively, checks cycles/indices, and paints the original images using Cairo. Edge margins preserve corners while the center stretches or tiles. Pango paints window titles.
4. Each Hyprland window gets a native `IHyprWindowDecoration`. The positioner reserves all four sides using the 0.56.2 four-edge sticky policy. That policy also provides the native input box; absolute positioning in this version does not provide an input box.
5. The plugin paints four narrow frame strips rather than allocating a texture covering the whole client. GPU textures are cached until size, monitor scale, title, focus, pin state, hover/press, or theme changes. Hyprland's texture pass applies alpha and monitor transforms. Normal workspace render offsets and floating offsets are included.
6. Native decoration input starts Hyprland's own move/resize controller. Button actions activate on release inside the same part. A global release listener handles releasing outside the frame, and the shared renderer samples alpha when hit testing.

## Runtime controls (Hyprland Lua)

```sh
hyprctl eval 'hl.plugin.e16.set_theme("/absolute/theme/e16/decoration.json")'
hyprctl eval 'hl.plugin.e16.set_theme("auto")'
hyprctl eval 'hl.plugin.e16.reload()'
hyprctl eval 'hl.plugin.e16.show_minimized()'
hyprctl repl 'hl.plugin.e16.status()'
```

`set_theme`, `reload`, and `show_minimized` return success and an error string where applicable. `status` returns the loaded border name, top extent, and per-window drawing/input geometry and hover/pressed state. To read a specific value: `hyprctl repl 'hl.plugin.e16.status().top'`.

A persistent explicit manifest can be selected through the plugin option:

```lua
hl.config({ plugin = { e16 = { theme = "/absolute/theme/e16/decoration.json" } } })
```

An empty option follows Omarchy. A runtime `set_theme` selection takes precedence until `set_theme("auto")` or plugin unload.

## Compatibility

The native adapter is built and exercised against Hyprland 0.56.2, commit `efb50993780079460b0cbed1363e2166a2de1d9f`. It uses that version's typed EventBus, renderer texture API, Lua plugin functions, fullscreen controller, window state and drag controller. The ABI check rejects a plugin compiled against mismatching compositor headers. It does not promise compatibility across compositor versions.

After a Hyprland upgrade, rebuild the plugin with matching headers. A running old compositor still needs its old build: restart into the updated compositor before loading a build made with the new headers. The Makefile replaces the output library atomically, so rebuilding does not truncate an already mapped shared object.

Reloading configuration may automatically unload a plugin that is not also requested by `hl.plugin.load(...)` in the configuration. For interactive trials, load with `hyprctl plugin load` and avoid a config reload until the trial is done.

Disabling native border size and rounding is recommended to preserve the E16 outline. Other titlebar/group decorations consume their own space and can overlap this frame. Client-side application titlebars are owned by the application and may also need to be disabled in that application's settings.

## Validation performed

- Native plugin built with local Hyprland 0.56.2 headers, exported entry points checked, and loaded/unloaded in an isolated nested compositor.
- BrushedMetal: 19 parts and 58 distinct bitmap assets, plus 11 wallpapers. Live maximize, titlebar move, side resize, close/cancelled close, scratch-workspace minimize/show, automatic theme replacement, invalid-theme recovery and clean unload checks passed.
- AlienConsole and Aqua: converter and shared offline renderer validated with their real catalog archives.
- Synthetic pixel tests compare active/inactive/hover/pressed bitmap states and preserved corners. C++ tests cover forward anchors, cyclic anchors, transparent hit areas, and client-surface exclusion.
- Invalid manifest paths and themes retain the last valid frame. Switching to a normal Omarchy theme hides the E16 frame.

Multi-monitor transforms, fractional scaling, touch/tablet decoration input, grouped windows, unusual E16 preprocessing/macros, font-metric-dependent border layouts, and long-running GPU resource behavior still need broader desktop testing. The offline renderer does not validate compositor behavior for those cases.

## References

- [Hyprland plugin development](https://wiki.hypr.land/Plugins/Development/Getting-Started/)
- [Hyprland 0.56.2 decoration positioner](https://github.com/hyprwm/Hyprland/blob/v0.56.2/src/render/decorations/DecorationPositioner.cpp)
- [Hyprland 0.56.2 group bar implementation](https://github.com/hyprwm/Hyprland/blob/v0.56.2/src/render/decorations/CHyprGroupBarDecoration.cpp)
- [Archived E16 1.0.0 source](https://archive.debian.org/debian/pool/main/e/e16/e16_1.0.0.orig.tar.gz), `src/borders.c` (`BorderWinpartCalc`), used to verify anchor and inclusive-coordinate semantics.
- [Theme catalog](https://themes.effx.us/packages/e16/)
