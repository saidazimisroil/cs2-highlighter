"""Turns a CS2 .dem file into plain match data (players, rounds, deaths) using demoparser2."""
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from demoparser2 import DemoParser

T, CT = 2, 3
_WINNER = {"T": T, "TERRORIST": T, "2": T, "CT": CT, "3": CT}


@dataclass
class Player:
    steamid: str
    name: str
    team: int  # team at the end of the demo


@dataclass
class Death:
    tick: int
    round: int
    attacker: str  # "" for world / bomb / fall damage
    attacker_name: str
    victim: str
    victim_name: str
    weapon: str
    headshot: bool = False
    wallbang: bool = False
    noscope: bool = False
    smoke: bool = False
    blind: bool = False
    airborne: bool = False


@dataclass
class Round:
    number: int
    start_tick: int  # freeze time ended
    end_tick: int  # round_end event (or the limit if the demo was cut)
    limit_tick: int  # next round's start; nothing after this belongs to the round
    winner: int | None  # T, CT or None
    roster: dict[str, int] = field(default_factory=dict)  # steamid -> team, alive at start


@dataclass
class MatchData:
    map_name: str
    players: list[Player]
    rounds: list[Round]
    deaths: list[Death]
    demo_version: int | None = None  # CS2 version the demo was recorded on, e.g. 14193

    def player(self, steamid: str) -> Player | None:
        return next((p for p in self.players if p.steamid == steamid), None)

    def score_for(self, steamid: str) -> tuple[int, int]:
        """(rounds won, rounds lost) from this player's side of the match."""
        won = lost = 0
        for r in self.rounds:
            team = r.roster.get(steamid)
            if team is None or r.winner is None:
                continue
            if r.winner == team:
                won += 1
            else:
                lost += 1
        return won, lost


def _sid(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
        return ""
    s = str(v)
    return "" if s in ("0", "nan", "<NA>") else s


def _str(v) -> str:
    return "" if v is None or v is pd.NA or (isinstance(v, float) and pd.isna(v)) else str(v)


def _bool(v) -> bool:
    try:
        return bool(v) and not pd.isna(v)
    except (TypeError, ValueError):
        return bool(v)


def _match_start_tick(parser: DemoParser, events: set[str]) -> int:
    # FACEIT/ESEA demos include knife rounds and warmup before the real match;
    # the last "match start" announcement marks the real first round.
    for name in ("round_announce_match_start", "begin_new_match"):
        if name in events:
            ticks = parser.parse_event(name)["tick"].tolist()
            if ticks:
                return int(max(ticks))
    return 0


def parse_demo(path: str | Path) -> MatchData:
    parser = DemoParser(str(path))
    header = parser.parse_header()
    events = set(parser.list_game_events())
    match_start = _match_start_tick(parser, events)

    freeze = parser.parse_event("round_freeze_end", other=["is_warmup_period"])
    freeze = freeze[(freeze["tick"] >= match_start) & (~freeze["is_warmup_period"].fillna(False).astype(bool))]
    starts = sorted(set(int(t) for t in freeze["tick"]))

    # round_end is not always in list_game_events() but parses fine
    try:
        ends = parser.parse_event("round_end")
    except Exception:
        ends = pd.DataFrame(columns=["tick", "winner"])
    end_rows = sorted((int(r.tick), _str(r.winner).upper()) for r in ends.itertuples() if int(r.tick) > 0)

    deaths_df = parser.parse_event("player_death")
    last_tick = int(deaths_df["tick"].max()) if len(deaths_df) else (starts[-1] if starts else 0)

    rosters: dict[int, dict[str, int]] = {t: {} for t in starts}
    if starts:
        alive = parser.parse_ticks(["is_alive", "team_num"], ticks=starts)
        for r in alive.itertuples():
            if _bool(r.is_alive) and int(r.team_num) in (T, CT):
                rosters.setdefault(int(r.tick), {})[_sid(r.steamid)] = int(r.team_num)

    rounds: list[Round] = []
    for i, start in enumerate(starts):
        limit = starts[i + 1] if i + 1 < len(starts) else max(last_tick, start) + 10 * 64
        end_tick, winner = limit, None
        for tick, w in end_rows:
            if start < tick <= limit:
                end_tick, winner = tick, _WINNER.get(w)
                break
        rounds.append(Round(i + 1, start, end_tick, limit, winner, rosters.get(start, {})))

    deaths: list[Death] = []
    for r in deaths_df.itertuples():
        tick = int(r.tick)
        idx = bisect_right(starts, tick) - 1
        if idx < 0:
            continue  # before the real match (warmup, knife round)
        deaths.append(Death(
            tick=tick,
            round=idx + 1,
            attacker=_sid(getattr(r, "attacker_steamid", None)),
            attacker_name=_str(getattr(r, "attacker_name", "")),
            victim=_sid(getattr(r, "user_steamid", None)),
            victim_name=_str(getattr(r, "user_name", "")),
            weapon=_str(getattr(r, "weapon", "")),
            headshot=_bool(getattr(r, "headshot", False)),
            wallbang=int(getattr(r, "penetrated", 0) or 0) > 0,
            noscope=_bool(getattr(r, "noscope", False)),
            smoke=_bool(getattr(r, "thrusmoke", False)),
            blind=_bool(getattr(r, "attackerblind", False)),
            airborne=_bool(getattr(r, "attackerinair", False)),
        ))
    deaths.sort(key=lambda d: d.tick)

    players = []
    for r in parser.parse_player_info().itertuples():
        sid = _sid(r.steamid)
        if sid:
            players.append(Player(sid, _str(r.name), int(r.team_number) if not pd.isna(r.team_number) else 0))
    players.sort(key=lambda p: (p.team, p.name.lower()))

    version = str(header.get("patch_version", ""))
    return MatchData(header.get("map_name", "unknown"), players, rounds, deaths,
                     int(version) if version.isdigit() else None)
