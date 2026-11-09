import re
from pathlib import Path

from app.config import TICKRATE as S
from app.hlae import MIN_TICK, PREP_LEAD, RESUME_TICK, SEEK_LEAD, _best_take, build_cfgs
from app.render import RenderSettings

STEAMID = "76561199396077107"
OUT = Path(r"C:\out\raw")


def schedule(cfgs):
    """[(tick, command)] from init.cfg's mirv_cmd lines."""
    return [(int(t), c) for t, c in re.findall(r'^mirv_cmd addAtTick (\d+) "(.*)"$', cfgs["init.cfg"], re.M)]


def build(segments, name="DIAS_GEEK", demo=r"C:\demos\match.dem"):
    return build_cfgs(demo, STEAMID, name, segments, OUT, RenderSettings())


def test_jumps_to_each_segment_and_records_it():
    cfgs = build([(136828, 137724), (144413, 145309)])
    assert schedule(cfgs) == [
        (RESUME_TICK, "demo_resume"),
        (MIN_TICK, "exec cs2hl/setup"),
        (MIN_TICK + 1, f"demo_gototick {136828 - SEEK_LEAD}"),
        (136828 - PREP_LEAD, "exec cs2hl/prep1"),
        (136828, "mirv_streams record start"),
        (137724, "mirv_streams record end"),
        (137725, f"demo_gototick {144413 - SEEK_LEAD}"),
        (144413 - PREP_LEAD, "exec cs2hl/prep2"),
        (144413, "mirv_streams record start"),
        (145309, "mirv_streams record end"),
        (145309 + S // 2, "quit"),
    ]
    assert cfgs["init.cfg"].startswith("mirv_cmd clear\n")
    assert cfgs["init.cfg"].rstrip().endswith('demo_ui_mode 0\nplaydemo "C:/demos/match.dem"')
    assert cfgs["prep1.cfg"].startswith("demo_ui_mode 0\n")


def test_close_segments_play_through_instead_of_jumping():
    cfgs = build([(10000, 10500), (10500 + S // 2, 11000)])
    jumps = [c for _, c in schedule(cfgs) if c.startswith("demo_gototick")]
    assert jumps == [f"demo_gototick {10000 - SEEK_LEAD}"]


def test_segment_near_demo_start_keeps_ticks_ordered():
    ticks = [t for t, _ in schedule(build([(50, 400)]))][1:]  # after the tick-0 resume
    assert ticks == sorted(ticks) and ticks[0] == MIN_TICK
    assert all(t >= MIN_TICK for t in ticks)


def test_paused_demo_is_resumed_as_it_loads():
    # FACEIT demos start paused, and CS2 crashes when it skips back to tick 0 a few seconds in.
    assert schedule(build([(136828, 137724)]))[0] == (RESUME_TICK, "demo_resume")


def test_prep_cfg_focuses_player_and_names_outputs():
    prep = build([(5000, 6000)], name='Bad"Name').get("prep1.cfg")
    assert 'spec_player "BadName"' in prep
    assert prep.index("spec_mode 1") < prep.index("spec_player")
    assert 'mirv_streams record name "C:/out/raw/1"' in prep
    assert "{QUOTE}C:/out/raw/1/video.mp4{QUOTE}" in prep
    assert "mirv_streams record screen settings cs2hl1" in prep


def test_full_hud_is_shown():
    assert "cl_draw_only_deathnotices 0" in build([(5000, 6000)])["setup.cfg"]


def test_player_voices_are_enabled_before_the_demo_loads():
    init = build([(5000, 6000)])["init.cfg"]
    assert init.index("tv_listen_voice_indices -1") < init.index("playdemo")
    assert init.index("tv_listen_voice_indices_h -1") < init.index("playdemo")


def test_kill_feed_shows_only_the_player():
    setup = build([(5000, 6000)])["setup.cfg"]
    assert "mirv_deathmsg filter add block=1" in setup
    assert f"attackerMatch=x{STEAMID}" in setup
    assert "demo_ui_mode 0" in setup


def test_empty_takes_left_at_demo_end_are_ignored(tmp_path):
    # HLAE wrote a header-only take0001 next to the real audio when the demo ended.
    for take, size in (("take0000", 15_000_000), ("take0001", 44)):
        (tmp_path / take).mkdir()
        (tmp_path / take / "audio.wav").write_bytes(b"\0" * size)
    assert _best_take(tmp_path) == tmp_path / "take0000" / "audio.wav"
    (tmp_path / "take0000" / "audio.wav").write_bytes(b"\0" * 44)
    assert _best_take(tmp_path) is None  # no audio at all: keep the video only
