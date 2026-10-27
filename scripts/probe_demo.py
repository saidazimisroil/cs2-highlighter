import sys, time
import pandas as pd
from demoparser2 import DemoParser
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
t = time.time()
p = DemoParser(sys.argv[1])
print("header", p.parse_header())
d = p.parse_event("player_death", player=["X", "Y", "Z", "team_num"], other=["total_rounds_played", "is_warmup_period"])
print("death cols", list(d.columns)); print(d.head(3).T)
print("dtypes", d[["attacker_steamid", "tick"]].dtypes.to_dict())
for name, df in p.parse_events(["round_end", "round_freeze_end", "round_start"], other=["total_rounds_played", "is_warmup_period"]):
    print("==", name, list(df.columns), len(df)); print(df.head(4))
pi = p.parse_player_info(); print(pi)
ft = p.parse_events(["round_freeze_end"])[0][1]["tick"].tolist()[:2]
print(p.parse_ticks(["is_alive", "team_num"], ticks=ft).head(12))
print("secs", time.time() - t)
