local ui = require("docent.ui")

local M = {}

-- Stack of frames; only the deepest is active (paced, listed, appended to).
-- frame = { stops, current, title, anchor, proposal } — anchor is the parent
-- frame's stop index this sub-tour branched from (nil for root); proposal is a
-- title the agent proposed via save_tour, confirmed by the user only when the
-- frame ends naturally (paced past its last stop).
local frames = {}

local save_prompt = true

local function top()
  return frames[#frames]
end

function M.set_save_prompt(v)
  save_prompt = v
end

function M.propose_save(title)
  local f = top()
  if f then
    f.proposal = title
  end
end

-- Ask the user to confirm (or rename, or decline) a proposed title. Async:
-- vim.ui.input calls back later, so callers must not depend on the outcome.
local function confirm_save(proposal, stops)
  vim.ui.input({ prompt = "Save tour as: ", default = proposal }, function(title)
    if not title or title:match("^%s*$") then
      vim.notify("docent: tour not saved", vim.log.levels.INFO)
      return
    end
    local ok, res = pcall(require("docent.store").save, title, stops)
    if not ok then
      vim.notify("docent: " .. tostring(res), vim.log.levels.ERROR)
      return
    end
    vim.notify(("docent: saved tour '%s' to %s"):format(title, (res.rel_path or res.path)), vim.log.levels.INFO)
  end)
end

-- Consume a frame's proposal (never prompt twice for the same one).
local function prompt_if_proposed(frame)
  local proposal = frame and frame.proposal
  if not proposal then
    return
  end
  frame.proposal = nil
  if not save_prompt then
    return
  end
  confirm_save(proposal, vim.deepcopy(frame.stops))
end

function M.depth()
  return #frames
end

function M.active_stops()
  local f = top()
  return f and f.stops or {}
end

function M.stop_count()
  return #M.active_stops()
end

function M.current()
  local f = top()
  return f and f.current or 0
end

function M.get_title()
  local f = top()
  return f and f.title or nil
end

function M.set_title(title)
  local f = top()
  if f then
    f.title = title
  end
end

-- Shared jump mechanics: open file (reusing a window that already shows it),
-- move cursor, center, highlight the range, show the info float.
function M.navigate(loc)
  ui.clear_highlights()
  ui.close_float()
  local buf = vim.fn.bufnr(loc.file)
  local win = buf ~= -1 and vim.fn.bufwinid(buf) or -1
  if win ~= -1 then
    vim.api.nvim_set_current_win(win)
  else
    vim.cmd.edit(vim.fn.fnameescape(loc.file))
  end
  local last = vim.api.nvim_buf_line_count(0)
  local line = math.min(math.max(loc.line_start or 1, 1), last)
  vim.api.nvim_win_set_cursor(0, { line, 0 })
  vim.cmd("normal! zz")
  if loc.line_end then
    ui.add_highlight(vim.api.nvim_get_current_buf(), line, loc.line_end)
  end
  if loc.info and loc.info ~= "" then
    ui.show_float(loc.info)
  end
  return line
end

local function push_frame(anchor)
  table.insert(frames, { stops = {}, current = 0, title = nil, anchor = anchor })
end

function M.add_stop(stop, branch)
  if branch and top() and #top().stops > 0 then
    push_frame(top().current)
  elseif not top() then
    push_frame(nil)
    require("docent.esc").install()
  end
  local f = top()
  table.insert(f.stops, stop)
  if #f.stops == 1 then
    f.current = 1
    M.navigate(stop)
  end
  return #f.stops, #f.stops
end

function M.goto_stop(n)
  local f = top()
  local stop = f and f.stops[n]
  if not stop then
    vim.notify(("docent: no tour stop %s"):format(n), vim.log.levels.WARN)
    return
  end
  f.current = n
  M.navigate(stop)
end

-- Pop one frame and return the user to the parent's anchor stop.
-- Returns the parent frame, or nil if there was no sub-tour to pop.
function M.pop()
  if #frames < 2 then
    return nil
  end
  local anchor = top().anchor
  table.remove(frames)
  local f = top()
  f.current = anchor
  M.navigate(f.stops[anchor])
  return f
end

function M.next()
  local f = top()
  if not f or #f.stops == 0 then
    vim.notify("docent: no active tour", vim.log.levels.INFO)
    return
  end
  if f.current < #f.stops then
    M.goto_stop(f.current + 1)
    return
  end
  if #frames > 1 then
    local ended = f
    local parent = M.pop()
    vim.notify(
      ("docent: end of sub-tour — back to %s (%d/%d)"):format(parent.title or "tour", parent.current, #parent.stops),
      vim.log.levels.INFO
    )
    prompt_if_proposed(ended)
    return
  end
  vim.notify(("docent: end of tour (%d/%d)"):format(#f.stops, #f.stops), vim.log.levels.INFO)
  prompt_if_proposed(f)
end

function M.prev()
  local f = top()
  if not f or #f.stops == 0 then
    vim.notify("docent: no active tour", vim.log.levels.INFO)
    return
  end
  if f.current <= 1 then
    vim.notify(("docent: start of tour (1/%d)"):format(#f.stops), vim.log.levels.INFO)
    return
  end
  M.goto_stop(f.current - 1)
end

function M.restart()
  if M.stop_count() == 0 then
    vim.notify("docent: no active tour to restart", vim.log.levels.WARN)
    return
  end
  M.goto_stop(1)
end

function M.load_stops(stops, title)
  frames = {}
  push_frame(nil)
  top().stops = stops
  top().title = title
  if #stops > 0 then
    require("docent.esc").install()
    M.goto_stop(1)
  end
end

function M.clear()
  frames = {}
  require("docent.esc").remove()
  ui.clear_highlights()
  ui.close_float()
end

function M.list()
  local f = top()
  local stops = {}
  if f then
    for i, s in ipairs(f.stops) do
      stops[i] = {
        file = s.file,
        line_start = s.line_start,
        line_end = s.line_end,
        info = s.info,
      }
    end
  end
  local result = { stops = stops, current = f and f.current or 0, depth = math.max(#frames, 1) }
  if #frames > 1 then
    local parent = frames[#frames - 1]
    result.parent = {
      title = parent.title or vim.NIL,
      anchor = f.anchor,
      total = #parent.stops,
    }
  end
  return result
end

return M
