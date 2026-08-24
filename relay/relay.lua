-- Stdio MCP relay: run as `nvim --headless -l relay.lua`.
-- stdout carries protocol JSON only (one JSON-RPC message per line); diagnostics go to stderr.

local PROTOCOL_VERSION = "2025-06-18"

-- Pacing keys are per-user config; never name specific keys unless the instance
-- confirms docent actually bound them.
local function build_instructions(keys)
  local pacing
  if keys and keys.next and keys.prev then
    pacing = ("with %s/%s"):format(keys.next, keys.prev)
  else
    pacing = "with their configured docent keys or :DocentNext/:DocentPrev"
  end
  return table.concat({
    "You are connected to the user's Neovim as a docent: answer codebase questions by navigating their editor, not by pasting code into chat.",
    'For single-answer "where is X" questions call jump_to with a 1-2 sentence narration.',
    'For "walk me through / explain this flow" questions, first call list_saved_tours — a saved tour IS the documented code path for its feature; if one covers the flow, load_tour it instead of re-deriving the path.',
    "Otherwise queue one add_tour_stop per hop in reading order with a 1-2 sentence narration each — the user paces through stops themselves "
      .. pacing
      .. "; never re-jump them.",
    "After building a good tour, save_tour it with a short feature-name title so it is there next time.",
    'Call get_editor_context first when the user says "this" or refers to what they\'re looking at. When the user is on a tour and asks about "this" or the current code, call get_editor_context — it includes their exact tour stop; answer in the context of that stop and narrate/jump onward rather than starting over.',
    "Never paste code blocks into chat that the user can be shown in their editor; keep chat replies to one short sentence.",
  }, " ")
end

local function log(msg)
  io.stderr:write("[docent-relay] " .. msg .. "\n")
end

local function send(msg)
  io.write(vim.json.encode(msg) .. "\n")
  io.stdout:flush()
end

local TOOLS = {
  {
    name = "jump_to",
    description = "Jump the user's editor to one location: opens the file, moves the cursor, centers it, optionally highlights a range and shows a short narration float. Use for single-answer 'where is X' questions instead of pasting code into chat. Not for multi-step explanations — queue add_tour_stop for those.",
    inputSchema = {
      type = "object",
      properties = {
        file = { type = "string", description = "File path, relative to the Neovim instance's cwd or absolute" },
        line_start = { type = "integer", description = "Line to land on (1-based, default 1)" },
        line_end = { type = "integer", description = "If given, highlight line_start..line_end" },
        narration = { type = "string", description = "1-2 sentence explanation shown in a float at the target" },
      },
      required = { "file" },
    },
  },
  {
    name = "highlight",
    description = "Visually highlight one or more line ranges in the user's editor without moving their cursor. Replaces any previous docent highlights. Use to point at supporting code while the user stays where they are.",
    inputSchema = {
      type = "object",
      properties = {
        ranges = {
          type = "array",
          description = "Ranges to highlight",
          items = {
            type = "object",
            properties = {
              file = { type = "string", description = "File path; defaults to the user's current buffer" },
              line_start = { type = "integer", description = "First line of the range (1-based)" },
              line_end = { type = "integer", description = "Last line of the range (inclusive)" },
            },
            required = { "line_start", "line_end" },
          },
        },
      },
      required = { "ranges" },
    },
  },
  {
    name = "narrate",
    description = "Show a short narration float at the user's cursor, not tied to a jump. Use for a 1-2 sentence remark about what they're already looking at. Keep it brief — this is a tooltip, not a chat message.",
    inputSchema = {
      type = "object",
      properties = {
        text = { type = "string", description = "1-2 sentence narration" },
      },
      required = { "text" },
    },
  },
  {
    name = "add_tour_stop",
    description = "Queue one stop of a guided tour: a location plus a 1-2 sentence narration. Use one call per hop, in reading order, when the user asks to be walked through a flow. The first stop of a fresh tour navigates the user there and its result includes pace_with — the key or command the user paces with; quote that, never assume specific keys. After the first stop the user paces themselves — never re-jump them.",
    inputSchema = {
      type = "object",
      properties = {
        file = { type = "string", description = "File path, relative to the Neovim instance's cwd or absolute" },
        line_start = { type = "integer", description = "First line of the stop (1-based)" },
        line_end = { type = "integer", description = "If given, highlight line_start..line_end at the stop" },
        narration = { type = "string", description = "1-2 sentence explanation of this stop" },
      },
      required = { "file", "line_start", "narration" },
    },
  },
  {
    name = "clear_tour",
    description = "Clear the current tour: removes all stops, highlights, and the narration float. Call before queueing a new tour on a different topic.",
    inputSchema = {
      type = "object",
      properties = vim.empty_dict(),
      required = {},
    },
  },
  {
    name = "list_tour",
    description = "List the current tour's stops and which one the user is on. Use to check state before appending stops or to see how far the user has paced.",
    inputSchema = {
      type = "object",
      properties = vim.empty_dict(),
      required = {},
    },
  },
  {
    name = "save_tour",
    description = "Persist the current tour to <project>/.docent/tours/<slug>.json (committable, so teammates and other agents find it). Call after building a good tour, with a short feature-name title. Errors if the tour is empty.",
    inputSchema = {
      type = "object",
      properties = {
        title = { type = "string", description = "Short feature name for the tour, e.g. 'meeting import flow'" },
      },
      required = { "title" },
    },
  },
  {
    name = "list_saved_tours",
    description = "List tours saved in the current project (.docent/tours/). Call this BEFORE building a tour for a flow — an existing tour is the documented code path; load_tour it instead of re-deriving the flow.",
    inputSchema = {
      type = "object",
      properties = vim.empty_dict(),
      required = {},
    },
  },
  {
    name = "load_tour",
    description = "Load a saved tour into the live tour state and navigate the user to stop 1. Returns the full stops list — read it to learn the code path for the feature without re-searching. Stops may drift as code changes; treat lines as approximate.",
    inputSchema = {
      type = "object",
      properties = {
        slug = { type = "string", description = "Tour slug from list_saved_tours" },
      },
      required = { "slug" },
    },
  },
  {
    name = "get_editor_context",
    description = "Read what the user is looking at: current file, cursor position, mode, visual selection (if any), and the instance cwd. If a tour is live, also includes tour = { title, current, total, stop } for the stop the user is at — the cursor may have wandered from the stop, so both are reported; both are signal. Call this first whenever the user says 'this' or otherwise refers to what's on their screen, and answer tour questions in the context of the reported stop.",
    inputSchema = {
      type = "object",
      properties = vim.empty_dict(),
      required = {},
    },
  },
}

local TOOL_NAMES = {}
for _, t in ipairs(TOOLS) do
  TOOL_NAMES[t.name] = true
end

-- Instance discovery ---------------------------------------------------------

local function instances_dir()
  local base = os.getenv("XDG_STATE_HOME")
  if base == nil or base == "" then
    base = vim.fn.expand("~/.local/state")
  end
  return base .. "/docent/instances"
end

local function realpath(p)
  return vim.uv.fs_realpath(p) or p
end

local function is_ancestor(dir, path)
  if dir == path then
    return true
  end
  if dir:sub(-1) ~= "/" then
    dir = dir .. "/"
  end
  return path:sub(1, #dir) == dir
end

local function pid_alive(pid)
  local ok, res = pcall(vim.uv.kill, pid, 0)
  return ok and res == 0
end

local function read_entries()
  local dir = instances_dir()
  local entries = {}
  local scan = vim.uv.fs_scandir(dir)
  if not scan then
    return entries
  end
  while true do
    local name = vim.uv.fs_scandir_next(scan)
    if not name then
      break
    end
    if name:match("%.json$") then
      local f = io.open(dir .. "/" .. name, "r")
      if f then
        local ok, entry = pcall(vim.json.decode, f:read("*a"))
        f:close()
        if ok and type(entry) == "table" and entry.pid and entry.socket and pid_alive(entry.pid) then
          table.insert(entries, entry)
        end
      end
    end
  end
  return entries
end

local chan = nil

local function try_connect(socket)
  local ok, ch = pcall(vim.fn.sockconnect, "pipe", socket, { rpc = true })
  if ok and type(ch) == "number" and ch > 0 then
    return ch
  end
  return nil
end

local function discover()
  local entries = read_entries()
  table.sort(entries, function(a, b)
    return (a.focused_at or 0) > (b.focused_at or 0)
  end)
  local mycwd = realpath(vim.uv.cwd())
  local matched, rest = {}, {}
  for _, e in ipairs(entries) do
    if e.cwd and is_ancestor(realpath(e.cwd), mycwd) then
      table.insert(matched, e)
    else
      table.insert(rest, e)
    end
  end
  for _, list in ipairs({ matched, rest }) do
    for _, e in ipairs(list) do
      local ch = try_connect(e.socket)
      if ch then
        log("connected to nvim pid " .. e.pid .. " at " .. e.socket)
        return ch
      end
    end
  end
  return nil
end

local function ensure_chan()
  if chan then
    return chan
  end
  chan = discover()
  if not chan then
    error(
      ("no running Neovim instance found for %s; is docent.nvim's setup() called?"):format(vim.uv.cwd()),
      0
    )
  end
  return chan
end

local function dispatch(name, args)
  local lua = "return require('docent.rpc').dispatch(...)"
  if type(args) ~= "table" then -- absent, or JSON null decoded to vim.NIL
    args = vim.empty_dict()
  end
  local payload = { name, args }
  local ok, result = pcall(vim.rpcrequest, ensure_chan(), "nvim_exec_lua", lua, payload)
  if ok then
    return result
  end
  -- connection may have died; reconnect once
  log("rpc failed (" .. tostring(result) .. "); reconnecting")
  chan = nil
  return vim.rpcrequest(ensure_chan(), "nvim_exec_lua", lua, payload)
end

-- MCP handlers ----------------------------------------------------------------

local function reply(id, result)
  send({ jsonrpc = "2.0", id = id, result = result })
end

local function reply_error(id, code, message)
  send({ jsonrpc = "2.0", id = id, error = { code = code, message = message } })
end

local function tool_result(id, text, is_error)
  reply(id, {
    content = { { type = "text", text = text } },
    isError = is_error,
  })
end

-- Must not fail the handshake: discovery and key fetch are best-effort here.
local function fetch_pacing_keys()
  local ok = pcall(ensure_chan)
  if not ok then
    return nil
  end
  local ok2, keys = pcall(vim.rpcrequest, chan, "nvim_exec_lua", "return require('docent').pacing_keys()", {})
  if ok2 and type(keys) == "table" then
    return keys
  end
  return nil
end

local function handle_initialize(msg)
  local client_version = msg.params and msg.params.protocolVersion
  local version = PROTOCOL_VERSION
  if type(client_version) == "string" and client_version:match("^%d%d%d%d%-%d%d%-%d%d$") then
    version = client_version
  end
  reply(msg.id, {
    protocolVersion = version,
    capabilities = { tools = { listChanged = false } },
    serverInfo = { name = "docent", version = "0.1.0" },
    instructions = build_instructions(fetch_pacing_keys()),
  })
end

local function handle_tools_call(msg)
  local params = msg.params or {}
  local name = params.name
  if not TOOL_NAMES[name] then
    reply_error(msg.id, -32602, "unknown tool: " .. tostring(name))
    return
  end
  local ok, result = pcall(dispatch, name, params.arguments)
  if not ok then
    local err = tostring(result)
    if err:find("no running Neovim instance", 1, true) then
      reply_error(msg.id, -32002, err)
    else
      tool_result(msg.id, err, true)
    end
    return
  end
  if type(result) == "table" and result.error ~= nil then
    tool_result(msg.id, tostring(result.error), true)
    return
  end
  tool_result(msg.id, vim.json.encode(result), false)
end

local function handle(msg)
  local method = msg.method
  local has_id = msg.id ~= nil and msg.id ~= vim.NIL
  if type(method) ~= "string" then
    if has_id then
      reply_error(msg.id, -32600, "invalid request")
    end
    return
  end
  if method:match("^notifications/") then
    return
  end
  if not has_id then
    return
  end
  if method == "initialize" then
    handle_initialize(msg)
  elseif method == "ping" then
    reply(msg.id, vim.empty_dict())
  elseif method == "tools/list" then
    reply(msg.id, { tools = TOOLS })
  elseif method == "tools/call" then
    handle_tools_call(msg)
  else
    reply_error(msg.id, -32601, "method not found: " .. method)
  end
end

while true do
  local line = io.read("*l")
  if line == nil then
    break
  end
  if line:match("%S") then
    local ok, msg = pcall(vim.json.decode, line)
    if not ok or type(msg) ~= "table" then
      send({ jsonrpc = "2.0", id = vim.NIL, error = { code = -32700, message = "parse error" } })
    else
      handle(msg)
    end
  end
end
