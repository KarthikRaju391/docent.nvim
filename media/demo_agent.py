#!/usr/bin/env python3
"""Scripted agent for the docent.nvim launch demo.

Nothing here mimics docent. This process is a stand-in for an LLM's *choice*
of stops only: it speaks real MCP (newline-delimited JSON-RPC 2.0) to the real
stdio relay (`nvim --headless -l relay/relay.lua`) via tests/driver.py's Relay
class, and issues genuine add_tour_stop / save_tour calls. Every jump, range
highlight, Info float, save prompt and message on screen is the plugin doing
its own thing in the visible Neovim.

Pacing (`]v`) is normally the human's job; here it is injected as real
keystrokes with `nvim --server <sock> --remote-send ]v`, which is the same
input path a keyboard takes.

Usage: demo_agent.py <nvim-socket> <narration-log> <workdir>
"""

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import driver  # noqa: E402  (reuses the real MCP client machinery)

NVIM = driver.NVIM

C = {
    "you": "\033[1;38;5;81m",
    "mcp": "\033[38;5;114m",
    "key": "\033[1;38;5;222m",
    "res": "\033[38;5;245m",
    "off": "\033[0m",
}


class Narration:
    def __init__(self, path):
        self.path = path

    def _write(self, s):
        with open(self.path, "a") as f:
            f.write(s + "\n")

    def header(self):
        self._write("\033[38;5;245m  agent  \033[0m"
                    "\033[38;5;245m—  MCP over stdio  →  docent.nvim\033[0m")
        self._write("")

    def you(self, text, pause=2.6):
        self._write("%s  you   %s%s" % (C["you"], text, C["off"]))
        time.sleep(pause)

    def mcp(self, call, pause=0.0):
        self._write("%s  mcp   %s%s" % (C["mcp"], call, C["off"]))
        if pause:
            time.sleep(pause)

    def res(self, text, pause=0.0):
        self._write("%s        %s%s" % (C["res"], text, C["off"]))
        if pause:
            time.sleep(pause)

    def key(self, keys, note="", pause=0.0):
        line = "%s  key   %s%s" % (C["key"], keys, C["off"])
        if note:
            line += "%s   %s%s" % (C["res"], note, C["off"])
        self._write(line)
        if pause:
            time.sleep(pause)


class Ed:
    """The visible Neovim, driven over its RPC socket (same as a keyboard)."""

    def __init__(self, sock):
        self.sock = sock

    def _client(self, args, timeout=6.0):
        return subprocess.run([NVIM, "--server", self.sock] + args,
                              capture_output=True, text=True, timeout=timeout)

    def send_keys(self, keys):
        self._client(["--remote-send", keys])

    def expr(self, e):
        r = self._client(["--remote-expr", e])
        return r.stdout.strip("\n")

    def redraw(self):
        """The recorder attaches after Neovim's first paint; force a repaint so
        no cells from the pre-resize frame linger beside the code."""
        self._client(["--remote-expr", 'execute("redraw!")'])

    def wait_up(self, timeout=25.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.expr("1+1") == "2":
                return True
            time.sleep(0.25)
        return False


# --------------------------------------------------------------- the tour ----
# The agent's *choice* of stops. Subset of the tour this repo ships at
# .docent/tours/tour-stop-from-mcp-to-editor.json, with its Info text.

ROOT_STOPS = [
    ("relay/relay.lua", 335, 356,
     "An Agent's MCP tools/call arrives here. The Relay validates the "
     "tool name, forwards its arguments, and converts success or failure "
     "into MCP's text result shape."),
    ("relay/relay.lua", 275, 287,
     "The Relay crosses the process boundary here: it discovers your "
     "Neovim, invokes docent.rpc.dispatch over nvim_exec_lua, and retries "
     "once if that RPC channel died."),
    ("lua/docent/rpc.lua", 3, 17,
     "Inside your Neovim, this tiny dispatcher maps the MCP tool name to a "
     "function in docent.tools. pcall keeps plugin errors contained and "
     "turns them into data the Relay can return."),
    ("lua/docent/ui.lua", 45, 78,
     "The UI renders a Stop's Info in a non-focusable cursor-relative "
     "float, sizes it to the wrapped content, and closes it on your next "
     "cursor move."),
]

TANGENT_STOPS = [
    ("lua/docent/registry.lua", 3, 14,
     "The Instance Registry is just a directory of lockfiles under "
     "XDG_STATE_HOME, one JSON file per live Neovim, named by pid."),
    ("lua/docent/registry.lua", 17, 33,
     "Each entry stores the RPC socket, the instance's cwd and a "
     "focused_at stamp — that is the whole discovery contract the Relay "
     "reads."),
]

SAVE_TITLE = "One MCP call, relay to editor"


def loc(stop):
    f, s, e, _ = stop
    return "%s:%d-%d" % (f, s, e)


def main():
    sock, log_path, workdir = sys.argv[1], sys.argv[2], sys.argv[3]
    say = Narration(log_path)
    ed = Ed(sock)

    if not ed.wait_up():
        say.res("could not reach the editor over RPC")
        return 1

    # Wait for the plugin to publish this instance in the Instance Registry;
    # without it the relay has nothing to discover.
    instances = os.path.join(os.environ["XDG_STATE_HOME"], "docent", "instances")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if os.path.isdir(instances) and os.listdir(instances):
            break
        time.sleep(0.2)

    relay = driver.Relay(workdir, ROOT, os.environ["XDG_STATE_HOME"])
    try:
        # Scene: the user is reading the README when the question comes up.
        # Opened here, not on nvim's command line, so it renders at the
        # recorder's final pane size.
        time.sleep(2.0)
        ed.expr('execute("edit README.md")')
        ed.redraw()

        say.header()
        time.sleep(1.4)

        relay.handshake()

        ed.redraw()
        say.you("walk me through what happens when you call one of your tools")
        ed.redraw()

        # --- stop 1: the first stop auto-jumps (docent's own behavior).
        s = ROOT_STOPS[0]
        say.mcp("add_tour_stop  %s" % loc(s))
        relay.tool_text("add_tour_stop",
                        {"file": s[0], "line_start": s[1], "line_end": s[2],
                         "info": s[3]})
        say.res("stop 1 queued — first stop jumps; you pace the rest with ]v")
        time.sleep(1.0)
        ed.redraw()
        time.sleep(4.0)

        # --- stops 2-4 are queued ahead and must NOT move the cursor.
        for s in ROOT_STOPS[1:]:
            say.mcp("add_tour_stop  %s" % loc(s), pause=0.55)
            relay.tool_text("add_tour_stop",
                            {"file": s[0], "line_start": s[1],
                             "line_end": s[2], "info": s[3]})
        say.res("4 stops queued, cursor untouched", pause=1.4)

        # --- the user paces.
        say.key("]v", "stop 2 of 4")
        ed.send_keys("]v")
        time.sleep(3.6)

        say.key("]v", "stop 3 of 4")
        ed.send_keys("]v")
        time.sleep(3.6)

        # --- a tangent branches into a sub-tour anchored at stop 3.
        say.you("wait — how does the relay even find my Neovim?", pause=2.4)
        t = TANGENT_STOPS[0]
        say.mcp("add_tour_stop  %s  branch: true" % loc(t))
        relay.tool_text("add_tour_stop",
                        {"file": t[0], "line_start": t[1], "line_end": t[2],
                         "branch": True, "info": t[3]})
        say.res("sub-tour anchored at stop 3")
        time.sleep(3.6)

        t = TANGENT_STOPS[1]
        say.mcp("add_tour_stop  %s" % loc(t), pause=0.8)
        relay.tool_text("add_tour_stop",
                        {"file": t[0], "line_start": t[1], "line_end": t[2],
                         "info": t[3]})

        say.key("]v", "tangent 2 of 2")
        ed.send_keys("]v")
        time.sleep(3.4)

        say.key("]v", "past the tangent's end")
        ed.send_keys("]v")
        say.res("popped back to stop 3 of the main tour", pause=4.0)

        say.key("]v", "stop 4 of 4")
        ed.send_keys("]v")
        time.sleep(3.6)

        # --- the agent proposes a title; it never writes.
        say.mcp('save_tour  title: "%s"' % SAVE_TITLE)
        text = relay.tool_text("save_tour", {"title": SAVE_TITLE})
        say.res("pending_confirmation — the agent proposes, you decide",
                pause=2.2)

        say.key("]v", "past the last stop")
        ed.send_keys("]v")
        time.sleep(3.2)

        say.key("<CR>", "accept the proposed title")
        ed.send_keys("<CR>")
        time.sleep(3.4)

        say.res("tour saved to .docent/tours/ — commit it, your team gets it",
                pause=2.6)
        return 0
    finally:
        relay.cleanup()


if __name__ == "__main__":
    sys.exit(main())
