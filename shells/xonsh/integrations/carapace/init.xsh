import shutil
import sys

if shutil.which('carapace'):
    $CARAPACE_BRIDGES = 'zsh,fish,bash,inshellisense'
    $COMPLETIONS_CONFIRM = True
    try:
        execx($(carapace _carapace xonsh))
    except Exception as _e:
        print(f'my-shell: carapace init skipped, completions off: {_e}', file=sys.stderr)
