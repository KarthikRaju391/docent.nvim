# Expose navigation as an MCP server for external agents, not as custom tools inside a chat plugin

We want the user's real coding agents (Claude Code, Pi, Gemini CLI — whatever they already run and pay for) to drive editor navigation. Building custom tools inside avante/CodeCompanion (even via mcphub.nvim) was rejected because those plugins own their own agent loop: that path is only provider-agnostic (swap LLM APIs), not agent-agnostic — the user's actual agents, with their skills and capabilities, never participate. Docent therefore is an MCP server the editor exposes; any MCP-capable agent connects as a client, and Docent contains zero per-agent code.

## Consequences

- The agent runs decoupled, in any terminal; Docent never launches or embeds it. Voice input is consequently out of scope (OS-level dictation into the agent's terminal).
- The behavioral contract ("navigate, don't paste") must travel inside the protocol — MCP server `instructions` + tool descriptions — since there is no per-agent prompt file to lean on.
