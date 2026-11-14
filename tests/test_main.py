import json

import pytest
from fastapi import HTTPException

import app.main as main
from app.parser import source_of


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    demos, output = tmp_path / "demos", tmp_path / "output"
    demos.mkdir()
    output.mkdir()
    monkeypatch.setattr(main, "DEMOS_DIR", demos)
    monkeypatch.setattr(main, "INDEX", demos / "index.json")
    monkeypatch.setattr(main, "OUTPUT_DIR", output)
    return tmp_path, demos, output


def test_delete_removes_uploaded_copies_but_never_the_users_own_file(dirs):
    tmp, demos, _ = dirs
    upload, own = demos / "abc_match.dem", tmp / "own.dem"
    upload.write_bytes(b"x")
    own.write_bytes(b"x")
    a, b = main._register(upload, "match.dem"), main._register(own, "own.dem")
    assert main.delete_demo(a) == {"deleted_file": True} and not upload.exists()
    assert main.delete_demo(b) == {"deleted_file": False} and own.exists()
    assert json.loads((demos / "index.json").read_text()) == {}


def test_saved_videos_cant_escape_the_output_folder(dirs):
    tmp, _, output = dirs
    (tmp / "secret.mp4").write_bytes(b"x")
    (output / "de_nuke_me_abc123.mp4").write_bytes(b"x")
    assert str(main.get_saved_video("de_nuke_me_abc123.mp4").path).endswith("de_nuke_me_abc123.mp4")
    for name in ("../secret.mp4", r"..\secret.mp4", "index.json"):
        with pytest.raises(HTTPException):
            main.get_saved_video(name)


def test_old_video_names_are_read_back():
    def info(name):
        return {k: v for k, v in main._video_info_from_name(name).items() if k in ("map", "player", "mode")}
    assert info("de_nuke_diaS_6dea75a80b") == {"map": "de_nuke", "player": "diaS", "mode": "best"}
    assert info("de_nuke_STARK_awp_miss_45d1f01e6b") == {"map": "de_nuke", "player": "STARK", "mode": "awp_miss"}
    assert info("de_dust2_far_cry001_1940f0a6a8")["player"] == "far_cry001"


def test_source_from_server_name():
    assert source_of("FACEIT.com register to play here") == "FACEIT"
    assert source_of("XPLAY.GG – CS2 Matches #280") == "xPlay"
    assert source_of("Valve Counter-Strike 2 eu_west Server") == "Valve MM"
    assert source_of(None) == "Other"
