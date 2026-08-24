"""Property-based tests (Hypothesis) for the pure render/escape transforms.

These functions are pure string transforms, so a property covers far more input
space than hand-picked examples: escaping round-trips, output stays parseable.
"""

import ast
import re
from unittest.mock import patch

from hypothesis import given
from hypothesis import strategies as st

from core.render import render_xonsh
from core.utils import escape_nushell_path, escape_python_path

# Control chars can't sit in a generated single-line string literal, and paths never have them.
_safe_text = st.text(st.characters(blacklist_categories=("Cs", "Cc")), max_size=60)

# Simple identifiers for generated alias group/name keys.
_ident = st.from_regex(r"[a-z][a-z0-9_]{0,9}", fullmatch=True)


@given(_safe_text)
def test_escape_python_path_roundtrips(s: str):
    """`'{escape_python_path(s)}'` must literal-eval back to exactly s."""
    escaped = escape_python_path(s)
    assert ast.literal_eval("'" + escaped + "'") == s


def _unescape_nushell(escaped: str) -> str:
    """Undo the two escapes a Nushell double-quoted string honours."""
    return re.sub(r"\\(.)", lambda m: m.group(1), escaped)


@given(_safe_text)
def test_escape_nushell_path_roundtrips_inside_double_quotes(s: str):
    """`"{escape_nushell_path(s)}"` must read back as exactly s."""
    with patch("core.utils.is_windows", return_value=False):
        escaped = escape_nushell_path(s)
    assert _unescape_nushell(escaped) == s


@given(_safe_text)
def test_escape_nushell_path_windows_has_no_backslash(s: str):
    """On Windows, backslashes become forward slashes -- only quote escapes are left."""
    with patch("core.utils.is_windows", return_value=True):
        escaped = escape_nushell_path(s)
    assert "\\" not in _unescape_nushell(escaped)


@given(st.dictionaries(_ident, st.dictionaries(_ident, _safe_text, max_size=4), max_size=3))
def test_render_xonsh_output_always_parses(groups: dict):
    """Generated xonsh aliases must always be valid Python (renderer uses repr)."""
    output = render_xonsh(groups, {})
    ast.parse(output)  # raises SyntaxError if the renderer emitted something invalid
