# Recording the "real agent" take

`media/demo.mp4` is rendered headlessly (`vhs media/demo.tape`) with a scripted
agent driving the real relay. This is the checklist for recording the same beats
with an actual agent and, optionally, your voice.

## Setup (do this before you hit record)

```sh
cd ~/code/docent.nvim
rm -rf /tmp/docent-take && mkdir -p /tmp/docent-take/state   # isolated registry
export XDG_STATE_HOME=/tmp/docent-take/state
nvim -u media/demo_init.lua --noplugin -i NONE README.md      # pane 1: the editor
```

In a second terminal, same `XDG_STATE_HOME`, same cwd:

```sh
claude mcp add docent -- nvim --headless -l "$PWD/relay/relay.lua"   # once
claude                                                               # pane 2: the agent
```

- Terminal: one window, two panes (agent left/top, Neovim right/bottom). Give
  Neovim ~70% of the area.
- Font 18-20pt, a dark theme, `termguicolors` on (`media/demo_init.lua` sets it).
- Pacing keys are `]v` / `[v` in that init. Say them out loud once so viewers
  connect the keypress to the jump.
- Hide anything with a path in it: no tabs, no window title, no dock, no
  notifications (Do Not Disturb on).
- Sanity check before recording: ask the agent "what saved tours are in this
  repo?" — it should call `list_saved_tours` and name the shipped tour.

## Beats (aim for 45-60s of final cut)

1. **The question.** Type or dictate, verbatim:
   > walk me through what happens when you call one of your tools — start at the relay
2. **First stop.** The agent calls `add_tour_stop`; the cursor lands in
   `relay/relay.lua` with the Info float. Hold 4-5s — this is the money shot,
   the float must be readable.
3. **Pace.** Press `]v` twice, ~4s on each stop. Say "next" as you press.
4. **The tangent.** Ask:
   > wait — how does the relay even find my Neovim?
   The agent must call `add_tour_stop` with `branch: true`. If it queues a plain
   stop instead, say "branch off, don't lose my place" and retry. Pace to the
   end of the sub-tour, then one more `]v` — the cursor pops back to the stop
   you left. Hold 4s on the pop; that beat is the whole pitch.
5. **The ending.** `]v` to the last stop, then ask "save this tour". The agent
   calls `save_tour` (which only proposes). One more `]v` past the last stop →
   the real `Save tour as: <title>` prompt → Enter → the "saved tour ... to
   .docent/tours/..." message. Hold 3s and stop recording.

**LLM wait points.** Expect 3-10s of thinking before each `add_tour_stop` batch
and after each tangent. Do not fill it with talking. Cut those gaps out (below)
rather than speeding them up — a 4x-sped spinner reads as latency.

## Record

Find your screen device index, then capture:

```sh
ffmpeg -f avfoundation -list_devices true -i "" 2>&1 | grep -i screen
ffmpeg -f avfoundation -capture_cursor 1 -framerate 30 -i "3:none" \
  -c:v libx264 -preset ultrafast -crf 18 -pix_fmt yuv420p /tmp/docent-take/raw.mov
```

(Swap `3` for your screen index. `-i "3:0"` if you want the mic for voiceover.)
QuickTime's File → New Screen Recording works too; pick the window, not the
whole display.

## Cut and convert

Trim the dead air first (repeat per keeper segment, then concat):

```sh
ffmpeg -ss 00:00:06 -to 00:01:02 -i /tmp/docent-take/raw.mov -c copy /tmp/docent-take/cut.mov
```

Twitter-ready H.264, 1280x720, no audio:

```sh
ffmpeg -i /tmp/docent-take/cut.mov -an \
  -vf "crop=iw:ih:0:0,scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0x1a1b26,fps=30" \
  -c:v libx264 -profile:v high -level 4.0 -pix_fmt yuv420p -crf 20 -movflags +faststart \
  media/demo.mp4
```

Adjust the `crop=w:h:x:y` to your terminal's rectangle (get it from a still:
`ffmpeg -i raw.mov -frames:v 1 /tmp/f.png`). README GIF under 10MB:

```sh
ffmpeg -i media/demo.mp4 -vf "fps=12,scale=1000:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse" media/demo.gif
```

## Before publishing

- Watch it muted at phone size. If the float text isn't readable, re-record
  bigger — nothing else matters as much.
- Delete the tour the take saved: `rm .docent/tours/<slug>.json`. Only
  `tour-stop-from-mcp-to-editor.json` belongs in git.
- `rm -rf /tmp/docent-take` and confirm no stray entries in
  `~/.local/state/docent/instances/`.
