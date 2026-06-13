"""pytest wrappers around the self-test stages (also run via
`python realbacktest.py selftest`)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rbt import selftest as st                                # noqa: E402

_fx = None


def _fx_once():
    global _fx
    if _fx is None:
        _fx = st.build(log=lambda *_: None)
    return _fx


def test_bs_roundtrip():
    st.test_bs_roundtrip()


def test_iv_recovery():
    st.test_iv_recovery(_fx_once())


def test_no_lookahead():
    st.test_no_lookahead(_fx_once())


def test_end_to_end():
    st.test_end_to_end(_fx_once())
