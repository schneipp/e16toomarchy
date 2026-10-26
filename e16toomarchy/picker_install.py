"""Install a user-owned Omarchy shell plugin and its independent runtime."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import venv

from .picker import PLUGIN_ID, VERSION, backup, lock_fd, paths, run_command


def install(source=None, enable=True):
    source = Path(source or Path(__file__).resolve().parents[1]).resolve()
    for required in ("omarchy-plugin/manifest.json", "plugin/frame.hpp", "plugin/plugin.cpp", "Makefile"):
        if not (source / required).is_file():
            raise ValueError("Install the picker from the e16toomarchy source checkout")
    run_command(["omarchy", "plugin", "validate", source / "omarchy-plugin"])
    fd = lock_fd()
    try:
        locations = paths()
        runtime = locations["runtime"]
        runtime.mkdir(parents=True, exist_ok=True)
        python = runtime / "venv/bin/python"
        if not python.is_file():
            print("Creating the picker's Python environment…", flush=True)
            venv.EnvBuilder(with_pip=True).create(runtime / "venv")
        dependency = subprocess.run([str(python), "-c", "from PIL import Image"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if dependency.returncode:
            print("Installing Pillow in the picker environment…", flush=True)
            run_command([python, "-m", "pip", "install", "--disable-pip-version-check", "Pillow>=10"], timeout=180)
        for folder in ("e16toomarchy", "plugin"):
            stage = runtime / ("." + folder + "-next")
            if stage.exists():
                shutil.rmtree(stage)
            shutil.copytree(source / folder, stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            old = runtime / folder
            if old.exists():
                shutil.rmtree(old)
            stage.rename(old)
        shutil.copy2(source / "Makefile", runtime / "Makefile")
        shutil.copy2(source / "LICENSE", runtime / "LICENSE")
        (runtime / "backend.py").write_text('''# Installed e16toomarchy runtime entry point.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from e16toomarchy.picker import main
main()
''')
        print("Building the frame preview renderer…", flush=True)
        process = subprocess.run(["make", "preview"], cwd=runtime, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        if process.returncode:
            raise RuntimeError(process.stdout[-1800:])
        plugins = locations["config"] / "omarchy/plugins"
        plugins.mkdir(parents=True, exist_ok=True)
        dest = plugins / PLUGIN_ID
        if dest.exists():
            manifest = json.loads((dest / "manifest.json").read_text())
            if manifest.get("id") != PLUGIN_ID:
                raise ValueError("Existing plugin directory is not owned by E16 Themes")
        # Code is replaced as a directory; no background job writes under the shell's watched plugin tree.
        with tempfile.TemporaryDirectory(prefix=".e16-stage-", dir=plugins) as tmp:
            stage = Path(tmp) / PLUGIN_ID
            shutil.copytree(source / "omarchy-plugin", stage)
            old = dest.with_name(".e16-themes-previous")
            if old.exists():
                shutil.rmtree(old)
            if dest.exists():
                dest.rename(old)
            try:
                stage.rename(dest)
            except BaseException:
                if dest.exists():
                    shutil.rmtree(dest)
                if old.exists():
                    old.rename(dest)
                raise
            if old.exists():
                shutil.rmtree(old)
        if enable:
            # Hot reload briefly removes IPC handlers. Read/retry until the registry is ready.
            deadline = time.monotonic() + 12
            rescanned = False
            known = None
            while time.monotonic() < deadline:
                try:
                    if not rescanned:
                        run_command(["omarchy-shell", "shell", "rescanPlugins"], timeout=3)
                        rescanned = True
                    listing = json.loads(run_command(["omarchy", "plugin", "list", "--json"], timeout=3))
                    known = next((plugin for plugin in listing if plugin.get("id") == PLUGIN_ID), None)
                    if known:
                        break
                except (RuntimeError, ValueError, subprocess.TimeoutExpired):
                    pass
                time.sleep(.15)
            if not known:
                raise RuntimeError("The shell did not discover E16 Themes after rescanning")
            if not known.get("enabled"):
                shell_config = locations["config"] / "omarchy/shell.json"
                backup(shell_config)
                run_command(["omarchy", "plugin", "enable", PLUGIN_ID])
        print(f"Installed E16 Themes {VERSION}: {dest}", flush=True)
        return dest
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install the native Omarchy E16 topbar picker")
    parser.add_argument("--source", type=Path, help="Source checkout (defaults to this checkout)")
    parser.add_argument("--no-enable", action="store_true", help="Install without changing the bar layout")
    args = parser.parse_args(argv)
    try:
        install(args.source, not args.no_enable)
    except Exception as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
