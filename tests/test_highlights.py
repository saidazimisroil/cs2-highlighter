from app.config import TICKRATE as S
from app.highlights import Options, detect_highlights, find_clutch, score_round
from app.parser import CT, T, Death, MatchData, Player, Round

ME = "me"
MATES = ["m1", "m2", "m3", "m4"]
ENEMIES = ["e1", "e2", "e3", "e4", "e5"]


def make_round(number=1, start=1000, end=1000 + 100 * S, winner=T):
    roster = {ME: T, **{m: T for m in MATES}, **{e: CT for e in ENEMIES}}
    return Round(number, start, end, end + 10 * S, winner, roster)


def kill(tick, victim, attacker=ME, round=1, weapon="ak47", **flags):
    return Death(tick, round, attacker, attacker, victim, victim, weapon, **flags)


def test_ace_with_headshots_and_label():
    rnd = make_round()
    deaths = [kill(2000 + i * S, e, headshot=i < 2) for i, e in enumerate(ENEMIES)]
    h = score_round(deaths, rnd, ME, Options())
    assert len(h.kills) == 5
    assert "ACE (2 HS)" in h.label
    # 5 kills + 2*0.3 HS + ACE bonus 7 + fast-kill bonus 1
    assert h.score == 5 + 0.6 + 7 + 1
    # kills 1 s apart -> one continuous segment from first-4s to last+2s
    assert len(h.segments) == 1
    assert h.segments[0].start_tick == 2000 - 4 * S
    assert h.segments[0].end_tick == 2000 + 4 * S + 2 * S


def test_far_apart_kills_stay_in_one_uncut_segment():
    rnd = make_round()
    deaths = [kill(2000, "e1"), kill(2000 + 30 * S, "e2"), kill(2000 + 31 * S, "e3")]
    h = score_round(deaths, rnd, ME, Options())
    assert "3K" in h.label
    assert len(h.segments) == 1
    assert h.segments[0].start_tick == 2000 - 4 * S
    assert h.segments[0].end_tick == 2000 + 31 * S + 2 * S


def test_teamkills_and_bomb_kills_ignored():
    rnd = make_round()
    deaths = [kill(2000, "m1"), kill(2100, "e1", weapon="planted_c4")]
    assert score_round(deaths, rnd, ME, Options()) is None


def test_won_1v3_clutch():
    rnd = make_round(winner=T)
    deaths = [
        kill(1500, "m1", attacker="e1"),
        kill(1600, "m2", attacker="e1"),
        kill(1700, "e5"),  # my first kill: 4v4 -> still not a clutch
        kill(1800, "m3", attacker="e1"),
        kill(1900, "e4", attacker="m4"),
        kill(2000, "m4", attacker="e1"),  # now I'm alone vs e1, e2, e3
        kill(2500, "e1"),
        kill(2600, "e2"),
        kill(2700, "e3"),
    ]
    assert find_clutch(deaths, rnd, ME) == (2000, 3)
    h = score_round(deaths, rnd, ME, Options())
    assert h.clutch == 3
    assert "1v3 CLUTCH" in h.label
    assert "4K" in h.label
    # the moment I was left alone is on screen
    assert any(s.start_tick <= 2000 - S <= s.end_tick for s in h.segments)


def test_clutch_lead_in_added_when_kills_come_late():
    rnd = make_round(winner=T)
    deaths = [kill(1500 + i, m, attacker="e1") for i, m in enumerate(MATES)]  # alone 1v5 at 1503
    deaths += [kill(1503 + 20 * S + i * S, e) for i, e in enumerate(ENEMIES)]
    h = score_round(deaths, rnd, ME, Options())
    assert h.clutch == 5
    first_kill = deaths[4].tick
    # lead-in extends past the normal 4 s, but by at most 8 s more
    assert h.segments[0].start_tick == first_kill - 4 * S - 8 * S


def test_lost_clutch_gets_no_bonus():
    rnd = make_round(winner=CT)
    deaths = [kill(1500 + i, m, attacker="e1") for i, m in enumerate(MATES)] + [kill(2000, "e1")]
    h = score_round(deaths, rnd, ME, Options())
    assert h.clutch is None
    assert "CLUTCH" not in h.label


def test_special_kills_tagged_and_scored():
    rnd = make_round()
    h = score_round([kill(2000, "e1", weapon="awp", noscope=True, wallbang=True)], rnd, ME, Options())
    assert h.tags == ["WALLBANG", "NOSCOPE"]
    assert h.score == 1 + 0.8 + 1.0


def test_segments_clamped_to_round():
    rnd = make_round(start=1000)
    h = score_round([kill(1000 + S, "e1", weapon="knife")], rnd, ME, Options())
    assert h.segments[0].start_tick == 1000
    assert "KNIFE" in h.tags


def test_selection_picks_top_scores_and_special_singles():
    rounds = [make_round(number=i, start=i * 10000, end=i * 10000 + 5000) for i in (1, 2, 3)]
    deaths = [
        kill(10500, "e1", round=1),  # plain single kill -> not picked
        *[kill(20500 + i * S, e, round=2) for i, e in enumerate(ENEMIES[:3])],  # 3K -> picked
        kill(30500, "e1", round=3, smoke=True),  # special -> picked
    ]
    match = MatchData("de_test", [Player(ME, "me", T)], rounds, deaths)
    hs = detect_highlights(match, ME, Options(max_clips=8))
    assert [h.round for h in hs] == [1, 2, 3]
    assert [h.selected for h in hs] == [False, True, True]

    hs = detect_highlights(match, ME, Options(max_clips=1))
    assert [h.round for h in hs if h.selected] == [2]
