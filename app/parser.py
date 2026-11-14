"""Turns a CS2 .dem file into plain match data (players, rounds, deaths) using demoparser2."""
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from demoparser2 import DemoParser

T, CT = 2, 3
END_MARGIN = 64  # ticks (1 s) kept free at the end of the demo
NOT_GUNS = ("knife", "bayonet", "grenade", "flashbang", "molotov", "decoy", "c4", "taser")  # weapon_fire names
HIT_WINDOW = 2  # ticks after a shot in which its player_hurt may land
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
class Shot:
    tick: int
    round: int
    shooter: str
    weapon: str  # as in weapon_fire, e.g. "weapon_awp"
    hit: bool  # damaged a player (player_hurt from the shooter on the same tick)


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
    shots: list[Shot] = field(default_factory=list)  # gun shots (is_gun), in tick order

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

    def kd_for(self, steamid: str) -> tuple[int, int]:
        """(kills, deaths) as on the scoreboard: kills leave out suicides and teamkills."""
        rosters = {r.number: r.roster for r in self.rounds}
        kills = deaths = 0
        for d in self.deaths:
            if d.victim == steamid:
                deaths += 1
            elif d.attacker == steamid:
                roster = rosters.get(d.round, {})
                if steamid not in roster or roster.get(d.victim) != roster[steamid]:
                    kills += 1
        return kills, deaths


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


def _last_tick(parser: DemoParser) -> int:
    """Last tick with player data, i.e. where playback ends (the header doesn't say)."""
    try:
        ticks = parser.parse_ticks(["game_time"])["tick"]
    except Exception:
        return 0
    return int(ticks.max()) if len(ticks) else 0


def is_gun(weapon: str) -> bool:
    return weapon.startswith("weapon_") and not any(w in weapon for w in NOT_GUNS)


def _parse_shots(parser: DemoParser, starts: list[int]) -> list[Shot]:
    """Gun shots in the real match, each marked hit or miss. A hit's player_hurt lands on the shot's
    tick; HIT_WINDOW allows for a tick or two of slack. Any damage by the shooter counts, because
    player_hurt names some guns differently from weapon_fire (e.g. hkp2000 vs usp_silencer)."""
    try:
        fire = parser.parse_event("weapon_fire")
        hurt = parser.parse_event("player_hurt")
    except Exception:
        return []
    if not len(fire):
        return []
    hits = {(_sid(r.attacker_steamid), int(r.tick)) for r in hurt.itertuples()} if len(hurt) else set()
    shots = []
    for r in fire[fire["weapon"].map(lambda w: is_gun(_str(w)))].itertuples():
        tick, shooter = int(r.tick), _sid(r.user_steamid)
        idx = bisect_right(starts, tick) - 1
        if idx < 0 or not shooter:
            continue  # before the real match (warmup, knife round)
        hit = any((shooter, t) in hits for t in range(tick, tick + HIT_WINDOW + 1))
        shots.append(Shot(tick, idx + 1, shooter, r.weapon, hit))
    shots.sort(key=lambda s: s.tick)
    return shots


SOURCES = (("faceit", "FACEIT"), ("xplay", "xPlay"), ("valve", "Valve MM"))  # server_name part -> label


def source_of(server_name: str | None) -> str:
    s = (server_name or "").lower()
    return next((label for key, label in SOURCES if key in s), "Other")


def demo_summary(path: str | Path) -> dict:
    """What the demo list shows, in about a second (parse_demo takes several):
    {map, score: [T rounds, CT rounds] at the end or None, source, patch}."""
    parser = DemoParser(str(path))
    header = parser.parse_header()
    score = None
    try:
        df = parser.parse_ticks(["team_rounds_total", "team_num"])
        last = df[df["tick"] == df["tick"].max()]
        teams = {int(r.team_num): int(r.team_rounds_total) for r in last.itertuples()
                 if pd.notna(r.team_num) and pd.notna(r.team_rounds_total) and int(r.team_num) in (T, CT)}
        if len(teams) == 2:
            score = [teams[T], teams[CT]]
    except Exception:
        pass
    version = str(header.get("patch_version", ""))
    return {"map": header.get("map_name", "unknown"), "score": score,
            "source": source_of(header.get("server_name")), "patch": int(version) if version.isdigit() else None}


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
    last_tick = _last_tick(parser) or (int(deaths_df["tick"].max()) if len(deaths_df) else 0)

    rosters: dict[int, dict[str, int]] = {t: {} for t in starts}
    if starts:
        alive = parser.parse_ticks(["is_alive", "team_num"], ticks=starts)
        for r in alive.itertuples():
            if _bool(r.is_alive) and int(r.team_num) in (T, CT):
                rosters.setdefault(int(r.tick), {})[_sid(r.steamid)] = int(r.team_num)

    rounds: list[Round] = []
    for i, start in enumerate(starts):
        # The last round ends with the demo, which often stops a few seconds after round end. Leave a
        # second of margin: commands scheduled after the demo's last tick never run, so the
        # recording wouldn't stop and CS2 wouldn't quit.
        limit = starts[i + 1] if i + 1 < len(starts) else max(last_tick - END_MARGIN, start + 1)
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
                     int(version) if version.isdigit() else None, _parse_shots(parser, starts))
