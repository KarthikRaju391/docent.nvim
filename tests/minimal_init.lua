-- Minimal init for docent.nvim tests: put the repo on the runtimepath and
-- call setup() explicitly (tests run with --noplugin).
-- Non-default pacing keys so tests can prove instructions/hints report the
-- REAL bound keys rather than a hardcoded "]t".
local this = debug.getinfo(1, "S").source:sub(2)
local root = vim.fs.dirname(vim.fs.dirname(vim.fn.fnamemodify(this, ":p")))
vim.opt.runtimepath:prepend(root)
require("docent").setup({
  keymaps = { next = "]v", prev = "[v" },
})
