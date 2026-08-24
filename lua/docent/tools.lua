local tour = require("docent.tour")
local ui = require("docent.ui")

local M = {}

local function resolve_file(file)
  if type(file) ~= "string" or file == "" then
    error("missing required argument: file", 0)
  end
  local path = file
  if not vim.startswith(path, "/") then
    path = vim.fn.getcwd() .. "/" .. path
  end
  path = vim.fn.fnamemodify(path, ":p")
  if vim.fn.filereadable(path) == 0 then
    error("file not found: " .. file, 0)
  end
  return path
end

function M.jump_to(args)
  local path = resolve_file(args.file)
  local line = tour.navigate({
    file = path,
    line_start = args.line_start,
    line_end = args.line_end,
    narration = args.narration,
  })
  return { file = path, line = line }
end

function M.highlight(args)
  local ranges = args.ranges
  if type(ranges) ~= "table" or #ranges == 0 then
    error("ranges must be a non-empty list", 0)
  end
  ui.clear_highlights()
  for _, r in ipairs(ranges) do
    if type(r.line_start) ~= "number" then
      error("each range needs a line_start", 0)
    end
    local buf
    if r.file then
      buf = vim.fn.bufadd(resolve_file(r.file))
      vim.fn.bufload(buf)
    else
      buf = vim.api.nvim_get_current_buf()
    end
    ui.add_highlight(buf, r.line_start, r.line_end)
  end
  return { count = #ranges }
end

function M.narrate(args)
  if type(args.text) ~= "string" or args.text == "" then
    error("missing required argument: text", 0)
  end
  ui.show_float(args.text)
  return vim.empty_dict()
end

function M.add_tour_stop(args)
  local path = resolve_file(args.file)
  if type(args.line_start) ~= "number" then
    error("missing required argument: line_start", 0)
  end
  if type(args.narration) ~= "string" or args.narration == "" then
    error("missing required argument: narration", 0)
  end
  local index, total = tour.add_stop({
    file = path,
    line_start = args.line_start,
    line_end = args.line_end,
    narration = args.narration,
  })
  local result = { index = index, total = total }
  if index == 1 then
    result.pace_with = require("docent").pacing_keys().next or ":DocentNext"
  end
  return result
end

function M.save_tour(args)
  if type(args.title) ~= "string" or args.title == "" then
    error("missing required argument: title", 0)
  end
  if #tour.stops == 0 then
    error("no active tour to save", 0)
  end
  local saved = require("docent.store").save(args.title, tour.stops, vim.fn.getcwd())
  tour.title = args.title
  return saved
end

function M.list_saved_tours(_)
  return { tours = require("docent.store").list(vim.fn.getcwd()) }
end

function M.load_tour(args)
  if type(args.slug) ~= "string" or args.slug == "" then
    error("missing required argument: slug", 0)
  end
  local cwd = vim.fn.getcwd()
  local data = require("docent.store").load(args.slug, cwd)
  local abs = {}
  for i, s in ipairs(data.stops) do
    local file = s.file
    if not vim.startswith(file, "/") then
      file = cwd .. "/" .. file
    end
    abs[i] = { file = file, line_start = s.line_start, line_end = s.line_end, narration = s.narration }
  end
  tour.load_stops(abs, data.title)
  return {
    title = data.title,
    slug = data.slug or args.slug,
    stops = data.stops,
    current = #abs > 0 and 1 or 0,
  }
end

function M.clear_tour(_)
  tour.clear()
  return vim.empty_dict()
end

function M.list_tour(_)
  return tour.list()
end

function M.get_editor_context(_)
  local pos = vim.api.nvim_win_get_cursor(0)
  local mode = vim.api.nvim_get_mode().mode
  local ctx = {
    file = vim.api.nvim_buf_get_name(0),
    cursor = { line = pos[1], col = pos[2] },
    mode = mode,
    cwd = vim.fn.getcwd(),
  }
  if #tour.stops > 0 then
    local stop = tour.stops[tour.current]
    ctx.tour = {
      title = tour.title or vim.NIL,
      current = tour.current,
      total = #tour.stops,
      stop = stop and {
        file = stop.file,
        line_start = stop.line_start,
        line_end = stop.line_end,
        narration = stop.narration,
      } or nil,
    }
  end
  if mode:match("^[vV\022]") then
    local anchor = vim.fn.getpos("v")[2]
    local head = vim.fn.getpos(".")[2]
    local s, e = math.min(anchor, head), math.max(anchor, head)
    local lines = vim.api.nvim_buf_get_lines(0, s - 1, e, false)
    ctx.selection = {
      text = table.concat(lines, "\n"),
      line_start = s,
      line_end = e,
    }
  end
  return ctx
end

return M
