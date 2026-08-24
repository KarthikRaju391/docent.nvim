local M = {}

local did_setup = false
local bound_keys = {}

-- Only keys docent actually bound; nil for keys skipped (user-mapped or keymaps=false).
function M.pacing_keys()
  return {
    next = bound_keys.next,
    prev = bound_keys.prev,
    commands = ":DocentNext/:DocentPrev",
  }
end

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
  vim.api.nvim_create_user_command("DocentRestart", function()
    tour.restart()
  end, { desc = "Docent: jump back to stop 1 of the active (sub-)tour" })
  vim.api.nvim_create_user_command("DocentBack", function()
    if tour.depth() < 2 then
      vim.notify("docent: not in a sub-tour", vim.log.levels.WARN)
      return
    end
    tour.pop()
  end, { desc = "Docent: end the sub-tour and return to the parent stop" })
  vim.api.nvim_create_user_command("DocentSave", function(cmd)
    if tour.stop_count() == 0 then
      vim.notify("docent: no active tour to save", vim.log.levels.WARN)
      return
    end
    local ok, res = pcall(require("docent.store").save, cmd.args, tour.active_stops())
    if not ok then
      vim.notify("docent: " .. tostring(res), vim.log.levels.ERROR)
      return
    end
    tour.set_title(cmd.args)
    vim.notify(("docent: saved tour '%s' to %s"):format(cmd.args, res.path), vim.log.levels.INFO)
  end, { nargs = "+", desc = "Docent: save the current tour as <title>" })
  vim.api.nvim_create_user_command("DocentTours", function()
    local tours = require("docent.store").list()
    if #tours == 0 then
      vim.notify("docent: no saved tours in " .. vim.fn.getcwd(), vim.log.levels.INFO)
      return
    end
    vim.ui.select(tours, {
      prompt = "Docent tours",
      format_item = function(t)
        return ("%s (%d stops)"):format(t.title or t.slug, t.stop_count)
      end,
    }, function(choice)
      if not choice then
        return
      end
      local ok, err = pcall(require("docent.tools").load_tour, { slug = choice.slug })
      if not ok then
        vim.notify("docent: " .. tostring(err), vim.log.levels.ERROR)
      end
    end)
  end, { desc = "Docent: pick and load a saved tour" })
  vim.api.nvim_create_user_command("DocentMcpCommand", function()
    vim.api.nvim_echo({ { M.mcp_command() } }, true, {})
  end, { desc = "Docent: show the MCP registration command for agents" })

  if opts.keymaps ~= false then
    local keymaps = type(opts.keymaps) == "table" and opts.keymaps or {}
    local next_key = keymaps.next or "]t"
    local prev_key = keymaps.prev or "[t"
    if not user_mapped(next_key) then
      vim.keymap.set("n", next_key, tour.next, { desc = "Docent: next tour stop" })
      bound_keys.next = next_key
    end
    if not user_mapped(prev_key) then
      vim.keymap.set("n", prev_key, tour.prev, { desc = "Docent: previous tour stop" })
      bound_keys.prev = prev_key
    end
  end
end

return M
