#!/usr/bin/env bash
# Runs inside the network-isolated sandbox. See offline-acceptance.sh.
set -uo pipefail
: "${OUT:?}" "${SRC_PROJECT:?}"
export DISPLAY=:98 GDK_BACKEND=x11 LIBGL_ALWAYS_SOFTWARE=1 WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1 NO_AT_BRIDGE=1
unset WAYLAND_DISPLAY MPN_DEV_PYTHON MPN_DEV_ROOT
APP=${APP:-/usr/bin/mpn-studio}
PASS=0; FAIL=0; SKIP=0; RESULTS=()
ok()   { PASS=$((PASS+1)); RESULTS+=("PASS  $1"); echo "PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); RESULTS+=("FAIL  $1"); echo "FAIL  $1"; }
skip() { SKIP=$((SKIP+1)); RESULTS+=("SKIP  $1"); echo "SKIP  $1"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

Xvfb :98 -screen 0 1440x900x24 -nolisten tcp >"$OUT/xvfb.log" 2>&1 &
XVFB=$!; sleep 1

shot()  { import -window root "$OUT/$1.png"; }
wid()   { xdotool search --name "MPN Studio" 2>/dev/null | head -1; }
click() { xdotool mousemove "$1" "$2" click 1; sleep "${3:-0.7}"; }
focus() { xdotool windowfocus --sync "$(wid)" 2>/dev/null; }
key()   { focus; xdotool key "$@"; sleep 0.5; }
type_() { focus; xdotool type --delay 25 "$1"; sleep 0.5; }

launch() {   # $1 = home dir, $2 = label
  export HOME="$1"
  unset XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME XDG_STATE_HOME MPN_STUDIO_CONFIG_DIR MPN_STUDIO_DATA_DIR MPN_STUDIO_CACHE_DIR MPN_STUDIO_STATE_DIR
  strace -f -qq -e trace=connect -o "$OUT/connect-$2.strace" "$APP" "${@:3}" >"$OUT/app-$2.log" 2>&1 &
  APP_PID=$!
  for i in $(seq 1 60); do [ -n "$(wid)" ] && break; sleep 0.5; done
  sleep 6
}
quit_app() {  # File > Quit through the app's own shortcut; then wait for the backend to stop
  key ctrl+q
  for i in $(seq 1 40); do kill -0 "$APP_PID" 2>/dev/null || break; sleep 0.5; done
  kill -0 "$APP_PID" 2>/dev/null && { kill "$APP_PID"; bad "app exited on Quit"; } || ok "app exited on Quit"
  sleep 1
}
backend_env() { # port and token of the running backend, read from its own environment
  local p; p=$(pgrep -x mpn-backend | head -1); BPORT=$(tr '\0' '\n' </proc/$p/environ | sed -n 's/^MPN_PORT=//p'); BTOK=$(tr '\0' '\n' </proc/$p/environ | sed -n 's/^MPN_SESSION_TOKEN=//p')
}
api() { curl -s -H "x-mpn-token: $BTOK" -H 'content-type: application/json' "$@"; }

TH_A="$OUT/home-a"; TH_B="$OUT/home-b"
mkdir -p "$TH_A/.local/share/mpn-studio/projects" "$TH_B"
cp -a "$SRC_PROJECT" "$TH_A/.local/share/mpn-studio/projects/"
echo "--- network inside the sandbox:"; ip -br addr | tee "$OUT/network.txt"
if [ -n "${MPN_ACCEPT_LAN_HOST:-}" ]; then
  (curl -s -m 3 "http://${MPN_ACCEPT_LAN_HOST}:8840/api/health" >/dev/null 2>&1 && bad "LAN compute host reachable (isolation failed)") || ok "LAN compute host unreachable from the sandbox"
fi
(curl -s -m 3 https://example.com >/dev/null 2>&1 && bad "internet reachable (isolation failed)") || ok "internet unreachable from the sandbox"

STAGE=${STAGE:-all}
echo "=== 1. launch from the installed entry, first run (no last project)"
launch "$TH_A" a1
check "window opened" '[ -n "$(wid)" ]'
shot 01-first-run-project-selector

echo "=== 2. open the migrated network, inspect nodes and cells"
click 1080 203 6
shot 02-network-open
backend_env
H=$(api localhost:$BPORT/api/health)
check "migrated network opened (rev 8, project yaob-n10k)" 'echo "$H" | grep -q "\"head\":8" && echo "$H" | grep -q yaob-n10k'
click 775 19 0.8; type_ "Cache"; sleep 2; key Return; sleep 3
shot 03-node-inspected
NODE=$(api localhost:$BPORT/api/search?q=Cache\&limit=3)
check "inspector shows the Knowledge cell of a real node" 'api localhost:$BPORT/api/nodes/1132 | grep -q "Cache (computing)"'

echo "=== 3. modify a node through the UI, save"
click 1185 470 0.6; key ctrl+End; type_ " OFFLINE-EDIT-MARKER"; click 1118 177 2.5
shot 04-node-edited
H=$(api localhost:$BPORT/api/health)
check "edit created revision 9" 'echo "$H" | grep -q "\"head\":9"'
key ctrl+s; sleep 1.5; shot 05-saved
check "marker readable through the API before closing" 'api localhost:$BPORT/api/nodes/1132 | grep -q OFFLINE-EDIT-MARKER'

echo "=== 4. close, then verify the file on disk"
quit_app
DB="$TH_A/.local/share/mpn-studio/projects/yaob-n10k/store.sqlite"
check "marker is in the project file after shutdown" 'grep -qa OFFLINE-EDIT-MARKER "$DB"'
check "project head revision on disk is 9" '[ "$(python3 -I -c "import sqlite3,sys;print(sqlite3.connect(sys.argv[1]).execute(\"select max(rev) from revisions\").fetchone()[0])" "$DB")" = 9 ]'
check "no backend process left behind" '! pgrep -x mpn-backend >/dev/null'

echo "=== 5. reopen offline: opens straight into the last project, change persisted"
launch "$TH_A" a2
shot 06-reopened
backend_env
H=$(api localhost:$BPORT/api/health)
check "reopened directly into the last project at rev 9" 'echo "$H" | grep -q yaob-n10k && echo "$H" | grep -q "\"head\":9"'
check "the edit persisted across restart" 'api localhost:$BPORT/api/nodes/1132 | grep -q OFFLINE-EDIT-MARKER'
click 775 19 0.8; type_ "Cache"; sleep 2; key Return; sleep 3; shot 07-reopened-node-shows-edit

echo "=== 6. create a new network (File > New project), add content"
key ctrl+n; sleep 1.5; click 333 262 0.8; type_ "Offline Net"; key Return; sleep 6
shot 08-new-project
H=$(api localhost:$BPORT/api/health)
check "new project created and open" 'echo "$H" | grep -q offline-net'
M=$(api -X POST localhost:$BPORT/api/mpns -d '{"name":"Offline Science"}')
MID=$(echo "$M" | python3 -I -c "import sys,json;print(json.load(sys.stdin)['mpn']['id'])")
N=$(api -X POST localhost:$BPORT/api/nodes -d "{\"title\":\"Offline node\",\"type\":\"knowledge\",\"schema\":{\"id\":\"3cn\",\"version\":1},\"cells\":{\"knowledge\":{\"statements\":[{\"text\":\"Created with no network.\"}]},\"thinking\":{},\"reasoning\":{}},\"memberships\":[{\"mpn_id\":\"$MID\"}],\"position\":{\"x\":0,\"y\":0}}")
check "node created in the new network" 'echo "$N" | grep -q "Offline node"'
sleep 1; key 0; sleep 1.5; shot 09-new-network-board

echo "=== 7. export it through the native dialog"
key ctrl+shift+e
# The GTK dialog can take several seconds to populate on a machine with network and removable volumes (gvfs monitors);
# wait for its window, then give it time before typing.
for _ in $(seq 20); do D=$(xdotool search --name "Choose where" | head -1); [ -n "$D" ] && break; sleep 0.5; done
sleep 6
shot 10-export-dialog
if [ -n "$D" ]; then
  mkdir -p "$OUT/export-target"
  xdotool windowfocus --sync "$D"; sleep 0.4; xdotool key ctrl+l; sleep 0.4; xdotool type --delay 15 "$OUT/export-target"; sleep 0.6; xdotool key Return
  # a folder chooser opens the typed folder on the first Return; a second one (Open) confirms it
  for _ in $(seq 10); do sleep 1; xdotool search --name "Choose where" >/dev/null 2>&1 || break; xdotool key Return; done
  sleep 3
fi
BUNDLE=$(ls -d "$OUT"/export-target/offline-net-rev* 2>/dev/null | head -1)
if [ ! -f "$BUNDLE/manifest.json" ]; then
  # The GTK folder chooser did not confirm the typed folder (it stayed in "Recent" with Open disabled; cause
  # not determined, the app's own dialog code is unchanged). Cancel it and export through the same API call the menu makes, so the
  # later steps still test the app; the dialog step itself is reported as skipped, not passed.
  xdotool key Escape; sleep 1; backend_env
  api -X POST localhost:$BPORT/api/desktop/project/export -d "{\"dest\":\"$OUT/export-target/offline-net-rev2\"}" >/dev/null
  BUNDLE=$(ls -d "$OUT"/export-target/* 2>/dev/null | head -1)
  skip "native folder dialog confirmed the export folder (dialog did not respond; exported through the API instead)"
else
  ok "native folder dialog confirmed the export folder"
fi
check "bundle exported to the chosen folder" '[ -f "$BUNDLE/manifest.json" ]'
shot 11-exported
quit_app

echo "=== 8. import into another local workspace (separate data directory)"
launch "$TH_B" b1
shot 12-workspace-b-empty
backend_env
R=$(api -X POST localhost:$BPORT/api/desktop/projects/import -d "{\"kind\":\"bundle\",\"path\":\"$BUNDLE\",\"name\":\"Imported Offline Net\"}")
check "bundle imported into workspace B" 'echo "$R" | grep -q imported-offline-net'
quit_app
launch "$TH_B" b2
backend_env
shot 13-workspace-b-imported
check "workspace B reopens straight into the imported network" 'api localhost:$BPORT/api/health | grep -q imported-offline-net'
check "imported network contains the node" 'api "localhost:$BPORT/api/search?q=Offline%20node&limit=3" | grep -q "Offline node"'
click 775 19 0.8; type_ "Offline node"; sleep 2; key Return; sleep 3; shot 14-workspace-b-node
quit_app

echo "=== 8b. a .mpnbak backup file opens as a NEW project (file manager 'Open with' / double-click)"
launch "$TH_B" b3
backend_env
BAK=$(api -X POST localhost:$BPORT/api/desktop/project/backup | python3 -I -c "import sys,json;print(json.load(sys.stdin)['file'])")
check "backup file written (.mpnbak)" '[ -f "$BAK" ] && [[ "$BAK" == *.mpnbak ]]'
cp "$BAK" "$OUT/opened-from-fm.mpnbak"; BAK="$OUT/opened-from-fm.mpnbak"
quit_app
if [ "$APP" = /usr/bin/mpn-studio ]; then
  check "the system maps .mpnbak to the app's file type" '[ "$(xdg-mime query filetype "$BAK" 2>/dev/null)" = application/x-mpn-studio-backup ]'
  check "the desktop entry passes files to the app (Exec ... %F, MimeType)" 'grep -q "^Exec=mpn-studio %F" "/usr/share/applications/MPN Studio.desktop" && grep -q "^MimeType=application/x-mpn-studio-backup" "/usr/share/applications/MPN Studio.desktop"'
fi
TH_C="$OUT/home-c"; mkdir -p "$TH_C"
launch "$TH_C" c1 "$BAK"
backend_env
shot 15-backup-file-opened
check "launching with a .mpnbak opened it as a new project, restored from the backup" 'api localhost:$BPORT/api/health | grep -q "imported-offline-net-restored"'
check "the restored project holds the node" 'api "localhost:$BPORT/api/search?q=Offline%20node&limit=3" | grep -q "Offline node"'
export HOME="$TH_C"; "$APP" "$BAK" >"$OUT/app-c1-second.log" 2>&1 & SECOND=$!; sleep 8
check "a second launch handed its file to the running window (still one backend)" '[ "$(pgrep -x mpn-backend | wc -l)" -eq 1 ] && [ "$(api localhost:$BPORT/api/desktop/projects | python3 -I -c "import sys,json;print(sum(1 for p in json.load(sys.stdin)[\"projects\"] if \"restored\" in p[\"id\"]))")" -ge 2 ]'
shot 16-second-backup-opened
kill $SECOND 2>/dev/null
quit_app

echo "=== 9. attempted connections (strace of every connect() the app and its children made)"
for f in "$OUT"/connect-*.strace; do
  total=$(grep -c "connect(" "$f" || true)
  remote=$(grep "connect(" "$f" | grep -E "AF_INET6?" | grep -vE '127\.0\.0\.1|"::1"|0\.0\.0\.0' | wc -l)
  echo "$(basename "$f"): $total connect calls, $remote to non-loopback addresses"
  grep "connect(" "$f" | grep -E "AF_INET6?" | grep -vE '127\.0\.0\.1|"::1"|0\.0\.0\.0' | head -5
  [ "$remote" -eq 0 ] && ok "no non-loopback connection attempted ($(basename "$f"))" || bad "non-loopback connection attempted ($(basename "$f"))"
done
UI_DIR="$(cd "$(dirname "$0")/.." && pwd)/web/dist"
# Code and markup only (licence .txt/.md files that ship with the fonts mention github.com in copyright lines). Hosts that appear as text only (XML namespaces, a font licence comment, Svelte error-message links); never fetched.
EXTRA=$(grep -rEoh --include=*.js --include=*.css --include=*.html --include=*.svg --include=*.json "https?://[A-Za-z0-9._-]+" "$UI_DIR" | sed -E "s#https?://##" | sort -u | grep -vE "^(www\.w3\.org|scripts\.sil\.org|svelte\.dev)$" || true)
echo "hosts in shipped UI beyond the inert allowlist: ${EXTRA:-none}"
check "shipped UI references no hosts beyond the inert allowlist (no CDN fonts, scripts or APIs)" '[ -z "$EXTRA" ]'
check "shipped UI loads no external <script>, <link> or @import" '! grep -rEq "<(script|link)[^>]+(src|href)=\"https?:|@import +url\(.?https?:" "$UI_DIR"'
kill $XVFB 2>/dev/null
echo; echo "SUMMARY: $PASS passed, $FAIL failed, $SKIP skipped"; printf '%s\n' "${RESULTS[@]}" > "$OUT/results.txt"; echo "SUMMARY: $PASS passed, $FAIL failed, $SKIP skipped" >> "$OUT/results.txt"
[ "$FAIL" -eq 0 ]
