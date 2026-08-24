#!/usr/bin/env python3
"""Test driver for docent.nvim.

Usage: python3 tests/driver.py <tmpdir>

Spawns headless "target" Neovim instances running the plugin, plus the stdio
relay (nvim --headless -l relay/relay.lua), and speaks MCP (newline-delimited
JSON-RPC 2.0) to the relay while asserting editor state over a second RPC
channel (nvim --server <sock> --remote-expr / --remote-send).
"""

import itertools
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

NVIM = os.environ.get("NVIM_BIN", "/opt/homebrew/bin/nvim")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.realpath(os.path.join(ROOT, "tests", "fixture"))
MINIMAL_INIT = os.path.join(ROOT, "tests", "minimal_init.lua")
RELAY_LUA = os.path.join(ROOT, "relay", "relay.lua")

APP = os.path.join(FIXTURE, "app.lua")
UTIL = os.path.join(FIXTURE, "lib", "util.lua")
READMEMD = os.path.join(FIXTURE, "README.md")

EXPECTED_TOOLS = {
    "jump_to", "highlight", "narrate", "add_tour_stop",
    "clear_tour", "list_tour", "get_editor_context",
}

RESPONSE_TIMEOUT = 5.0

# Unix sockets are capped at 104 bytes on macOS, so they cannot live under the
# (long) per-run temp dir. main() sets this to a short mkdtemp dir.
SOCK_DIR = None
_sock_seq = itertools.count(1)


def new_sock(name):
    return os.path.join(SOCK_DIR, "%d-%s.sock" % (next(_sock_seq), name[:12]))


class Fail(Exception):
    pass


def check(cond, msg, expected=None, actual=None):
    if not cond:
        detail = ""
        if expected is not None or actual is not None:
            detail = "\n      expected: %r\n      actual:   %r" % (expected, actual)
        raise Fail(msg + detail)


def tail(path, n=25):
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:]).strip()
    except OSError:
        return ""


# ---------------------------------------------------------------- editor ----

class Editor:
    """A headless 'interactive' Neovim running the docent plugin."""

    def __init__(self, name, case_dir, cwd, state_dir):
        self.name = name
        self.sock = new_sock(name)
        self.stderr_path = os.path.join(case_dir, name + ".stderr.log")
        self.state_dir = state_dir
        env = dict(os.environ, XDG_STATE_HOME=state_dir)
        env.pop("NVIM", None)  # never inherit an outer nvim
        self._stderr_f = open(self.stderr_path, "wb")
        self.proc = subprocess.Popen(
            [NVIM, "--headless", "--noplugin", "-i", "NONE",
             "-u", MINIMAL_INIT, "--listen", self.sock],
            cwd=cwd, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=self._stderr_f,
        )

    @property
    def instances_dir(self):
        return os.path.join(self.state_dir, "docent", "instances")

    @property
    def registry_file(self):
        return os.path.join(self.instances_dir, "%d.json" % self.proc.pid)

    def wait_registry(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise Fail("%s: nvim exited early (code %s)\n      stderr: %s"
                           % (self.name, self.proc.returncode, tail(self.stderr_path)))
            if os.path.exists(self.registry_file):
                try:
                    with open(self.registry_file) as f:
                        return json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass  # partially written; retry
            time.sleep(0.05)
        listing = []
        if os.path.isdir(self.instances_dir):
            listing = os.listdir(self.instances_dir)
        raise Fail("%s: registry file never appeared" % self.name,
                   expected=self.registry_file, actual="dir contents: %r" % listing)

    def _client(self, args, timeout=5.0):
        r = subprocess.run([NVIM, "--server", self.sock] + args,
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise Fail("%s: nvim --server %s failed: %s"
                       % (self.name, " ".join(args), (r.stderr or r.stdout).strip()))
        return r.stdout

    def expr(self, e):
        return self._client(["--remote-expr", e]).strip("\n")

    def send_keys(self, keys):
        self._client(["--remote-send", keys])

    def current(self):
        """(absolute file path, cursor line) of the editor right now."""
        out = self.expr("expand('%:p') . '||' . line('.')")
        file, _, line = out.rpartition("||")
        return os.path.realpath(file) if file else file, int(line)

    def set_position(self, file, line):
        self.expr('execute("edit " . fnameescape("%s"))' % file)
        self.expr("cursor(%d, 1)" % line)
        f, l = self.current()
        check((f, l) == (os.path.realpath(file), line),
              "%s: failed to set up editor position" % self.name,
              expected=(file, line), actual=(f, l))

    def wait_position(self, file, line, what, timeout=3.0):
        want = (os.path.realpath(file), line)
        deadline = time.monotonic() + timeout
        got = None
        while time.monotonic() < deadline:
            got = self.current()
            if got == want:
                return
            time.sleep(0.1)
        raise Fail(what, expected="%s:%d" % want, actual="%s:%d" % got)

    def assert_position(self, file, line, what, settle=0.5):
        """Assert the cursor is (still) here and stays here for `settle` sec."""
        time.sleep(settle)
        got = self.current()
        want = (os.path.realpath(file), line)
        check(got == want, what, expected="%s:%d" % want, actual="%s:%d" % got)

    def quit(self, timeout=5.0):
        try:
            self._client(["--remote-send", "<Esc>:qa!<CR>"])
        except Exception:
            pass
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Fail("%s: nvim did not exit after :qa!" % self.name)

    def cleanup(self):
        if self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self._stderr_f.close()


# ----------------------------------------------------------------- relay ----

class Relay:
    """The MCP stdio relay, driven over stdin/stdout."""

    def __init__(self, case_dir, cwd, state_dir, name="relay"):
        self.name = name
        self.stderr_path = os.path.join(case_dir, name + ".stderr.log")
        env = dict(os.environ, XDG_STATE_HOME=state_dir)
        env.pop("NVIM", None)
        self._stderr_f = open(self.stderr_path, "wb")
        self.proc = subprocess.Popen(
            [NVIM, "--headless", "-l", RELAY_LUA],
            cwd=cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr_f,
            text=True, bufsize=1,
        )
        self.raw_lines = []      # every stdout line, verbatim
        self.bad_lines = []      # lines that were not valid JSON
        self._q = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._next_id = 100

    def _read_loop(self):
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            if not line:
                continue
            self.raw_lines.append(line)
            try:
                self._q.put(json.loads(line))
            except json.JSONDecodeError:
                self.bad_lines.append(line)
                self._q.put(None)

    def send(self, obj):
        try:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            raise Fail("relay stdin closed (relay exited? code=%s)\n      stderr: %s"
                       % (self.proc.poll(), tail(self.stderr_path)))

    def recv(self, timeout=RESPONSE_TIMEOUT):
        try:
            msg = self._q.get(timeout=timeout)
        except queue.Empty:
            state = ("still running" if self.proc.poll() is None
                     else "exited with code %s" % self.proc.returncode)
            raise Fail("timed out (%.0fs) waiting for a relay response; relay %s"
                       "\n      relay stderr: %s" % (timeout, state, tail(self.stderr_path)))
        if msg is None:
            raise Fail("relay wrote a non-JSON line to stdout",
                       actual=self.bad_lines[-1][:300])
        return msg

    def request(self, method, params=None, rid=None, timeout=RESPONSE_TIMEOUT):
        if rid is None:
            self._next_id += 1
            rid = self._next_id
        req = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            req["params"] = params
        self.send(req)
        deadline = time.monotonic() + timeout
        while True:
            msg = self.recv(timeout=max(0.1, deadline - time.monotonic()))
            if msg.get("id") == rid:
                return msg
            # tolerate server-initiated notifications; anything else is noise

    def notify(self, method, params=None):
        req = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            req["params"] = params
        self.send(req)

    def expect_silence(self, secs, what):
        try:
            msg = self._q.get(timeout=secs)
        except queue.Empty:
            return
        raise Fail(what, expected="no response line", actual=msg)

    def initialize(self):
        resp = self.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "docent-tests", "version": "0.0.0"},
        }, rid=1)
        check("result" in resp, "initialize did not return a result", actual=resp)
        return resp["result"]

    def handshake(self):
        result = self.initialize()
        self.notify("notifications/initialized")
        return result

    def call_tool(self, name, arguments):
        resp = self.request("tools/call", {"name": name, "arguments": arguments})
        check("result" in resp,
              "tools/call %s returned a JSON-RPC error instead of a result "
              "(tool failures must be isError=true results)" % name, actual=resp)
        result = resp["result"]
        check(isinstance(result.get("content"), list) and result["content"],
              "tools/call %s: result.content missing/empty" % name, actual=result)
        first = result["content"][0]
        check(first.get("type") == "text" and isinstance(first.get("text"), str),
              "tools/call %s: content[0] is not {type:'text', text:...}" % name,
              actual=first)
        return bool(result.get("isError")), first["text"]

    def tool_text(self, name, arguments):
        is_error, text = self.call_tool(name, arguments)
        check(not is_error, "tool %s unexpectedly failed (isError=true)" % name,
              actual=text[:300])
        return text

    def assert_all_json(self):
        check(not self.bad_lines,
              "relay stdout contained non-JSON lines",
              actual=[l[:200] for l in self.bad_lines[:5]])

    def cleanup(self):
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        self._reader.join(timeout=2)
        self._stderr_f.close()


# ------------------------------------------------------------------- ctx ----

class Ctx:
    def __init__(self, case_dir):
        self.case_dir = case_dir
        self.state_dir = os.path.join(case_dir, "state")
        os.makedirs(self.state_dir, exist_ok=True)
        self.editors = []
        self.relays = []

    def editor(self, name="editor", cwd=FIXTURE, state_dir=None, wait=True):
        ed = Editor(name, self.case_dir, cwd, state_dir or self.state_dir)
        self.editors.append(ed)
        if wait:
            ed.wait_registry()
        return ed

    def relay(self, cwd=FIXTURE, state_dir=None, name="relay"):
        r = Relay(self.case_dir, cwd, state_dir or self.state_dir, name=name)
        self.relays.append(r)
        return r

    def cleanup(self):
        for r in self.relays:
            r.cleanup()
        for e in self.editors:
            e.cleanup()

    def stderr_tails(self):
        out = []
        for p in self.relays + self.editors:
            t = tail(p.stderr_path)
            if t:
                out.append("--- %s stderr ---\n%s" % (p.name, t))
        return "\n".join(out)


# ----------------------------------------------------------------- cases ----

def case_registry(ctx):
    ed = ctx.editor(wait=False)
    entry = ed.wait_registry()

    for field in ("pid", "socket", "cwd", "focused_at"):
        check(field in entry, "registry entry missing field %r" % field, actual=entry)
    check(entry["pid"] == ed.proc.pid, "registry pid mismatch",
          expected=ed.proc.pid, actual=entry["pid"])
    check(os.path.realpath(entry["socket"]) == os.path.realpath(ed.sock),
          "registry socket mismatch", expected=ed.sock, actual=entry["socket"])
    check(os.path.realpath(entry["cwd"]) == FIXTURE, "registry cwd mismatch",
          expected=FIXTURE, actual=entry["cwd"])
    check(isinstance(entry["focused_at"], (int, float)),
          "focused_at is not a number", actual=entry["focused_at"])

    ed.quit()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not os.path.exists(ed.registry_file):
            return
        time.sleep(0.1)
    raise Fail("registry file was not removed after clean editor exit",
               expected="file gone", actual=ed.registry_file)


def case_handshake(ctx):
    ctx.editor()
    relay = ctx.relay()

    init = relay.initialize()
    check(isinstance(init.get("protocolVersion"), str) and init["protocolVersion"],
          "initialize result missing protocolVersion", actual=init)
    caps = init.get("capabilities") or {}
    check("tools" in caps, "initialize capabilities missing 'tools'", actual=caps)
    check((init.get("serverInfo") or {}).get("name") == "docent",
          "serverInfo.name != 'docent'", expected="docent",
          actual=init.get("serverInfo"))
    instr = init.get("instructions")
    check(isinstance(instr, str) and instr.strip(),
          "instructions missing or empty", actual=instr)
    check(any(w in instr.lower() for w in ("navigat", "jump", "tour")),
          "instructions do not contain navigation guidance",
          actual=instr[:300])

    relay.notify("notifications/initialized")
    relay.expect_silence(0.7, "notifications/initialized produced a response")

    resp = relay.request("tools/list", {}, rid=2)
    check("result" in resp, "tools/list errored", actual=resp)
    tools = resp["result"].get("tools") or []
    names = {t.get("name") for t in tools}
    check(names == EXPECTED_TOOLS, "tools/list names mismatch",
          expected=sorted(EXPECTED_TOOLS), actual=sorted(names))
    for t in tools:
        schema = t.get("inputSchema") or {}
        check(schema.get("type") == "object",
              "tool %s inputSchema.type != 'object'" % t.get("name"), actual=schema)

    resp = relay.request("bogus/method", {}, rid=99)
    err = resp.get("error") or {}
    check(err.get("code") == -32601,
          "unknown method did not return JSON-RPC error -32601", actual=resp)

    time.sleep(0.3)  # let any stragglers land
    relay.assert_all_json()


def case_jump(ctx):
    ed = ctx.editor()
    relay = ctx.relay()
    relay.handshake()

    relay.tool_text("jump_to", {"file": APP, "line_start": 5})
    ed.wait_position(APP, 5, "jump_to did not land on app.lua:5")

    relay.tool_text("jump_to", {"file": UTIL, "line_start": 3,
                                "narration": "util.add lives here"})
    ed.wait_position(UTIL, 3, "jump_to with narration did not land on util.lua:3")

    before = ed.current()
    is_error, text = relay.call_tool(
        "jump_to", {"file": os.path.join(FIXTURE, "does_not_exist.lua"),
                    "line_start": 1})
    check(is_error, "jump_to nonexistent file should return isError=true",
          actual=text[:300])
    ed.assert_position(before[0], before[1],
                       "editor state changed after a failed jump_to")

    relay.assert_all_json()


def case_tour(ctx):
    ed = ctx.editor()
    relay = ctx.relay()
    relay.handshake()

    ed.set_position(READMEMD, 1)

    stops = [(APP, 3), (UTIL, 5), (READMEMD, 2)]

    relay.tool_text("add_tour_stop",
                    {"file": stops[0][0], "line_start": stops[0][1],
                     "narration": "stop one: module table"})
    ed.wait_position(*stops[0], what="first tour stop did not auto-jump")

    relay.tool_text("add_tour_stop",
                    {"file": stops[1][0], "line_start": stops[1][1],
                     "narration": "stop two: the return"})
    relay.tool_text("add_tour_stop",
                    {"file": stops[2][0], "line_start": stops[2][1],
                     "narration": "stop three: docs"})
    ed.assert_position(*stops[0],
                       what="queueing stops 2/3 moved the cursor (only stop 1 may auto-jump)")

    text = relay.tool_text("list_tour", {})
    for f, _ in stops:
        check(os.path.basename(f) in text,
              "list_tour does not mention stop file %s" % os.path.basename(f),
              actual=text[:500])

    ed.send_keys("]t")
    ed.wait_position(*stops[1], what="]t did not advance to stop 2")

    ed.send_keys("]t")
    ed.wait_position(*stops[2], what="second ]t did not advance to stop 3")

    ed.send_keys("]t")
    ed.assert_position(*stops[2], settle=1.0,
                       what="]t past the last stop moved the cursor")

    relay.tool_text("clear_tour", {})
    text = relay.tool_text("list_tour", {})
    check("util.lua" not in text,
          "list_tour still shows stops after clear_tour", actual=text[:500])

    relay.assert_all_json()


def case_context(ctx):
    ed = ctx.editor()
    relay = ctx.relay()
    relay.handshake()

    ed.set_position(UTIL, 7)
    text = relay.tool_text("get_editor_context", {})
    check("util.lua" in text,
          "get_editor_context does not mention the current file (util.lua)",
          actual=text[:500])
    check("7" in text,
          "get_editor_context does not mention the cursor line (7)",
          actual=text[:500])

    relay.assert_all_json()


def case_narrate_highlight(ctx):
    ed = ctx.editor()
    relay = ctx.relay()
    relay.handshake()

    ed.set_position(APP, 6)

    relay.tool_text("narrate", {"text": "This is a free-floating narration."})

    is_error, text = relay.call_tool(
        "highlight",
        {"ranges": [{"file": APP, "line_start": 1, "line_end": 3}]})
    check(not is_error, "highlight returned isError=true", actual=text[:300])
    ed.assert_position(APP, 6, "highlight moved the cursor")

    relay.assert_all_json()


def case_discovery(ctx):
    other_cwd = os.path.join(ctx.case_dir, "other")
    os.makedirs(other_cwd, exist_ok=True)
    other_file = os.path.join(other_cwd, "other.lua")
    with open(other_file, "w") as f:
        f.write("-- other project\nlocal x = 1\nreturn x\n")

    ed_a = ctx.editor(name="editor_fixture", cwd=FIXTURE)
    ed_b = ctx.editor(name="editor_other", cwd=other_cwd)

    ed_a.set_position(APP, 4)
    ed_b.set_position(other_file, 2)

    relay = ctx.relay(cwd=FIXTURE)
    relay.handshake()
    text = relay.tool_text("get_editor_context", {})
    check("app.lua" in text,
          "relay in tests/fixture did not target the fixture instance",
          expected="context mentioning app.lua", actual=text[:500])
    check("other.lua" not in text,
          "relay targeted the wrong (other-cwd) instance", actual=text[:500])

    relay.assert_all_json()


def case_empty_registry(ctx):
    # Fresh state dir, no editor registered at all.
    relay = ctx.relay(state_dir=os.path.join(ctx.case_dir, "empty-state"))

    got_clean_error = False
    resp = relay.request("initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "docent-tests", "version": "0.0.0"},
    }, rid=1)
    if "error" in resp:
        got_clean_error = True  # clean JSON-RPC error at initialize is acceptable
    else:
        relay.notify("notifications/initialized")
        resp = relay.request("tools/call",
                             {"name": "jump_to",
                              "arguments": {"file": APP, "line_start": 1}})
        if "error" in resp:
            got_clean_error = True
        else:
            result = resp["result"]
            check(result.get("isError") is True,
                  "with an empty registry, tools/call neither errored nor "
                  "returned isError=true", actual=result)
            got_clean_error = True

    check(got_clean_error, "no clean error with empty registry")
    relay.assert_all_json()


CASES = [
    ("registry", case_registry),
    ("handshake", case_handshake),
    ("jump", case_jump),
    ("tour", case_tour),
    ("context", case_context),
    ("narrate_highlight", case_narrate_highlight),
    ("discovery_two_instances", case_discovery),
    ("discovery_empty_registry", case_empty_registry),
]


def main():
    if len(sys.argv) != 2:
        print("usage: driver.py <tmpdir>", file=sys.stderr)
        return 2
    tmp = sys.argv[1]

    missing = [p for p in (NVIM,) if not os.path.exists(p)]
    if missing:
        print("FATAL: nvim not found at %s (set NVIM_BIN)" % NVIM, file=sys.stderr)
        return 2

    impl_missing = [p for p in (RELAY_LUA, os.path.join(ROOT, "lua", "docent"))
                    if not os.path.exists(p)]
    if impl_missing:
        print("IMPLEMENTATION NOT PRESENT — cannot run tests yet.")
        for p in impl_missing:
            print("  missing: %s" % p)
        return 2

    global SOCK_DIR
    SOCK_DIR = tempfile.mkdtemp(prefix="docent-t-")

    results = []
    for name, fn in CASES:
        case_dir = os.path.join(tmp, name)
        os.makedirs(case_dir, exist_ok=True)
        ctx = Ctx(case_dir)
        t0 = time.monotonic()
        try:
            fn(ctx)
            results.append((name, True, "", time.monotonic() - t0))
        except Fail as e:
            results.append((name, False, str(e), time.monotonic() - t0))
        except Exception as e:  # harness bug or unexpected condition
            results.append((name, False, "%s: %s" % (type(e).__name__, e),
                            time.monotonic() - t0))
        finally:
            failed = not results[-1][1]
            tails = ctx.stderr_tails() if failed else ""
            ctx.cleanup()
            if failed and tails:
                results[-1] = (results[-1][0], False,
                               results[-1][2] + "\n" + tails, results[-1][3])

    shutil.rmtree(SOCK_DIR, ignore_errors=True)

    print()
    print("=" * 64)
    n_pass = 0
    for name, ok, msg, dur in results:
        status = "PASS" if ok else "FAIL"
        n_pass += ok
        print("%-4s  %-28s (%.1fs)" % (status, name, dur))
        if not ok:
            for line in msg.splitlines():
                print("      " + line)
    print("=" * 64)
    print("%d/%d cases passed" % (n_pass, len(results)))
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
