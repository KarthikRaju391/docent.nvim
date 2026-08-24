-- Minimal init for docent.nvim tests: put the repo on the runtimepath and
-- call setup() explicitly (tests run with --noplugin).
local this = debug.getinfo(1, "S").source:sub(2)
local root = vim.fs.dirname(vim.fs.dirname(vim.fn.fnamemodify(this, ":p")))
vim.opt.runtimepath:prepend(root)
require("docent").setup()
