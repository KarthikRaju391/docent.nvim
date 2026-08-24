# The stdio relay is `nvim --headless -l relay.lua`, with a hand-written MCP layer

Agents connect by spawning a stdio process, the one MCP transport every client supports. Instead of a Node (`@modelcontextprotocol/sdk` + node-client, like mcp-neovim-server) or Python relay, the relay runs on Neovim itself: `nvim --headless -l relay.lua`, using luv for stdio and the RPC socket. This means zero dependencies beyond Neovim, one language for the whole project, and distribution inside the plugin repo — at the cost of hand-writing a minimal MCP server (no SDK). Accepted because the surface is tiny (~6 tools) and MCP stdio is plain JSON-RPC.

## Consequences

- Instance discovery cannot rely on `$NVIM` (agents run outside `:terminal`): the plugin maintains a lockfile registry (`~/.local/state/docent/instances/`) mapping cwd → socket; the relay picks the instance whose cwd contains its own, most-recently-focused winning ties.
- Protocol-version bumps in MCP are ours to track by hand; if that becomes a burden, swapping the relay for the Node SDK changes nothing else in the architecture.
