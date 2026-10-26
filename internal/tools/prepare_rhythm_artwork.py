#!/usr/bin/env python3
"""Prepare local Rhythm Heaven launcher art from a legally supplied base NSP.

Requires Pillow (with BC7 support), zstandard, and the prepare_game dependencies.
All extracted/copyrighted output stays under .touchable; no keys are reported.
The .tex files are zstd-compressed NVN block-linear BC7, with a 4096-byte
footer holding <format, version, width, height, levels> as little-endian u16.
Only the observed version-1, format-101, single-level layout is supported.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile

from PIL import Image
import zstandard

from patch_storage import decrypt_header
from prepare_game import (copy_ncas, package_ncas, read_display_version,
                          write_nca_keyset)
from workspace import ROOT, DEPENDENCIES


def extract_control(package, keys, output):
    """Reuse bounded NSP reads, authenticated content copies and safe hactool."""
    _, entries = package_ncas(package)
    controls = []
    with tempfile.TemporaryDirectory(prefix='.art-control-', dir=output) as temp:
        temp = Path(temp)
        keyset = temp / 'nca.keys'
        header_key = write_nca_keyset(keys, keyset)
        with package.open('rb') as source:
            for entry in entries:
                source.seek(entry['offset'])
                candidate = temp / entry['name']
                candidate.write_bytes(source.read(0xc00))
                header = decrypt_header(candidate, header_key)
                candidate.unlink()
                if header[0x205] == 2:
                    controls.append(entry)
        if len(controls) != 1:
            raise ValueError('Expected exactly one Control NCA in the base NSP.')
        hashes, _ = copy_ncas(package, controls, temp, header_key)
        version = read_display_version(DEPENDENCIES / 'hactool/hactool', keyset,
                                      temp / controls[0]['name'], output / 'control')
    return {'content_sha256': hashes, 'display_version': version}


def decode_texture(path):
    data = zstandard.ZstdDecompressor().decompress(path.read_bytes())
    if len(data) < 4096:
        raise ValueError('Missing texture footer: ' + str(path))
    fmt, version, width, height, levels = struct.unpack_from('<5H', data, len(data) - 4096)
    if (fmt, version, levels) != (101, 1, 1) or not width or not height:
        raise ValueError('Unsupported texture layout: ' + str(path))
    columns, rows = (width + 3) // 4, (height + 3) // 4
    # Same floor-based GOB height selection as the Rhythm Heaven NVN layout.
    tile = 1 << min(4, max(0, ((rows + rows // 2) // 8).bit_length() - 1))
    pitch = (columns * 16 + 63) // 64
    linear = bytearray(columns * rows * 16)
    for y in range(rows):
        for x in range(columns):
            xb = x * 16
            offset = ((y // (8 * tile)) * 512 * tile * pitch
                      + (xb // 64) * 512 * tile + ((y % (8 * tile)) // 8) * 512
                      + ((xb % 64) // 32) * 256 + ((y % 8) // 2) * 64
                      + ((xb % 32) // 16) * 32 + (y % 2) * 16)
            if offset + 16 > len(data) - 4096:
                raise ValueError('Texture pixels exceed their payload: ' + str(path))
            target = (y * columns + x) * 16
            linear[target:target + 16] = data[offset:offset + 16]
    return Image.frombytes('RGBA', (width, height), bytes(linear), 'bcn', (7, 'BC7'))


def prepare_artwork(romfs, output):
    sources = {
        'title-atlas': 'graph/menu/title/bg.tex',
        'start-menu-bg-atlas': 'graph/menu/start_menu/bg.tex',
        'start-menu-main-atlas': 'graph/menu/start_menu/main.tex',
    }
    atlases, provenance = {}, {}
    for name, relative in sources.items():
        source = romfs / relative
        atlas = decode_texture(source)
        atlas.save(output / (name + '.png'))
        atlases[name] = atlas
        provenance[name] = {'source': relative, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                            'dimensions': list(atlas.size)}
    # Sprite rectangles selected from transparent gutters in the decoded atlases.
    rectangles = {
        'title-logo-zh': ('title-atlas', (0, 1372, 876, 2032)),
        'mascot-dog': ('start-menu-main-atlas', (0, 0, 248, 328)),
        'mascot-girl': ('start-menu-main-atlas', (260, 0, 448, 308)),
        'mascot-ninja': ('start-menu-main-atlas', (448, 0, 636, 304)),
    }
    sprites = {}
    for name, (atlas, rectangle) in rectangles.items():
        sprite = atlases[atlas].crop(rectangle)
        sprite.save(output / (name + '.png'))
        sprites[name] = sprite
        provenance[name] = {'atlas': atlas, 'crop_xyxy': list(rectangle), 'dimensions': list(sprite.size)}
    # Original illustration only; a flat cream backing leaves controls readable.
    canvas = Image.new('RGBA', (1920, 1080), '#fff8ed')
    placements = [('title-logo-zh', (100, 108), (648, 488)),
                  ('mascot-dog', (106, 676), (199, 263)),
                  ('mascot-girl', (339, 689), (151, 247)),
                  ('mascot-ninja', (538, 690), (151, 244))]
    for name, position, size in placements:
        canvas.alpha_composite(sprites[name].resize(size, Image.Resampling.LANCZOS), position)
    canvas.convert('RGB').save(output / 'launcher-background.png')
    with Image.open(output / 'control/icon_SimplifiedChinese.dat') as icon:
        icon.convert('RGB').save(output / 'ic_launcher.png')
    provenance['launcher-background'] = {'dimensions': [1920, 1080], 'backing': '#fff8ed',
                                        'placements': placements, 'overlays': 'none; no screenshot pixels'}
    provenance['ic_launcher'] = {'source': 'control/icon_SimplifiedChinese.dat',
                                 'dimensions': [256, 256], 'operation': 'JPEG decoded to RGB PNG, no resize'}
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--keys', type=Path, required=True)
    parser.add_argument('--romfs', type=Path, default=ROOT / '.touchable/work/rhythm-heaven/base/RomFS')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--install', action='store_true', help='Copy the two launcher images into local Android build inputs.')
    args = parser.parse_args()
    output = args.out.resolve()
    if not output.is_relative_to((ROOT / '.touchable').resolve()):
        parser.error('Extracted artwork must stay under .touchable.')
    output.mkdir(parents=True, exist_ok=False)
    control = extract_control(args.package.resolve(), args.keys.resolve(), output)
    report = prepare_artwork(args.romfs.resolve(), output)
    report['control'] = control
    report['package'] = str(args.package)
    (output / 'provenance.json').write_text(json.dumps(report, indent=2) + '\n')
    if args.install:
        local = DEPENDENCIES / 'akari-android/dm/android/local'
        local.mkdir(parents=True, exist_ok=True)
        for name in ('ic_launcher.png', 'launcher-background.png'):
            shutil.copyfile(output / name, local / name)
    print('Prepared local original artwork: ' + str(output))


if __name__ == '__main__':
    main()
