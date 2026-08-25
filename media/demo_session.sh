#!/usr/bin/env bash
# Sets up the recorded scene and launches the scripted agent.
#
#   top pane    — the agent's side: its question and the real MCP calls it makes
#   bottom pane — a visible Neovim running docent (the whole point)
#
# Run from the repo root. Called by media/demo.tape (vhs).
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="${DOCENT_DEMO_RUN:-/tmp/docent-demo}"
SOCK="$RUN/nvim.sock"
LOG="$RUN/agent.log"
export XDG_STATE_HOME="$RUN/state"
unset NVIM

rm -rf "$RUN"
mkdir -p "$RUN" "$XDG_STATE_HOME"
: >"$LOG"

TMUX_CONF="$ROOT/media/demo.tmux.conf"
tmux -f "$TMUX_CONF" kill-server 2>/dev/null

# No file argument: the pane is 80x24 until the recorder attaches and resizes
# it, and text rendered at the old width leaves reflow residue behind. The
# agent opens README.md over RPC once the resize has settled.
tmux -f "$TMUX_CONF" new-session -d -s docent -x 120 -y 32 -c "$ROOT" \
  "$NVIM_BIN --listen '$SOCK' -u '$ROOT/media/demo_init.lua' --noplugin -i NONE"
tmux -f "$TMUX_CONF" split-window -t docent -v -b -l 9 -c "$ROOT" \
  "tail -n 200 -f '$LOG'"
tmux -f "$TMUX_CONF" select-pane -t docent:0.1

( python3 "$ROOT/media/demo_agent.py" "$SOCK" "$LOG" "$RUN" \
    >"$RUN/agent.out" 2>&1
  sleep 1
  tmux -f "$TMUX_CONF" kill-server 2>/dev/null
  # the demo's last beat really saves a tour; drop it so re-rendering is
  # side-effect-free and only the tour this repo ships stays in git
  rm -f "$ROOT/.docent/tours/one-mcp-call-relay-to-editor.json" ) &

exec tmux -f "$TMUX_CONF" attach -t docent
