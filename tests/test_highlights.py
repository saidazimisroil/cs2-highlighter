from app.config import TICKRATE as S
from app.highlights import MISS_GROUP_S, Options, detect_highlights, find_clutch, score_round
from app.parser import CT, T, Death, MatchData, Player, Round, Shot

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


def test_mvp_music_plays_when_the_round_ends_soon_after_the_last_kill():
    rnd = make_round(end=2000 + 3 * S)  # round decided 3 s after my kill
    h = score_round([kill(2000, "e1")], rnd, ME, Options(mvp_s=6))
    assert h.segments[0].end_tick == rnd.end_tick + 6 * S
    h = score_round([kill(2000, "e1")], rnd, ME, Options(mvp_s=0))
    assert h.segments[0].end_tick == 2000 + 2 * S


def test_mvp_music_skipped_when_the_round_ends_long_after():
    rnd = make_round(end=2000 + 40 * S)
    h = score_round([kill(2000, "e1")], rnd, ME, Options(mvp_s=10))
    assert h.segments[0].end_tick == 2000 + 2 * S


def test_mvp_music_stops_at_the_next_round():
    rnd = make_round(end=2000 + S)  # next round starts 10 s after round end
    h = score_round([kill(2000, "e1")], rnd, ME, Options(mvp_s=20))
    assert h.segments[0].end_tick == rnd.limit_tick - 1


def test_until_death_records_until_the_player_dies():
    rnd = make_round()  # round ends long after, so no MVP music
    deaths = [kill(2000, "e1"), kill(2000 + 30 * S, ME, attacker="e2")]
    h = score_round(deaths, rnd, ME, Options(), until_death=True)
    assert h.until_death and h.death_tick == 2000 + 30 * S
    assert h.segments[0].end_tick == 2000 + 30 * S + 2 * S
    # off: stops after the last kill as before
    h = score_round(deaths, rnd, ME, Options())
    assert not h.until_death
    assert h.segments[0].end_tick == 2000 + 2 * S


def test_until_death_ignored_when_the_player_survived():
    rnd = make_round()
    h = score_round([kill(2000, "e1")], rnd, ME, Options(), until_death=True)
    assert h.death_tick is None and not h.until_death
    assert h.segments[0].end_tick == 2000 + 2 * S


def test_until_death_keeps_kills_made_after_dying():
    rnd = make_round()
    deaths = [kill(2000, ME, attacker="e2"), kill(2000 + 3 * S, "e1", weapon="hegrenade")]
    h = score_round(deaths, rnd, ME, Options(), until_death=True)
    assert h.segments[0].end_tick == 2000 + 3 * S + 2 * S


def test_until_round_end_records_to_the_round_end_and_mvp():
    rnd = make_round(end=2000 + 60 * S)
    h = score_round([kill(2000, "e1")], rnd, ME, Options(mvp_s=6), until_round_end=True)
    assert h.until_round_end
    assert h.segments[0].end_tick == rnd.end_tick + 6 * S
    h = score_round([kill(2000, "e1")], rnd, ME, Options(mvp_s=0), until_round_end=True)
    assert h.segments[0].end_tick == rnd.end_tick + 2 * S


def test_both_options_use_whichever_is_later():
    rnd = make_round(end=2000 + 60 * S)
    died_after_round = [kill(2000, "e1"), kill(rnd.end_tick + 3 * S, ME, attacker="e2")]  # killed while saving
    h = score_round(died_after_round, rnd, ME, Options(mvp_s=0), until_death=True, until_round_end=True)
    assert h.segments[0].end_tick == rnd.end_tick + 3 * S + 2 * S
    died_first = [kill(2000, "e1"), kill(2000 + 10 * S, ME, attacker="e2")]
    h = score_round(died_first, rnd, ME, Options(mvp_s=0), until_death=True, until_round_end=True)
    assert h.segments[0].end_tick == rnd.end_tick + 2 * S


def test_detect_highlights_applies_options_per_round():
    rounds = [make_round(number=i, start=i * 10000, end=i * 10000 + 5000) for i in (1, 2)]
    deaths = [kill(10500, "e1", round=1), kill(20500, "e1", round=2)]
    match = MatchData("de_test", [Player(ME, "me", T)], rounds, deaths)
    hs = detect_highlights(match, ME, Options(mvp_s=0), until_round_end={"r2"})
    assert [h.until_round_end for h in hs] == [False, True]
    assert hs[0].segments[0].end_tick == 10500 + 2 * S
    assert hs[1].segments[0].end_tick == 25000 + 2 * S


# ---- knife and AWP-miss modes -------------------------------------------------------------------

def shot(tick, round=1, shooter=ME, hit=False, weapon="weapon_awp"):
    return Shot(tick, round, shooter, weapon, hit)


def two_rounds(deaths=(), shots=()):
    rounds = [make_round(number=i, start=i * 10000, end=i * 10000 + 5000) for i in (1, 2)]
    return MatchData("de_test", [Player(ME, "me", T)], rounds, list(deaths), shots=list(shots))


def test_knife_mode_keeps_only_knife_kills():
    match = two_rounds([
        kill(10500, "e1", round=1, weapon="ak47"),
        kill(10600, "e2", round=1, weapon="knife_t"),
        kill(10700, "e3", round=1, weapon="taser"),
        kill(10800, "e4", round=1, weapon="bayonet"),
        kill(20500, "e1", round=2, weapon="ak47"),  # no knife kill: no clip
    ])
    hs = detect_highlights(match, ME, Options(mvp_s=0), mode="knife")
    assert [h.id for h in hs] == ["r1"]
    h = hs[0]
    assert [k.victim for k in h.kills] == ["e2", "e4"]
    assert h.label == "ROUND 1 · 2 KNIFE KILLS" and h.selected
    assert (h.segments[0].start_tick, h.segments[0].end_tick) == (10600 - 4 * S, 10800 + 2 * S)


def test_knife_mode_single_kill_label():
    hs = detect_highlights(two_rounds([kill(10600, "e2", weapon="knife")]), ME, Options(), mode="knife")
    assert hs[0].label == "ROUND 1 · KNIFE KILL"


def test_awp_misses_grouped_per_round_and_hits_ignored():
    gap = MISS_GROUP_S * S
    match = two_rounds(shots=[
        shot(10500), shot(10500 + gap), shot(10500 + gap + 10, hit=True),  # 2 misses, then a hit
        shot(10500 + 3 * gap),  # too long after: own clip
        shot(10600, shooter="e1"),  # someone else's miss
        shot(14990), shot(20100, round=2),  # close in time, but different rounds
        shot(20200, weapon="weapon_ssg08"),
    ])
    hs = detect_highlights(match, ME, Options(mvp_s=0), mode="awp_miss")
    assert [(h.id, h.misses) for h in hs] == [
        ("m10500", 2), (f"m{10500 + 3 * gap}", 1), ("m14990", 1), ("m20100", 1)]
    assert hs[0].label == "ROUND 1 · 2 AWP MISSES" and hs[2].label == "ROUND 1 · AWP MISS"
    assert all(h.selected and h.kills == [] for h in hs)
    assert (hs[0].segments[0].start_tick, hs[0].segments[0].end_tick) == (10500 - 4 * S, 10500 + gap + 2 * S)


def test_awp_miss_until_death():
    match = two_rounds([kill(12000, ME, attacker="e1")], [shot(10500)])
    h = detect_highlights(match, ME, Options(mvp_s=0), until_death={"m10500"}, mode="awp_miss")[0]
    assert h.until_death and h.segments[0].end_tick == 12000 + 2 * S
    h = detect_highlights(match, ME, Options(mvp_s=0), mode="awp_miss")[0]
    assert not h.until_death and h.segments[0].end_tick == 10500 + 2 * S


def test_unknown_mode_rejected():
    import pytest
    with pytest.raises(ValueError):
        detect_highlights(two_rounds(), ME, mode="nope")


# ---- deaths mode --------------------------------------------------------------------------------

def deaths_of(deaths):
    return detect_highlights(two_rounds(deaths), ME, Options(), mode="deaths")


def test_death_is_recorded_from_my_view_then_the_killers():
    h, = deaths_of([kill(10500, "e1", round=1), kill(11000, ME, attacker="e2", round=1, weapon="awp", headshot=True)])
    assert h.id == "d11000" and h.selected and h.death_tick == 11000
    assert h.label == "ROUND 1 · KILLED BY e2 (AWP · HS)"
    mine, theirs = h.segments
    assert (mine.start_tick, mine.end_tick) == (11000 - 4 * S, 11000 + 2 * S)
    assert (theirs.start_tick, theirs.end_tick) == (mine.start_tick, mine.end_tick)
    assert (mine.pov, theirs.pov, theirs.pov_name) == ("", "e2", "e2")


def test_death_label_lists_special_flags_and_silencer_names():
    h, = deaths_of([kill(11000, ME, attacker="e2", weapon="m4a1_silencer", wallbang=True)])
    assert h.label == "ROUND 1 · KILLED BY e2 (M4A1-S) · WALLBANG"


def test_deaths_without_a_killer_have_only_my_view():
    hs = deaths_of([kill(11000, ME, attacker="", weapon="planted_c4"),
                    kill(21000, ME, attacker="", round=2, weapon="world")])
    assert [h.label for h in hs] == ["ROUND 1 · BOMB", "ROUND 2 · FALL"]
    assert all(len(h.segments) == 1 for h in hs)
    h, = deaths_of([kill(11000, ME, attacker=ME, weapon="hegrenade")])
    assert h.label == "ROUND 1 · SUICIDE" and len(h.segments) == 1


def test_teamkill_label():
    h, = deaths_of([kill(11000, ME, attacker="m1", weapon="ak47")])
    assert h.label == "ROUND 1 · TEAMKILLED BY m1 (AK47)" and h.segments[1].pov == "m1"


def test_no_killer_view_when_the_killer_was_already_dead():
    h, = deaths_of([kill(10900, "e2", attacker="m1"), kill(11000, ME, attacker="e2", weapon="molotov")])
    assert len(h.segments) == 1


def test_other_players_deaths_and_mvp_are_ignored():
    match = two_rounds([kill(10500, "e1"), kill(11000, "m1", attacker="e1")])
    assert detect_highlights(match, ME, Options(), mode="deaths") == []
    rnd_end = make_round(end=11000 + 2 * S)
    m = MatchData("de_test", [Player(ME, "me", T)], [rnd_end], [kill(11000, ME, attacker="e1")])
    h, = detect_highlights(m, ME, Options(mvp_s=10), mode="deaths")
    assert h.segments[0].end_tick == 11000 + 2 * S  # no MVP music after a death
