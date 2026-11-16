#!/usr/bin/env python3
"""Reassemble the project-local LibreOffice runtime without root.

Usage: python3 setup_office.py [--download]
The existing runtime/packages/*.deb files are the offline installation source.
--download resolves missing dependencies against this Ubuntu host using apt-get
simulation, then uses apt download (never apt install). Packages are unpacked
with dpkg-deb -x; maintainer scripts and system configuration are never changed.
The runtime/soffice launcher is version-controlled separately from the packages.
This assembly uses the host's installed glibc/ICU and other already installed
libraries; it targets Ubuntu 26.04 amd64, rather than a fully portable bundle.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
PACKAGES = RUNTIME / "packages"
OFFICE = RUNTIME / "office"
MODULES = ["libreoffice-writer", "libreoffice-calc", "libreoffice-impress", "libreoffice-draw"]
ENV = {**os.environ, "LC_ALL": "C"}


def output(args):
    return subprocess.run(args, check=True, text=True, capture_output=True, env=ENV).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Download Ubuntu apt candidates and missing dependencies")
    args = parser.parse_args()
    PACKAGES.mkdir(parents=True, exist_ok=True)
    if args.download or not any(PACKAGES.glob("*.deb")):
        plan = output(["apt-get", "-s", "--no-install-recommends", "install", *MODULES])
        resolved = dict(re.findall(r"^Inst (\S+) \((\S+)", plan, re.M))
        for name in MODULES:
            if name not in resolved:
                policy = output(["apt-cache", "policy", name])
                match = re.search(r"Candidate: (\S+)", policy)
                if not match or match.group(1) == "(none)":
                    raise RuntimeError(f"No apt candidate for {name}")
                resolved[name] = match.group(1)
        subprocess.run(["apt", "download", *[f"{name}={version}" for name, version in resolved.items()]], cwd=PACKAGES, env=ENV, check=True)
    archives = sorted(PACKAGES.glob("*.deb"))
    if not archives:
        raise RuntimeError("No Debian packages are available")
    OFFICE.mkdir(exist_ok=True)
    metadata = []
    for archive in archives:
        fields = output(["dpkg-deb", "-f", str(archive), "Package", "Version", "Architecture"])
        package = dict(line.split(": ", 1) for line in fields.splitlines())
        subprocess.run(["dpkg-deb", "-x", str(archive), str(OFFICE)], check=True)
        package.update(file=archive.name, sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
        show = subprocess.run(["apt-cache", "show", f"{package['Package']}={package['Version']}"], text=True, capture_output=True, env=ENV)
        if show.returncode:
            package["source_lookup_error"] = show.stderr.strip()
        else:
            match = re.search(r"^Filename: (.+)$", show.stdout, re.M)
            if match:
                package["download_url"] = "http://archive.ubuntu.com/ubuntu/" + match.group(1)
        metadata.append(package)
    registry = OFFICE / "usr/lib/libreoffice/share/registry/main.xcd"
    if registry.is_symlink():
        registry.unlink()
    elif registry.exists():
        raise RuntimeError(f"Expected a package symlink at {registry}")
    registry.symlink_to("../.registry/main.xcd")
    if not registry.is_file():
        raise RuntimeError(f"Registry source missing: {registry.resolve()}")
    wrapper = RUNTIME / "soffice"
    wrapper.chmod(0o755)
    version = output([str(wrapper), "--version"]).strip()
    manifest = {
        "method": "apt download; dpkg-deb -x; relative main.xcd symlink; runtime/soffice environment wrapper",
        "target": "Ubuntu 26.04 amd64 with host system libraries",
        "modules": MODULES,
        "version": version,
        "packages": metadata,
        "known_limitations": ["liblangtag 0.6.8 has a compiled /usr/share/liblangtag data path and may emit warnings; DOCX to PDF was verified successfully."],
    }
    path = PACKAGES / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(version)
    print(f"Packages: {len(archives)}; manifest: {path}")
    print(f"Launcher: {wrapper}")


if __name__ == "__main__":
    main()
