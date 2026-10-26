"""Read E16 data without executing its preprocessor, scripts, or configuration."""
from __future__ import annotations

import argparse
from collections import Counter
import colorsys
from html.parser import HTMLParser
import json
import posixpath
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import tempfile
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw

CATALOG = "https://themes.effx.us/packages/e16/"
MAX_BYTES = 256 * 1024 * 1024
MAX_FILES = 20000
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".xpm", ".tif", ".tiff", ".webp"}


def slug(text):
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not value:
        raise ValueError("Theme name must contain letters or numbers")
    return value


def download(url, target):
    if urlparse(url).scheme not in {"https", "http"}:
        raise ValueError("Only HTTP(S) downloads are supported")
    with urlopen(Request(url, headers={"User-Agent": "e16toomarchy/0.1"}), timeout=30) as response:
        with target.open("wb") as out:
            total = 0
            while block := response.read(65536):
                total += len(block)
                if total > MAX_BYTES:
                    raise ValueError("Download exceeds 256 MiB limit")
                out.write(block)


def unpack(archive, target):
    """Reject traversal, external links, devices and oversized archives before writing."""
    with tarfile.open(archive, "r:*") as tar:
        members = tar.getmembers()
        if len(members) > MAX_FILES or sum(m.size for m in members) > MAX_BYTES:
            raise ValueError("Archive exceeds extraction limits")
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or "\\" in member.name:
                raise ValueError(f"Unsafe archive path: {member.name}")
            if member.issym():
                resolved = posixpath.normpath(posixpath.join(str(path.parent), member.linkname))
                if member.linkname.startswith("/") or resolved == ".." or resolved.startswith("../"):
                    raise ValueError(f"Unsafe archive link: {member.name}")
                continue  # E16 bundles often contain font aliases; never create links.
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"Unsupported archive entry: {member.name}")
        for member in members:
            if member.issym():
                continue
            dest = target.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, dest.open("wb") as out:
                    shutil.copyfileobj(source, out)


def files_under(root):
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are unsupported: {path}")
        if path.is_file():
            files.append(path)
    if len(files) > MAX_FILES or sum(p.stat().st_size for p in files) > MAX_BYTES:
        raise ValueError("Theme exceeds input limits")
    return files


def rgbhex(rgb):
    return "#" + "".join(f"{round(c):02x}" for c in rgb)


def mix(a, b, weight):
    return tuple(round(x * (1 - weight) + y * weight) for x, y in zip(a, b))


def luminance(rgb):
    vals = [c / 255 for c in rgb]
    vals = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in vals]
    return sum(c * w for c, w in zip(vals, (.2126, .7152, .0722)))


def contrast(a, b):
    x, y = sorted((luminance(a), luminance(b)))
    return (y + .05) / (x + .05)


def readable(color, bg, minimum=4.5):
    target = max(((0, 0, 0), (255, 255, 255)), key=lambda c: contrast(c, bg))
    for step in range(101):
        candidate = mix(color, target, step / 100)
        if contrast(candidate, bg) >= minimum:
            return candidate
    return target


def pixel_data(image):
    method = getattr(image, "get_flattened_data", None)
    return method() if method else image.getdata()


def image_colors(path):
    with Image.open(path) as image:
        image.thumbnail((96, 96))
        pixels = [p[:3] for p in pixel_data(image.convert("RGBA"))
                  if p[3] >= 128 and p[:3] != (255, 0, 255)]
    if not pixels:
        return Counter()
    # Equal-sized samples keep large wallpapers from drowning out UI artwork.
    strip = Image.new("RGB", (len(pixels), 1))
    strip.putdata(pixels)
    quantized = strip.quantize(colors=12).convert("RGB")
    return Counter(pixel_data(quantized))


def analyze(root):
    files = files_under(root)
    configs = [(p, re.sub(r"/\*.*?\*/", "", p.read_text(errors="replace"), flags=re.S))
               for p in files if p.suffix.lower() in {".cfg", ".theme"}]
    if not any("__E_CFG_VERSION" in text or "__ICLASS" in text or "BEGIN_BACKGROUND" in text
               for _, text in configs):
        raise ValueError("No recognizable E16 theme configuration found")
    backgrounds = set()
    referenced = set()
    foregrounds = Counter()
    # References are matched as data; includes and macros are never executed.
    by_suffix = {}
    for path in files:
        parts = path.relative_to(root).parts
        for i in range(len(parts)):
            by_suffix.setdefault("/".join(parts[i:]), []).append(path)
    dialog_images = set()
    for config, text in configs:
        for value in re.findall(r'["\']([^"\'\n]+)["\']', text):
            matches = by_suffix.get(value.removeprefix("./"), [])
            if not matches:
                continue
            for path in matches:
                if path.suffix.lower() not in IMAGE_EXT:
                    continue
                referenced.add(path)
                if "desktop" in config.name.lower() or "background" in config.name.lower():
                    backgrounds.add(path)
        for block in re.findall(r"__ICLASS\s+__BGN(.*?)__END", text, re.S):
            if re.search(r"__NAME\s+(?:DIALOG|MENU)(?:_BACKGROUND|_BG)?\s", block):
                normal = re.search(r'__NORMAL\s+"([^"]+)"', block)
                if normal:
                    dialog_images.update(by_suffix.get(normal[1], []))
        for values in re.findall(r"__(?:FORGROUND|FOREGROUND)_COLOR\s+(\d+)\s+(\d+)\s+(\d+)", text):
            color = tuple(map(int, values))
            if max(color) <= 255:
                foregrounds[color] += 1
    for path in files:
        if path.suffix.lower() in IMAGE_EXT and any("background" in p.lower() or p.lower() == "wallpapers"
                                                   for p in path.relative_to(root).parts[:-1]):
            backgrounds.add(path)
    ui = sorted(referenced - backgrounds) or [p for p in files if p.suffix.lower() in IMAGE_EXT and p not in backgrounds]
    palette, surfaces, warnings = Counter(), Counter(), []
    for path in ui[:1000]:
        try:
            colors = image_colors(path)
            palette.update(colors)
            if path in dialog_images:
                surfaces.update(colors)
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            warnings.append(f"Skipped image {path.relative_to(root)}: {error}")
    if not palette:
        warnings.append("No readable UI images; used neutral fallback palette")
        palette[(40, 40, 40)] = 1
    bg = (surfaces or palette).most_common(1)[0][0]
    fg = readable(foregrounds.most_common(1)[0][0] if foregrounds else (220, 220, 220), bg)
    return palette, bg, fg, sorted(backgrounds), warnings


def make_palette(histogram, bg, fg, mode):
    if mode == "dark" and luminance(bg) > .18:
        bg = mix(bg, (0, 0, 0), .8)
    elif mode == "light" and luminance(bg) < .5:
        bg = mix(bg, (255, 255, 255), .85)
    fg = readable(fg, bg)
    dark = luminance(bg) < .35
    colorful = [(c, n) for c, n in histogram.items() if colorsys.rgb_to_hsv(*(v / 255 for v in c))[1] > .25]
    accent = max(colorful, key=lambda item: item[1])[0] if colorful else (100, 160, 200)
    accent = readable(accent, bg, 3)
    result = dict(mode="dark" if dark else "light", background=bg, foreground=fg, accent=accent,
                  selection=mix(bg, accent, .4), muted=mix(bg, fg, .4),
                  dark_background=mix(bg, (0, 0, 0), .2), darker_background=mix(bg, (0, 0, 0), .4),
                  lighter_background=mix(bg, fg, .12), dark_foreground=mix(bg, fg, .55),
                  light_foreground=mix(bg, fg, .85), bright_foreground=fg)
    for name, hue in dict(red=0, orange=30, yellow=55, green=120, cyan=180, blue=225, magenta=290, brown=25).items():
        def distance(item):
            h = colorsys.rgb_to_hsv(*(v / 255 for v in item[0]))[0] * 360
            return min(abs(h - hue), 360 - abs(h - hue))
        choices = [item for item in colorful if distance(item) < 35]
        color = min(choices, key=lambda item: (distance(item), -item[1]))[0] if choices else tuple(round(v * 255) for v in colorsys.hsv_to_rgb(hue / 360, .55, .78 if dark else .55))
        result[name] = readable(color, bg, 3)
        if name not in {"brown", "orange"}:
            result["bright_" + name] = mix(result[name], fg, .2)
    # Also support Omarchy releases using numbered ANSI colors.
    ansi = [bg, result["red"], result["green"], result["yellow"], result["blue"],
            result["magenta"], result["cyan"], fg, result["muted"], result["bright_red"],
            result["bright_green"], result["bright_yellow"], result["bright_blue"],
            result["bright_magenta"], result["bright_cyan"], result["bright_foreground"]]
    result.update({f"color{i}": color for i, color in enumerate(ansi)})
    result["selection_background"] = result["selection"]
    result["selection_foreground"] = readable(fg, result["selection"])
    return {k: v if isinstance(v, str) else rgbhex(v) for k, v in result.items()}


def convert(root, output, name, source, mode="auto", wallpapers=True, border_name=None, decorations=True):
    histogram, bg, fg, backgrounds, warnings = analyze(root)
    palette = make_palette(histogram, bg, fg, mode)
    output.mkdir(parents=True, exist_ok=False)
    try:
        (output / "colors.toml").write_text("# Generated from an Enlightenment E16 theme\n" + "".join(f'{k} = "{v}"\n' for k, v in palette.items()))
        bgdir = output / "backgrounds"
        bgdir.mkdir()
        count = 0
        if wallpapers:
            for path in backgrounds:
                try:
                    with Image.open(path) as image:
                        image.convert("RGB").save(bgdir / f"{count + 1:02d}-{slug(path.stem)}.png")
                    count += 1
                except (OSError, ValueError, Image.DecompressionBombError) as error:
                    warnings.append(f"Skipped wallpaper {path.name}: {error}")
        if count == 0:
            Image.new("RGB", (1920, 1080), palette["background"]).save(bgdir / "01-solid.png")
        preview = Image.new("RGB", (800, 320), palette["background"])
        draw = ImageDraw.Draw(preview)
        draw.text((24, 24), name, fill=palette["foreground"])
        for i, key in enumerate(("red", "orange", "yellow", "green", "cyan", "blue", "magenta", "accent")):
            x = 24 + i * 94
            draw.rectangle((x, 90, x + 80, 225), fill=palette[key])
            draw.text((x, 240), key, fill=palette["foreground"])
        preview.save(output / "preview.png")
        decoration_report = {"available": False, "warnings": []}
        if decorations:
            from .decorations import export_decorations
            decoration_report = export_decorations(root, output, border_name)
        warnings.extend(decoration_report["warnings"])
        report = dict(decorations=decoration_report, name=name, source=source, mode=palette["mode"], wallpapers=count,
                      warnings=warnings, limitations=["E16 decorations require the e16-decorations Hyprland plugin; custom actions, sounds and keybindings are not executed.",
                                                    "Wallpaper tiling and desktop assignments are not reproduced.",
                                                    "ANSI colors missing in the source are synthesized for readability."])
        (output / "conversion.json").write_text(json.dumps(report, indent=2) + "\n")
    except BaseException:
        shutil.rmtree(output)
        raise
    return report


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href" and value and urlparse(value).path.endswith(".etheme"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert E16 themes into Omarchy colors, wallpapers and original bitmap window frames.")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="List themes from themes.effx.us")
    listing.add_argument("--filter", default="", help="Case-insensitive name filter")
    conv = sub.add_parser("convert", help="Convert a directory, tar archive, URL, or catalog theme name")
    conv.add_argument("source")
    conv.add_argument("-o", "--output", type=Path, default=Path("converted"), help="Parent output directory (default: converted)")
    conv.add_argument("--name", help="Output theme name")
    conv.add_argument("--mode", choices=("auto", "dark", "light"), default="auto")
    conv.add_argument("--no-wallpapers", action="store_true")
    conv.add_argument("--border", help="E16 border class to recreate (default: DEFAULT or first available)")
    conv.add_argument("--no-decorations", action="store_true", help="Export only colors and wallpapers")
    args = parser.parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="e16toomarchy-") as tmp:
            work = Path(tmp)
            if args.command == "list":
                index = work / "index.html"
                download(CATALOG, index)
                links = Links()
                links.feed(index.read_text())
                for link in sorted(set(links.links), key=str.lower):
                    name = unquote(Path(urlparse(link).path).stem)
                    if args.filter.lower() in name.lower():
                        print(name)
                return
            source = args.source
            local = Path(source).expanduser()
            if not local.exists():
                if "://" not in source:
                    from urllib.parse import quote
                    source = urljoin(CATALOG, quote(source.removesuffix(".etheme"), safe="") + ".etheme")
                archive = work / "theme.archive"
                download(source, archive)
                local = archive
            inferred = Path(unquote(urlparse(source).path)).name
            for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".etheme", ".tgz", ".tar"):
                if inferred.endswith(suffix):
                    inferred = inferred[:-len(suffix)]
                    break
            name = args.name or inferred
            output = args.output.expanduser() / slug(name)
            if output.exists():
                raise ValueError(f"Output already exists: {output}; choose a new --name or --output")
            if local.is_dir():
                root = local
                # Output inside input would contaminate subsequent conversions.
                if output.resolve().is_relative_to(root.resolve()):
                    raise ValueError("Output must be outside the input theme directory")
            else:
                root = work / "theme"
                root.mkdir()
                unpack(local, root)
            report = convert(root, output, name, source, args.mode, not args.no_wallpapers, args.border, not args.no_decorations)
            print(f"Created {output} ({report['mode']}, {report['wallpapers']} source wallpapers)")
            for warning in report["warnings"]:
                print(f"Warning: {warning}", file=sys.stderr)
            print(f"Install: copy {output} to ~/.config/omarchy/themes/{output.name}")
            print(f"Apply: omarchy theme set {output.name}")
    except (OSError, ValueError, tarfile.TarError) as error:
        parser.exit(1, f"Error: {error}\n")
