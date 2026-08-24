local M = {}

function M.dispatch(tool_name, args)
  local tools = require("docent.tools")
  local fn = tools[tool_name]
  if type(fn) ~= "function" then
    return { error = "unknown tool: " .. tostring(tool_name) }
  end
  if type(args) ~= "table" then
    args = {}
  end
  local ok, result = pcall(fn, args)
  if not ok then
    return { error = tostring(result) }
  end
  return result
end

return M
