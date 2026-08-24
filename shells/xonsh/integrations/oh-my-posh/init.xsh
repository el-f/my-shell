import os
import shutil
import sys

if shutil.which('oh-my-posh'):
    try:
        _omp_theme_name = __xonsh__.env.get('MY_SHELL_OMP_THEME', 'jblab_2021')
        _theme = os.path.join(
            __xonsh__.env.get('MY_SHELL_DIR', '.'),
            'shells',
            'shared',
            'oh-my-posh',
            'themes',
            f'{_omp_theme_name}.omp.json',
        )
        if os.path.exists(_theme):
            execx($(oh-my-posh init xonsh --config @(_theme)))
        else:
            execx($(oh-my-posh init xonsh --config @(_omp_theme_name)))
        del _theme, _omp_theme_name
    except Exception as _e:
        print(f'my-shell: oh-my-posh init skipped, default prompt in use: {_e}', file=sys.stderr)
