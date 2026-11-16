#!/usr/bin/env bash
set -euo pipefail
FORMAT_HUB_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$FORMAT_HUB_ROOT"
if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python -r requirements.txt
  else
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
  fi
fi
if [ "$(uname -s)" = Linux ] && [ ! -f runtime/liboffice-ipc.so ]; then
  if command -v cc >/dev/null 2>&1; then
    cc -shared -fPIC -O2 runtime/office-ipc.c -ldl -o runtime/liboffice-ipc.so
  else
    echo '缺少C编译器，Linux隔离Office转换需要先编译runtime/office-ipc.c。' >&2
    exit 1
  fi
fi
exec .venv/bin/python run.py "$@"
