#!/usr/bin/env bash
# Test J: drive the packaged app in a real X11 window (Xvfb + xdotool), the way a person would, and check every step
# through the backend API. Needs Xvfb, xdotool, ImageMagick (import) and curl. Usage:
#   OUT=/tmp/wa APP=./MPN-Studio_0.4.0_x86_64.AppImage SRC_PROJECT=<a 0.3 project folder> REMOTE=http://mpn-host.local:8844 scripts/window-acceptance.sh
# Coordinates are for the 1440x900 window the app opens with. Origin search terms assume a Wikidata-based network.
set -uo pipefail
: "${OUT:?}" "${APP:?}" "${SRC_PROJECT:?}" "${REMOTE:?address of a computer serving the large network, e.g. http://mpn-host.local:8844}"
export DISPLAY=:97 GDK_BACKEND=x11 LIBGL_ALWAYS_SOFTWARE=1 WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1 NO_AT_BRIDGE=1
unset WAYLAND_DISPLAY MPN_DEV_PYTHON MPN_DEV_ROOT
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "PASS  $1" | tee -a "$OUT/results.txt"; }
bad() { FAIL=$((FAIL+1)); echo "FAIL  $1" | tee -a "$OUT/results.txt"; }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }
Xvfb :97 -screen 0 1440x900x24 -nolisten tcp >"$OUT/xvfb.log" 2>&1 & XVFB=$!; sleep 1
shot()  { import -window root "$OUT/$1.png"; }
wid()   { xdotool search --name "MPN Studio" 2>/dev/null | head -1; }
focus() { xdotool windowfocus --sync "$(wid)" 2>/dev/null; }
click() { xdotool mousemove "$1" "$2" click 1; sleep "${3:-0.8}"; }
dbl()   { xdotool mousemove "$1" "$2" click --repeat 2 --delay 80 1; sleep "${3:-3}"; }
key()   { focus; xdotool key "$@"; sleep 0.5; }
type_() { focus; xdotool type --delay 20 "$1"; sleep 0.4; }
backend_env(){ local p; p=$(pgrep -x mpn-backend | head -1); BPORT=$(tr '\0' '\n' </proc/$p/environ | sed -n 's/^MPN_PORT=//p'); BTOK=$(tr '\0' '\n' </proc/$p/environ | sed -n 's/^MPN_SESSION_TOKEN=//p'); }
api() { curl -s -m 20 -H "x-mpn-token: $BTOK" -H 'content-type: application/json' "http://127.0.0.1:$BPORT$1" "${@:2}"; }
js()  { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
launch(){ export HOME="$TH"; unset XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME XDG_STATE_HOME MPN_STUDIO_DATA_DIR MPN_STUDIO_CONFIG_DIR MPN_STUDIO_CACHE_DIR MPN_STUDIO_STATE_DIR
  "$APP" >"$OUT/app-$1.log" 2>&1 & APP_PID=$!
  for i in $(seq 1 90); do [ -n "$(wid)" ] && break; sleep 0.5; done; sleep 8; backend_env; }
quit_app(){ key ctrl+q; for i in $(seq 1 40); do kill -0 "$APP_PID" 2>/dev/null || break; sleep 0.5; done
  if kill -0 "$APP_PID" 2>/dev/null; then kill "$APP_PID"; bad "app exits on Ctrl+Q"; else ok "app exits on Ctrl+Q"; fi; sleep 1; }
nnode(){ click 720 450 0.5; key n; sleep 0.8; type_ "$1"; key Tab Tab Tab; type_ "$2"; key ctrl+Return; sleep 2; }

TH="$OUT/home"; mkdir -p "$TH/.local/share/mpn-studio/projects"; cp -a "$SRC_PROJECT" "$TH/.local/share/mpn-studio/projects/"
: >"$OUT/results.txt"

echo "=== 1 first launch: home screen"
launch 1
check "window opened" '[ -n "$(wid)" ]'
check "backend answers" 'api /api/health | grep -q "\"ok\""'
shot 01-home

echo "=== 2 Test A in the window: blank project, nodes, connect, save, close, reopen"
click 236 85 1; shot 02-create; type_ "Window Plants"; key Return; sleep 4; shot 03-blank
check "blank project created and open" 'api /api/projects | grep -q "Window Plants"'
nnode "Photosynthesis" "Plants turn light, water and carbon dioxide into sugar and oxygen."
nnode "Chlorophyll" "Chlorophyll is the green pigment that absorbs light for photosynthesis."
shot 04-two-nodes
N=$(api "/api/search?q=Chlorophyll&limit=5"); echo "$N" > "$OUT/search-chl.json"
check "two nodes created through the window" 'api "/api/search?q=Photosynthesis&limit=5" | grep -q Photosynthesis && echo "$N" | grep -q Chlorophyll'
key ctrl+s; sleep 1.5
click 1176 20 1.2; type_ "Two plant facts"; key Return; sleep 2; shot 05-versions; key Escape; sleep 1
check "version saved through the window" 'api /api/versions | grep -q "Two plant facts"'
click 1394 20 0.8; shot 06-more; click 1312 533 3; shot 07-closed-home
dbl 326 159 4; shot 08-reopened
check "reopened project still has its nodes" 'api "/api/search?q=Chlorophyll&limit=5" | grep -q Chlorophyll'

echo "=== 3 Test B/I: open the 0.3 N10K project from the home screen"
click 67 20 2; shot 09-home2
NAME10K=$(python3 -c "import json;print(json.load(open('$SRC_PROJECT/project.json'))['name'])")
api /api/projects > "$OUT/projects-1.json"
# card order is most recently modified first: N10K is second
dbl 703 159 6; shot 10-n10k
check "N10K project opened ($NAME10K)" 'api /api/health | grep -q "yaob-n10k"'

echo "=== 4 Test C: link Yaob Origin by address, search, inspect"
click 67 20 2; click 385 85 1; click 686 491 0.3; type_ "$REMOTE"; click 953 491 4; shot 11-probe; click 915 584 6; shot 12-origin
check "Origin linked project created" 'api /api/projects | python3 -c "import json,sys; print([p for p in json.load(sys.stdin)[\"projects\"] if p.get(\"kind\")==\"linked\"])" | grep -q linked'
api /api/ws/state > "$OUT/ws-origin.json"
check "Origin reports 119M nodes" 'grep -qE "1191768[0-9]{2}" "$OUT/ws-origin.json"'
click 150 64 0.3; type_ "Douglas Adams"; sleep 3; shot 13-search; click 150 196 3; shot 14-node
check "search answers in the window session" 'api "/api/ws/search?q=Douglas%20Adams&limit=5" | grep -q "Douglas Adams"'

echo "=== 5 Test D: Duplicate to edit, edit, original unchanged"
click 1287 20 1.5; shot 15-dup; click 720 562 0.3; key ctrl+a; type_ "Window Fork"; key Return; sleep 6; shot 16-fork
check "fork project created and open" 'api /api/ws/state | grep -q "\"fork\""'
click 150 64 0.3; key ctrl+a; type_ "Douglas Adams"; sleep 3; click 150 196 3
click 1118 121 1; shot 17-editform; key Tab; key ctrl+End; type_ " WINDOW-EDIT-MARKER"; key ctrl+Return; sleep 3; shot 18-edited
api "/api/ws/search?q=Douglas%20Adams&limit=1" > "$OUT/fork-search.json"
NID=$(js 'd["results"][0]["id"]' <"$OUT/fork-search.json" 2>/dev/null)
check "fork node carries the edit" 'api "/api/ws/node/$NID" | grep -q WINDOW-EDIT-MARKER'
check "original on the server is unchanged" '! curl -s -m 10 "$REMOTE/api/network/v1/node/$NID" | grep -q WINDOW-EDIT-MARKER && curl -s -m 10 "$REMOTE/api/network/v1/node/$NID" | grep -q "Douglas Adams"'
FORKDIR=$(ls -d "$TH/.local/share/mpn-studio/projects"/window-fork* 2>/dev/null | head -1)
du -sk "$FORKDIR" > "$OUT/fork-size.txt"; check "fork folder is small (<2 MiB)" '[ "$(cut -f1 "$OUT/fork-size.txt")" -lt 2048 ]'

echo "=== 6 smaller window"
xdotool windowsize "$(wid)" 1024 700; sleep 2; shot 19-small-fork; click 67 20 2; shot 20-small-home
xdotool windowsize "$(wid)" 1440 900; sleep 1
quit_app

echo "=== 7 relaunch: everything still there"
launch 2; shot 21-relaunch
api /api/projects > "$OUT/projects-2.json"
check "four projects after relaunch" '[ "$(js "len(d[\"projects\"])" <"$OUT/projects-2.json")" -ge 4 ]'
quit_app
kill $XVFB 2>/dev/null
echo "PASS=$PASS FAIL=$FAIL" | tee -a "$OUT/results.txt"
