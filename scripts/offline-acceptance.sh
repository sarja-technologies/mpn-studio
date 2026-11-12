#!/usr/bin/env bash
# Offline acceptance test for the INSTALLED MPN Studio (/usr/bin/mpn-studio).
#
# Runs the app inside a private network namespace (bubblewrap --unshare-net): the only interface is loopback, so
# a LAN compute host (set MPN_ACCEPT_LAN_HOST to check one) and the internet are unreachable. The host's networking is not touched.
# Everything is recorded: screenshots of the real window, the database file, and every connect() the app makes (strace).
#
#   scripts/offline-acceptance.sh [outdir]
set -euo pipefail
OUT=${1:-$PWD/build/acceptance}
SRC_PROJECT=${SRC_PROJECT:-$HOME/.local/share/mpn-studio/projects/yaob-n10k}   # the migrated network (copied, never modified)
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
rm -rf "$OUT"/*
exec bwrap --dev-bind / / --unshare-net --setenv OUT "$OUT" --setenv SRC_PROJECT "$SRC_PROJECT" \
     dbus-run-session -- bash "$(dirname "$0")/offline-acceptance-inner.sh"
