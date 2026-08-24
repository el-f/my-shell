"""Box-drawing helpers."""

import re
import shutil
import sys

_BOX_CHARS = "─│╭╮╰╯✓✗"


def _ensure_unicode_stdout(stream=None):
    """Windows gives a redirected stdout the cp1252 codec, which cannot encode a box."""
    stream = sys.stdout if stream is None else stream
    try:
        _BOX_CHARS.encode(getattr(stream, "encoding", None) or "utf-8")
        return
    except UnicodeEncodeError, LookupError:
        pass
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def _box_width(minimum=46, maximum=80):
    """Terminal-aware box width: grows with the terminal, floored for alignment
    and capped so boxes stay readable on very wide terminals."""
    cols = shutil.get_terminal_size(fallback=(80, 24)).columns
    return max(minimum, min(cols - 2, maximum))


def _box_header(title, width=46):
    title_part = f"─ {title} "
    remaining = width - len(title_part) - 2
    pad = "─" * remaining
    return f"\033[36m╭{title_part}{pad}╮\033[0m"


def _box_row(label, value, width=46):
    content = f"  {label:<14}{value}"
    pad = " " * max(0, width - len(content) - 2)
    return f"\033[36m│\033[0m{content}{pad}\033[36m│\033[0m"


def _box_row_raw(content, width=46):
    visible = re.sub(r"\033\[[0-9;]*m", "", content)
    pad = " " * max(0, width - len(visible) - 2)
    return f"\033[36m│\033[0m{content}{pad}\033[36m│\033[0m"


def _box_footer(width=46):
    inner = "─" * (width - 2)
    return f"\033[36m╰{inner}╯\033[0m"
