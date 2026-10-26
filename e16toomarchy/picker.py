"""Detached, bounded background jobs for the native Omarchy theme picker."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, unquote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

from .cli import CATALOG, Links, MAX_BYTES, convert, slug, unpack

VERSION = "0.3.0"
PLUGIN_ID = "schneipp.e16-themes"
MODES = {"auto", "dark", "light"}
ACTIONS = {"prepare", "apply", "restore"}
KINDS = {"full", "frames"}
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_THEMES = 3000


class BusyError(ValueError):
    pass


def paths():
    home = Path.home()
    return {
        "cache": Path(os.environ.get("XDG_CACHE_HOME", home / ".cache")) / "e16toomarchy",
        "state": Path(os.environ.get("XDG_STATE_HOME", home / ".local/state")) / "e16toomarchy",
        "runtime": Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "e16toomarchy",
        "config": Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")),
    }


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(data, out, ensure_ascii=True)
            out.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_text(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(contents)
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path, fallback=None, limit=2 * 1024 * 1024):
    try:
        if path.stat().st_size > limit:
            return fallback
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return fallback


def valid_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _+.,()'\-]{0,119}", name) or ".." in name:
        raise ValueError("Invalid catalog theme name")
    return name


def validate_request(action, name="", mode="auto", kind="full"):
    if action not in ACTIONS or mode not in MODES or kind not in KINDS:
        raise ValueError("Invalid picker request")
    if action != "restore":
        valid_name(name)


def theme_url(name):
    return CATALOG + quote(valid_name(name), safe="") + ".etheme"


def allowed_source(url):
    parsed = urlparse(url)
    decoded = unquote(parsed.path)
    return (parsed.scheme == "https" and parsed.netloc == "themes.effx.us"
            and decoded.startswith(("/packages/e16/", "/preview/e16/"))
            and ".." not in Path(decoded).parts and "\\" not in decoded
            and not parsed.query and not parsed.fragment)


class CatalogRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not allowed_source(newurl):
            raise ValueError("Theme server redirected outside its HTTPS catalog")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch(url, target, limit=MAX_BYTES, timeout=30):
    """The picker only fetches from its fixed HTTPS catalog, including redirects."""
    if not allowed_source(url):
        raise ValueError("Unsupported theme source")
    opener = build_opener(CatalogRedirects())
    with opener.open(Request(url, headers={"User-Agent": "e16toomarchy/" + VERSION}), timeout=timeout) as response:
        total = 0
        with target.open("wb") as out:
            while block := response.read(65536):
                total += len(block)
                if total > limit:
                    raise ValueError("Theme server response exceeds size limit")
                out.write(block)


def parse_catalog(html):
    parser = Links()
    parser.feed(html)
    names = set()
    for link in parser.links[:MAX_THEMES * 3]:
        absolute = urljoin(CATALOG, link)
        url = urlparse(absolute)
        if url.scheme != "https" or url.netloc != "themes.effx.us" or not url.path.startswith("/packages/e16/") or url.query or url.fragment:
            continue
        relative = url.path.removeprefix("/packages/e16/")
        if "/" in relative or not relative.endswith(".etheme"):
            continue
        try:
            names.add(valid_name(unquote(relative[:-7])))
        except ValueError:
            continue
        if len(names) >= MAX_THEMES:
            break
    if not names:
        raise ValueError("No E16 themes found in the catalog response")
    return sorted(names, key=str.casefold)


def catalog(refresh=False):
    cache = paths()["cache"] / "catalog.json"
    cached = read_json(cache, {})
    names = cached.get("themes", []) if isinstance(cached, dict) else []
    try:
        names = [valid_name(n) for n in names[:MAX_THEMES]]
    except (ValueError, TypeError):
        names = []
    fresh = bool(names) and time.time() - cached.get("fetched", 0) < 86400
    warning = ""
    if refresh or not fresh:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="catalog-", dir=cache.parent) as tmp:
                target = Path(tmp) / "index.html"
                fetch(CATALOG, target, MAX_CATALOG_BYTES)
                names = parse_catalog(target.read_text(errors="replace"))
            atomic_json(cache, {"fetched": time.time(), "themes": names})
        except (OSError, ValueError) as error:
            if not names:
                raise
            warning = "Offline catalog: " + str(error)[:300]
    return {"themes": names, "warning": warning}


def key_for(name, mode):
    valid_name(name)
    if mode not in MODES:
        raise ValueError("Invalid palette mode")
    identity = hashlib.sha256((VERSION + "\0" + name + "\0" + mode).encode()).hexdigest()[:10]
    return "e16-" + slug(name)[:70] + "-" + identity


def prepared_path(name, mode):
    return paths()["cache"] / "themes" / key_for(name, mode)


def managed_theme(path, name, mode):
    marker = read_json(path / "e16-picker.json", {})
    return marker == {"version": VERSION, "name": name, "mode": mode, "key": key_for(name, mode)}


def run_command(argv, timeout=60):
    # argv only: catalog names, titles and paths are never shell fragments.
    process = subprocess.run([str(arg) for arg in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             timeout=timeout, check=False)
    if process.returncode:
        raise RuntimeError(process.stdout[-1800:].strip() or f"Command failed: {argv[0]}")
    return process.stdout.strip()


def lua_string(value):
    # Lua strings do not use JSON's \uXXXX escapes. Escape controls byte-wise.
    return '"' + ''.join('\\' + c if c in {'"', '\\'} else f"\\{ord(c):03d}" if ord(c) < 32 or ord(c) == 127 else c for c in str(value)) + '"'


def hypr_eval(code):
    result = run_command(["hyprctl", "eval", code])
    if result != "ok":
        raise RuntimeError(result or "Hyprland did not accept the operation")


def integration_text(manifest, binary, kind):
    """Generate only our own trusted Lua; downloaded theme config is never sourced."""
    if kind not in KINDS:
        raise ValueError("Invalid theme scope")
    if kind == "frames":
        target = lua_string(manifest)
    else:
        target = '(os.getenv("XDG_STATE_HOME") or (os.getenv("HOME") .. "/.local/state")) .. "/omarchy/current/theme/e16/decoration.json"'
    return f'''-- Managed by e16toomarchy. Restoring the picker disables this integration.
local manifest = {target}
local check = io.open(manifest, "r")
if check then
  check:close()
  hl.plugin.load({lua_string(binary)})
  if hl.plugin.e16 then
    hl.config({{
      general = {{ border_size = 0 }},
      decoration = {{ rounding = 0 }},
      plugin = {{ e16 = {{ theme = manifest }} }},
    }})
    if hl.plugin.hyprbars then
      hl.config({{ plugin = {{ hyprbars = {{ enabled = false }} }} }})
    end
  end
end
'''


def backup(path):
    if not path.exists():
        return None
    dest = path.with_name(path.name + ".e16-backup-" + str(time.time_ns()))
    shutil.copy2(path, dest)
    return dest


def ensure_config_loader(config_home, state_home):
    main = config_home / "hypr/hyprland.lua"
    if not main.is_file():
        raise ValueError("This picker requires Omarchy's Lua Hyprland configuration")
    marker = "-- e16toomarchy picker integration"
    existing = main.read_text()
    if marker in existing:
        return
    backup(main)
    loader = state_home / "integration.lua"
    snippet = f'\n{marker}\ndo\n  local path = {lua_string(loader)}\n  local file = io.open(path, "r")\n  if file then file:close(); dofile(path) end\nend\n'
    atomic_text(main, existing.rstrip() + "\n" + snippet)


def reload_hyprland():
    result = run_command(["hyprctl", "reload"])
    if result and result != "ok":
        raise RuntimeError(result)
    # Plugin loading can request a second config pass. Wait for its registered API.
    time.sleep(.35)
    errors = run_command(["hyprctl", "configerrors"])
    if errors:
        raise RuntimeError("Hyprland configuration error: " + errors[:1600])


def ensure_native(progress):
    runtime = paths()["runtime"]
    version = json.loads(run_command(["hyprctl", "-j", "version"]))
    if version.get("version") != "0.56.2":
        raise ValueError("E16 decorations currently require Hyprland 0.56.2")
    headers = run_command(["pkg-config", "--modversion", "hyprland"])
    if headers != version["version"]:
        raise ValueError("Hyprland development headers do not match the running compositor")
    abi = version.get("abiHash", version["commit"])
    stamp = runtime / "build/picker-abi.json"
    binary = runtime / "build/e16-decorations.so"
    old = read_json(stamp, {})
    source_hash = hashlib.sha256()
    for filename in ("plugin/plugin.cpp", "plugin/frame.hpp", "Makefile"):
        source_hash.update((runtime / filename).read_bytes())
    signature = source_hash.hexdigest()
    if binary.is_file() and old.get("abi") == abi and old.get("version") == VERSION and old.get("sources") == signature:
        return binary
    progress("building")
    # Compile local project source only, never theme code.
    run_build(runtime)
    atomic_json(stamp, {"abi": abi, "version": VERSION, "sources": signature})
    return binary


def run_build(runtime):
    process = subprocess.run(["make", "-B", "plugin", "preview"], cwd=runtime, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, timeout=240)
    if process.returncode:
        raise RuntimeError(process.stdout[-1800:])


def prepare(name, mode, progress):
    dest = prepared_path(name, mode)
    if managed_theme(dest, name, mode) and (dest / "e16/frame-preview.png").is_file():
        return dest
    # Membership comes from our validated cached/fetched catalog, not user URLs.
    progress("catalog")
    if name not in catalog()["themes"]:
        raise ValueError("Theme is not in the E16 catalog")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=dest.parent) as tmp:
        work = Path(tmp)
        progress("downloading")
        fetch(theme_url(name), work / "theme.etheme")
        progress("converting")
        root = work / "source"
        root.mkdir()
        unpack(work / "theme.etheme", root)
        report = convert(root, work / "theme", name, theme_url(name), mode)
        if not report["decorations"]["available"]:
            raise ValueError("This theme uses an unsupported E16 border format")
        progress("previewing")
        preview = paths()["runtime"] / "build/e16-frame-preview"
        if not preview.is_file():
            run_build(paths()["runtime"])
        for state in ("active", "inactive"):
            out = work / "theme/e16" / ("frame-preview.png" if state == "active" else "frame-inactive.png")
            run_command([preview, work / "theme/e16/decoration.json", out, "640", "280", state, name], timeout=30)
        atomic_json(work / "theme/e16-picker.json", {"version": VERSION, "name": name, "mode": mode, "key": key_for(name, mode)})
        if dest.exists():
            if not managed_theme(dest, name, mode):
                raise ValueError("Cache path is not owned by the theme picker")
            shutil.rmtree(dest)
        os.replace(work / "theme", dest)
    return dest


def install_theme(source, name, mode):
    parent = paths()["config"] / "omarchy/themes"
    parent.mkdir(parents=True, exist_ok=True)
    dest = parent / key_for(name, mode)
    if dest.exists():
        if not managed_theme(dest, name, mode):
            raise ValueError("Refusing to overwrite a theme not installed by this picker")
        return dest
    with tempfile.TemporaryDirectory(prefix=".e16-install-", dir=parent) as tmp:
        stage = Path(tmp) / "theme"
        shutil.copytree(source, stage)
        os.replace(stage, dest)
    return dest


def current_theme():
    # Omarchy's CLI prints a title ("Tokyo Night"), while comparison and install paths use slugs.
    name = run_command(["omarchy", "theme", "current"]).strip()
    if not name or name.lower() == "unknown":
        raise ValueError("Cannot determine the current Omarchy theme")
    return slug(name)


def apply_theme(name, mode, kind, progress):
    source = prepare(name, mode, progress)
    binary = ensure_native(progress)
    progress("installing")
    dest = install_theme(source, name, mode)
    locations = paths()
    state = locations["state"]
    state.mkdir(parents=True, exist_ok=True)
    config = locations["config"]
    preferences = state / "active.json"
    previous = read_json(preferences, {})
    old_theme = current_theme()
    integration = state / "integration.lua"
    old_lua = integration.read_text() if integration.exists() else "-- E16 decorations disabled\n"
    ensure_config_loader(config, state)
    snapshot = previous.get("original_theme") if previous.get("enabled") else old_theme
    try:
        atomic_text(integration, integration_text(dest / "e16/decoration.json", binary, kind))
        if kind == "full":
            progress("applying")
            run_command(["omarchy", "theme", "set", dest.name], timeout=180)
        progress("decorating")
        reload_hyprland()
        # Configure via Lua after the plugin's load/reload passes have completed.
        hypr_eval('assert(hl.plugin.e16, "E16 decoration plugin could not load"); assert(hl.plugin.e16.set_theme(' + lua_string(dest / "e16/decoration.json") + '))')
        atomic_json(preferences, {"enabled": True, "name": name, "mode": mode, "kind": kind,
                                  "theme": dest.name, "original_theme": snapshot,
                                  "palette_theme": dest.name if kind == "full" else previous.get("palette_theme")})
    except Exception:
        atomic_text(integration, old_lua)
        if kind == "full" and old_theme:
            try:
                run_command(["omarchy", "theme", "set", old_theme], timeout=180)
            except Exception:
                pass
        try:
            reload_hyprland()
        except Exception:
            pass
        raise
    return source


def restore(progress):
    state = paths()["state"]
    preferences = state / "active.json"
    active = read_json(preferences, {})
    integration = state / "integration.lua"
    previous = integration.read_text() if integration.exists() else "-- E16 decorations disabled\n"
    progress("restoring")
    try:
        atomic_text(integration, "-- E16 decorations disabled by the picker\n")
        if active.get("enabled") and current_theme() == active.get("palette_theme", active.get("theme") if active.get("kind") == "full" else None) and active.get("original_theme"):
            run_command(["omarchy", "theme", "set", active["original_theme"]], timeout=180)
        reload_hyprland()
        atomic_json(preferences, {"enabled": False})
    except Exception:
        atomic_text(integration, previous)
        raise


def lock_fd():
    state = paths()["state"]
    state.mkdir(parents=True, exist_ok=True)
    fd = os.open(state / "job.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise BusyError("A theme operation is already running")
    return fd


def status():
    statefile = paths()["state"] / "status.json"
    current = read_json(statefile, {"stage": "idle", "busy": False})
    if current.get("busy"):
        try:
            fd = lock_fd()
        except BusyError:
            return current
        try:
            current.update(busy=False, stage="failed", error="The previous theme operation was interrupted")
            atomic_json(statefile, current)
        finally:
            os.close(fd)
    return current


def start(action, name="", mode="auto", kind="full"):
    validate_request(action, name, mode, kind)
    fd = lock_fd()
    job = uuid.uuid4().hex
    record = {"job": job, "action": action, "name": name, "mode": mode, "kind": kind,
              "stage": "queued", "busy": True, "updated": time.time()}
    atomic_json(paths()["state"] / "status.json", record)
    try:
        with (paths()["state"] / "worker.log").open("w") as log:
            entry = paths()["runtime"] / "backend.py"
            subprocess.Popen([sys.executable, str(entry), "run", job, str(fd), action, name, mode, kind],
                             pass_fds=(fd,), stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    except Exception as error:
        record.update(busy=False, stage="failed", error=str(error)[:1800])
        atomic_json(paths()["state"] / "status.json", record)
        raise
    finally:
        os.close(fd)  # The inherited file description keeps the worker's flock held.
    return {"started": True, "job": job}


def run_job(job, fd, action, name, mode, kind):
    validate_request(action, name, mode, kind)
    record = {"job": job, "action": action, "name": name, "mode": mode, "kind": kind, "busy": True}
    state = paths()["state"] / "status.json"
    def progress(stage):
        record.update(stage=stage, updated=time.time())
        atomic_json(state, record)
    try:
        progress("starting")
        if action == "restore":
            restore(progress)
            record.update(stage="restored", busy=False)
        else:
            source = prepare(name, mode, progress) if action == "prepare" else apply_theme(name, mode, kind, progress)
            report = read_json(source / "conversion.json", {})
            record.update(stage="ready" if action == "prepare" else "applied", busy=False, preview=str(source / "e16/frame-preview.png"),
                          inactive=str(source / "e16/frame-inactive.png"), warnings=report.get("warnings", [])[:20])
    except Exception as error:
        record.update(stage="failed", busy=False, error=str(error)[-1800:])
    finally:
        record["updated"] = time.time()
        atomic_json(state, record)
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Omarchy E16 picker background worker")
    sub = parser.add_subparsers(dest="command", required=True)
    cat = sub.add_parser("catalog")
    cat.add_argument("--refresh", action="store_true")
    sub.add_parser("status")
    thumb = sub.add_parser("thumbnail")
    thumb.add_argument("name")
    batch = sub.add_parser("thumbnails")
    batch.add_argument("names", nargs="+")
    for command in ("start", "run"):
        p = sub.add_parser(command)
        if command == "run":
            p.add_argument("job")
            p.add_argument("fd", type=int)
        p.add_argument("action", choices=sorted(ACTIONS))
        p.add_argument("name", nargs="?", default="")
        p.add_argument("mode", nargs="?", default="auto", choices=sorted(MODES))
        p.add_argument("kind", nargs="?", default="full", choices=sorted(KINDS))
    args = parser.parse_args(argv)
    try:
        if args.command == "catalog":
            result = catalog(args.refresh)
        elif args.command == "status":
            result = status()
        elif args.command == "thumbnail":
            from .gallery import thumbnail
            result = thumbnail(args.name)
        elif args.command == "thumbnails":
            from .gallery import thumbnails
            thumbnails(args.names)
            return
        elif args.command == "start":
            result = start(args.action, args.name, args.mode, args.kind)
        else:
            run_job(args.job, args.fd, args.action, args.name, args.mode, args.kind)
            return
        print(json.dumps(result, ensure_ascii=True))
    except Exception as error:
        print(json.dumps({"error": str(error)[-1800:]}, ensure_ascii=True))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
