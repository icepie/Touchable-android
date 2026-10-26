#!/usr/bin/env python3
"""Translate captured Switch shaders to validated Vulkan SPIR-V modules for Android.

Two output contracts share one capture source:

* touchable: per-chain directories with a manifest. Keep this as the baseline
  for tool comparison. It does not run on the akari host.
* akari: a flat cache of <key>.spv plus binary <key>.meta files, produced by the
  pinned akari-android translator. The Android host loads this cache as
  $DM_VK_SHADERS. The key rule is the capture key (control plus code when the
  profile sets SS_SHADER_KEY_CODE); the akari key derivation is patched to match.

For lower-left NVN window origins, translated shaders also invert fragment-coordinate
Y reads. The Vulkan host uses bottom-up render targets and flips only at presentation.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys

from build_shaders import XLATE, run, shader_job
from shader_keys import functional_key
from workspace import DEPENDENCIES, ROOT, GameError, load_game


def validator_path(explicit):
    if explicit:
        found = Path(explicit)
        if not found.is_file():
            raise GameError('The SPIR-V validator was not found: ' + str(found))
        return found
    env = os.environ.get('D2R_SHADER_TOOLS')
    if env:
        found = Path(env) / 'spirv-val'
        if found.is_file():
            return found
    ndk = os.environ.get('ANDROID_NDK_HOME')
    roots = [Path(ndk)] if ndk else []
    sdk = DEPENDENCIES.parent / 'android-sdk'
    roots.extend(sorted((sdk / 'ndk').glob('*')))
    for root in roots:
        for found in sorted(root.glob('shader-tools/*/spirv-val')):
            if found.is_file():
                return found
    raise GameError('No spirv-val was found. Set D2R_SHADER_TOOLS or ANDROID_NDK_HOME.')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tool_fingerprint(config, validator, translator_source):
    names = [Path(__file__), Path(__file__).with_name('extract_shaders.py'),
             Path(__file__).with_name('shader_keys.py'), Path(__file__).with_name('build_shaders.py'),
             XLATE / 'dmsh_xlate.dll', XLATE / 'Ryujinx.Graphics.Shader.dll', translator_source]
    fingerprint = {p.name: digest(p) for p in names}
    fingerprint['spirv-val'] = run([validator, '--version']).strip().splitlines()[0]
    fingerprint['target'] = 'vulkan1.1'
    options = {key: config.profile[key] for key in ('nvn_window_origin', 'vertex_attribute_types',
               'nvn_bindings', 'shader_functional_aliases', 'shader_exefs') if key in config.profile}
    if options:
        fingerprint['options'] = json.dumps(options, sort_keys=True)
    return fingerprint


def build_akari(args, jobs, out, work, validator, y_negate):
    """Write akari-contract jobs and run the pinned translator driver.

    The driver at dm/android/tools/build_shaders.py builds its own patched
    translator, emits <key>.spv plus binary <key>.meta files, and writes
    host-subgroup-size.txt. Its cache directory is <out>/spirv. The job keys
    are the capture keys; the host key derivation is patched to match, so do
    not let the driver re-key anything (--reuse-jobs passes keys through).
    """
    sets = dict(vs=0, ps=1, gs=2, hs=3, ds=4, cs=0)
    converted = []
    for job in jobs:
        if job['stage'] not in sets:
            raise GameError('The akari host has no pipeline stage ' + job['stage'] + '.')
        entry = dict(key=job['key'], stage=job['stage'], code=f"{job['key']}.bin",
                     c1=job['c1'], tex=job['tex'], img=job['img'], attr=job['attr'],
                     fout=job['fout'], debug=False, reduced=False,
                     set=sets[job['stage']], host_subgroup_size=args.host_subgroup_size,
                     y_negate=y_negate, shared=job.get('shared', 0))
        if 'local' in job:
            entry['local'] = job['local']
        converted.append(entry)
    work.mkdir(parents=True, exist_ok=True)
    (work / 'jobs.json').write_text(json.dumps(converted, indent=2) + '\n')
    # shader_job() already wrote each {key}.bin next to the jobs file.
    driver = args.akari / 'dm/android/tools/build_shaders.py'
    if not driver.is_file():
        raise GameError('No akari-android tree at ' + str(args.akari) + '.')
    opt = validator.with_name('spirv-opt')
    command = [sys.executable, driver, out, '--reuse-jobs', work / 'jobs.json',
               '--dotnet', args.dotnet, '--host-subgroup-size', str(args.host_subgroup_size),
               '--jobs', str(args.jobs), '--validate', '--spirv-val', validator, '--spirv-opt', opt]
    if args.no_driver_variants:
        command.append('--no-driver-variants')
    print(run(command), flush=True)
    cache = out / 'spirv'
    spv = sorted(cache.glob('*.spv'))
    if len(spv) < len(converted):
        raise GameError('The akari driver produced ' + str(len(spv)) + ' modules for '
                        + str(len(converted)) + ' shaders.')
    missing_meta = [p.stem for p in spv if not p.with_suffix('.meta').is_file()]
    if missing_meta:
        raise GameError('Missing reflection for: ' + ', '.join(missing_meta) + '.')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=8, help='Parallel translation jobs.')
    parser.add_argument('--game', default='silksong', help='Registered game ID.')
    parser.add_argument('--capture', type=Path, help='Default: work/<game>/shaders/capture.')
    parser.add_argument('--out', type=Path, help='Default: work/<game>/shaders-android.')
    parser.add_argument('--validator', type=Path, help='Default: D2R_SHADER_TOOLS/spirv-val or the NDK shader-tools.')
    parser.add_argument('--contract', choices=('touchable', 'akari'), default='touchable',
                        help='Output contract. akari builds the cache the Android host loads.')
    parser.add_argument('--akari', type=Path, default=DEPENDENCIES / 'akari-android',
                        help='Pinned akari-android tree with the patched translator.')
    parser.add_argument('--dotnet', type=Path, default=Path('/usr/bin/dotnet'),
                        help='dotnet for the akari translator build (net10.0).')
    parser.add_argument('--host-subgroup-size', type=int, choices=(32, 64, 128), default=64,
                        help='Device Vulkan subgroup size for the akari contract (default 64, Adreno 6xx).')
    parser.add_argument('--no-driver-variants', action='store_true',
                        help='Skip the akari per-driver SPIR-V variants.')
    args = parser.parse_args()
    config = load_game(args.game)
    if config.require_adapted('shader_build') not in ('unity-nvn', 'nvn'):
        raise GameError('No shader build implementation is registered for ' + config.id + '.')
    validator = validator_path(args.validator)
    capture = (args.capture or config.capture_dir).resolve()
    default_out = 'shaders-android-akari' if args.contract == 'akari' else 'shaders-android'
    out = (args.out or config.work_dir / default_out).resolve()
    controls = sorted(capture.glob('*.control'))
    if not controls:
        raise GameError('No captured shaders were found: ' + str(capture))
    work = out / ('jobs-src' if args.contract == 'akari' else 'translate')
    work.mkdir(parents=True, exist_ok=True)

    translator_source = args.akari / 'dm/gfx/tools/dmsh_xlate/Program.cs' if args.contract == 'akari' else XLATE / 'dmsh_xlate.dll'
    fingerprint = tool_fingerprint(config, validator, translator_source)
    origin = config.profile.get('nvn_window_origin', 'upper-left')
    if origin not in ('upper-left', 'lower-left'):
        raise GameError('profile.nvn_window_origin must be upper-left or lower-left.')
    lower_left = origin == 'lower-left'
    if lower_left != ('SS_NVN_LOWER_LEFT_ORIGIN' in config.compile_defines):
        raise GameError('profile.nvn_window_origin lower-left requires the SS_NVN_LOWER_LEFT_ORIGIN compile define, and only it.')
    chain = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()[:12]
    code_key = 'SS_SHADER_KEY_CODE' in config.compile_defines
    jobs = [shader_job(path, work, code_key) for path in controls]
    attribute_types = config.profile.get('vertex_attribute_types', {})
    if any(not re.fullmatch(r'\d+', k) or v not in ('float', 'sint', 'uint') for k, v in attribute_types.items()):
        raise GameError('profile.vertex_attribute_types maps a location to float, sint, or uint.')
    for job in jobs:
        if job['stage'] == 'vs':
            job['attr'] = dict(attribute_types)

    if args.contract == 'akari':
        build_akari(args, jobs, out, work, validator, lower_left)
        return
    jobs_file = work / 'jobs.json'
    jobs_file.write_text(json.dumps(jobs, indent=2) + '\n')
    print(run([DEPENDENCIES / 'dotnet/dotnet', XLATE / 'dmsh_xlate.dll', jobs_file, work]).strip(), flush=True)
    # TEXS/SULD/SUST encode dimensions. Reuse those dimensions for image TXQ.
    changed = []
    for job in jobs:
        resources = json.loads((work / f'{job["key"]}.res.json').read_text())
        image_types = {f'{r["handle"]:X}': r['type'] for r in resources['images']
                       if r['cbuf_slot'] == 2 and 0x120 <= (r['handle'] & 0x3fff) * 4 < 0x168}
        if image_types:
            job['handle_types'] = image_types
            changed.append(job)
    if changed:
        followup = work / 'image-query-jobs.json'
        followup.write_text(json.dumps(changed, indent=2) + '\n')
        print(run([DEPENDENCIES / 'dotnet/dotnet', XLATE / 'dmsh_xlate.dll', followup, work]).strip(), flush=True)
    target = out / f'b-{chain}'
    target.mkdir(parents=True, exist_ok=True)
    entries = []
    for job in jobs:
        key = job['key']
        spv = work / f'{key}.spv'
        if not spv.is_file():
            raise GameError('The translator produced no module: ' + key)
        run([validator, '--target-env', 'vulkan1.1', spv])
        deployed = target / f'{key}.spv'
        deployed.write_bytes(spv.read_bytes())
        resources = json.loads((work / f'{key}.res.json').read_text())
        reflection = dict(key=key, chain=chain, dm_ctl=key, source='Switch NVN capture',
                          stage=job['stage'], c1=job['c1'], threadgroup_size=job.get('local'),
                          origin=origin, resources=resources)
        (target / f'{key}.json').write_text(json.dumps(reflection, indent=2) + '\n')
        entries.append(dict(key=key, stage=job['stage'], spv=f'{key}.spv', sha256=digest(deployed)))
    (out / 'manifest.json').write_text(json.dumps(dict(game_id=config.id, chain=chain,
        origin=origin, target='vulkan1.1', tools=fingerprint, shaders=entries), indent=2) + '\n')
    # The runtime matches these stage values in shader_map.tsv. Do not translate them.
    names = dict(vs='顶点', ps='像素', cs='计算')
    rows = []
    for job in jobs:
        key = job['key']
        alias = functional_key((capture / (key + '.control')).read_bytes(),
                               (capture / (key + '.code')).read_bytes()) if config.profile.get('shader_functional_aliases') else ''
        rows.append(f'capture\t{key}\t{names[job["stage"]]}\t{key}\t{alias}\n')
    (out / 'shader_map.tsv').write_text('package\tname\tstage\tkey\tfunctional_key\n' + ''.join(rows))
    print(f'Built {len(entries)} Switch shaders for Vulkan. Chain: {chain}')


if __name__ == '__main__':
    try:
        sys.exit(main() or 0)
    except (GameError, OSError, RuntimeError, ValueError) as error:
        sys.exit('Error: ' + str(error))
