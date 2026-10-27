"""Finds and scores a player's highlight moments. Pure functions over MatchData (no I/O).

Run as a script to print the highlights of a demo:
    python -m app.highlights path/to/demo.dem --player NAME_OR_STEAMID
"""
from dataclasses import dataclass, field

from .config import TICKRATE
from .parser import CT, T, Death, MatchData, Round

POINTS_KILL = 1.0
POINTS_SPECIAL = {"headshot": 0.3, "wallbang": 0.8, "smoke": 0.6, "blind": 0.8, "airborne": 0.8, "noscope": 1.0}
POINTS_MELEE = 1.5
POINTS_MULTI = {3: 2.0, 4: 4.0, 5: 7.0}
POINTS_CLUTCH = {2: 2.0, 3: 4.0, 4: 6.0, 5: 9.0}
POINTS_FAST = 1.0  # two kills within FAST_GAP_S

FAST_GAP_S = 2
CLUSTER_GAP_S = 10  # kills further apart than this become separate segments
MERGE_GAP_S = 3  # segments closer than this are joined
CLUTCH_LEAD_S = 1
CLUTCH_MAX_EXTRA_S = 8

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

    @property
    def duration_s(self) -> float:
        return sum(s.end_tick - s.start_tick for s in self.segments) / TICKRATE


@dataclass
class Options:
    max_clips: int = 8
    min_score: float = 3.0
    pre_s: float = 4.0
    post_s: float = 2.0


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


def _segments(kills: list[Death], rnd: Round, clutch_tick: int | None, opts: Options) -> list[Segment]:
    pre, post = int(opts.pre_s * TICKRATE), int(opts.post_s * TICKRATE)
    clusters: list[list[Death]] = []
    for k in kills:
        if clusters and k.tick - clusters[-1][-1].tick <= CLUSTER_GAP_S * TICKRATE:
            clusters[-1].append(k)
        else:
            clusters.append([k])

    segs: list[Segment] = []
    clutch_done = clutch_tick is None
    for c in clusters:
        start, end = c[0].tick - pre, c[-1].tick + post
        if not clutch_done and c[-1].tick >= clutch_tick:
            # show the moment they were left alone, but not a long walk before it
            lead = max(clutch_tick - CLUTCH_LEAD_S * TICKRATE, start - CLUTCH_MAX_EXTRA_S * TICKRATE)
            start = min(start, lead)
            clutch_done = True
        start = max(start, rnd.start_tick)
        end = min(end, rnd.limit_tick - 1)
        if segs and start - segs[-1].end_tick < MERGE_GAP_S * TICKRATE:
            segs[-1].end_tick = max(segs[-1].end_tick, end)
        else:
            segs.append(Segment(start, end))
    return segs


def score_round(deaths: list[Death], rnd: Round, steamid: str, opts: Options) -> Highlight | None:
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

    segs = _segments(kills, rnd, clutch[0] if won_clutch else None, opts)
    return Highlight(
        id=f"r{rnd.number}",
        round=rnd.number,
        kills=kills,
        score=round(score, 1),
        label=" · ".join(parts),
        tags=specials,
        clutch=won_clutch,
        segments=segs,
    )


def detect_highlights(match: MatchData, steamid: str, opts: Options | None = None) -> list[Highlight]:
    """Every round where the player got a kill, in round order. The best ones have selected=True."""
    opts = opts or Options()
    candidates = [h for r in match.rounds if (h := score_round(match.deaths, r, steamid, opts))]
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
    for h in detect_highlights(match, player.steamid, Options(max_clips=args.max_clips)):
        mark = "*" if h.selected else " "
        segs = ", ".join(f"{s.start_tick}-{s.end_tick}" for s in h.segments)
        print(f" {mark} {h.score:5.1f}  {h.duration_s:5.1f}s  {h.label:<45} ticks {segs}")
    print("\n* = picked for the video")


if __name__ == "__main__":
    _main()
