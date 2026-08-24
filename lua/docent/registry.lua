local M = {}

local function instances_dir()
  local base = vim.env.XDG_STATE_HOME
  if base == nil or base == "" then
    base = vim.fn.expand("~/.local/state")
  end
  return base .. "/docent/instances"
end

local function entry_path()
  return instances_dir() .. "/" .. vim.fn.getpid() .. ".json"
end

local function write_entry()
  local socket = vim.v.servername
  if socket == "" then
    socket = vim.fn.serverstart()
  end
  -- pcall: mkdir -p races when several instances start at once
  pcall(vim.fn.mkdir, instances_dir(), "p")
  local cwd = vim.fn.getcwd()
  local entry = {
    pid = vim.fn.getpid(),
    socket = socket,
    cwd = vim.uv.fs_realpath(cwd) or cwd,
    focused_at = os.time(),
  }
  local f = io.open(entry_path(), "w")
  if f then
    f:write(vim.json.encode(entry))
    f:close()
  end
end

local function remove_entry()
  os.remove(entry_path())
end

local function cleanup_stale()
  local dir = instances_dir()
  local scan = vim.uv.fs_scandir(dir)
  if not scan then
    return
  end
  while true do
    local name = vim.uv.fs_scandir_next(scan)
    if not name then
      break
    end
    local pid = tonumber(name:match("^(%d+)%.json$"))
    if pid and pid ~= vim.fn.getpid() then
      local ok, res = pcall(vim.uv.kill, pid, 0)
      if not ok or res ~= 0 then
        os.remove(dir .. "/" .. name)
      end
    end
  end
end

function M.setup()
  cleanup_stale()
  write_entry()
  local group = vim.api.nvim_create_augroup("DocentRegistry", { clear = true })
  vim.api.nvim_create_autocmd({ "FocusGained", "DirChanged", "VimResume" }, {
    group = group,
    callback = write_entry,
  })
  vim.api.nvim_create_autocmd("VimLeavePre", {
    group = group,
    callback = remove_entry,
  })
end

return M
