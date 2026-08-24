# Hand-rolled activation: execx(mise activate xonsh) fails -- mise emits unescaped Windows backslashes and `env=`, a xonsh keyword.

import shutil

_mise_bin = shutil.which('mise')
if _mise_bin:
    import subprocess
    from xonsh.built_ins import XSH

    XSH.env['MISE_SHELL'] = 'xonsh'

    def _mise_alias(args, _bin=_mise_bin):
        if args and args[0] in ('deactivate', 'shell', 'sh'):
            out = subprocess.run(
                [_bin] + list(args),
                env=XSH.env.detype(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout.decode()
            if out.strip():
                execx(out)
        else:
            subprocess.run([_bin] + list(args), env=XSH.env.detype())

    _mise_alias.__xonsh_callable__ = True
    XSH.aliases['mise'] = _mise_alias

    def _mise_hook(*args, _bin=_mise_bin, **kwargs):
        script = subprocess.run(
            [_bin, 'hook-env', '-s', 'xonsh'],
            env=XSH.env.detype(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ).stdout.decode()
        if script.strip():
            try:
                exec(compile(script, '<mise-hook>', 'exec'), {'XSH': XSH})
            except Exception as _e:
                # Warn once, not on every cd -- a broken hook would spam stderr.
                if not getattr(_mise_hook, '_warned', False):
                    _mise_hook._warned = True
                    import sys
                    print(f'my-shell: mise hook-env output not executable, '
                          f'tool auto-switching off: {_e}', file=sys.stderr)

    # Only refresh on directory changes; per-prompt hooks make the shell noisy.
    XSH.builtins.events.on_chdir(_mise_hook)

    # Run hook once to set initial PATH
    _mise_hook()

del _mise_bin
