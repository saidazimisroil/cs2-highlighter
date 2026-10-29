import sys

import pytest

from app.render import GAME_ERROR_HINT, RenderError, _run


def fake_csdm(lines, code=0):
    """A command that prints these lines and exits with this code, like csdm would."""
    return [sys.executable, "-c", f"import sys; print({chr(10).join(lines)!r}); sys.exit({code})"]


def test_game_error_fails_even_when_csdm_exits_0():
    log = []
    cmd = fake_csdm(["Starting Counter-Strike...", "Recording in progress...", "Game error"])
    with pytest.raises(RenderError) as e:
        _run(cmd, log.append)
    assert str(e.value) == GAME_ERROR_HINT
    assert "Game error" in log


def test_clean_run_passes():
    log = []
    _run(fake_csdm(["Starting Counter-Strike...", "Recording in progress...", "Done"]), log.append)
    assert log[-1] == "Done"


def test_nonzero_exit_without_game_error():
    with pytest.raises(RenderError, match="exited with code 3"):
        _run(fake_csdm(["Something went wrong"], code=3), [].append)
