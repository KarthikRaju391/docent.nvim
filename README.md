# docent.nvim

Let any coding agent guide you through a codebase by navigating your Neovim — jumping, highlighting, explaining in place — instead of pasting code into chat. You talk (OS-level dictation into the agent's terminal); the agent moves your editor.

Docent is an MCP server: Claude Code, Pi, Gemini CLI, or any MCP-capable agent connects via a stdio relay (`nvim --headless -l relay.lua`) that finds your Neovim through an instance registry. See `CONTEXT.md` for the domain language and `docs/adr/` for the decisions.

## Install

With [lazy.nvim](https://github.com/folke/lazy.nvim):

```lua
{
  "<your-github-user>/docent.nvim",  -- or { dir = "~/code/docent.nvim" } for a local checkout
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
- `:DocentBack` ends the sub-tour explicitly and returns to the parent stop.
- `[t` at stop 1 of a sub-tour stays put — leaving a sub-tour is always past-the-end or explicit.
- `:DocentRestart` restarts the deepest (active) tour only.
- `:DocentSave` / `save_tour` saves the active (deepest) tour only — saving a whole tree is out of v1 scope.

### Keymap conflicts (LazyVim etc.)

The `]t` / `[t` defaults are only set if you haven't mapped them yourself (Nvim's built-in tag defaults are overridden, plugin/user maps are not). On LazyVim, todo-comments.nvim owns `]t`/`[t`, so docent skips them — pick your own keys:

```lua
opts = { keymaps = { next = "]v", prev = "[v" } }
```

Agents are told your real pacing keys: the relay reads what docent actually bound and puts it in the MCP instructions and in the first tour stop's result (`pace_with`), falling back to `:DocentNext`/`:DocentPrev` wording if no keys were bound.

### Saved tours

A good tour is documentation. `save_tour` (or `:DocentSave <title>`) writes it to `.docent/tours/<slug>.json` in your project — file paths are stored relative to the project root, so commit the directory and your whole team (and their agents) gets the tour. Agents are instructed to check `list_saved_tours` before re-deriving a flow, and to load an existing tour instead; `load_tour` / `:DocentTours` brings one back and jumps to stop 1. Saved tours can drift as code changes — lines are approximate, and that's accepted v1 behavior. `clear_tour` only clears the live tour, never saved files.

The agent's tool surface is navigation + info only — no edits, no shell. Tools: `jump_to`, `highlight`, `show_info`, `add_tour_stop`, `clear_tour`, `list_tour`, `save_tour`, `list_saved_tours`, `load_tour`, `get_editor_context`.

## v1 definition of done

In a real repo, both intents work end-to-end against **two different agents** (Claude Code + one other):

1. **Jump** — "where is X handled?" → cursor lands there with an info float.
2. **Tour** — "walk me through flow Y" → agent queues stops; user paces with `]t` / `[t`.

Out of v1 scope: TTS of stop info, tour sidebar, voice capture of any kind, editor state beyond file/cursor/selection.
