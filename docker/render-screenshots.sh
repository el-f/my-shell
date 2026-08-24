#!/usr/bin/env bash
# Renders the README images. Run via docker/Dockerfile.screenshots with /out mounted.
set -euo pipefail

OUT="${1:-/out}"
# The PNG rasteriser silently drops every glyph when the family is only named,
# so hand it the file.
FONT_FILE="/home/ubuntu/.local/share/fonts/FiraCodeNerdFontMono-Regular.ttf"
mkdir -p "$OUT"

cd /home/ubuntu/my-shell
# `mise activate` runs the repo's enter hook, which needs a .git this image has not got.
export PATH="/home/ubuntu/.local/share/mise/shims:$PATH"

# `nu -c` does not load config.nu on its own, so point at the deployed pair the
# way tests/test_e2e_shell.py::_run_nu does.
NU_CFG="/home/ubuntu/.config/nushell"
NU="nu --no-history --config $NU_CFG/config.nu --env-config $NU_CFG/env.nu"

shot() {
    local name="$1"
    shift
    # freeze --execute captures nothing without a tty, so run under `script` and
    # render the recorded ANSI instead. The pty defaults to 80 columns, which
    # truncates the alias descriptions.
    script -qec "stty cols 110 rows 400; $*" /dev/null > "/tmp/$name.ansi" 2>&1
    freeze "/tmp/$name.ansi" \
        --language ansi \
        --output "$OUT/$name.png" \
        --font.file "$FONT_FILE" \
        --font.size 14 \
        --padding 20 \
        --margin 0 \
        --border.radius 8 \
        --window
    echo "rendered $OUT/$name.png ($(wc -c < "/tmp/$name.ansi") bytes captured)"
}

shot sysinfo "$NU -c sysinfo"
shot commands "$NU -c commands"
# No doctor shot: its status column uses Nerd Font private-use glyphs, which the
# PNG rasteriser renders as tofu.

ls -la "$OUT"
