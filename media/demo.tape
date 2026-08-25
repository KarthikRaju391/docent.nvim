# docent.nvim launch demo — rendered with vhs (https://github.com/charmbracelet/vhs)
#
#   cd <repo root> && vhs media/demo.tape
#
# Everything on screen is the real plugin: a visible Neovim with docent loaded,
# driven over MCP by media/demo_agent.py through the real stdio relay.
# See media/RECORDING.md for the human-recorded "real agent" version.

Output media/demo.mp4
Output media/demo.gif

Require tmux
Require nvim

Set Shell bash
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 19
Set Width 1280
Set Height 720
Set Padding 12
Set Framerate 24
Set PlaybackSpeed 1.0
Set Theme "TokyoNight"
Set TypingSpeed 1ms
Set WindowBar Colorful
Set WindowBarSize 32
Set BorderRadius 8
Set Margin 0

Env NVIM_BIN "/opt/homebrew/bin/nvim"
Env DOCENT_DEMO_RUN "/tmp/docent-demo"

Hide
Type "clear"
Enter
Sleep 500ms
Type "bash media/demo_session.sh"
Enter
Sleep 4s
Show

Sleep 47s
Hide
