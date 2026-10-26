#!/usr/bin/env python3
"""Build the Android host APK for a game from the akari-sora Android host tree.

The akari tree stays game-agnostic. This tool writes the per-game host source
manifest (Touchable runtime + the akari host whitelist), generates the game
header, and drives the akari build. Every akari-tree change is recorded in
internal/patches/akari-android.patch.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

from prepare_ios import write_game_header
from workspace import DEPENDENCIES, ROOT, PROJECT_ROOT, WorkspaceError, load_game, validate_registry

AKARI = DEPENDENCIES / 'akari-android'
AKARI_DM = AKARI / 'dm'

# The generic host layer. hle_fs.c, hle_audio.c, and hle_net.c compile through
# the Touchable wrappers (ss_fs.c, ss_audio.c, ss_net.c), not as units here.
HOST_SOURCES = ('loader.c', 'hle.c', 'hle_svc.c', 'hle_thread.c', 'hle_os.c',
                'hle_sdkinit.c', 'hle_table.c', 'hle_count.c', 'hle_account.c', 'hle_swkbd.c')
NATIVE_SOURCES = ('runtime.c', 'host_posix.c', 'host_tls.c', 'host_sections.c', 'legacy_digest.c',
                  'pending_ranges.c', 'performance_hint.c', 'launch_settings.c', 'posture.c',
                  'frontend_activity.c', 'gpu_driver.c', 'present_rect.c', 'controller.c',
                  'fold_layout.c', 'fold_state.c', 'nvn_vulkan.c')
GFX_SOURCES = ('hle_nv.c', 'hle_vi.c', 'nvn/hle_nvn.c', 'nvn/nvn_tex.c', 'nvn/nvn_dirty.c',
               'tex/nvn_deswizzle.c')
LIBDEFLATE = ('deflate_decompress.c', 'zlib_decompress.c', 'adler32.c', 'utils.c',
              'arm/cpu_features.c')


def unit(path, darwin_shims=False, extra=()):
    entry = {'source': str(path)}
    if darwin_shims: entry['darwin_shims'] = True
    if extra: entry['extra'] = list(extra)
    return entry


def build_manifest(game, android_dir, host_out):
    modules = ','.join(game.module_names)
    defines = list(game.compile_defines)
    defines.append('DM_HOST_MODULES="{}"'.format(modules))
    sources = (*game.common_runtime_sources, *game.runtime_sources)
    # rh_batch.c removes Metal waits. Vulkan must retain the original waits.
    units = [unit(p, darwin_shims=True) for p in sources if p.name != 'rh_batch.c']
    units += [unit(AKARI_DM / 'host' / name) for name in HOST_SOURCES]
    units += [unit(AKARI_DM / 'android/native' / name) for name in NATIVE_SOURCES]
    units.append(unit(ROOT / 'platform/android/ss_android_touch.c'))
    units.append(unit(ROOT / 'platform/android/ss_android_rumble.c'))
    units.append(unit(ROOT / 'platform/android/ss_android_lifecycle.c'))
    units += [unit(AKARI_DM / 'gfx' / name) for name in GFX_SOURCES]
    units += [unit(host_out / 'entry_elf.S'), unit(host_out / 'nvn_thunks_elf.S'),
              unit(host_out / 'nvn_entry_elf.S')]
    third = AKARI_DM / 'third_party/libdeflate/lib'
    units += [unit(third / name) for name in LIBDEFLATE]
    generated = {str(host_out / name) for name in ('entry_elf.S', 'nvn_thunks_elf.S', 'nvn_entry_elf.S')}
    for path in units:
        if path['source'] in generated: continue
        if not Path(path['source']).is_file():
            raise WorkspaceError('Source does not exist: ' + path['source'])
    return {
        'compile_units': units,
        'include_dirs': [str(android_dir), str(ROOT / 'runtime/common'), str(game.game_dir / 'runtime'),
                         str(ROOT / 'platform/android')],
        'defines': defines,
        'link_flags': [],
        'game': {'id': game.id, 'display_name': game.display_name, 'module_names': list(game.module_names)},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', default='silksong', help='Game ID in games/<id>/game.json')
    parser.add_argument('--manifest-only', action='store_true', help='Write the manifest and header, then stop')
    parser.add_argument('--install', action='store_true', help='Pass --install to the akari build')
    parser.add_argument('--embedded', action='store_true', help='Embed the patched game modules in the APK')
    parser.add_argument('--standalone', action='store_true', help='Embed modules, full active RomFS, shader cache and Turnip; prepare automatically on first launch')
    parser.add_argument('--shader-cache', type=Path, default=None, help='SPIR-V cache directory to bundle with --standalone or push with developer --install')
    parser.add_argument('--passthrough', nargs='*', default=[], help='Extra arguments for the akari build.py')
    args = parser.parse_args()
    try:
        game = load_game(args.game)
        validate_registry(game.root)
        game.require_adapted()
        android_dir = game.build_dir / 'android'
        host_out = android_dir / 'host-out'
        android_dir.mkdir(parents=True, exist_ok=True)
        write_game_header(game, android_dir)
        manifest_path = android_dir / 'host-sources.json'
        manifest = build_manifest(game, android_dir, host_out)
        manifest_path.write_text(json.dumps(manifest, indent=1) + '\n')
        print('Manifest:', manifest_path)
        if args.manifest_only: return 0
        apk = android_dir / ('{}-android-{}.apk'.format(game.product_name, datetime.now().strftime('%Y%m%d-%H%M%S-%f')))
        command = [sys.executable, str(AKARI_DM / 'android/tools/build.py'), '--game-run',
                   '--sources-json', str(manifest_path), '--host-out', str(host_out),
                   '--label', game.display_name, '--package-name', game.bundle_id.lower(),
                   '--output-apk', str(apk)]
        if args.embedded or args.standalone:
            source = game.work_dir / 'patched-android'
            if not source.is_dir():
                raise WorkspaceError('Patched game modules do not exist: ' + str(source))
            command += ['--game-source', str(source)]
        if args.standalone or args.install:
            cache = args.shader_cache or (game.work_dir / 'shaders-android-akari/spirv')
            if not cache.is_dir():
                raise WorkspaceError('Shader cache does not exist: ' + str(cache))
            command += ['--shader-cache', str(cache)]
        if args.standalone:
            romfs = game.content_dir / 'RomFS'
            if not romfs.is_dir():
                raise WorkspaceError('Active RomFS does not exist: ' + str(romfs))
            command += ['--standalone', '--romfs', str(romfs)]
        if args.install:
            command.append('--install')
        command += args.passthrough
        env = dict(os.environ)
        env.setdefault('DM_ANDROID_TOOLS', str(PROJECT_ROOT / '.touchable/android-akari-tools'))
        result = subprocess.run(command, cwd=AKARI_DM, env=env)
        return result.returncode
    except (WorkspaceError, OSError) as error:
        print('Error:', error, file=sys.stderr)
        return 1


if __name__ == '__main__': sys.exit(main())
