# docent.nvim

Let any coding agent guide you through a codebase by navigating your Neovim — jumping, highlighting, narrating — instead of pasting code into chat. You talk (OS-level dictation into the agent's terminal); the agent moves your editor.

Docent is an MCP server: Claude Code, Pi, Gemini CLI, or any MCP-capable agent connects via a stdio relay (`nvim --headless -l relay.lua`) that finds your Neovim through an instance registry. See `CONTEXT.md` for the domain language and `docs/adr/` for the decisions.

## v1 definition of done

In a real repo, both intents work end-to-end against **two different agents** (Claude Code + one other):

1. **Jump** — "where is X handled?" → cursor lands there with a narration float.
2. **Tour** — "walk me through flow Y" → agent queues stops; user paces with `]t` / `[t`.

Out of v1 scope: TTS narration, tour sidebar, voice capture of any kind, editor state beyond file/cursor/selection.
