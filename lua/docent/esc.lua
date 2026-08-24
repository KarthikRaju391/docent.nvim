-- Transient <Esc><Esc> = end-tour mapping: installed only while a tour is
-- live. A single <Esc> chains to whatever it was mapped to before (restored
-- when the tour ends); a second press within the timeout ends the tour.
local M = {}

local TIMEOUT_MS = 1000

local enabled = true
local installed = false
local orig
local last_press = 0

local function chain_orig()
  if not orig then
    return
  end
  if orig.callback then
    orig.callback()
  elseif orig.rhs and orig.rhs ~= "" then
    local keys = vim.api.nvim_replace_termcodes(orig.rhs, true, true, true)
    vim.api.nvim_feedkeys(keys, orig.noremap == 1 and "n" or "m", false)
  end
end

function M.set_enabled(v)
  enabled = v
end

function M.install()
  if not enabled or installed then
    return
  end
  local m = vim.fn.maparg("<Esc>", "n", false, true)
  orig = not vim.tbl_isempty(m) and m or nil
  last_press = 0
  vim.keymap.set("n", "<Esc>", function()
    local now = vim.uv.now()
    if now - last_press <= TIMEOUT_MS then
      require("docent.tour").clear()
      vim.notify("docent: tour ended", vim.log.levels.INFO)
      return
    end
    last_press = now
    chain_orig()
  end, { desc = "Docent: end tour (press twice)" })
  installed = true
end

function M.remove()
  if not installed then
    return
  end
  pcall(vim.keymap.del, "n", "<Esc>")
  if orig then
    vim.fn.mapset("n", 0, orig)
  end
  orig = nil
  installed = false
end

return M
