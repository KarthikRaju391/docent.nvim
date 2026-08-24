# Docent

A Neovim plugin that lets any external coding agent (Claude Code, Pi, Gemini CLI, …) guide the user through a codebase by navigating their editor — jumping, highlighting, and narrating — instead of pasting code into chat. The user typically talks to the agent by voice, but voice is environmental (OS-level dictation), not part of the plugin.

## Language

**Docent**:
The role the connected agent plays: a guide that answers codebase questions by moving the user's editor, not by quoting code.
_Avoid_: assistant, copilot, bot

**Agent**:
Any external MCP-capable coding CLI the user runs in a terminal of their choosing; Docent never launches, embeds, or configures it.
_Avoid_: LLM, model, provider (those are the agent's concern)

**Relay**:
The stdio process an Agent spawns (`nvim --headless -l relay.lua`) that speaks MCP on stdin/stdout and forwards tool calls to the target Neovim instance over its RPC socket.
_Avoid_: server, bridge, proxy

**Instance Registry**:
The lockfile directory (`~/.local/state/docent/instances/`) mapping each live Neovim instance's cwd to its RPC socket, written by the plugin and read by the Relay for discovery.
_Avoid_: lockfile (the registry contains lockfiles; it isn't one)

**Jump**:
An immediate cursor move to one location with optional selection, used for single-answer "where is X" questions.
_Avoid_: goto, open (a Jump may open a file, but opening is incidental)

**Tour**:
An ordered sequence of Stops the Agent queues to explain a flow; the user paces through it.
_Avoid_: walkthrough, session

**Stop**:
One location in a Tour plus its Narration.
_Avoid_: step, waypoint, bookmark

**Sub-tour**:
A Tour branched from a Stop of a parent Tour to explore a tangent; ending it returns the user to that parent Stop. Tours therefore form a tree, but only the deepest branch is active at a time.
_Avoid_: detour, side tour, nested tour

**Narration**:
The Agent's 1–2 sentence explanation attached to a Stop or Jump, shown in a float at the target range and optionally spoken via TTS.
_Avoid_: comment, annotation, message

**Editor Context**:
The read-only snapshot Docent exposes to the Agent: current file, cursor position, and visual selection.
_Avoid_: state, environment

## Relationships

- An **Agent** spawns one **Relay** per session; the Relay targets exactly one Neovim instance chosen via the **Instance Registry** (instance whose cwd contains the Relay's cwd; most-recently-focused wins ties).
- A **Tour** contains one or more **Stops**, each carrying one **Narration**.
- A **Jump** carries an optional **Narration** but never belongs to a **Tour**.
- The **Agent** advances nothing in a **Tour** after the first Stop — the user paces with keymaps (`]t` / `[t`) while the Agent may keep queueing Stops ahead.
- A **Sub-tour** is anchored to exactly one parent **Stop**; pacing past its end (or an explicit back command) pops to the parent Tour at that Stop.

## Tool surface (the whole contract)

Navigation + narration only — no edits, no shell, no buffer writes. Agents use their own tools to read/grep/edit files on disk.

- `jump_to(file, line_start?, line_end?, narration?)` — immediate Jump
- `highlight(ranges)` — visual markers without moving the cursor
- `narrate(text)` — Narration not tied to a location
- `add_tour_stop(file, line_start, line_end?, narration)` — queue a Stop (first Stop auto-jumps)
- `clear_tour()` / `list_tour()` — Tour lifecycle
- `get_editor_context()` — Editor Context readback

The "navigate, don't paste" behavior ships inside the protocol: MCP server `instructions` on initialize plus rich per-tool descriptions. No per-agent prompt files are maintained.

The tool count grows reluctantly: new capabilities extend the semantics of existing tools (flags, richer results) rather than adding tools, and anything the Agent can already do natively (e.g. tracking which Stop the user is on via `list_tour`) is not duplicated into the surface.

## Example dialogue

> **Dev:** "If the user asks 'where do we dedupe pre-meeting deliveries?', does the **Agent** start a **Tour**?"
> **Domain expert:** "No — that's a single-answer question, so it's a **Jump** with a one-line **Narration**. A **Tour** only exists when the user asks to be walked through a flow, and even then the **Agent** only queues **Stops**; the user decides when to move."

## Flagged ambiguities

- "voice plugin" — Docent is not one. Voice input is OS-level dictation into the Agent's terminal; voice output (TTS of Narration) is an opt-in plugin feature. Resolved: Docent is a code-tour plugin whose primary user is an Agent.
- "agent-agnostic" vs "provider-agnostic" — CodeCompanion/avante custom tools would only be provider-agnostic (their own agent loop, swapping LLM APIs). Docent is agent-agnostic: the user's real coding agents drive it over MCP. Resolved: agent-agnostic is the requirement.
