# docent.nvim

Let any coding agent guide you through a codebase by navigating your Neovim — jumping, highlighting, explaining in place — instead of pasting code into chat. You talk (OS-level dictation into the agent's terminal); the agent moves your editor.

Docent is an MCP server: Claude Code, Pi, Gemini CLI, or any MCP-capable agent connects via a stdio relay (`nvim --headless -l relay.lua`) that finds your Neovim through an instance registry. See `CONTEXT.md` for the domain language and `docs/adr/` for the decisions.

## Install

With [lazy.nvim](https://github.com/folke/lazy.nvim):

```lua
{
  "KarthikRaju391/docent.nvim",  -- or { dir = "~/code/docent.nvim" } for a local checkout
  opts = {},
  -- opts = { keymaps = { next = "]t", prev = "[t" } }  -- defaults; or keymaps = false
}
```

Or call `require("docent").setup()` yourself. Setup registers this Neovim instance (RPC socket + cwd) in `$XDG_STATE_HOME/docent/instances/` so relays can find it; without setup, agents can't connect.

## Agent registration

Run `:DocentMcpCommand` in Neovim to get the exact command with your install path. It prints something like:

```
claude mcp add docent -- nvim --headless -l /path/to/docent.nvim/relay/relay.lua
```

Per agent:

- **Claude Code**: `claude mcp add docent -- nvim --headless -l /path/to/docent.nvim/relay/relay.lua`
- **Gemini CLI**: `gemini mcp add docent nvim --headless -l /path/to/docent.nvim/relay/relay.lua`
- **Pi** (or any client configured via JSON): add a stdio server with `command: "nvim"` and `args: ["--headless", "-l", "/path/to/docent.nvim/relay/relay.lua"]`

Run the agent from inside (a subdirectory of) the directory where Neovim is running — the relay targets the instance whose cwd contains the agent's, most recently focused winning ties.

## Usage

Keep Neovim open with `setup()` called, then ask your agent things like:

- "Where do we dedupe pre-meeting deliveries?" → the agent calls `jump_to`; your cursor lands there with an info float.
- "Walk me through the import flow" → the agent queues tour stops; you pace through them with `]t` / `[t` (or `:DocentNext` / `:DocentPrev` / `:DocentStop <n>`).

Talking to your agent by voice? Voice-mode agents read each stop's info aloud as they navigate — the float and the spoken reply are the same text.

While a tour is live, pressing `<Esc>` twice within a second (normal mode) ends the whole tour. A single `<Esc>` behaves exactly as before — the transient mapping chains to whatever your `<Esc>` did (e.g. clearing search highlights) and is removed when the tour ends. Disable with `opts = { esc_ends_tour = false }`.

Commands: `:DocentNext`, `:DocentPrev`, `:DocentStop <n>`, `:DocentRestart` (back to stop 1 of the active tour), `:DocentBack` (end a sub-tour), `:DocentEnd` (exit the whole tour), `:DocentInfo` (re-show the current stop's info float), `:DocentSave <title>`, `:DocentTours` (picker), `:DocentMcpCommand`. Range highlights use the `DocentRange` group (links to `Visual`).

### Sub-tours

Tours form a tree. Mid-tour you can ask a tangent question ("wait, what does the registry actually store?") and the agent branches: a sub-tour starts at your current stop, and your pacing keys now walk the tangent. Pacing past the last tangent stop pops you right back to the stop you left the main tour from — so tangents never lose your place. Only the deepest branch is active at a time.

- Past-the-end pop: `]t` (or your next key) at the last sub-tour stop returns you to the parent stop.
- Skip: the uppercase variant of your next key (`]v` → `]V`, configurable as `keymaps.skip`) leaves the sub-tour immediately — same as `:DocentBack`.
- `[t` at stop 1 of a sub-tour stays put — a sub-tour is contained: you leave by finishing it, skipping it, or `:DocentBack`.
- `:DocentRestart` restarts the deepest (active) tour only.
- `:DocentSave` / `save_tour` saves the active (deepest) tour only — saving a whole tree is out of v1 scope.

### Keymap conflicts (LazyVim etc.)

The `]t` / `[t` defaults are only set if you haven't mapped them yourself (Nvim's built-in tag defaults are overridden, plugin/user maps are not). On LazyVim, todo-comments.nvim owns `]t`/`[t`, so docent skips them — pick your own keys:

```lua
opts = { keymaps = { next = "]v", prev = "[v" } }
```

Agents are told your real pacing keys: the relay reads what docent actually bound and puts it in the MCP instructions and in the first tour stop's result (`pace_with`), falling back to `:DocentNext`/`:DocentPrev` wording if no keys were bound.

### Saved tours

A good tour is documentation. (This repo ships one of itself — ask your agent what saved tours it can find here.) Confirmed tours live at `.docent/tours/<slug>.json` in your project, with file paths relative to the project root, so commit the directory and your whole team (and their agents) gets the tour.

**The agent proposes; you decide.** `save_tour` never writes — it only proposes a title. When you pace past the last stop of a tour, docent asks: `Save tour as: <proposed title>` — press Enter to accept, type over it to rename, or `<Esc>` to decline. Explicit exits (`]V` skip, `:DocentBack`, `:DocentEnd`, double-`<Esc>`, `clear_tour`, loading another tour) discard the proposal silently — no nagging when you're trying to leave. `:DocentSave <title>` still writes immediately, since typing it is itself the confirmation. Don't want the prompt at all? `opts = { save_prompt = false }`.

Agents are instructed to check `list_saved_tours` before re-deriving a flow, and to load an existing tour instead; `load_tour` / `:DocentTours` brings one back and jumps to stop 1. Saved tours can drift as code changes — lines are approximate, and that's accepted v1 behavior. `clear_tour` only clears the live tour, never saved files.

The agent's tool surface is navigation + info only — no edits, no shell. Tools: `jump_to`, `highlight`, `show_info`, `add_tour_stop`, `clear_tour`, `list_tour`, `save_tour`, `list_saved_tours`, `load_tour`, `get_editor_context`.

## Status

Early but real: both flows — a single jump and a paced multi-stop tour — are verified end to end against two different agents (Claude Code and Pi) in live repos, and the suite runs 19 integration cases that drive an actual relay against an actual Neovim.

Deliberately not included: docent does no text-to-speech of its own (a voice-mode agent speaks the stop info as its reply), no voice capture (use OS-level dictation into your agent's terminal), no tour sidebar, and no editor state beyond file, cursor, and selection. Saved tours store line numbers, so they drift as code changes — the lines are a starting point, not a guarantee.
