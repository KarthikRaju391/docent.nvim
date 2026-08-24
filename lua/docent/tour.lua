local ui = require("docent.ui")

local M = {}

M.stops = {}
M.current = 0

-- Shared jump mechanics: open file (reusing a window that already shows it),
-- move cursor, center, highlight the range, show the narration float.
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
  if loc.narration and loc.narration ~= "" then
    ui.show_float(loc.narration)
  end
  return line
end

function M.add_stop(stop)
  table.insert(M.stops, stop)
  if #M.stops == 1 then
    M.current = 1
    M.navigate(stop)
  end
  return #M.stops, #M.stops
end

function M.goto_stop(n)
  local stop = M.stops[n]
  if not stop then
    vim.notify(("docent: no tour stop %s"):format(n), vim.log.levels.WARN)
    return
  end
  M.current = n
  M.navigate(stop)
end

function M.next()
  if #M.stops == 0 then
    vim.notify("docent: no active tour", vim.log.levels.INFO)
    return
  end
  if M.current >= #M.stops then
    vim.notify(("docent: end of tour (%d/%d)"):format(#M.stops, #M.stops), vim.log.levels.INFO)
    return
  end
  M.goto_stop(M.current + 1)
end

function M.prev()
  if #M.stops == 0 then
    vim.notify("docent: no active tour", vim.log.levels.INFO)
    return
  end
  if M.current <= 1 then
    vim.notify(("docent: start of tour (1/%d)"):format(#M.stops), vim.log.levels.INFO)
    return
  end
  M.goto_stop(M.current - 1)
end

function M.clear()
  M.stops = {}
  M.current = 0
  ui.clear_highlights()
  ui.close_float()
end

function M.list()
  local stops = {}
  for i, s in ipairs(M.stops) do
    stops[i] = {
      file = s.file,
      line_start = s.line_start,
      line_end = s.line_end,
      narration = s.narration,
    }
  end
  return { stops = stops, current = M.current }
end

return M
