"""Export E16 border classes and original bitmap states as inert renderer data.

Geometry follows E16's 1024-unit anchors and inclusive bottom-right coordinates.
No theme code, preprocessor, shell command or custom action is executed.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shlex

from PIL import Image


STATES = {
    "NORMAL", "HILITED", "CLICKED", "NORMAL_ACTIVE", "HILITED_ACTIVE", "CLICKED_ACTIVE",
    "NORMAL_STICKY", "HILITED_STICKY", "CLICKED_STICKY", "NORMAL_ACTIVE_STICKY",
    "HILITED_ACTIVE_STICKY", "CLICKED_ACTIVE_STICKY", "NORMAL_ACTIVE_HILITED", "NORMAL_ACTIVE_CLICKED",
}
ACTIONS = {
    "ACTION_KILL": "close", "ACTION_CLOSE": "close", "ACTION_MAX": "maximize",
    "ACTION_MAXIMIZE": "maximize", "ACTION_ICONIFY": "minimize", "ACTION_MOVE": "move",
    "ACTION_RESIZE": "resize", "ACTION_RESIZE_H": "resize_h", "ACTION_RESIZE_V": "resize_v",
    "ACTION_STICK": "pin", "ACTION_STICKY": "pin",
}


def number(value, default=0):
    if isinstance(value, list):
        value = value[0] if value else ""
    if value in {"__ON", "ON", "__FLAG_TITLE"}:
        return 1
    if value in {"__OFF", "OFF"}:
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def parse_files(root):
    """Keep block nesting and state-local image settings, including include order."""
    objects, seen, warnings = [], set(), []
    paths = sorted(root.rglob("*.cfg"))
    entries = [p for p in paths if p.name in {"borders.cfg", "imageclasses.cfg", "textclasses.cfg"}]

    def read(path):
        path = path.resolve()
        if path in seen or not path.is_relative_to(root.resolve()) or not path.is_file():
            return
        seen.add(path)
        text = re.sub(r"/\*.*?\*/", "", path.read_text(errors="replace"), flags=re.S)
        stack = []
        for line in text.splitlines():
            line = line.strip().rstrip(";")
            include = re.match(r'#include\s*[<"]([^>"]+)[>"]', line)
            if include:
                # Only literal includes within this source tree.
                for base in (path.parent, root):
                    candidate = base / include[1]
                    if candidate.is_file():
                        read(candidate)
                        break
                continue
            if line.startswith(("#if", "#else", "#define")):
                warnings.append(f"Unevaluated preprocessor directive in {path.name}: {line}")
            begin = re.match(r"__(\w+)\s+(?:__BGN|BGN)\b", line)
            if begin:
                obj = {"kind": begin[1], "fields": {}, "children": [], "lines": [], "file": path}
                if stack:
                    stack[-1]["children"].append(obj)
                else:
                    objects.append(obj)
                stack.append(obj)
            elif line == "__END":
                if stack:
                    stack.pop()
            elif stack and line.startswith("__"):
                try:
                    values = shlex.split(line, comments=True)
                except ValueError:
                    warnings.append(f"Unparseable field in {path.name}: {line}")
                    continue
                if values:
                    key = values[0].removeprefix("__")
                    stack[-1]["fields"][key] = values[1:]
                    stack[-1]["lines"].append((key, values[1:]))
    for path in entries + paths:
        read(path)
    return objects, list(dict.fromkeys(warnings))


def export_decorations(root: Path, output: Path, border_name=None):
    objects, warnings = parse_files(root)
    borders = [obj for obj in objects if obj["kind"] == "BORDER" and obj["children"]]
    if not borders:
        return {"available": False, "warnings": warnings + ["No supported E16 border blocks found"]}

    def name(obj):
        return " ".join(obj["fields"].get("NAME", ["unnamed"]))

    if border_name:
        border = next((obj for obj in borders if name(obj).lower() == border_name.lower()), None)
        if border is None:
            raise ValueError(f"Border {border_name!r} not found; available: {', '.join(name(b) for b in borders)}")
    else:
        border = next((obj for obj in borders if name(obj).upper() == "DEFAULT"), borders[0])
    classes = {name(obj): obj for obj in objects if obj["kind"] == "ICLASS"}
    texts = {name(obj): obj for obj in objects if obj["kind"] == "TCLASS"}
    dest = output / "e16"
    (dest / "assets").mkdir(parents=True)
    assets, images, textstyles = {}, {}, {}
    allfiles = sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink())

    def asset(value, config):
        for candidate in (config.parent / value, root / value):
            if candidate.is_file() and candidate.resolve().is_relative_to(root.resolve()):
                path = candidate
                break
        else:
            matches = [p for p in allfiles if p.as_posix().endswith("/" + value.removeprefix("./"))]
            path = matches[0] if len(matches) == 1 else None
        if path is None:
            warnings.append(f"Missing or ambiguous decoration asset: {value}")
            return None
        key = path.resolve()
        if key in assets:
            return assets[key]
        filename = f"assets/{len(assets):04d}.png"
        try:
            with Image.open(path) as image:
                rgba = image.convert("RGBA")
                # Magenta is E16/Imlib's conventional shape-mask color.
                from .cli import pixel_data
                rgba.putdata([(r, g, b, 0 if (r, g, b) == (255, 0, 255) else a)
                              for r, g, b, a in pixel_data(rgba)])
                rgba.save(dest / filename)
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            warnings.append(f"Cannot convert decoration asset {value}: {error}")
            return None
        assets[key] = filename
        return filename

    def export_image(classname):
        if classname in images:
            return
        obj = classes.get(classname)
        if not obj:
            warnings.append(f"Missing image class: {classname}")
            return
        states, current, padding = {}, None, [0, 0, 0, 0]
        for key, values in obj["lines"]:
            if key in STATES and values:
                current = key.lower()
                path = asset(values[0], obj["file"])
                states[current] = {"file": path or "", "edges": [0, 0, 0, 0], "tile": False}
            elif key == "EDGE_SCALING" and current:
                states[current]["edges"] = [number(v) for v in values[:4]]
            elif key == "FILLRULE" and current:
                rule = " ".join(values)
                states[current]["tile"] = "TILE" in rule or rule == "1"
                if "TILE" in rule and rule not in {"__TILE", "__TILE_HV"}:
                    warnings.append(f"Directional tile rule {rule} in {classname} rendered as two-axis tiling")
            elif key == "PADDING":
                padding = [number(v) for v in values[:4]]
        images[classname] = {"states": states, "padding": padding}

    def export_text(classname):
        if classname in textstyles or classname not in texts:
            return
        obj = texts[classname]
        styles, current = {}, "normal"
        for key, values in obj["lines"]:
            if key in STATES:
                current = key.lower()
                styles.setdefault(current, {})
            elif key in {"FORGROUND_COLOR", "FOREGROUND_COLOR", "BACKGROUND_COLOR"}:
                styles.setdefault(current, {})["background" if key == "BACKGROUND_COLOR" else "color"] = [number(v) for v in values[:3]]
            elif key == "DRAWING_EFFECT":
                styles.setdefault(current, {})["effect"] = " ".join(values).lower().removeprefix("__effect_")
        textstyles[classname] = {"justify": number(obj["fields"].get("JUSTIFICATION")), "styles": styles}

    parts = []
    for obj in border["children"]:
        if obj["kind"] != "BORDER_PART":
            continue
        fields = obj["fields"]
        getstr = lambda key: " ".join(fields.get(key, []))
        iclass, tclass, aclass = getstr("ICLASS"), getstr("TCLASS"), getstr("ACLASS")
        export_image(iclass)
        export_text(tclass)
        geometry = {}
        for prefix in ("TOPLEFT", "BOTTOMRIGHT"):
            geometry[prefix.lower()] = {
                "origin": number(fields.get(prefix + "_ORIGIN"), -1),
                "xp": number(fields.get(prefix + "_X_PERCENTAGE")), "xa": number(fields.get(prefix + "_X_ABSOLUTE")),
                "yp": number(fields.get(prefix + "_Y_PERCENTAGE")), "ya": number(fields.get(prefix + "_Y_ABSOLUTE")),
            }
        for dimension in ("WIDTH", "HEIGHT"):
            minimum = number(fields.get("MIN_" + dimension))
            geometry[dimension.lower()] = [minimum, number(fields.get("MAX_" + dimension), minimum)]
        parts.append(dict(image=iclass, text=tclass, action=ACTIONS.get(aclass, ""), source_action=aclass,
                          title="TITLE" in getstr("FLAGS") or bool(tclass),
                          ontop=number(fields.get("KEEP_ON_TOP"), 1), geometry=geometry))
        if aclass and aclass not in ACTIONS:
            warnings.append(f"Action {aclass} has no Hyprland equivalent; its artwork is preserved")
    extents = {edge.lower(): number(border["fields"].get("BORDER_SIZE_" + edge))
               for edge in ("LEFT", "RIGHT", "TOP", "BOTTOM")}
    if any(v < 0 or v > 512 for v in extents.values()):
        raise ValueError("Border extents must be within 0..512 pixels")
    if not any(extents.values()):
        warnings.append("Selected border has no extents; it may be a borderless E16 style")
    manifest = dict(version=1, border=name(border), available_borders=[name(obj) for obj in borders],
                    extents=extents, images=images, texts=textstyles, parts=parts,
                    warnings=list(dict.fromkeys(warnings)), font="Sans Bold 10")
    (dest / "decoration.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return dict(available=True, border=name(border), parts=len(parts), assets=len(assets), warnings=manifest["warnings"])
