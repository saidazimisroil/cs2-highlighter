import sys

import pytest

from app.config import find_cs2_version
from app.render import GAME_ERROR_HINT, RenderError, _run, demo_warning


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


def test_warns_only_for_the_known_crashing_pair(monkeypatch):
    monkeypatch.setattr("app.render.CRASHING_DEMOS", {14194: {14193}})
    assert "14193" in demo_warning(14193, 14194)
    assert demo_warning(14192, 14194) is None  # other demos play on this CS2
    assert demo_warning(14193, 14195) is None  # gone once CS2 updates
    assert demo_warning(None, 14194) is None
    assert demo_warning(14193, None) is None


def test_cs2_version_comes_from_steam_inf(tmp_path, monkeypatch):
    cs2 = tmp_path / "game" / "bin" / "win64" / "cs2.exe"
    cs2.parent.mkdir(parents=True)
    cs2.touch()
    inf = tmp_path / "game" / "csgo" / "steam.inf"
    inf.parent.mkdir()
    monkeypatch.setenv("CS2_PATH", str(cs2))
    assert find_cs2_version() is None  # no steam.inf
    inf.write_text("ClientVersion=2000939\nPatchVersion=1.41.9.4\n", encoding="utf-8")
    assert find_cs2_version() == 14194


def test_nonzero_exit_without_game_error():
    with pytest.raises(RenderError, match="exited with code 3"):
        _run(fake_csdm(["Something went wrong"], code=3), [].append)
