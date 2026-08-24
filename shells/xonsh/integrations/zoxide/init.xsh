import shutil
import sys

if shutil.which('zoxide'):
    try:
        _zoxide_out = $(zoxide init xonsh --cmd cd)
    except Exception as _e:
        _zoxide_out = None
        print(f'my-shell: zoxide init failed, cd stays builtin: {_e}', file=sys.stderr)

    if _zoxide_out:
        # An uncaught failure here aborts the rest of ~/.xonshrc, not just zoxide.
        try:
            execx(_zoxide_out)
        except Exception as _e:
            print(f'my-shell: zoxide init skipped (output changed?): {_e}', file=sys.stderr)
