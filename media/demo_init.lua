-- Minimal Neovim config for the launch demo recording.
-- Deliberately tiny: repo on the runtimepath, docent.setup(), a dark theme.
local this = debug.getinfo(1, "S").source:sub(2)
local root = vim.fs.dirname(vim.fs.dirname(vim.fn.fnamemodify(this, ":p")))
vim.opt.runtimepath:prepend(root)

vim.o.termguicolors = true
vim.o.background = "dark"
pcall(vim.cmd.colorscheme, "habamax")

vim.o.number = true
vim.o.relativenumber = false
vim.o.cursorline = true
vim.o.signcolumn = "no"
vim.o.scrolloff = 6
vim.o.laststatus = 2
-- the "saved tour ... to <abs path>" message wraps; two rows avoids a
-- hit-enter prompt landing in the middle of the demo's last beat
vim.o.cmdheight = 2
vim.o.showmode = false
vim.o.ruler = false
vim.o.swapfile = false
vim.o.shortmess = vim.o.shortmess .. "IcF"
vim.o.fillchars = "eob: "
vim.o.statusline = "  %f %= %l:%c   "

vim.api.nvim_set_hl(0, "DocentRange", { bg = "#2e3b2f" })
vim.api.nvim_set_hl(0, "NormalFloat", { bg = "#1c2430", fg = "#d7e3f4" })
vim.api.nvim_set_hl(0, "FloatBorder", { bg = "#1c2430", fg = "#5f87af" })
vim.api.nvim_set_hl(0, "FloatTitle", { bg = "#1c2430", fg = "#87d7af", bold = true })

require("docent").setup({
  keymaps = { next = "]v", prev = "[v" },
})
