"""Bounded, local-only caches of the E16 gallery's original screenshots."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import quote
from urllib.error import HTTPError

from PIL import Image

from .picker import atomic_json, fetch, paths, read_json, valid_name

GALLERY = "https://themes.effx.us/preview/e16/"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 8 * 1024 * 1024
MAX_BATCH = 12


def gallery_url(name):
    return GALLERY + quote(valid_name(name), safe="") + ".png"


def cache_path(name):
    identity = hashlib.sha256(valid_name(name).encode()).hexdigest()[:24]
    return paths()["cache"] / "gallery" / identity


def save_image(image, path, size, quality):
    copy = image.copy()
    copy.thumbnail(size, Image.Resampling.LANCZOS)
    fd, temporary = tempfile.mkstemp(prefix=".image-", suffix=".jpg", dir=path.parent)
    os.close(fd)
    try:
        copy.save(temporary, "JPEG", quality=quality, optimize=True)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def thumbnail(name):
    """Fetch only the screenshot, never the .etheme archive or converter."""
    valid_name(name)
    folder = cache_path(name)
    folder.mkdir(parents=True, exist_ok=True)
    preview, thumb = folder / "preview.jpg", folder / "thumb.jpg"
    result = {"name": name, "available": False, "preview": "", "thumbnail": ""}
    # The same image may be requested by a selected card, visible row, or another monitor.
    with (folder / "fetch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if preview.is_file() and thumb.is_file():
            return dict(result, available=True, preview=str(preview), thumbnail=str(thumb))
        metadata = read_json(folder / "status.json", {})
        if metadata.get("retry_after", 0) > time.time():
            return result
        try:
            with tempfile.TemporaryDirectory(prefix=".download-", dir=folder) as tmp:
                source = Path(tmp) / "source.png"
                fetch(gallery_url(name), source, MAX_IMAGE_BYTES, timeout=8)
                with Image.open(source) as image:
                    if image.format not in {"PNG", "JPEG"} or image.width * image.height > MAX_IMAGE_PIXELS:
                        raise ValueError("Unsupported or oversized gallery image")
                    image.load()
                    rgb = image.convert("RGB")
                    save_image(rgb, preview, (800, 600), 87)
                    save_image(rgb, thumb, (144, 100), 80)
            atomic_json(folder / "status.json", {"name": name, "fetched": time.time()})
            return dict(result, available=True, preview=str(preview), thumbnail=str(thumb))
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            retry = 86400 if isinstance(error, HTTPError) and error.code == 404 else 300
            if isinstance(error, HTTPError):
                error.close()
            atomic_json(folder / "status.json", {"name": name, "retry_after": time.time() + retry})
            return result


def thumbnails(names):
    """Stream visible rows as they arrive, with at most two network workers."""
    if not 1 <= len(names) <= MAX_BATCH:
        raise ValueError("Request between 1 and 12 gallery thumbnails")
    unique = list(dict.fromkeys(valid_name(name) for name in names))
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(thumbnail, name) for name in unique]
        for future in as_completed(futures):
            print(json.dumps(future.result(), ensure_ascii=True), flush=True)
