import platform
import shutil
import sys

if shutil.which('atuin'):
    try:
        _atuin_out = $(atuin init xonsh)
    except Exception as _e:
        _atuin_out = None
        print(f'my-shell: atuin init failed, history integration off: {_e}', file=sys.stderr)

    if _atuin_out:
        if platform.system() == 'Windows':
            _atuin_out = _atuin_out.replace('/dev/null', 'NUL')

        # Atuin's generated xonsh init can assume optional deps or a writable
        # home directory. Skip it instead of breaking shell startup.
        try:
            execx(_atuin_out)
        except Exception as _e:
            print(f'my-shell: atuin init skipped (output changed?): {_e}', file=sys.stderr)
