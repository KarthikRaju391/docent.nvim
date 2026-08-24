local M = {}

local ns = vim.api.nvim_create_namespace("docent")
local hl_bufs = {}
local float_win = nil
local float_group = nil

function M.setup_hl()
  vim.api.nvim_set_hl(0, "DocentRange", { link = "Visual", default = true })
end

function M.clear_highlights()
  for buf in pairs(hl_bufs) do
    if vim.api.nvim_buf_is_valid(buf) then
      vim.api.nvim_buf_clear_namespace(buf, ns, 0, -1)
    end
  end
  hl_bufs = {}
end

function M.add_highlight(buf, line_start, line_end)
  M.setup_hl()
  local last = vim.api.nvim_buf_line_count(buf)
  local s = math.min(math.max(line_start or 1, 1), last)
  local e = math.min(math.max(line_end or s, s), last)
  for line = s, e do
    vim.api.nvim_buf_set_extmark(buf, ns, line - 1, 0, {
      line_hl_group = "DocentRange",
    })
  end
  hl_bufs[buf] = true
end

function M.close_float()
  if float_win and vim.api.nvim_win_is_valid(float_win) then
    vim.api.nvim_win_close(float_win, true)
  end
  float_win = nil
  if float_group then
    pcall(vim.api.nvim_del_augroup_by_id, float_group)
    float_group = nil
  end
end

function M.show_float(text)
  M.close_float()
  local lines = vim.split(text, "\n", { plain = true })
  local maxlen = 1
  for _, l in ipairs(lines) do
    maxlen = math.max(maxlen, vim.fn.strdisplaywidth(l))
  end
  local width = math.min(60, maxlen)
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.bo[buf].bufhidden = "wipe"
  float_win = vim.api.nvim_open_win(buf, false, {
    relative = "cursor",
    row = 1,
    col = 0,
    width = width,
    height = 1,
    style = "minimal",
    border = "rounded",
    title = " Docent ",
    focusable = false,
  })
  vim.wo[float_win].wrap = true
  vim.wo[float_win].linebreak = true
  -- the estimate ceil(len/width) undercounts word-boundary wrapping; ask for the real height
  local wrapped = vim.api.nvim_win_text_height(float_win, {}).all
  vim.api.nvim_win_set_height(float_win, math.min(wrapped, 10))
  -- Close on real movement only: scroll animations and other plugins emit
  -- CursorMoved without the cursor changing position, and the jump's own
  -- CursorMoved is still queued when we arm — position compare absorbs both.
  local opened_buf = vim.api.nvim_get_current_buf()
  local opened_pos = vim.api.nvim_win_get_cursor(0)
  float_group = vim.api.nvim_create_augroup("DocentFloat", { clear = true })
  vim.api.nvim_create_autocmd({ "CursorMoved", "BufLeave" }, {
    group = float_group,
    callback = function(ev)
      if ev.event == "CursorMoved" and vim.api.nvim_get_current_buf() == opened_buf then
        local p = vim.api.nvim_win_get_cursor(0)
        if p[1] == opened_pos[1] and p[2] == opened_pos[2] then
          return
        end
      end
      M.close_float()
    end,
  })
end

return M
