-- Same as minimal_init.lua but with the end-of-tour save prompt disabled,
-- used only by the save_prompt_disabled case.
local this = debug.getinfo(1, "S").source:sub(2)
local root = vim.fs.dirname(vim.fs.dirname(vim.fn.fnamemodify(this, ":p")))
vim.opt.runtimepath:prepend(root)
require("docent").setup({
  keymaps = { next = "]v", prev = "[v" },
  save_prompt = false,
})
