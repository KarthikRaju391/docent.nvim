# docent.nvim tests

Black-box end-to-end tests: they start real headless Neovim instances running
the plugin, spawn the real stdio relay (`nvim --headless -l relay/relay.lua`),
speak MCP (newline-delimited JSON-RPC 2.0) over the relay's stdin/stdout, and
assert editor state through a second RPC channel
(`nvim --server <sock> --remote-expr` / `--remote-send`).

## Run

From the repo root:

```sh
tests/run.sh
```

- Exits nonzero if any case fails; prints a per-case PASS/FAIL summary with
  expected-vs-actual details and relay/editor stderr tails on failure.
- `NVIM_BIN=/path/to/nvim tests/run.sh` to use a different Neovim
  (default `/opt/homebrew/bin/nvim`).
- `KEEP_TMP=1 tests/run.sh` keeps the per-run temp dir (sockets, state dirs,
  stderr logs) for inspection.
- Requires: Neovim >= 0.12, python3 (stdlib only). No other dependencies.

Each run gets its own temp dir with `XDG_STATE_HOME` pointed inside it, so the
instance registry is fully isolated from your real `~/.local/state/docent`.
RPC sockets live in a separate short `mkdtemp` dir (under `$TMPDIR`) because
macOS caps unix socket paths at 104 bytes; the deep temp dir would truncate
and collide.
Each case additionally gets its own state dir, sockets, and processes; all
processes are killed on teardown even when a case fails.

## Files

- `run.sh` — entrypoint (temp dir + trap cleanup, delegates to the driver).
- `driver.py` — the test driver; all cases live here.
- `minimal_init.lua` — editor init: prepend repo to `runtimepath`, call
  `require('docent').setup({ keymaps = { next = "]v", prev = "[v" } })`
  (editors run with `--noplugin -i NONE`). The non-default pacing keys are
  deliberate: they prove instructions/hints report the real bound keys.
- `fixture/` — small project with stable line numbers (`app.lua` 13 lines,
  `lib/util.lua` 11 lines, `README.md` 7 lines). Editors and the relay run
  with cwd here so registry discovery-by-cwd is exercised for real.

## Cases

| Case | What it asserts |
| --- | --- |
| `registry` | `$XDG_STATE_HOME/docent/instances/<pid>.json` appears after editor start with `pid`/`socket`/`cwd`/`focused_at` (correct values), and is removed after a clean `:qa!` exit. |
| `handshake` | `initialize` → `protocolVersion`, `capabilities.tools`, `serverInfo.name == "docent"`, nonempty `instructions` with navigation guidance that mentions branching for tangents and no longer advertises tour info in `get_editor_context` (reverted feature); `notifications/initialized` gets no response; `tools/list` returns exactly the 10 tools (`show_info`, not the old `narrate`) each with `inputSchema.type == "object"` and no schema mentioning the removed `narration` arg (renamed to `info`); unknown method → JSON-RPC error `-32601`; stdout contained only valid JSON lines throughout. |
| `jump` | `jump_to` lands file + cursor (with and without `info`); nonexistent file → `isError=true` and the editor state is unchanged after the failed call. |
| `tour` | First `add_tour_stop` auto-jumps; stops 2/3 do not move the cursor; `list_tour` mentions all three stop files; `]v` (the configured next key) advances to stop 2 then 3; `]v` past the end does not move; `clear_tour` empties `list_tour`. |
| `context` | `get_editor_context` reflects a cursor position set up out-of-band (file + line). |
| `info_highlight` | `show_info` succeeds; `highlight` succeeds and does not move the cursor. |
| `discovery_two_instances` | With two registered editors (fixture cwd vs unrelated cwd), a relay spawned in `tests/fixture` targets the fixture instance (proved via `get_editor_context`). |
| `discovery_empty_registry` | With an empty registry, the relay returns a clean error (isError result or JSON-RPC error) rather than hanging or dumping a stack trace on stdout; stdout stays JSON-only. |
| `pacing_keys` | `require('docent').pacing_keys()` reports the configured keys; with a live instance discoverable at initialize time, `instructions` mention the real bound key (`]v`) and not the default (`]t`); the first `add_tour_stop` result carries a `pace_with` hint with the real key. |
| `persistent_tours` | `save_tour` on an empty tour → `isError=true`; after 3 stops, `save_tour {title:"Import Flow"}` writes `<project>/.docent/tours/import-flow.json` with `title`/`slug`/`created_at`/`stops`, every `stop.file` relative to the project root (and resolvable there) with `line_start` + `info` (and no leftover `narration` key); `list_saved_tours` reports it with a stop count; after `clear_tour`, `load_tour {slug:"import-flow"}` navigates to stop 1, restores `list_tour`, and returns the full stops list; unknown slug → `isError=true`. |
| `user_commands` | `:DocentRestart`, `:DocentSave`, `:DocentTours`, `:DocentInfo`, `:DocentEnd` exist (`exists(':Cmd') == 2`); after `]v`-ing to stop 2, `:DocentRestart` returns the cursor to stop 1; `:DocentSave <title>` writes a tour file under `.docent/tours/`. |
| `subtour_branching` | From root stop 2, `add_tour_stop {branch:true}` auto-navigates to the sub-tour's stop 1; `[v` at sub stop 1 stays put (no pop); the second sub stop does not move the cursor; nested `list_tour` shows the sub frame (2 stops, `depth=2`, `parent = {title, anchor:2, total:3}`); `]v` paces the sub-tour and `]v` past its end pops back to root stop 2 (`depth=1`, `current=2`); a nested `clear_tour {}` pops one level (result carries `popped_to` with `current=2`, cursor back at the anchor); `clear_tour {}` at the root empties everything. |
| `subtour_clear_all` | From a nested state, `clear_tour {all:true}` wipes the whole tree in one call. |
| `subtour_docent_back` | `:DocentBack` exists; from a nested state it pops (cursor back at the anchor, `depth=1`); at the root and with no tour it neither errors the instance nor moves the cursor. |
| `subtour_save` | `save_tour` while nested saves only the active sub-tour's stops (saved JSON has exactly the 2 sub stops, each with `info` and no `narration` key). |
| `esc_ends_tour` | No docent `<Esc>` mapping before a tour; a transient normal-mode `<Esc>` mapping exists while a tour is live; a single `<Esc>` does not end the tour (list unchanged, current stop unchanged); `<Esc><Esc>` in one send ends the whole tour and removes the mapping; from inside a sub-tour, double-`<Esc>` clears the WHOLE tree. |

The double-Esc feature means the driver must never run ex commands via
`remote-send '<Esc>:Cmd<CR>'` while a tour is live — two such sends within 1s
are a real double-Esc and end the tour. `Editor.cmd()` uses
`--remote-expr execute(...)` instead.

All relay reads have a 5s timeout, so a hung relay fails the case instead of
blocking the run. Tool-call failures are asserted to arrive as
`isError=true` results (never JSON-RPC errors), per the MCP contract.
