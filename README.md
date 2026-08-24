# docent.nvim

Let any coding agent guide you through a codebase by navigating your Neovim — jumping, highlighting, narrating — instead of pasting code into chat. You talk (OS-level dictation into the agent's terminal); the agent moves your editor.

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

- "Where do we dedupe pre-meeting deliveries?" → the agent calls `jump_to`; your cursor lands there with a narration float.
- "Walk me through the import flow" → the agent queues tour stops; you pace through them with `]t` / `[t` (or `:DocentNext` / `:DocentPrev` / `:DocentStop <n>`).

Commands: `:DocentNext`, `:DocentPrev`, `:DocentStop <n>`, `:DocentMcpCommand`. The `]t` / `[t` maps are only set if you haven't mapped them yourself (Nvim's built-in tag defaults are overridden). Range highlights use the `DocentRange` group (links to `Visual`).

The agent's tool surface is navigation + narration only — no edits, no shell. Tools: `jump_to`, `highlight`, `narrate`, `add_tour_stop`, `clear_tour`, `list_tour`, `get_editor_context`.

## v1 definition of done

In a real repo, both intents work end-to-end against **two different agents** (Claude Code + one other):

1. **Jump** — "where is X handled?" → cursor lands there with a narration float.
2. **Tour** — "walk me through flow Y" → agent queues stops; user paces with `]t` / `[t`.

Out of v1 scope: TTS narration, tour sidebar, voice capture of any kind, editor state beyond file/cursor/selection.
