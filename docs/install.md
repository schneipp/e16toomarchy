# One-command installation

From a terminal in your Omarchy desktop:

```sh
curl -fsSL https://raw.githubusercontent.com/schneipp/e16toomarchy/main/install.sh | sh
```

Run it as your normal user. The same command installs or updates E16 Themes. Once it finishes, open the palette icon in your topbar and select a theme.

## What it handles

1. Checks for an active Omarchy session and supported Hyprland version.
2. Installs only missing packages through `omarchy pkg add`: curl, tar, Python, GCC, make, pkgconf, Cairo, Pango and json-c. Omarchy requests sudo if needed; the installer itself stays unprivileged.
3. Checks Python 3.11+, venv support, Hyprland 0.56.2 headers and the exact compositor commit. It does not replace or upgrade Hyprland.
4. Downloads the public source archive over HTTPS into a temporary directory and prepares a disposable bootstrap environment.
5. Installs an independent runtime, renderer, native decoration plugin, converter command and Omarchy bar widget in user-owned paths.
6. Enables the widget after compilation succeeds, preserving an existing bar placement. It removes its temporary download and bootstrap environment on exit.

It does not apply a theme, wallpaper or decoration to the desktop during installation. Choosing **Apply** in the picker activates your selection. The existing configuration backup and Restore behavior are described in [picker notes](picker.md).

The command-line converter is available as `~/.local/bin/e16toomarchy`, using the installed runtime's Python environment. Omarchy normally includes that directory in PATH. An existing unrelated command with the same name is preserved. Neither the bootstrap directory nor a source checkout is needed after installation.

## Options

Check compatibility and dependencies without downloading source or changing anything:

```sh
curl -fsSL https://raw.githubusercontent.com/schneipp/e16toomarchy/main/install.sh | sh -s -- --check
```

Install without adding any system packages (reports missing dependencies instead):

```sh
curl -fsSL https://raw.githubusercontent.com/schneipp/e16toomarchy/main/install.sh | sh -s -- --no-packages
```

Install and compile without enabling the bar button:

```sh
curl -fsSL https://raw.githubusercontent.com/schneipp/e16toomarchy/main/install.sh | sh -s -- --no-enable
```

Enable it later with `omarchy plugin enable schneipp.e16-themes`. Use `--help` for a summary. Options can be combined.

## Compatibility and validation

The native adapter currently supports Hyprland **0.56.2**. If the running compositor and installed headers differ, restart into their matching version before rerunning. This installer intentionally leaves compositor upgrades to Omarchy.

The installer is POSIX `sh` and works when piped through stdin. Tests exercise first installation, updates to the independent command/runtime, missing-package installation, read-only preflight, opting out of package installation or bar enablement, version/header mismatches, root rejection, argument quoting and cleanup after failed downloads/builds. The native build cache also checks its source content, so an updated implementation is rebuilt even when the compositor ABI is unchanged.
