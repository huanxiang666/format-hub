#!/usr/bin/env python3
"""Vendor Homebrew Cairo/LibreDWG closure; never alter the upstream Office app."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = ('/usr/lib/', '/System/Library/')
MAGIC = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def macho(path):
    if not path.is_file() or path.is_symlink():
        return False
    with path.open('rb') as stream:
        return stream.read(4) in MAGIC


def load_commands(path):
    dependencies, rpaths = [], []
    command = ''
    loads = {'LC_LOAD_DYLIB', 'LC_LOAD_WEAK_DYLIB', 'LC_REEXPORT_DYLIB', 'LC_LOAD_UPWARD_DYLIB', 'LC_LAZY_LOAD_DYLIB'}
    for line in run('otool', '-l', str(path)).splitlines():
        line = line.strip()
        if line.startswith('cmd '):
            command = line.split()[1]
        elif command in loads and line.startswith('name '):
            dependencies.append(line[5:].rsplit(' (offset ', 1)[0])
        elif command == 'LC_RPATH' and line.startswith('path '):
            rpaths.append(line[5:].rsplit(' (offset ', 1)[0])
    return dependencies, rpaths


def dependencies(path):
    return load_commands(path)[0]


def resolve_dependency(source, dependency, rpaths, executable_dir):
    def expand(value):
        return Path(value.replace('@loader_path', str(source.parent)).replace('@executable_path', str(executable_dir)))
    if dependency.startswith('@rpath/'):
        suffix = dependency.removeprefix('@rpath/')
        candidates = [expand(path) / suffix for path in rpaths]
    elif dependency.startswith(('/', '@loader_path/', '@executable_path/')):
        candidates = [expand(dependency)]
    else:
        raise RuntimeError(f'Unresolved native load command {source}: {dependency}')
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(f'Native dependency missing: {source}: {dependency}; searched {candidates}')


def audit_office(app):
    count = 0
    for path in app.rglob('*'):
        if macho(path):
            count += 1
            for dependency in dependencies(path):
                if dependency.startswith('/') and not dependency.startswith(SYSTEM):
                    raise RuntimeError(f'Office external non-system dependency: {path}: {dependency}')
    if not count:
        raise RuntimeError('Office bundle contains no Mach-O executables')
    run('codesign', '--verify', '--deep', '--strict', str(app))
    return count


def main():
    if sys.platform != 'darwin':
        raise RuntimeError('Run this staging script on macOS')
    runtime = ROOT / 'runtime'
    native = runtime / 'native'
    licenses = runtime / 'native-licenses'
    native.mkdir(parents=True, exist_ok=True)
    licenses.mkdir(parents=True, exist_ok=True)
    roots = [Path(run('brew', '--prefix', name)) for name in ('cairo', 'libredwg')]
    pending = [roots[0] / 'lib/libcairo.2.dylib', roots[1] / 'bin/dwg2dxf']
    copies = {}
    edges = {}
    while pending:
        source = pending.pop().resolve()
        if source in copies:
            continue
        target = runtime / 'dwg2dxf' if source.name == 'dwg2dxf' else native / source.name
        if target.exists():
            raise RuntimeError(f'Native filename collision: {target}')
        shutil.copy2(source, target)
        copies[source] = target
        edges[source] = []
        loads, rpaths = load_commands(source)
        for dependency in loads:
            if dependency.startswith(SYSTEM):
                continue
            resolved = resolve_dependency(source, dependency, rpaths, roots[1] / 'bin')
            edges[source].append((dependency, resolved))
            pending.append(resolved)
    for source, target in copies.items():
        target.chmod(0o755)
        if target.suffix == '.dylib':
            run('install_name_tool', '-id', '@loader_path/' + target.name, str(target))
        for original, dependency in edges[source]:
            name = copies[dependency].name
            replacement = ('@executable_path/native/' if target.name == 'dwg2dxf' else '@loader_path/') + name
            run('install_name_tool', '-change', original, replacement, str(target))
        run('codesign', '--force', '--sign', '-', str(target))
    # Preserve recipe, exact bottle/source provenance and original license files.
    package_info = json.loads(run('brew', 'info', '--json=v2', '--installed'))
    (licenses / 'homebrew-installed.json').write_text(json.dumps(package_info, indent=2) + '\n')
    for formula in package_info['formulae']:
        keg = Path(run('brew', '--prefix', formula['name']))
        for path in keg.rglob('*'):
            if path.is_file() and path.name.upper().startswith(('COPYING', 'LICENSE', 'NOTICE', 'AUTHORS')):
                dest = licenses / formula['name'] / path.relative_to(keg)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
    audit = {'office_macho_files': audit_office(runtime / 'LibreOffice.app'),
             'native_files': [str(p.relative_to(ROOT)) for p in copies.values()],
             'dwg_version': run(str(runtime / 'dwg2dxf'), '--version'),
             'signing': 'Homebrew native closure ad-hoc signed after relocation; original LibreOffice signature verified unchanged'}
    (licenses / 'macos-native-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    print(json.dumps(audit, indent=2))


if __name__ == '__main__':
    main()
