"""Finds and scores a player's highlight moments. Pure functions over MatchData (no I/O).

Run as a script to print the highlights of a demo:
    python -m app.highlights path/to/demo.dem --player NAME_OR_STEAMID
"""
from dataclasses import dataclass, field

from .config import TICKRATE
from .parser import CT, T, Death, MatchData, Round, Shot

POINTS_KILL = 1.0
POINTS_SPECIAL = {"headshot": 0.3, "wallbang": 0.8, "smoke": 0.6, "blind": 0.8, "airborne": 0.8, "noscope": 1.0}
POINTS_MELEE = 1.5
POINTS_MULTI = {3: 2.0, 4: 4.0, 5: 7.0}
POINTS_CLUTCH = {2: 2.0, 3: 4.0, 4: 6.0, 5: 9.0}
POINTS_FAST = 1.0  # two kills within FAST_GAP_S

FAST_GAP_S = 2
CLUTCH_LEAD_S = 1
CLUTCH_MAX_EXTRA_S = 8
MVP_MAX_GAP_S = 15  # keep the round-end music only if the round ended this soon after the last kill
MISS_GROUP_S = 5  # missed shots this close together (same round) share one clip

# Recording modes: "best" scores every round with a kill; the others collect one kind of moment.
MODES = {"best": "HIGHLIGHTS", "knife": "KNIFE KILLS", "awp_miss": "AWP MISSES"}  # mode -> title card text

SPECIAL_TAGS = [  # (flag, label) in display order
    ("wallbang", "WALLBANG"), ("noscope", "NOSCOPE"), ("smoke", "THROUGH SMOKE"),
    ("blind", "BLIND"), ("airborne", "AIRBORNE"),
]
IGNORED_WEAPONS = {"world", "planted_c4", "trigger_hurt", ""}


@dataclass
class Segment:
    start_tick: int
    end_tick: int


@dataclass
class Highlight:
    id: str
    round: int
    kills: list[Death]
    score: float
    label: str
    tags: list[str]
    clutch: int | None  # number of enemies in a won 1vN
    segments: list[Segment]
    selected: bool = False
    death_tick: int | None = None  # when the player died this round; None if they survived
    until_death: bool = False  # clip runs on until the player's death
    until_round_end: bool = False  # clip runs on until the round ends
    misses: int = 0  # missed shots in the clip (awp_miss mode)

    @property
    def duration_s(self) -> float:
        return sum(s.end_tick - s.start_tick for s in self.segments) / TICKRATE


@dataclass
class Options:
    max_clips: int = 8
    min_score: float = 3.0
    pre_s: float = 4.0
    post_s: float = 2.0
    mvp_s: float = 10.0  # seconds after round end, for the MVP music; 0 = off


def is_melee(weapon: str) -> bool:
    return weapon.startswith("knife") or weapon == "bayonet" or weapon == "taser"


def player_kills(deaths: list[Death], rnd: Round, steamid: str) -> list[Death]:
    my_team = rnd.roster.get(steamid)
    out = []
    for d in deaths:
        if d.attacker != steamid or d.victim == steamid or d.weapon in IGNORED_WEAPONS:
            continue
        if my_team is not None and rnd.roster.get(d.victim) == my_team:
            continue  # teamkill
        out.append(d)
    return out


def find_clutch(deaths: list[Death], rnd: Round, steamid: str) -> tuple[int, int] | None:
    """(tick the player became last alive, enemies alive then) if they ended up 1vN with N >= 2."""
    team = rnd.roster.get(steamid)
    if team is None:
        return None
    enemy = CT if team == T else T
    alive = {T: set(), CT: set()}
    for sid, t in rnd.roster.items():
        alive[t].add(sid)

    def check(tick):
        if alive[team] == {steamid} and len(alive[enemy]) >= 2:
            return tick, len(alive[enemy])
        return None

    found = check(rnd.start_tick)
    for d in deaths:
        if found or d.tick > rnd.end_tick:
            break
        alive[T].discard(d.victim)
        alive[CT].discard(d.victim)
        found = check(d.tick)
    return found


def _segment(ticks: list[int], rnd: Round, clutch_tick: int | None, opts: Options,
             anchor_tick: int | None = None) -> Segment:
    """One uncut clip: from before the first moment (kill, missed shot) until after the anchor (by
    default the last moment; later for the until-death / until-round-end options), plus the MVP music."""
    anchor = max(ticks[-1], anchor_tick or 0)
    start = ticks[0] - int(opts.pre_s * TICKRATE)
    end = anchor + int(opts.post_s * TICKRATE)
    if opts.mvp_s > 0 and rnd.end_tick - anchor <= MVP_MAX_GAP_S * TICKRATE:
        end = max(end, rnd.end_tick + int(opts.mvp_s * TICKRATE))  # let the MVP music play
    if clutch_tick is not None:
        # show the moment they were left alone, but not a long walk before it
        lead = max(clutch_tick - CLUTCH_LEAD_S * TICKRATE, start - CLUTCH_MAX_EXTRA_S * TICKRATE)
        start = min(start, lead)
    return Segment(max(start, rnd.start_tick), min(end, rnd.limit_tick - 1))


def _run_on(round_deaths: list[Death], rnd: Round, steamid: str, until_death: bool,
            until_round_end: bool) -> tuple[int | None, bool, int | None]:
    """(the player's death tick this round, whether until-death applies, the tick the clip must run
    on to, or None) for the per-round until-death / until-round-end options."""
    death_tick = next((d.tick for d in round_deaths if d.victim == steamid), None)
    until_death = until_death and death_tick is not None  # can't record until a death that never happened
    anchors = [t for t, on in ((death_tick, until_death), (rnd.end_tick, until_round_end)) if on]
    return death_tick, until_death, max(anchors, default=None)


def score_round(deaths: list[Death], rnd: Round, steamid: str, opts: Options,
                until_death: bool = False, until_round_end: bool = False) -> Highlight | None:
    round_deaths = [d for d in deaths if d.round == rnd.number]
    kills = player_kills(round_deaths, rnd, steamid)
    if not kills:
        return None

    score = POINTS_KILL * len(kills)
    specials: list[str] = []
    for k in kills:
        for flag, pts in POINTS_SPECIAL.items():
            if getattr(k, flag):
                score += pts
        if is_melee(k.weapon):
            score += POINTS_MELEE
    for flag, tag in SPECIAL_TAGS:
        if any(getattr(k, flag) for k in kills):
            specials.append(tag)
    if any(k.weapon == "taser" for k in kills):
        specials.append("ZEUS")
    if any(is_melee(k.weapon) and k.weapon != "taser" for k in kills):
        specials.append("KNIFE")

    n = len(kills)
    score += POINTS_MULTI.get(min(n, 5), 0.0)
    if any(b.tick - a.tick <= FAST_GAP_S * TICKRATE for a, b in zip(kills, kills[1:])):
        score += POINTS_FAST

    clutch = find_clutch(round_deaths, rnd, steamid)
    won_clutch = None
    if clutch and rnd.winner is not None and rnd.winner == rnd.roster.get(steamid):
        won_clutch = clutch[1]
        score += POINTS_CLUTCH.get(min(won_clutch, 5), 0.0)

    parts = [f"ROUND {rnd.number}"]
    if won_clutch:
        parts.append(f"1v{won_clutch} CLUTCH")
    if n >= 2:
        hs = sum(k.headshot for k in kills)
        mk = "ACE" if n >= 5 else f"{n}K"
        parts.append(f"{mk} ({hs} HS)" if hs else mk)
    parts.extend(specials[:2])
    if len(parts) == 1:
        parts.append(f"{kills[0].weapon.upper()} KILL" + (" (HS)" if kills[0].headshot else ""))

    death_tick, until_death, anchor = _run_on(round_deaths, rnd, steamid, until_death, until_round_end)
    seg = _segment([k.tick for k in kills], rnd, clutch[0] if won_clutch else None, opts, anchor)
    return Highlight(
        id=f"r{rnd.number}",
        round=rnd.number,
        kills=kills,
        score=round(score, 1),
        label=" · ".join(parts),
        tags=specials,
        clutch=won_clutch,
        segments=[seg],
        death_tick=death_tick,
        until_death=until_death,
        until_round_end=until_round_end,
    )


def _count(n: int, one: str, many: str) -> str:
    return one if n == 1 else f"{n} {many}"


def detect_knife(match: MatchData, steamid: str, opts: Options, until_death=frozenset(),
                 until_round_end=frozenset()) -> list[Highlight]:
    """One clip per round in which the player got knife kills (the zeus doesn't count)."""
    out = []
    for rnd in match.rounds:
        round_deaths = [d for d in match.deaths if d.round == rnd.number]
        kills = [k for k in player_kills(round_deaths, rnd, steamid) if is_melee(k.weapon) and k.weapon != "taser"]
        if not kills:
            continue
        hid = f"r{rnd.number}"
        death_tick, ud, anchor = _run_on(round_deaths, rnd, steamid, hid in until_death, hid in until_round_end)
        out.append(Highlight(
            id=hid, round=rnd.number, kills=kills, score=float(len(kills)),
            label=f"ROUND {rnd.number} · {_count(len(kills), 'KNIFE KILL', 'KNIFE KILLS')}",
            tags=["KNIFE"], clutch=None, segments=[_segment([k.tick for k in kills], rnd, None, opts, anchor)],
            selected=True, death_tick=death_tick, until_death=ud, until_round_end=hid in until_round_end,
        ))
    return out


def detect_awp_misses(match: MatchData, steamid: str, opts: Options, until_death=frozenset(),
                      until_round_end=frozenset()) -> list[Highlight]:
    """The player's AWP shots that hit nobody. Misses in the same round less than MISS_GROUP_S apart
    share one clip."""
    groups: list[list[Shot]] = []
    for s in match.shots:
        if s.shooter != steamid or s.hit or s.weapon != "weapon_awp":
            continue
        last = groups[-1][-1] if groups else None
        if last and last.round == s.round and s.tick - last.tick <= MISS_GROUP_S * TICKRATE:
            groups[-1].append(s)
        else:
            groups.append([s])
    rounds = {r.number: r for r in match.rounds}
    out = []
    for g in groups:
        rnd = rounds.get(g[0].round)
        if rnd is None or g[0].tick >= rnd.limit_tick:
            continue
        hid = f"m{g[0].tick}"
        round_deaths = [d for d in match.deaths if d.round == rnd.number]
        death_tick, ud, anchor = _run_on(round_deaths, rnd, steamid, hid in until_death, hid in until_round_end)
        out.append(Highlight(
            id=hid, round=rnd.number, kills=[], score=float(len(g)),
            label=f"ROUND {rnd.number} · {_count(len(g), 'AWP MISS', 'AWP MISSES')}",
            tags=["MISS"], clutch=None, segments=[_segment([s.tick for s in g], rnd, None, opts, anchor)],
            selected=True, death_tick=death_tick, until_death=ud, until_round_end=hid in until_round_end,
            misses=len(g),
        ))
    return out


def detect_highlights(match: MatchData, steamid: str, opts: Options | None = None,
                      until_death: set[str] | frozenset = frozenset(),
                      until_round_end: set[str] | frozenset = frozenset(), mode: str = "best") -> list[Highlight]:
    """Clips for the given mode, in match order. mode "best": every round where the player got a kill;
    the best ones have selected=True. "knife" / "awp_miss": every knife-kill round / missed AWP shot,
    all selected. until_death / until_round_end are highlight ids ("r3") whose clips should run on longer."""
    opts = opts or Options()
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}")
    if mode == "knife":
        return detect_knife(match, steamid, opts, until_death, until_round_end)
    if mode == "awp_miss":
        return detect_awp_misses(match, steamid, opts, until_death, until_round_end)
    candidates = [h for r in match.rounds
                  if (h := score_round(match.deaths, r, steamid, opts,
                                       f"r{r.number}" in until_death, f"r{r.number}" in until_round_end))]
    qualifying = [h for h in candidates if h.score >= opts.min_score or h.tags]
    for h in sorted(qualifying, key=lambda h: -h.score)[: opts.max_clips]:
        h.selected = True
    return candidates


def _main():
    import argparse
    import sys
    from .parser import parse_demo

    ap = argparse.ArgumentParser(description="Print a player's highlights from a CS2 demo.")
    ap.add_argument("demo")
    ap.add_argument("--player", help="player name or SteamID64 (omit to list players)")
    ap.add_argument("--max-clips", type=int, default=8)
    ap.add_argument("--mode", choices=list(MODES), default="best")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    match = parse_demo(args.demo)
    print(f"Map: {match.map_name}  Rounds: {len(match.rounds)}")
    if not args.player:
        for p in match.players:
            print(f"  {p.steamid}  {'T ' if p.team == T else 'CT'}  {p.name}")
        return
    q = args.player.lower()
    player = next((p for p in match.players if p.steamid == args.player or p.name.lower() == q), None)
    if not player:
        raise SystemExit(f"No player matching {args.player!r}. Run without --player to list them.")
    won, lost = match.score_for(player.steamid)
    print(f"Player: {player.name}  Score from their side: {won}-{lost}\n")
    for h in detect_highlights(match, player.steamid, Options(max_clips=args.max_clips), mode=args.mode):
        mark = "*" if h.selected else " "
        segs = ", ".join(f"{s.start_tick}-{s.end_tick}" for s in h.segments)
        print(f" {mark} {h.score:5.1f}  {h.duration_s:5.1f}s  {h.label:<45} ticks {segs}")
    print("\n* = picked for the video")


if __name__ == "__main__":
    _main()
