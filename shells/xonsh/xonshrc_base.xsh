# my-shell template -- overwritten on update; edit user-custom.xsh instead

import os
import platform
import shutil
from pathlib import Path

# Shell Behavior

$AUTO_CD = True
$XONSH_AUTOPAIR = True
$COMPLETIONS_CONFIRM = True
$XONSH_SHOW_TRACEBACK = True
$CASE_SENSITIVE_COMPLETIONS = False
$COMPLETION_IN_THREAD = True
$UPDATE_COMPLETIONS_ON_KEYPRESS = True
$XONSH_PROMPT_AUTO_SUGGEST = True  # Fish-like history suggestions (right arrow to accept)
$SUGGEST_COMMANDS = True          # Suggest corrections for misspelled commands
$SUGGEST_THRESHOLD = 3            # Max Levenshtein distance for suggestions
$XONSH_CAPTURE_ALWAYS = True      # Required for xontrib-output-search to capture output

# History
$XONSH_HISTORY_BACKEND = 'sqlite'
$XONSH_HISTORY_SIZE = (1000000, 'commands')

# Path Configuration

_is_windows = platform.system() == 'Windows'
_home = Path.home()
if 'HOME' not in __xonsh__.env:
    $HOME = str(_home)

if _is_windows:
    _sysroot = os.environ.get('SystemRoot', r'C:\Windows')
    _common_paths = [
        os.path.join(_sysroot, 'System32'),
        _sysroot,
        str(_home / 'AppData' / 'Local' / 'Programs'),
        str(_home / '.local' / 'bin'),
        str(_home / '.cargo' / 'bin'),
        str(_home / 'scoop' / 'shims'),
        str(_home / 'AppData' / 'Local' / 'Microsoft' / 'WindowsApps'),
    ]

    # Add Chocolatey bin
    _choco = os.environ.get('ChocolateyInstall', r'C:\ProgramData\chocolatey')
    _choco_bin = os.path.join(_choco, 'bin')
    if os.path.isdir(_choco_bin):
        _common_paths.insert(0, _choco_bin)

    # Add user-level Python scripts to PATH
    _py_root = os.path.join(os.environ.get('APPDATA', ''), 'Python')
    if os.path.isdir(_py_root):
        for _d in os.listdir(_py_root):
            if _d.startswith('Python'):
                _scripts = os.path.join(_py_root, _d, 'Scripts')
                if os.path.isdir(_scripts):
                    _common_paths.append(_scripts)
else:
    _common_paths = [
        '/usr/local/bin',
        '/usr/bin',
        '/bin',
        '/usr/sbin',
        '/sbin',
        str(_home / '.cargo' / 'bin'),
        str(_home / '.local' / 'bin'),
        str(_home / 'bin'),
    ]

    # Homebrew on macOS
    if platform.system() == 'Darwin':
        for _brew_prefix in ['/opt/homebrew/bin', '/usr/local/bin']:
            if os.path.isdir(_brew_prefix) and _brew_prefix not in _common_paths:
                _common_paths.insert(0, _brew_prefix)

for _p in _common_paths:
    if os.path.isdir(_p) and _p not in $PATH:
        $PATH.insert(0, _p)

if shutil.which('code'):
    $EDITOR = 'code --wait'
    $VISUAL = 'code --wait'
elif shutil.which('vim'):
    $EDITOR = 'vim'
    $VISUAL = 'vim'
elif shutil.which('nano'):
    $EDITOR = 'nano'
    $VISUAL = 'nano'
elif _is_windows:
    $EDITOR = 'notepad'
    $VISUAL = 'notepad'

# Never override a locale the user already set.
if 'LANG' not in ${...}:
    $LANG = 'en_US.UTF-8'

# XDG Base Directory Specification (Linux only). On macOS, exporting XDG_CONFIG_HOME
# makes child Nushell processes ignore their native Application Support config.
if platform.system() == 'Linux':
    if 'XDG_CONFIG_HOME' not in ${...}:
        $XDG_CONFIG_HOME = str(_home / '.config')
    if 'XDG_DATA_HOME' not in ${...}:
        $XDG_DATA_HOME = str(_home / '.local' / 'share')
    if 'XDG_CACHE_HOME' not in ${...}:
        $XDG_CACHE_HOME = str(_home / '.cache')

# FZF
$FZF_DEFAULT_OPTS = '--height 40% --layout=reverse --border --multi'
if shutil.which('fd'):
    $FZF_DEFAULT_COMMAND = 'fd --type f --hidden --follow --exclude .git'
    $FZF_CTRL_T_COMMAND = $FZF_DEFAULT_COMMAND
    $FZF_ALT_C_COMMAND = 'fd --type d --hidden --follow --exclude .git'

# Bat theme and style
$BAT_THEME = 'Monokai Extended'
$BAT_STYLE = 'numbers,changes,header,grid'

# Less options
$LESS = '-R'

# Color output
$CLICOLOR = '1'

# Ripgrep config
_rg_config = _home / '.ripgreprc'
if _rg_config.exists():
    $RIPGREP_CONFIG_PATH = str(_rg_config)

# Python
$PYTHONDONTWRITEBYTECODE = '1'
$VIRTUAL_ENV_DISABLE_PROMPT = '1'

# Go
if shutil.which('go'):
    _go_path = str(_home / 'go')
    $GOPATH = _go_path
    _go_bin = os.path.join(_go_path, 'bin')
    if os.path.isdir(_go_bin) and _go_bin not in $PATH:
        $PATH.insert(0, _go_bin)

# Rust / Cargo
$CARGO_HOME = str(_home / '.cargo')

if shutil.which('vivid'):
    import subprocess as _sp
    try:
        $LS_COLORS = _sp.check_output(['vivid', 'generate', 'molokai'], text=True).strip()
    except Exception:
        pass

$PROMPT_TOOLKIT_COLOR_DEPTH = 'DEPTH_24_BIT'
$XONSH_COLOR_STYLE = 'monokai'

# Mouse support in prompt-toolkit (uncomment to enable)
# $MOUSE_SUPPORT = True

# Cleanup temp vars
for _v in ['_is_windows', '_home', '_common_paths', '_rg_config',
           '_choco', '_choco_bin', '_py_root', '_d', '_scripts',
           '_brew_prefix', '_proj', '_p', '_go_path', '_go_bin', '_sp', '_sysroot']:
    globals().pop(_v, None)
del _v
