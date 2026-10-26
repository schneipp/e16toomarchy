#!/usr/bin/env python3
"""Render a looping feature reel from real gallery images and E16 frame assets.

Requires Pillow, ffmpeg, fontconfig and the project's frame-preview binary.
Only the GIF/MP4/poster are committed; source theme artwork stays in local caches.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from e16toomarchy.cli import convert, download, unpack
from e16toomarchy.picker import theme_url
from e16toomarchy.gallery import thumbnail

W, H = 1200, 750
BG = '#0b111a'
PAPER = '#ecebe4'
MINT = '#b9f4d4'
MUTED = '#9babbc'
THEMES = [('BrushedMetal', 'brushedmetal-frames'), ('Aqua', 'aqua-test'), ('AlienConsole', 'alienconsole-test')]


def command(argv):
    subprocess.run([str(arg) for arg in argv], check=True, stdout=subprocess.DEVNULL)


@lru_cache(maxsize=1)
def fonts():
    def find(pattern):
        return subprocess.check_output(['fc-match', '-f', '%{file}', pattern], text=True).strip()
    return {'regular': find('Liberation Sans'), 'bold': find('Liberation Sans:style=Bold'),
            'mono': find('JetBrains Mono')}


@lru_cache(maxsize=64)
def font(size, family='regular'):
    return ImageFont.truetype(fonts()[family], size)


def text(draw, xy, value, size=20, fill=PAPER, family='regular', anchor=None):
    draw.text(xy, value, font=font(size, family), fill=fill, anchor=anchor)


def tracking(draw, xy, value, size=12, fill=MUTED, spacing=2):
    x, y = xy
    face = font(size, 'mono')
    for ch in value:
        draw.text((x, y), ch, font=face, fill=fill)
        x += draw.textlength(ch, font=face) + spacing


def smooth(p):
    p = max(0, min(1, p))
    return p * p * (3 - 2 * p)


def alpha(image, amount):
    if amount >= 1:
        return image
    copy = image.copy()
    copy.putalpha(copy.getchannel('A').point(lambda v: round(v * max(0, amount))))
    return copy


def shadow(image, radius=18, strength=.5):
    layer = Image.new('RGBA', (image.width + radius * 4, image.height + radius * 4))
    mask = Image.new('RGBA', image.size, (0, 0, 0, 0))
    mask.putalpha(image.getchannel('A').point(lambda a: int(a * strength)))
    layer.alpha_composite(mask, (radius * 2, radius * 2))
    layer = layer.filter(ImageFilter.GaussianBlur(radius))
    layer.alpha_composite(image, (radius * 2, radius * 2 - 5))
    return layer


def background():
    image = Image.new('RGBA', (W, H), BG)
    glow = Image.new('RGBA', (W, H))
    d = ImageDraw.Draw(glow)
    d.ellipse((530, 10, 1230, 680), fill=(44, 100, 82, 30))
    d.ellipse((-220, 260, 570, 1080), fill=(38, 61, 112, 35))
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(100)))
    d = ImageDraw.Draw(image)
    for x in range(30, W, 36):
        for y in range(30, H, 36):
            d.point((x, y), fill=(112, 139, 161, 28))
    d.line((60, 108, W - 60, 108), fill='#26313e', width=1)
    text(d, (60, 41), 'E16', 28, MINT, 'mono')
    text(d, (121, 40), '→', 30, PAPER, 'regular')
    text(d, (166, 41), 'OMARCHY', 28, PAPER, 'mono')
    tracking(d, (895, 54), 'ORIGINAL E16 ARTWORK', 10, MUTED, 1)
    return image


def footer(image, phase, progress):
    d = ImageDraw.Draw(image)
    d.line((60, 666, W - 60, 666), fill='#26313e', width=1)
    for i, label in enumerate(('01  BROWSE', '02  PREVIEW', '03  APPLY')):
        x = 64 + i * 370
        d.ellipse((x, 693, x + 6, 699), fill=MINT if i == phase else '#405063')
        tracking(d, (x + 19, 687), label, 12, MINT if i == phase else MUTED, 1.8)
    d.line((0, H - 2, int(W * progress), H - 2), fill=MINT, width=2)


def prepare_assets(work, gallery_cache):
    renderer = ROOT / 'build/e16-frame-preview'
    if not renderer.is_file():
        command(['make', '-C', ROOT, 'preview'])
    assets = []
    for name, local in THEMES:
        theme = ROOT / 'converted' / local
        if not (theme / 'e16/decoration.json').is_file():
            theme = work / name
            archive = work / (name + '.etheme')
            source = work / (name + '-source')
            source.mkdir()
            download(theme_url(name), archive)
            unpack(archive, source)
            convert(source, theme, name, theme_url(name))
        metadata = json.loads((theme / 'e16/decoration.json').read_text())
        ext = metadata['extents']
        outputs = {}
        for state in ('active', 'inactive'):
            path = work / (name + '-' + state + '.png')
            command([renderer, theme / 'e16/decoration.json', path, 624, 330, state, name + '  /  E16 → Omarchy'])
            window = Image.open(path).convert('RGBA')
            # An illustrated terminal inside the real frame, never a private desktop capture.
            client = Image.new('RGBA', (624, 330), '#151d29')
            d = ImageDraw.Draw(client)
            tracking(d, (25, 25), 'A CLASSIC, RECREATED', 11, MUTED, 1.4)
            text(d, (25, 69), '$', 19, MINT, 'mono')
            text(d, (51, 69), 'e16toomarchy convert', 19, PAPER, 'mono')
            text(d, (51, 101), name, 21, '#f4c288', 'mono')
            for i, value in enumerate(('colors.toml', 'backgrounds/', 'e16/decoration.json')):
                cy = 173 + i * 31
                d.line((32, cy, 37, cy + 5, 46, cy - 6), fill=MINT, width=2)
                text(d, (60, 163 + i * 31), value, 17, '#aebdd1', 'mono')
            d.line((25, 290, 599, 290), fill='#2c3948')
            tracking(d, (25, 307), 'ORIGINAL BITMAP FRAME · HYPRLAND', 9, MUTED, 1.2)
            window.alpha_composite(client, (ext['left'], ext['top']))
            outputs[state] = window
        identity = hashlib.sha256(name.encode()).hexdigest()[:24]
        gallery = gallery_cache / 'gallery' / identity / 'preview.jpg' if gallery_cache else None
        if not gallery or not gallery.is_file():
            cached = thumbnail(name)
            if not cached['available']:
                raise RuntimeError(f'Gallery artwork unavailable for {name}')
            gallery = Path(cached['preview'])
        screenshot = Image.open(gallery).convert('RGBA')
        screenshot.thumbnail((800, 525), Image.Resampling.LANCZOS)
        assets.append({'name': name, 'gallery': screenshot, **outputs})
    return assets


def pill(draw, box, label, selected=False):
    draw.rounded_rectangle(box, radius=18, fill='#1c342e' if selected else '#17212e', outline='#467760' if selected else '#344354')
    text(draw, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2 - 1), label, 12, MINT if selected else MUTED, 'mono', 'mm')


def window_hero(asset, width):
    window = asset['active'].copy()
    window = window.resize((width, round(width * window.height / window.width)), Image.Resampling.LANCZOS)
    return shadow(window)


def scene_intro(bg, assets, elapsed):
    image = bg.copy()
    d = ImageDraw.Draw(image)
    text(d, (60, 184), 'Classic frames.', 65, PAPER, 'bold')
    text(d, (60, 258), 'Fresh desktop.', 65, MINT, 'bold')
    text(d, (64, 366), 'Enlightenment E16 themes,', 25, MUTED)
    text(d, (64, 402), 'reborn on Omarchy.', 25, MUTED)
    pill(d, (64, 488, 335, 526), 'NATIVE TOPBAR PICKER', True)
    bob = round(math.sin(elapsed * 1.8) * 4)
    back = window_hero(assets[1], 536)
    image.alpha_composite(alpha(back, .65), (520, 182 + bob))
    front = window_hero(assets[0], 554)
    image.alpha_composite(front, (545, 232 - bob))
    return image


def scene_gallery(bg, assets, elapsed):
    image = bg.copy()
    d = ImageDraw.Draw(image)
    text(d, (60, 154), 'Find your classic.', 58, PAPER, 'bold')
    text(d, (64, 224), 'Quick gallery previews. Cached locally.', 23, MUTED)
    active = min(2, max(0, int((elapsed - .5) / .9)))
    for i, asset in enumerate(assets):
        offset = round(22 * (1 - smooth((elapsed - i * .09) / .4)))
        card = Image.new('RGBA', (342, 305), '#131d29')
        cd = ImageDraw.Draw(card)
        cd.rounded_rectangle((0, 0, 341, 304), radius=12, fill='#151f2b', outline='#6cb795' if i == active else '#344254', width=2)
        screenshot = asset['gallery'].resize((318, 209), Image.Resampling.LANCZOS)
        card.alpha_composite(screenshot, (12, 12))
        text(cd, (18, 238), asset['name'], 20, MINT if i == active else PAPER, 'mono')
        text(cd, (18, 273), 'Original E16 screenshot', 13, MUTED)
        image.alpha_composite(card, (60 + i * 368, 294 + offset))
    return image


def scene_frames(bg, assets, elapsed):
    image = bg.copy()
    d = ImageDraw.Draw(image)
    text(d, (60, 171), 'Original pixels.', 55, PAPER, 'bold')
    text(d, (60, 236), 'Modern windows.', 55, MINT, 'bold')
    for i, label in enumerate(('Native bitmap frames', 'Focused / unfocused states', 'Move, resize, maximize')):
        d.ellipse((66, 378 + i * 43, 72, 384 + i * 43), fill=MINT)
        text(d, (89, 369 + i * 43), label, 21, MUTED)
    index = min(2, int(elapsed / 1.65))
    within = elapsed - index * 1.65
    active = assets[index]
    pill(d, (553, 160, 1124, 198), active['name'].upper() + '  /  ORIGINAL FRAME', True)
    state = 'inactive' if .95 < within < 1.23 else 'active'
    window = active[state]
    window = shadow(window)
    x, y = 488, 219
    image.alpha_composite(window, (x, y))
    if index > 0 and within < .18:
        previous = shadow(assets[index - 1]['active'])
        image.alpha_composite(alpha(previous, 1 - smooth(within / .18)), (x, y))
    text(ImageDraw.Draw(image), (555, 624), 'Actual frames from the project’s renderer', 13, MUTED)
    return image


def scene_outro(bg, assets, elapsed):
    image = bg.copy()
    d = ImageDraw.Draw(image)
    text(d, (W / 2, 180), 'Pick your era.', 78, PAPER, 'bold', 'mt')
    text(d, (W / 2, 270), 'Keep your workflow.', 70, MINT, 'bold', 'mt')
    text(d, (W / 2, 385), 'Browse  →  Preview  →  Apply', 25, MUTED, 'mono', 'mt')
    d.rounded_rectangle((270, 478, 930, 547), radius=14, fill='#1d3b30', outline='#5b9d7c', width=2)
    text(d, (W / 2, 513), 'OPEN THE TOPBAR PICKER', 22, MINT, 'mono', 'mm')
    text(d, (W / 2, 585), 'github.com/schneipp/e16toomarchy', 22, PAPER, 'mono', 'mt')
    return image


def render_frame(bg, assets, t, duration):
    boundaries = [0, 2.25, 5.5, 10.45, duration]
    functions = [scene_intro, scene_gallery, scene_frames, scene_outro]
    index = next((i for i in range(4) if t < boundaries[i + 1]), 3)
    image = functions[index](bg, assets, t - boundaries[index])
    if index > 0 and t - boundaries[index] < .3:
        previous = functions[index - 1](bg, assets, boundaries[index] - boundaries[index - 1])
        image = Image.blend(previous, image, smooth((t - boundaries[index]) / .3))
    phase = 0 if index < 2 else 1 if index == 2 else 2
    footer(image, phase, t / duration)
    if t > duration - .4:
        first = scene_intro(bg, assets, 0)
        footer(first, 0, 0)
        image = Image.blend(image, first, smooth((t - duration + .4) / .4))
    return image.convert('RGB')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/assets')
    parser.add_argument('--gallery-cache', type=Path, help='Existing e16toomarchy cache directory for offline gallery artwork')
    parser.add_argument('--fps', type=int, default=18)
    args = parser.parse_args()
    if not 8 <= args.fps <= 30:
        parser.error('--fps must be between 8 and 30')
    args.output.mkdir(parents=True, exist_ok=True)
    duration = 13.8
    with tempfile.TemporaryDirectory(prefix='e16-marketing-') as temp:
        work = Path(temp)
        assets = prepare_assets(work, args.gallery_cache)
        bg = background()
        count = round(duration * args.fps)
        for index in range(count):
            frame = render_frame(bg, assets, index * duration / (count - 1), duration)
            frame.save(work / f'{index:04d}.png', compress_level=3)
            if index == 0:
                frame.save(args.output / 'e16toomarchy-demo-poster.png', optimize=True)
        command(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-framerate', args.fps,
                 '-i', work / '%04d.png', '-vf', 'scale=1040:-1:flags=lanczos,palettegen=stats_mode=diff:max_colors=256',
                 '-frames:v', 1, work / 'palette.png'])
        command(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-framerate', args.fps,
                 '-i', work / '%04d.png', '-i', work / 'palette.png', '-filter_complex',
                 '[0:v]scale=1040:-1:flags=lanczos[v];[v][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle',
                 '-loop', 0, '-gifflags', '+transdiff', args.output / 'e16toomarchy-demo.gif'])
        command(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-framerate', args.fps,
                 '-i', work / '%04d.png', '-c:v', 'libx264', '-crf', 19, '-preset', 'slow', '-pix_fmt', 'yuv420p',
                 '-movflags', '+faststart', args.output / 'e16toomarchy-demo.mp4'])
        # Contact sheet is for local visual review, not a README dependency.
        sheet = Image.new('RGB', (W, H), BG)
        for i, moment in enumerate((.5, 3.7, 6.4, 12)):
            shot = render_frame(bg, assets, moment, duration).resize((W // 2, H // 2), Image.Resampling.LANCZOS)
            sheet.paste(shot, ((i % 2) * W // 2, (i // 2) * H // 2))
        sheet.save(args.output / 'demo-contact-sheet.png')
    for path in sorted(args.output.glob('e16toomarchy-demo.*')):
        print(f'{path}: {path.stat().st_size / 1024 / 1024:.2f} MiB')


if __name__ == '__main__':
    main()
