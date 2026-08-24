local M = {}

local did_setup = false

function M.mcp_command()
  local src = debug.getinfo(1, "S").source:sub(2)
  local repo = vim.fn.fnamemodify(src, ":p:h:h:h")
  return ("claude mcp add docent -- nvim --headless -l %s/relay/relay.lua"):format(repo)
end

-- Nvim's built-in defaults (e.g. ]t -> :tnext) don't count as user mappings.
local function user_mapped(lhs)
  local m = vim.fn.maparg(lhs, "n", false, true)
  if vim.tbl_isempty(m) then
    return false
  end
  if m.callback then
    local src = debug.getinfo(m.callback, "S").source
    if src:match("^@vim/") then
      return false
    end
  end
  return true
end

function M.setup(opts)
  if did_setup then
    return
  end
  did_setup = true
  opts = opts or {}

  require("docent.registry").setup()
  require("docent.ui").setup_hl()
  local tour = require("docent.tour")

  vim.api.nvim_create_user_command("DocentNext", function()
    tour.next()
  end, { desc = "Docent: go to next tour stop" })
  vim.api.nvim_create_user_command("DocentPrev", function()
    tour.prev()
  end, { desc = "Docent: go to previous tour stop" })
  vim.api.nvim_create_user_command("DocentStop", function(cmd)
    local n = tonumber(cmd.args)
    if not n then
      vim.notify("docent: usage :DocentStop <n>", vim.log.levels.WARN)
      return
    end
    tour.goto_stop(n)
  end, { nargs = 1, desc = "Docent: go to tour stop <n>" })
  vim.api.nvim_create_user_command("DocentMcpCommand", function()
    vim.api.nvim_echo({ { M.mcp_command() } }, true, {})
  end, { desc = "Docent: show the MCP registration command for agents" })

  if opts.keymaps ~= false then
    local keymaps = type(opts.keymaps) == "table" and opts.keymaps or {}
    local next_key = keymaps.next or "]t"
    local prev_key = keymaps.prev or "[t"
    if not user_mapped(next_key) then
      vim.keymap.set("n", next_key, tour.next, { desc = "Docent: next tour stop" })
    end
    if not user_mapped(prev_key) then
      vim.keymap.set("n", prev_key, tour.prev, { desc = "Docent: previous tour stop" })
    end
  end
end

return M
