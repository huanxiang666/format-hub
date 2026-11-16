#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"
export FORMAT_HUB_OFFLINE=1
open 'http://127.0.0.1:8765'
exec "$ROOT/format-hub" --offline --host 127.0.0.1
