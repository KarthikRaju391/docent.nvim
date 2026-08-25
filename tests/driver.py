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
import re
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
NOSAVEPROMPT_INIT = os.path.join(ROOT, "tests",
                                 "minimal_init_nosaveprompt.lua")
RELAY_LUA = os.path.join(ROOT, "relay", "relay.lua")

APP = os.path.join(FIXTURE, "app.lua")
UTIL = os.path.join(FIXTURE, "lib", "util.lua")
READMEMD = os.path.join(FIXTURE, "README.md")

EXPECTED_TOOLS = {
    "jump_to", "highlight", "show_info", "add_tour_stop",
    "clear_tour", "list_tour", "get_editor_context",
    "save_tour", "list_saved_tours", "load_tour",
}

# Pacing keys configured in tests/minimal_init.lua (deliberately non-default,
# to prove instructions/hints report the REAL bound keys).
NEXT_KEY = "]v"
PREV_KEY = "[v"
SKIP_KEY = "]V"  # derived from NEXT_KEY: leave a sub-tour without finishing it
DEFAULT_NEXT_KEY = "]t"

DOCENT_DIR = os.path.join(FIXTURE, ".docent")


def clean_docent():
    """tests/fixture is inside the repo working tree; keep it clean."""
    shutil.rmtree(DOCENT_DIR, ignore_errors=True)


def wait_for(pred, timeout, what):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.1)
    raise Fail(what)


def tour_file(slug):
    return os.path.join(DOCENT_DIR, "tours", slug + ".json")


def assert_pending(text, title, slug):
    """save_tour must only propose: pending status, no path, nothing on disk."""
    check("pending" in text.lower(),
          "save_tour result does not report a pending confirmation",
          expected="a status like pending_confirmation", actual=text[:400])
    check(title in text,
          "save_tour result does not echo the proposed title",
          expected=title, actual=text[:400])
    data = _try_json(text)
    if data is not None:
        check("path" not in data,
              "save_tour result carries a path; it must not write anything "
              "before the user confirms", actual=data)
    check(not os.path.exists(tour_file(slug)),
          "save_tour wrote a file before the user confirmed",
          expected="no file", actual=tour_file(slug))

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

    def __init__(self, name, case_dir, cwd, state_dir, init=None):
        self.name = name
        self.sock = new_sock(name)
        self.stderr_path = os.path.join(case_dir, name + ".stderr.log")
        self.state_dir = state_dir
        env = dict(os.environ, XDG_STATE_HOME=state_dir)
        env.pop("NVIM", None)  # never inherit an outer nvim
        self._stderr_f = open(self.stderr_path, "wb")
        self.proc = subprocess.Popen(
            [NVIM, "--headless", "--noplugin", "-i", "NONE",
             "-u", init or MINIMAL_INIT, "--listen", self.sock],
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

    def cmd(self, command):
        """Run an ex command via RPC.

        Never drive commands with remote-send '<Esc>:Cmd<CR>': while a tour
        is live docent maps <Esc>, and two such sends within 1s register as a
        real double-Esc, ending the whole tour.
        """
        self.expr('execute("%s")' % command)

    def stub_ui_input(self, case_dir, answer, name="ui_input_stub.lua"):
        """Replace vim.ui.input with a recording stub.

        answer=None declines (callback gets nil); a string accepts with that
        text. Call count / prompt / default land in g: vars so a test can prove
        the prompt was — or was not — shown.
        """
        cb_arg = "nil" if answer is None else json.dumps(answer)
        lua = (
            "vim.g.docent_test_input_count = 0\n"
            "vim.g.docent_test_input_prompt = ''\n"
            "vim.g.docent_test_input_default = ''\n"
            "vim.ui.input = function(opts, cb)\n"
            "  vim.g.docent_test_input_count = "
            "(vim.g.docent_test_input_count or 0) + 1\n"
            "  vim.g.docent_test_input_prompt = (opts and opts.prompt) or ''\n"
            "  vim.g.docent_test_input_default = (opts and opts.default) or ''\n"
            "  if cb then cb(%s) end\n"
            "end\n" % cb_arg
        )
        path = os.path.join(case_dir, name)
        with open(path, "w") as f:
            f.write(lua)
        self.cmd("luafile " + path)

    def input_count(self):
        return int(self.expr("get(g:, 'docent_test_input_count', 0)"))

    def input_default(self):
        return self.expr("get(g:, 'docent_test_input_default', '')")

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

    def call_raw(self, name, arguments):
        """tools/call → the full result dict (content shape asserted)."""
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
        return result

    def call_tool(self, name, arguments):
        result = self.call_raw(name, arguments)
        return bool(result.get("isError")), result["content"][0]["text"]

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

    def editor(self, name="editor", cwd=FIXTURE, state_dir=None, wait=True,
               init=None):
        ed = Editor(name, self.case_dir, cwd, state_dir or self.state_dir,
                    init=init)
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
    check("branch" in instr.lower(),
          "instructions do not mention branching for tangents",
          actual=instr[:600])
    # The get_editor_context tour field was reverted; instructions must not
    # still advertise it.
    for sent in re.split(r"[.;\n]", instr):
        low = sent.lower()
        check(not ("get_editor_context" in low and "tour" in low),
              "instructions still describe get_editor_context reporting tour "
              "info (feature was reverted)", actual=sent.strip())

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
        check("narration" not in json.dumps(schema),
              "tool %s schema still mentions 'narration' (renamed to 'info')"
              % t.get("name"), actual=schema)

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
                                "info": "util.add lives here"})
    ed.wait_position(UTIL, 3, "jump_to with info did not land on util.lua:3")

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
                     "info": "stop one: module table"})
    ed.wait_position(*stops[0], what="first tour stop did not auto-jump")

    relay.tool_text("add_tour_stop",
                    {"file": stops[1][0], "line_start": stops[1][1],
                     "info": "stop two: the return"})
    relay.tool_text("add_tour_stop",
                    {"file": stops[2][0], "line_start": stops[2][1],
                     "info": "stop three: docs"})
    ed.assert_position(*stops[0],
                       what="queueing stops 2/3 moved the cursor (only stop 1 may auto-jump)")

    text = relay.tool_text("list_tour", {})
    for f, _ in stops:
        check(os.path.basename(f) in text,
              "list_tour does not mention stop file %s" % os.path.basename(f),
              actual=text[:500])

    ed.send_keys(NEXT_KEY)
    ed.wait_position(*stops[1],
                     what="%s did not advance to stop 2" % NEXT_KEY)

    ed.send_keys(NEXT_KEY)
    ed.wait_position(*stops[2],
                     what="second %s did not advance to stop 3" % NEXT_KEY)

    ed.send_keys(NEXT_KEY)
    ed.assert_position(*stops[2], settle=1.0,
                       what="%s past the last stop moved the cursor" % NEXT_KEY)

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


def case_info_highlight(ctx):
    ed = ctx.editor()
    relay = ctx.relay()
    relay.handshake()

    ed.set_position(APP, 6)

    relay.tool_text("show_info", {"text": "This is free-floating info."})

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


def case_pacing_keys(ctx):
    ed = ctx.editor()
    relay = ctx.relay()

    # The plugin itself must report the configured keys.
    keys_json = ed.expr(
        "luaeval('vim.json.encode(require(\"docent\").pacing_keys())')")
    check(NEXT_KEY in keys_json,
          "pacing_keys() does not report the configured next key",
          expected=NEXT_KEY, actual=keys_json)
    check(DEFAULT_NEXT_KEY not in keys_json,
          "pacing_keys() still reports the default %s" % DEFAULT_NEXT_KEY,
          actual=keys_json)

    # With a live instance discoverable at initialize time, instructions must
    # carry the REAL bound keys.
    init = relay.handshake()
    instr = init.get("instructions") or ""
    check(NEXT_KEY in instr,
          "initialize instructions do not mention the real bound key %s"
          % NEXT_KEY, actual=instr[:600])
    check(DEFAULT_NEXT_KEY not in instr,
          "initialize instructions mention %s, but that key is not bound"
          % DEFAULT_NEXT_KEY, actual=instr[:600])

    # First stop of a tour returns a pace_with hint with the real key.
    result = relay.call_raw("add_tour_stop",
                            {"file": APP, "line_start": 3,
                             "info": "first stop"})
    check(not result.get("isError"), "add_tour_stop failed", actual=result)
    dump = json.dumps(result)
    check("pace_with" in dump,
          "first add_tour_stop result has no pace_with hint", actual=dump[:600])
    check(NEXT_KEY in dump,
          "pace_with hint does not carry the real key %s" % NEXT_KEY,
          actual=dump[:600])

    relay.assert_all_json()


def case_persistent_tours(ctx):
    clean_docent()
    try:
        ed = ctx.editor()
        relay = ctx.relay()
        relay.handshake()

        is_error, text = relay.call_tool("save_tour", {"title": "Import Flow"})
        check(is_error, "save_tour with an empty tour should be isError=true",
              actual=text[:300])

        stops = [(APP, 3, "module table"), (UTIL, 5, "the add"),
                 (READMEMD, 2, "docs")]
        for f, l, n in stops:
            relay.tool_text("add_tour_stop",
                            {"file": f, "line_start": l, "info": n})
        ed.wait_position(APP, 3, "first tour stop did not auto-jump")

        # save_tour only PROPOSES: nothing on disk until the user confirms.
        text = relay.tool_text("save_tour", {"title": "Import Flow"})
        assert_pending(text, "Import Flow", "import-flow")

        # Confirm at the end of the tour: pace past the last stop, accepting
        # the proposed title at the vim.ui.input prompt.
        ed.stub_ui_input(ctx.case_dir, "Import Flow")
        ed.send_keys(NEXT_KEY)
        ed.wait_position(UTIL, 5, "%s did not advance to stop 2" % NEXT_KEY)
        ed.send_keys(NEXT_KEY)
        ed.wait_position(READMEMD, 2, "%s did not advance to stop 3" % NEXT_KEY)
        ed.send_keys(NEXT_KEY)

        tour_path = os.path.join(DOCENT_DIR, "tours", "import-flow.json")
        wait_for(lambda: os.path.exists(tour_path), 3.0,
                 "pacing past the last stop with a pending proposal did not "
                 "write %s" % tour_path)
        check(ed.input_count() >= 1,
              "the tour was saved without ever prompting via vim.ui.input")
        check(ed.input_default() == "Import Flow",
              "the confirmation prompt did not default to the proposed title",
              expected="Import Flow", actual=ed.input_default())
        with open(tour_path) as f:
            saved = json.load(f)
        check(saved.get("title") == "Import Flow", "saved tour title wrong",
              expected="Import Flow", actual=saved.get("title"))
        check(saved.get("slug") == "import-flow", "saved tour slug wrong",
              expected="import-flow", actual=saved.get("slug"))
        check("created_at" in saved, "saved tour missing created_at",
              actual=sorted(saved.keys()))
        saved_stops = saved.get("stops")
        check(isinstance(saved_stops, list) and len(saved_stops) == len(stops),
              "saved tour stops count wrong",
              expected=len(stops), actual=saved_stops)
        for i, stop in enumerate(saved_stops):
            f = stop.get("file")
            check(isinstance(f, str) and f, "stop %d has no file" % i, actual=stop)
            check(not os.path.isabs(f),
                  "stop %d file is absolute; must be relative to project root"
                  % i, actual=f)
            check(os.path.exists(os.path.join(FIXTURE, f)),
                  "stop %d relative file does not resolve under project root"
                  % i, actual=f)
            check(isinstance(stop.get("line_start"), int),
                  "stop %d missing line_start" % i, actual=stop)
            check(isinstance(stop.get("info"), str) and stop["info"],
                  "stop %d missing info" % i, actual=stop)
            check("narration" not in stop,
                  "stop %d still has a 'narration' key (renamed to 'info')"
                  % i, actual=stop)

        result = relay.call_raw("list_saved_tours", {})
        check(not result.get("isError"), "list_saved_tours failed", actual=result)
        dump = json.dumps(result)
        check("import-flow" in dump or "Import Flow" in dump,
              "list_saved_tours does not mention the saved tour",
              actual=dump[:600])
        check("stop_count" in dump or re.search(r"\b3\b\s*stops?", dump),
              "list_saved_tours does not report stop_count", actual=dump[:600])

        relay.tool_text("clear_tour", {})
        text = relay.tool_text("list_tour", {})
        check("util.lua" not in text, "clear_tour did not empty the tour",
              actual=text[:300])

        result = relay.call_raw("load_tour", {"slug": "import-flow"})
        check(not result.get("isError"), "load_tour failed", actual=result)
        dump = json.dumps(result)
        for f, _, _ in stops:
            check(os.path.basename(f) in dump,
                  "load_tour result does not contain the full stops list "
                  "(missing %s)" % os.path.basename(f), actual=dump[:800])
        ed.wait_position(APP, 3, "load_tour did not navigate to stop 1")
        text = relay.tool_text("list_tour", {})
        for f, _, _ in stops:
            check(os.path.basename(f) in text,
                  "after load_tour, list_tour is missing %s"
                  % os.path.basename(f), actual=text[:500])

        is_error, text = relay.call_tool("load_tour", {"slug": "no-such-tour"})
        check(is_error, "load_tour with unknown slug should be isError=true",
              actual=text[:300])

        relay.assert_all_json()
    finally:
        clean_docent()


def case_user_commands(ctx):
    clean_docent()
    try:
        ed = ctx.editor()
        relay = ctx.relay()
        relay.handshake()

        for cmd in ("DocentRestart", "DocentSave", "DocentTours",
                    "DocentInfo", "DocentEnd"):
            got = ed.expr("exists(':%s')" % cmd)
            check(got == "2", "user command :%s does not exist" % cmd,
                  expected="2 (full command match)", actual=got)

        relay.tool_text("add_tour_stop", {"file": APP, "line_start": 3,
                                          "info": "stop one"})
        relay.tool_text("add_tour_stop", {"file": UTIL, "line_start": 5,
                                          "info": "stop two"})
        ed.wait_position(APP, 3, "first tour stop did not auto-jump")

        ed.send_keys(NEXT_KEY)
        ed.wait_position(UTIL, 5, "%s did not advance to stop 2" % NEXT_KEY)

        ed.cmd("DocentRestart")
        ed.wait_position(APP, 3, ":DocentRestart did not return to stop 1")

        ed.cmd("DocentSave Cmd Flow")
        tours_dir = os.path.join(DOCENT_DIR, "tours")
        wait_for(lambda: os.path.isdir(tours_dir) and os.listdir(tours_dir),
                 3.0, ":DocentSave did not create a tour file under %s"
                 % tours_dir)

        relay.assert_all_json()
    finally:
        clean_docent()


def _field_is(text, field, value):
    """Match a numeric field in JSON-ish or prose tool text."""
    return re.search(r'"?%s"?\s*[:=]\s*%d\b' % (field, value), text) is not None


def _try_json(text):
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def case_subtour_branching(ctx):
    ed = ctx.editor()
    relay = ctx.relay()
    relay.handshake()

    root = [(APP, 3), (UTIL, 5), (READMEMD, 2)]
    for i, (f, l) in enumerate(root):
        relay.tool_text("add_tour_stop",
                        {"file": f, "line_start": l,
                         "info": "root stop %d" % (i + 1)})
    ed.wait_position(*root[0], what="root stop 1 did not auto-jump")

    ed.send_keys(NEXT_KEY)
    ed.wait_position(*root[1],
                     what="%s did not advance to root stop 2" % NEXT_KEY)

    # Branch from root stop 2: the branch call auto-navigates to sub stop 1.
    relay.tool_text("add_tour_stop",
                    {"file": APP, "line_start": 6, "branch": True,
                     "info": "sub stop 1"})
    ed.wait_position(APP, 6,
                     "add_tour_stop{branch:true} did not auto-jump to the "
                     "sub-tour's stop 1")

    # [v at stop 1 of a sub-tour stays put (no pop).
    ed.send_keys(PREV_KEY)
    ed.assert_position(APP, 6, settle=1.0,
                       what="%s at sub stop 1 moved the cursor (must not pop)"
                       % PREV_KEY)

    relay.tool_text("add_tour_stop",
                    {"file": READMEMD, "line_start": 5,
                     "info": "sub stop 2"})
    ed.assert_position(APP, 6, what="second sub stop moved the cursor")

    text = relay.tool_text("list_tour", {})
    data = _try_json(text)
    if data is not None:
        stops = data.get("stops")
        check(isinstance(stops, list) and len(stops) == 2,
              "nested list_tour does not show the 2-stop sub frame",
              expected=2, actual=stops)
        check(data.get("depth") == 2, "nested list_tour depth != 2",
              expected=2, actual=data.get("depth"))
        parent = data.get("parent") or {}
        check("title" in parent, "nested list_tour parent has no title",
              actual=parent)
        check(parent.get("anchor") == 2, "nested list_tour parent.anchor != 2",
              expected=2, actual=parent)
        check(parent.get("total") == 3, "nested list_tour parent.total != 3",
              expected=3, actual=parent)
    else:
        check(_field_is(text, "depth", 2), "nested list_tour depth != 2",
              actual=text[:600])
        check(_field_is(text, "anchor", 2),
              "nested list_tour parent.anchor != 2", actual=text[:600])
        check(_field_is(text, "total", 3),
              "nested list_tour parent.total != 3", actual=text[:600])

    # Pace through the sub-tour; ]v past its end pops back to the anchor.
    ed.send_keys(NEXT_KEY)
    ed.wait_position(READMEMD, 5,
                     "%s did not advance to sub stop 2" % NEXT_KEY)
    ed.send_keys(NEXT_KEY)
    ed.wait_position(*root[1],
                     what="%s past the sub-tour end did not pop back to root "
                     "stop 2" % NEXT_KEY)
    text = relay.tool_text("list_tour", {})
    check(_field_is(text, "depth", 1),
          "after popping, list_tour depth != 1", actual=text[:600])
    check(_field_is(text, "current", 2),
          "after popping, list_tour current != 2", actual=text[:600])

    # Branch again; clear_tour {} pops one level rather than nuking the tree.
    relay.tool_text("add_tour_stop",
                    {"file": APP, "line_start": 9, "branch": True,
                     "info": "second branch stop 1"})
    ed.wait_position(APP, 9, "second branch did not auto-jump")

    text = relay.tool_text("clear_tour", {})
    check("popped_to" in text,
          "nested clear_tour result has no popped_to", actual=text[:600])
    check(_field_is(text, "current", 2),
          "nested clear_tour popped_to.current != 2", actual=text[:600])
    ed.wait_position(*root[1],
                     what="nested clear_tour did not return the cursor to the "
                     "anchor (root stop 2)")
    text = relay.tool_text("list_tour", {})
    check(_field_is(text, "depth", 1),
          "after nested clear_tour, list_tour depth != 1", actual=text[:600])

    # clear_tour at the root: everything gone.
    relay.tool_text("clear_tour", {})
    text = relay.tool_text("list_tour", {})
    for f, _ in root:
        check(os.path.basename(f) not in text,
              "root clear_tour left stops behind", actual=text[:600])

    relay.assert_all_json()


def case_subtour_clear_all(ctx):
    ed = ctx.editor()
    relay = ctx.relay()
    relay.handshake()

    relay.tool_text("add_tour_stop", {"file": APP, "line_start": 3,
                                      "info": "root stop 1"})
    relay.tool_text("add_tour_stop", {"file": UTIL, "line_start": 5,
                                      "info": "root stop 2"})
    ed.wait_position(APP, 3, "root stop 1 did not auto-jump")
    relay.tool_text("add_tour_stop", {"file": READMEMD, "line_start": 2,
                                      "branch": True,
                                      "info": "sub stop 1"})
    ed.wait_position(READMEMD, 2, "branch did not auto-jump")

    relay.tool_text("clear_tour", {"all": True})
    text = relay.tool_text("list_tour", {})
    for f in (APP, UTIL, READMEMD):
        check(os.path.basename(f) not in text,
              "clear_tour {all:true} did not wipe the whole tree in one call",
              actual=text[:600])

    relay.assert_all_json()


def case_subtour_docent_back(ctx):
    ed = ctx.editor()
    relay = ctx.relay()
    relay.handshake()

    got = ed.expr("exists(':DocentBack')")
    check(got == "2", "user command :DocentBack does not exist",
          expected="2 (full command match)", actual=got)

    relay.tool_text("add_tour_stop", {"file": APP, "line_start": 3,
                                      "info": "root stop 1"})
    relay.tool_text("add_tour_stop", {"file": UTIL, "line_start": 5,
                                      "info": "root stop 2"})
    ed.wait_position(APP, 3, "root stop 1 did not auto-jump")
    ed.send_keys(NEXT_KEY)
    ed.wait_position(UTIL, 5, "%s did not advance to root stop 2" % NEXT_KEY)

    relay.tool_text("add_tour_stop", {"file": APP, "line_start": 6,
                                      "branch": True,
                                      "info": "sub stop 1"})
    ed.wait_position(APP, 6, "branch did not auto-jump")

    ed.cmd("DocentBack")
    ed.wait_position(UTIL, 5, ":DocentBack did not pop back to the anchor")
    text = relay.tool_text("list_tour", {})
    check(_field_is(text, "depth", 1),
          "after :DocentBack, list_tour depth != 1", actual=text[:600])

    # At the root, :DocentBack must not error the instance (ed.cmd raises if
    # the command throws).
    ed.cmd("DocentBack")
    check(ed.expr("1+1") == "2",
          ":DocentBack at root broke the instance (RPC unresponsive)")
    ed.assert_position(UTIL, 5, ":DocentBack at root moved the cursor",
                       settle=0.2)

    # With no tour at all, it must also stay quiet.
    relay.tool_text("clear_tour", {"all": True})
    ed.cmd("DocentBack")
    check(ed.expr("1+1") == "2",
          ":DocentBack with no tour broke the instance (RPC unresponsive)")

    relay.assert_all_json()


def case_subtour_save(ctx):
    clean_docent()
    try:
        ed = ctx.editor()
        relay = ctx.relay()
        relay.handshake()

        for f, l in ((APP, 3), (UTIL, 5), (READMEMD, 2)):
            relay.tool_text("add_tour_stop",
                            {"file": f, "line_start": l, "info": "root"})
        ed.wait_position(APP, 3, "root stop 1 did not auto-jump")
        ed.send_keys(NEXT_KEY)
        ed.wait_position(UTIL, 5,
                         "%s did not advance to root stop 2" % NEXT_KEY)

        relay.tool_text("add_tour_stop", {"file": APP, "line_start": 6,
                                          "branch": True,
                                          "info": "sub stop 1"})
        ed.wait_position(APP, 6, "branch did not auto-jump")
        relay.tool_text("add_tour_stop", {"file": READMEMD, "line_start": 5,
                                          "info": "sub stop 2"})

        text = relay.tool_text("save_tour", {"title": "Sub Flow"})
        assert_pending(text, "Sub Flow", "sub-flow")

        # Confirm at the end of the SUB frame.
        ed.stub_ui_input(ctx.case_dir, "Sub Flow")
        ed.send_keys(NEXT_KEY)
        ed.wait_position(READMEMD, 5,
                         "%s did not advance to sub stop 2" % NEXT_KEY)
        ed.send_keys(NEXT_KEY)

        tour_path = tour_file("sub-flow")
        wait_for(lambda: os.path.exists(tour_path), 3.0,
                 "pacing past the last sub stop with a pending proposal did "
                 "not write %s" % tour_path)
        with open(tour_path) as f:
            saved = json.load(f)
        stops = saved.get("stops")
        check(isinstance(stops, list) and len(stops) == 2,
              "save_tour while nested must save ONLY the active sub-tour's "
              "stops (expected the 2 sub stops)", expected=2, actual=stops)
        files = {s.get("file") for s in stops}
        check(files == {"app.lua", "README.md"},
              "nested save_tour saved the wrong stops",
              expected={"app.lua", "README.md"}, actual=files)
        for i, stop in enumerate(stops):
            check(isinstance(stop.get("info"), str) and stop["info"],
                  "saved sub stop %d missing info" % i, actual=stop)
            check("narration" not in stop,
                  "saved sub stop %d still has a 'narration' key "
                  "(renamed to 'info')" % i, actual=stop)

        relay.assert_all_json()
    finally:
        clean_docent()


def case_save_confirm_flow(ctx):
    """The four confirmation paths: accept, decline, rename, discard."""
    clean_docent()
    try:
        ed = ctx.editor()
        relay = ctx.relay()
        relay.handshake()

        def queue_two():
            relay.tool_text("clear_tour", {"all": True})
            relay.tool_text("add_tour_stop", {"file": APP, "line_start": 3,
                                              "info": "stop one"})
            relay.tool_text("add_tour_stop", {"file": UTIL, "line_start": 5,
                                              "info": "stop two"})
            ed.wait_position(APP, 3, "stop 1 did not auto-jump")

        def pace_past_end():
            ed.send_keys(NEXT_KEY)
            ed.wait_position(UTIL, 5,
                             "%s did not advance to stop 2" % NEXT_KEY)
            ed.send_keys(NEXT_KEY)

        # --- accept: prompt answered with the proposed title -> file written.
        queue_two()
        assert_pending(relay.tool_text("save_tour", {"title": "Accept Flow"}),
                       "Accept Flow", "accept-flow")
        ed.stub_ui_input(ctx.case_dir, "Accept Flow")
        pace_past_end()
        wait_for(lambda: os.path.exists(tour_file("accept-flow")), 3.0,
                 "accept path: confirming the prompt did not write %s"
                 % tour_file("accept-flow"))

        # --- decline: prompt cancelled (nil) -> nothing written.
        queue_two()
        assert_pending(relay.tool_text("save_tour", {"title": "Decline Flow"}),
                       "Decline Flow", "decline-flow")
        ed.stub_ui_input(ctx.case_dir, None)
        pace_past_end()
        wait_for(lambda: ed.input_count() >= 1, 3.0,
                 "decline path: the confirmation prompt was never shown")
        time.sleep(0.5)
        check(not os.path.exists(tour_file("decline-flow")),
              "decline path: cancelling the prompt still wrote a tour file",
              expected="no file", actual=tour_file("decline-flow"))

        # --- rename: prompt answered with an edited title.
        queue_two()
        assert_pending(relay.tool_text("save_tour", {"title": "Original Name"}),
                       "Original Name", "original-name")
        ed.stub_ui_input(ctx.case_dir, "Edited Name")
        pace_past_end()
        renamed = tour_file("edited-name")
        wait_for(lambda: os.path.exists(renamed), 3.0,
                 "rename path: the edited title was not used (expected %s)"
                 % renamed)
        with open(renamed) as f:
            saved = json.load(f)
        check(saved.get("title") == "Edited Name",
              "rename path: saved title is not the edited one",
              expected="Edited Name", actual=saved.get("title"))
        check(not os.path.exists(tour_file("original-name")),
              "rename path: the proposed title was also written",
              expected="no file", actual=tour_file("original-name"))

        # --- discard: an explicit exit drops the proposal with NO prompt.
        queue_two()
        assert_pending(relay.tool_text("save_tour", {"title": "Discard Flow"}),
                       "Discard Flow", "discard-flow")
        ed.stub_ui_input(ctx.case_dir, "Discard Flow")  # resets the counter
        ed.cmd("DocentEnd")
        time.sleep(0.8)
        check(ed.input_count() == 0,
              ":DocentEnd prompted for a save; an explicit exit must discard "
              "the proposal silently", expected=0, actual=ed.input_count())
        check(not os.path.exists(tour_file("discard-flow")),
              ":DocentEnd wrote a tour file; the proposal must be discarded",
              expected="no file", actual=tour_file("discard-flow"))

        relay.assert_all_json()
    finally:
        clean_docent()


def case_save_discard_paths(ctx):
    """Every explicit exit must drop a pending proposal with NO prompt."""
    clean_docent()
    try:
        ed = ctx.editor()
        relay = ctx.relay()
        relay.handshake()

        # A saved tour to feed the load_tour phase (:DocentSave writes at once).
        relay.tool_text("add_tour_stop", {"file": APP, "line_start": 3,
                                          "info": "seed stop"})
        ed.wait_position(APP, 3, "seed stop did not auto-jump")
        ed.cmd("DocentSave Seed Tour")
        wait_for(lambda: os.path.exists(tour_file("seed-tour")), 3.0,
                 ":DocentSave did not write the seed tour")
        relay.tool_text("clear_tour", {"all": True})

        def build(nested):
            relay.tool_text("clear_tour", {"all": True})
            relay.tool_text("add_tour_stop", {"file": APP, "line_start": 3,
                                              "info": "root stop 1"})
            relay.tool_text("add_tour_stop", {"file": UTIL, "line_start": 5,
                                              "info": "root stop 2"})
            ed.wait_position(APP, 3, "root stop 1 did not auto-jump")
            if nested:
                relay.tool_text("add_tour_stop",
                                {"file": READMEMD, "line_start": 5,
                                 "branch": True, "info": "sub stop 1"})
                ed.wait_position(READMEMD, 5, "branch did not auto-jump")

        # A phase whose exit silently did nothing would satisfy "no prompt, no
        # file" vacuously, so each phase also proves the exit really fired:
        # nested exits land back on the anchor, root exits empty the tour.
        def popped_to_anchor():
            ed.wait_position(APP, 3, "exit did not pop back to the anchor")

        def tour_gone():
            wait_for(lambda: "util.lua" not in relay.tool_text("list_tour", {}),
                     3.0, "exit did not end the tour")

        phases = [
            ("skip key %s" % SKIP_KEY, True,
             lambda: ed.send_keys(SKIP_KEY), popped_to_anchor),
            (":DocentBack", True,
             lambda: ed.cmd("DocentBack"), popped_to_anchor),
            ("double-<Esc>", False,
             lambda: ed.send_keys("<Esc><Esc>"), tour_gone),
            ("clear_tour {}", False,
             lambda: relay.tool_text("clear_tour", {}), tour_gone),
            ("clear_tour {all:true}", True,
             lambda: relay.tool_text("clear_tour", {"all": True}), tour_gone),
            ("load_tour", False,
             lambda: relay.tool_text("load_tour", {"slug": "seed-tour"}),
             tour_gone),
        ]

        check(ed.expr("!empty(maparg('%s', 'n', 0, 1))" % SKIP_KEY) == "1",
              "the skip key %s is not bound, so its phase would pass "
              "vacuously" % SKIP_KEY)

        for i, (label, nested, trigger, prove_exit) in enumerate(phases):
            title = "Discard Phase %d" % (i + 1)
            slug = "discard-phase-%d" % (i + 1)
            build(nested)
            assert_pending(relay.tool_text("save_tour", {"title": title}),
                           title, slug)
            ed.stub_ui_input(ctx.case_dir, title)  # resets the call counter
            trigger()
            try:
                prove_exit()
            except Fail as e:
                raise Fail("%s: %s" % (label, e))
            time.sleep(0.5)
            check(ed.input_count() == 0,
                  "%s prompted to save; an explicit exit must discard the "
                  "proposal silently" % label,
                  expected=0, actual=ed.input_count())
            check(not os.path.exists(tour_file(slug)),
                  "%s wrote %s; the pending proposal must be discarded"
                  % (label, os.path.basename(tour_file(slug))),
                  expected="no file", actual=tour_file(slug))

        relay.assert_all_json()
    finally:
        clean_docent()


def case_save_prompt_disabled(ctx):
    """save_prompt=false silences the prompt but not :DocentSave."""
    clean_docent()
    try:
        ed = ctx.editor(init=NOSAVEPROMPT_INIT)
        relay = ctx.relay()
        relay.handshake()

        ed.stub_ui_input(ctx.case_dir, "Should Never Be Used")

        relay.tool_text("add_tour_stop", {"file": APP, "line_start": 3,
                                          "info": "stop one"})
        relay.tool_text("add_tour_stop", {"file": UTIL, "line_start": 5,
                                          "info": "stop two"})
        ed.wait_position(APP, 3, "stop 1 did not auto-jump")

        assert_pending(relay.tool_text("save_tour", {"title": "Nosave Flow"}),
                       "Nosave Flow", "nosave-flow")

        ed.send_keys(NEXT_KEY)
        ed.wait_position(UTIL, 5, "%s did not advance to stop 2" % NEXT_KEY)
        ed.send_keys(NEXT_KEY)
        time.sleep(1.0)

        check(ed.input_count() == 0,
              "save_prompt=false still prompted at the end of the tour",
              expected=0, actual=ed.input_count())
        check(not os.path.exists(tour_file("nosave-flow")),
              "save_prompt=false still wrote a tour file",
              expected="no file", actual=tour_file("nosave-flow"))

        # Saving explicitly must still work in the same instance.
        ed.cmd("DocentSave Immediate Flow")
        wait_for(lambda: os.path.exists(tour_file("immediate-flow")), 3.0,
                 ":DocentSave did not write immediately with "
                 "save_prompt=false (only the prompt should be disabled)")

        relay.assert_all_json()
    finally:
        clean_docent()


def case_esc_ends_tour(ctx):
    ed = ctx.editor()
    relay = ctx.relay()
    relay.handshake()

    # No tour yet: <Esc> must have no docent mapping.
    check(ed.expr("!empty(maparg('<Esc>', 'n', 0, 1))") == "0",
          "<Esc> is mapped in normal mode before any tour exists")

    stops = [(APP, 3), (UTIL, 5), (READMEMD, 2)]
    for i, (f, l) in enumerate(stops):
        relay.tool_text("add_tour_stop",
                        {"file": f, "line_start": l,
                         "info": "stop %d" % (i + 1)})
    ed.wait_position(*stops[0], what="stop 1 did not auto-jump")
    ed.send_keys(NEXT_KEY)
    ed.wait_position(*stops[1], what="%s did not advance to stop 2" % NEXT_KEY)

    check(ed.expr("!empty(maparg('<Esc>', 'n', 0, 1))") == "1",
          "no transient <Esc> mapping exists while the tour is live")

    # A single <Esc> does not end the tour.
    ed.send_keys("<Esc>")
    time.sleep(1.2)  # let the single press expire the 1000ms double window
    text = relay.tool_text("list_tour", {})
    for f, _ in stops:
        check(os.path.basename(f) in text,
              "a single <Esc> ended the tour", actual=text[:600])
    check(_field_is(text, "current", 2),
          "a single <Esc> changed the current stop", actual=text[:600])

    # Two <Esc> within 1s (one send) end the whole tour.
    ed.send_keys("<Esc><Esc>")
    wait_for(lambda: "util.lua" not in relay.tool_text("list_tour", {}),
             3.0, "double-<Esc> did not end the tour")
    text = relay.tool_text("list_tour", {})
    for f, _ in stops:
        check(os.path.basename(f) not in text,
              "double-<Esc> left stops behind", actual=text[:600])
    check(not _field_is(text, "depth", 2),
          "after double-<Esc>, list_tour still reports a nested depth",
          actual=text[:600])

    # After the tour ends, the transient mapping is gone.
    wait_for(lambda: ed.expr("!empty(maparg('<Esc>', 'n', 0, 1))") == "0",
             3.0, "the transient <Esc> mapping was not removed when the tour "
             "ended")

    # Branch state: double-<Esc> from inside a sub-tour clears the WHOLE tree.
    for i, (f, l) in enumerate(stops[:2]):
        relay.tool_text("add_tour_stop",
                        {"file": f, "line_start": l,
                         "info": "root stop %d" % (i + 1)})
    ed.wait_position(*stops[0], what="root stop 1 did not auto-jump (round 2)")
    relay.tool_text("add_tour_stop", {"file": READMEMD, "line_start": 5,
                                      "branch": True, "info": "sub stop 1"})
    ed.wait_position(READMEMD, 5, "branch did not auto-jump")

    ed.send_keys("<Esc><Esc>")
    wait_for(lambda: "app.lua" not in relay.tool_text("list_tour", {}),
             3.0, "double-<Esc> from a sub-tour did not clear the tree")
    text = relay.tool_text("list_tour", {})
    for f in (APP, UTIL, READMEMD):
        check(os.path.basename(f) not in text,
              "double-<Esc> from a sub-tour left part of the tree",
              actual=text[:600])

    relay.assert_all_json()


CASES = [
    ("registry", case_registry),
    ("handshake", case_handshake),
    ("jump", case_jump),
    ("tour", case_tour),
    ("context", case_context),
    ("info_highlight", case_info_highlight),
    ("discovery_two_instances", case_discovery),
    ("discovery_empty_registry", case_empty_registry),
    ("pacing_keys", case_pacing_keys),
    ("persistent_tours", case_persistent_tours),
    ("user_commands", case_user_commands),
    ("subtour_branching", case_subtour_branching),
    ("subtour_clear_all", case_subtour_clear_all),
    ("subtour_docent_back", case_subtour_docent_back),
    ("subtour_save", case_subtour_save),
    ("save_confirm_flow", case_save_confirm_flow),
    ("save_discard_paths", case_save_discard_paths),
    ("save_prompt_disabled", case_save_prompt_disabled),
    ("esc_ends_tour", case_esc_ends_tour),
]


def impl_has(marker):
    for base in (os.path.join(ROOT, "lua"), os.path.join(ROOT, "relay")):
        for dirpath, _, files in os.walk(base):
            for fn in files:
                if not fn.endswith(".lua"):
                    continue
                try:
                    with open(os.path.join(dirpath, fn), errors="replace") as f:
                        if marker in f.read():
                            return True
                except OSError:
                    pass
    return False


def wait_for_impl(marker="save_tour", timeout=600, interval=30):
    """The executor may still be writing; poll for the round-2 surface."""
    deadline = time.monotonic() + timeout
    while not impl_has(marker):
        if time.monotonic() >= deadline:
            print("WARNING: implementation marker %r never appeared after "
                  "%ds; running the suite anyway." % (marker, timeout))
            return False
        print("waiting for implementation (%r not found in lua/ or relay/); "
              "retrying in %ds..." % (marker, interval))
        time.sleep(interval)
    return True


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

    if os.environ.get("DOCENT_WAIT_IMPL", "0") == "1":
        wait_for_impl()

    global SOCK_DIR
    SOCK_DIR = tempfile.mkdtemp(prefix="docent-t-")
    clean_docent()

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
    clean_docent()

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
