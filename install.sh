#!/bin/sh
# One-command Omarchy installer. All functions are parsed before main runs so
# subprocesses cannot consume an unfinished script from curl's stdin.
set -eu

say() { printf '\n%s\n' "[E16 -> Omarchy] $*"; }
fail() { printf '\n%s\n' "[E16 -> Omarchy] Error: $*" >&2; exit 1; }
cleanup() { if [ -n "${E16_WORK_DIR:-}" ]; then rm -rf -- "$E16_WORK_DIR"; fi; }

usage() {
    cat <<'HELP'
Usage: curl -fsSL https://raw.githubusercontent.com/schneipp/e16toomarchy/main/install.sh | sh

Run as your normal user in an active Omarchy terminal. Missing build
packages are installed through 'omarchy pkg add'; it asks for sudo only
when needed. Your active theme is left alone until you click Apply.
Run the same command again to update.

Options (append: | sh -s -- OPTION):
  --check        Check compatibility and dependencies without changing anything
  --no-packages  Do not install system packages; fail if dependencies are missing
  --no-enable    Install and build without enabling the topbar button
  --help         Show this help
HELP
}

check_headers() {
    python - <<'PY'
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

if sys.version_info < (3, 11):
    sys.exit('Python 3.11 or newer is required')
try:
    import ensurepip
    import venv
except ImportError:
    sys.exit('Python venv/ensurepip support is missing from this Python installation')

try:
    running = json.loads(subprocess.check_output(['hyprctl', '-j', 'version'], text=True))
    headers = subprocess.check_output(['pkg-config', '--modversion', 'hyprland'], text=True).strip()
    if running.get('version') != '0.56.2' or headers != running['version']:
        sys.exit('Matching Hyprland 0.56.2 development headers are required; the installer will not upgrade your compositor')
    includes = shlex.split(subprocess.check_output(['pkg-config', '--cflags-only-I', 'hyprland'], text=True))
    candidates = []
    for item in includes:
        if item.startswith('-I'):
            base = Path(item[2:])
            candidates.extend((base / 'src/version.h', base / 'hyprland/src/version.h', base / 'version.h'))
    for header in candidates:
        if header.is_file():
            match = re.search(r'#define\s+GIT_COMMIT_HASH\s+"([^"]+)"', header.read_text())
            if match:
                if match[1] != running.get('commit'):
                    sys.exit('Hyprland headers differ from the running compositor. Restart into the matching installed version and rerun')
                break
    else:
        sys.exit('Could not find the Hyprland development version header')
except (OSError, ValueError, subprocess.CalledProcessError) as error:
    sys.exit('Cannot verify Hyprland development headers: ' + str(error))
PY
}

main() {
    E16_CHECK=0
    E16_NO_PACKAGES=0
    E16_NO_ENABLE=0
    E16_WORK_DIR=''
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --check) E16_CHECK=1 ;;
            --no-packages) E16_NO_PACKAGES=1 ;;
            --no-enable) E16_NO_ENABLE=1 ;;
            --help|-h) usage; return 0 ;;
            *) fail "Unknown option: $1 (use --help)" ;;
        esac
        shift
    done

    [ "$(id -u)" -ne 0 ] || fail 'Run this as your normal Omarchy user, without sudo.'
    for E16_TOOL in omarchy omarchy-shell hyprctl pacman; do
        command -v "$E16_TOOL" >/dev/null 2>&1 || fail 'This installer requires an active Omarchy desktop session.'
    done
    E16_VERSION=$(hyprctl version 2>/dev/null) || fail 'Cannot reach Hyprland. Run this from your Omarchy desktop terminal.'
    case "$E16_VERSION" in
        'Hyprland 0.56.2 '*|'Hyprland 0.56.2') ;;
        *) fail 'The decoration plugin currently supports Hyprland 0.56.2. Your compositor will not be changed.' ;;
    esac

    # A fixed package allowlist; no package names come from downloaded data.
    set --
    for E16_PACKAGE in curl tar python gcc make pkgconf cairo pango json-c; do
        if ! pacman -Q -- "$E16_PACKAGE" >/dev/null 2>&1; then
            set -- "$@" "$E16_PACKAGE"
        fi
    done
    if [ "$#" -gt 0 ]; then
        if [ "$E16_CHECK" -eq 1 ] || [ "$E16_NO_PACKAGES" -eq 1 ]; then
            fail "Missing packages: $*. Rerun without --check/--no-packages to install them automatically."
        fi
        say "Installing missing packages: $*"
        # Omarchy manages its own elevation; never run the whole installer as root.
        omarchy pkg add "$@" </dev/null || fail 'Dependency installation failed.'
    fi
    check_headers || fail 'Compatibility check failed.'
    if [ "$E16_CHECK" -eq 1 ]; then
        say 'Checks passed. Omarchy, Python and matching Hyprland headers are ready.'
        return 0
    fi

    E16_WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/e16toomarchy-install.XXXXXXXX")
    trap cleanup 0
    trap 'exit 130' INT
    trap 'exit 143' HUP TERM
    mkdir "$E16_WORK_DIR/source"
    say 'Downloading E16 -> Omarchy...'
    curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --connect-timeout 15 --max-time 180 --retry 2 \
        'https://codeload.github.com/schneipp/e16toomarchy/tar.gz/refs/heads/main' \
        --output "$E16_WORK_DIR/source.tar.gz" || fail 'Source download failed.'
    tar -xzf "$E16_WORK_DIR/source.tar.gz" -C "$E16_WORK_DIR/source" \
        --strip-components=1 --no-same-owner --no-same-permissions || fail 'Source extraction failed.'
    [ -f "$E16_WORK_DIR/source/e16toomarchy/picker_install.py" ] || fail 'Downloaded source is incomplete.'

    say 'Preparing an isolated Python environment...'
    python -m venv "$E16_WORK_DIR/bootstrap" || fail 'Could not create the bootstrap environment.'
    "$E16_WORK_DIR/bootstrap/bin/python" -m pip install --quiet --disable-pip-version-check \
        "$E16_WORK_DIR/source" </dev/null || fail 'Python dependency installation failed.'

    say 'Installing the converter, building native decorations and setting up the picker...'
    if [ "$E16_NO_ENABLE" -eq 1 ]; then
        "$E16_WORK_DIR/bootstrap/bin/python" -m e16toomarchy.picker_install \
            --source "$E16_WORK_DIR/source" --build-native --no-enable </dev/null
        say 'Installed. Enable later with: omarchy plugin enable schneipp.e16-themes'
    else
        "$E16_WORK_DIR/bootstrap/bin/python" -m e16toomarchy.picker_install \
            --source "$E16_WORK_DIR/source" --build-native </dev/null
        say 'Ready! Open E16 Themes using the palette icon in your topbar.'
    fi
    say 'No theme was applied. Run this installer again whenever you want to update.'
}

main "$@"
