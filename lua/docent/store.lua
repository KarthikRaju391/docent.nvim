local M = {}

local function tours_dir(cwd)
  return (cwd or vim.fn.getcwd()) .. "/.docent/tours"
end

function M.slugify(title)
  local slug = title:lower()
  slug = slug:gsub("[^%w]+", "-")
  slug = slug:gsub("^%-+", ""):gsub("%-+$", "")
  if slug == "" then
    error("cannot make a slug from title: " .. title, 0)
  end
  return slug
end

local function relative(path, cwd)
  if vim.startswith(path, cwd .. "/") then
    return path:sub(#cwd + 2)
  end
  return path
end

function M.save(title, stops, cwd)
  cwd = cwd or vim.fn.getcwd()
  local slug = M.slugify(title)
  local dir = tours_dir(cwd)
  pcall(vim.fn.mkdir, dir, "p")
  local rel_stops = {}
  for i, s in ipairs(stops) do
    rel_stops[i] = {
      file = relative(s.file, cwd),
      line_start = s.line_start,
      line_end = s.line_end,
      info = s.info,
    }
  end
  local data = { title = title, slug = slug, created_at = os.time(), stops = rel_stops }
  local path = dir .. "/" .. slug .. ".json"
  local f, err = io.open(path, "w")
  if not f then
    error("cannot write " .. path .. ": " .. tostring(err), 0)
  end
  f:write(vim.json.encode(data))
  f:close()
  return { slug = slug, path = path, rel_path = relative(path, cwd), stops = rel_stops }
end

function M.list(cwd)
  cwd = cwd or vim.fn.getcwd()
  local tours = {}
  local dir = tours_dir(cwd)
  local scan = vim.uv.fs_scandir(dir)
  if not scan then
    return tours
  end
  while true do
    local name = vim.uv.fs_scandir_next(scan)
    if not name then
      break
    end
    if name:match("%.json$") then
      local f = io.open(dir .. "/" .. name)
      if f then
        local ok, data = pcall(vim.json.decode, f:read("*a"))
        f:close()
        if ok and type(data) == "table" and type(data.stops) == "table" then
          table.insert(tours, {
            title = data.title,
            slug = data.slug or name:gsub("%.json$", ""),
            created_at = data.created_at,
            stop_count = #data.stops,
          })
        end
      end
    end
  end
  table.sort(tours, function(a, b)
    return (a.created_at or 0) > (b.created_at or 0)
  end)
  return tours
end

function M.load(slug, cwd)
  cwd = cwd or vim.fn.getcwd()
  local path = tours_dir(cwd) .. "/" .. slug .. ".json"
  local f = io.open(path)
  if not f then
    error(("no saved tour '%s' in %s"):format(slug, tours_dir(cwd)), 0)
  end
  local ok, data = pcall(vim.json.decode, f:read("*a"))
  f:close()
  if not ok or type(data) ~= "table" or type(data.stops) ~= "table" then
    error("invalid tour file: " .. path, 0)
  end
  for _, s in ipairs(data.stops) do
    if type(s.info) ~= "string" then
      error(
        ("tour file %s has a stop without an 'info' field — it predates the rename of stop text to 'info'; re-save the tour"):format(
          path
        ),
        0
      )
    end
  end
  return data
end

return M
