"""
The world and the lookout: map, weather, fire, events, needs, activities,
memory (diary, opinions, beliefs, projects) and the radio.
"""
import json
import math
import os
import random
import threading
import time
from datetime import date, timedelta

import content as C

W, H = 64, 40            # map size in tiles
TILE_MI = 0.5            # one tile = half a mile
DAY = 1440
SEASON_DAYS = 98
START_DATE = date(2027, 6, 20)
MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]
BURNABLE = set("FML")

WINDOWS = {
    "daylight": set(range(7, 20)), "day": set(range(9, 18)), "dawn": set(range(5, 9)),
    "dusk": set(range(18, 22)), "night": {21, 22, 23, 0, 1, 2, 3}, "evening": {20, 21, 22, 23},
}
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


class SafeDict(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def cal(day):
    return START_DATE + timedelta(days=int(day))


def date_str(day):
    d = cal(day)
    return f"{MONTH_NAMES[d.month]} {d.day}"


def year_of(day):
    return 1 + int(day) // 365


def doy(day):
    return cal(day).timetuple().tm_yday


def season_of(day):
    m = cal(day).month
    return "winter" if m in (12, 1, 2) else "spring" if m in (3, 4, 5) else "summer" if m in (6, 7, 8) else "fall"


def fire_season(day):
    return 6 <= cal(day).month <= 9


def snow_cover(day):
    n = doy(day)
    return n >= 312 or n <= 118


def hhmm(minute):
    m = int(minute) % DAY
    return f"{m // 60:02d}:{m % 60:02d}"


def clamp(v, a, b):
    return max(a, min(b, v))


# ---------------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------------
def gen_map(seed):
    rng = random.Random(seed)
    tx, ty = 32, 19
    bumps = [(tx, ty, 1.0, 6.5)]
    for _ in range(16):
        bumps.append((rng.uniform(0, W), rng.uniform(0, H), rng.uniform(0.3, 0.75), rng.uniform(2.5, 7)))
    elev = [[0.0] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            e = 0.0
            for bx, by, a, s in bumps:
                e += a * math.exp(-((x - bx) ** 2 + (y - by) ** 2) / (2 * s * s))
            elev[y][x] = e + rng.uniform(-0.04, 0.04)
    t = [["F"] * W for _ in range(H)]
    # meadows
    for _ in range(9):
        cx, cy, r = rng.uniform(4, W - 4), rng.uniform(4, H - 4), rng.uniform(1.4, 3.0)
        for y in range(H):
            for x in range(W):
                if (x - cx) ** 2 + ((y - cy) * 1.3) ** 2 < r * r and elev[y][x] < 0.95:
                    t[y][x] = "M"
    # rock and snow on high ground
    for y in range(H):
        for x in range(W):
            if elev[y][x] > 1.18:
                t[y][x] = "R"
            if elev[y][x] > 1.9 and math.hypot(x - tx, y - ty) > 2.5:
                t[y][x] = "S"
    # lake
    lx, ly = 38.5, 24.5
    for y in range(H):
        for x in range(W):
            if ((x - lx) / 3.3) ** 2 + ((y - ly) / 2.2) ** 2 + rng.uniform(-0.12, 0.12) < 1:
                t[y][x] = "W"
    # logging cut
    for y in range(7, 12):
        for x in range(12, 19):
            t[y][x] = "L"

    def line(points, ch, jitter=0.0, keep=""):
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            n = int(max(abs(x1 - x0), abs(y1 - y0)) * 2) + 1
            for i in range(n + 1):
                f = i / n
                x = round(x0 + (x1 - x0) * f + rng.uniform(-jitter, jitter))
                y = round(y0 + (y1 - y0) * f + rng.uniform(-jitter, jitter))
                if 0 <= x < W and 0 <= y < H and t[y][x] not in keep:
                    t[y][x] = ch

    line([(42, 26), (47, 30), (53, 33), (58, 36), (63, 39)], "C", 0.4, keep="W")
    line([(0, 31), (10, 29), (20, 32), (30, 31), (35, 29), (45, 32), (55, 29), (63, 27)], "O", 0.0, keep="W")
    line([(10, 29), (13, 20), (15, 12)], "O", 0.0, keep="W")
    line([(30, 31), (31, 26), (33, 23), (32, 20)], "T", 0.3, keep="WO")
    line([(33, 23), (36, 24)], "T", 0.0, keep="WO")
    line([(32, 18), (27, 15), (24, 12)], "T", 0.3, keep="WO")
    for y in range(27, 29):
        for x in range(34, 37):
            if t[y][x] != "W":
                t[y][x] = "G"
    t[30][30] = "H"
    for y in range(ty - 1, ty + 2):
        for x in range(tx - 1, tx + 2):
            t[y][x] = "P"
    tiles = ["".join(r) for r in t]
    spots = {}
    kinds = {"F": "forest", "M": "meadow", "O": "road", "T": "trail", "W": "lake", "R": "rock",
             "G": "campground", "L": "forest", "S": "rock", "C": "forest"}
    for y in range(H):
        for x in range(W):
            k = kinds.get(tiles[y][x])
            if k:
                spots.setdefault(k, []).append([x, y])
    # nearest lakeshore point & a ridge point
    shore = min(([x, y] for y in range(H) for x in range(W) if tiles[y][x] == "W"),
                key=lambda p: math.hypot(p[0] - tx, p[1] - ty))
    ridge = [24.5, 12.5]
    return {"tiles": tiles, "tower": [tx, ty], "spots": spots,
            "shore": [shore[0] - 0.6, shore[1] - 0.9], "ridge": ridge}


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------
ACTS = {
    "scan": dict(loc="tower_cab", bored=0.03, attn=1.0, flav="cab",
                 desc="sweep the horizon with binoculars and the firefinder (your core duty)"),
    "watch_closely": dict(loc="catwalk", bored=-0.04, attn=1.0, flav=None, dur=(20, 45),
                          desc="study one sighting closely; target = sighting id like S3"),
    "log_weather": dict(loc="catwalk", bored=0.0, attn=0.8, flav="catwalk", dur=(15, 15),
                        desc="take and log a weather observation (duty: around 08:00 and 14:00)"),
    "read": dict(loc="tower_cab", bored=-0.12, attn=0.4, flav="cab",
                 desc="read a book you own; target = the title"),
    "write": dict(loc="tower_cab", bored=-0.10, attn=0.4, flav="cab",
                  desc="write in your notebook (thoughts, sketches, lists; not the diary)"),
    "think": dict(loc="catwalk", bored=-0.04, attn=0.6, flav="catwalk",
                  desc="sit on the catwalk and think"),
    "birdwatch": dict(loc="catwalk", bored=-0.10, attn=0.8, flav="catwalk",
                      desc="watch birds and wildlife with the field guide"),
    "carve": dict(loc="catwalk", bored=-0.12, attn=0.6, flav="catwalk",
                  desc="whittle wood with your pocketknife"),
    "chess": dict(loc="tower_cab", bored=-0.10, attn=0.3, flav="cab",
                  desc="play chess against yourself or study positions"),
    "listen_radio": dict(loc="tower_cab", bored=-0.08, attn=0.4, flav="radio_listen",
                         desc="listen to distant stations on the transistor radio"),
    "maintain": dict(loc="catwalk", bored=0.03, attn=0.5, flav="cab",
                     desc="general maintenance: wash windows, check lightning ground wires, tidy up"),
    "repair": dict(loc="tower_cab", bored=-0.06, attn=0.4, flav=None, dur=(30, 180),
                   desc="try to fix something that's broken; target = the thing (e.g. the east window). Longer attempts work better; some things take several tries"),
    "cook_eat": dict(loc="tower_cab", bored=-0.03, attn=0.4, flav="cab", dur=(30, 50),
                     desc="cook and eat"),
    "chop_wood": dict(loc="woodpile", bored=-0.04, attn=0.2, flav="woodpile", energy=2.0,
                      desc="split firewood"),
    "fetch_water": dict(loc="cistern", bored=0.0, attn=0.2, flav="cistern", dur=(15, 25),
                        desc="pump water from the cistern"),
    "garden": dict(loc="garden", bored=-0.08, attn=0.3, flav="garden",
                   desc="tend your small garden bed"),
    "walk_to_lake": dict(loc="lake_shore", bored=-0.15, attn=0.1, flav="lake", energy=1.8,
                         desc="hike down to {lake} (about {lake_mi} mi; you are away from the tower)"),
    "fish": dict(loc="lake_shore", bored=-0.12, attn=0.1, flav="lake", energy=1.3,
                 desc="fish at {lake} (you hike there; away from the tower)"),
    "hike_ridge": dict(loc="ridge", bored=-0.15, attn=0.4, flav="ridge", energy=1.6,
                       desc="hike out along the ridge"),
    "project": dict(loc="tower_cab", bored=-0.15, attn=0.4, flav="cab",
                    desc="work on one of your side projects; target = project name"),
    "use_item": dict(loc="catwalk", bored=-0.10, attn=0.5, flav="catwalk",
                     desc="use or play with something you own; target = the item"),
    "nap": dict(loc="tower_cab", bored=-0.02, attn=0.0, flav=None, sleep=True, dur=(20, 90),
                desc="nap on the cot"),
}
LOC_NAMES = {
    "tower_cab": "in the glass cab at the top of the tower", "catwalk": "on the catwalk around the cab",
    "woodpile": "at the woodpile below the tower", "garden": "at the garden bed by the tower",
    "cistern": "at the cistern", "outhouse": "at the outhouse", "helispot": "at the helispot",
    "lake_shore": "down at the lake", "ridge": "out on the ridge",
}
LOC_SEE = {"tower_cab": 1.0, "catwalk": 1.0, "woodpile": 0.45, "garden": 0.45, "cistern": 0.45,
           "outhouse": 0.3, "helispot": 0.5, "lake_shore": 0.1, "ridge": 0.6}
SUMMIT = {"tower_cab", "catwalk", "woodpile", "garden", "cistern", "outhouse", "helispot"}


# ---------------------------------------------------------------------------
class Sim:
    def __init__(self, cfg, save_dir):
        self.cfg = cfg
        self.save_dir = save_dir
        os.makedirs(save_dir, exist_ok=True)
        self.path = os.path.join(save_dir, "world.json")
        self.lock = threading.RLock()
        self.rng = random.Random()
        self.thinking = False
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.s = json.load(f)
            self.s.setdefault("voice", [])
            self.s.setdefault("voice_in", [])
            self.s.setdefault("memories", [])
            self.s.setdefault("thoughts", [])
            self.s.setdefault("self_facts", [])
            self.merge_ledger()
            self.init_fixables()
            self.geometry(self.s["map"])
        else:
            self.s = self.new_state()

    def geometry(self, m):
        self.map = m
        tx, ty = self.map["tower"]
        self.locs = {
            "tower_cab": [tx + 0.5, ty + 0.35], "catwalk": [tx + 0.95, ty + 0.45],
            "woodpile": [tx - 0.55, ty + 1.45], "garden": [tx - 0.4, ty - 0.45],
            "cistern": [tx + 1.6, ty - 0.35], "outhouse": [tx + 1.75, ty + 1.55],
            "helispot": [tx + 0.5, ty + 2.2],
            "lake_shore": self.map["shore"], "ridge": self.map["ridge"],
        }
        lake_mi = math.hypot(self.locs["lake_shore"][0] - tx, self.locs["lake_shore"][1] - ty) * TILE_MI
        self.lake_mi = round(lake_mi * 1.4, 1)  # trail is longer than straight line

    # ----- helpers -----------------------------------------------------
    @property
    def now(self):
        return self.s["minute"]

    def day(self, m=None):
        return int((self.now if m is None else m) // DAY)

    def tod(self):
        return self.now % DAY

    def fields(self):
        p = self.cfg["place"]
        ch = self.cfg["character"]
        f = SafeDict(p)
        f.update(name=ch["name"], short_name=ch["short_name"],
                 other_lookout=self.rng.choice(C.OTHER_LOOKOUTS), lake_mi=self.lake_mi)
        f.update(C.REGULARS)
        f["bird"] = self.rng.choice(C.BIRDS[self.phase_key()])
        return f

    def fmt(self, text):
        return text.format_map(self.fields())

    def sun(self):
        n = doy(self.day())
        length = (12.2 + 3.8 * math.cos(2 * math.pi * (n - 172) / 365)) * 60
        noon = 13 * 60 + 25
        return int(noon - length / 2), int(noon + length / 2)

    def is_dark(self):
        rise, sset = self.sun()
        return not (rise - 20 <= self.tod() <= sset + 25)

    def phase_key(self):
        rise, sset = self.sun()
        t = self.tod()
        if t < rise - 20 or t > sset + 30:
            return "night"
        if t < rise + 150:
            return "dawn"
        if t > sset - 90:
            return "dusk"
        return "day"

    def bearing(self, x, y):
        tx, ty = self.map["tower"]
        dx, dy = x + 0.5 - (tx + 0.5), y + 0.5 - (ty + 0.5)
        az = (math.degrees(math.atan2(dx, -dy)) + 360) % 360
        return round(az), round(math.hypot(dx, dy) * TILE_MI, 1)

    def tile(self, x, y):
        if 0 <= x < W and 0 <= y < H:
            return self.map["tiles"][y][x]
        return " "

    def think_note(self, text, kind="thought"):
        text = str(text or "").strip()
        if not text or text == "(autopilot)":
            return
        a = self.s["agent"]
        doing = a["activity"] + (f": {a['arg']}" if a["arg"] else "")
        self.s["thoughts"].append({"t": self.now, "text": text[:600], "kind": kind, "doing": doing})
        self.s["thoughts"] = self.s["thoughts"][-5000:]

    def add_facts(self, facts):
        have = {f["text"].strip().lower() for f in self.s["self_facts"]}
        for f in (facts or [])[:4] if isinstance(facts, list) else []:
            t = str(f).strip()
            if 3 < len(t) <= 240 and t.lower() not in have:
                self.s["self_facts"].append({"day": self.day(), "text": t})
                have.add(t.lower())
        self.s["self_facts"] = self.s["self_facts"][-150:]

    def exp(self, text, kind="flavor"):
        self.s["today"].append({"t": self.now, "text": text, "kind": kind})
        self.s["today"] = self.s["today"][-120:]

    def log(self, text):
        self.s["log"].append({"t": self.now, "text": text})
        self.s["log"] = self.s["log"][-600:]

    def radio(self, who, text, report=None):
        self.s["radio"].append({"t": self.now, "from": who, "text": text, "report": report})
        self.s["radio"] = self.s["radio"][-400:]

    def dispatch_says(self, text, who=None, urgent=False):
        """Dispatch talks to the lookout: shown in the radio and queued for him to hear.
        Routine traffic waits until he next stops to think; urgent traffic interrupts him."""
        self.radio(who or "Dispatch (relay)", text)
        self.s["inbox"].append({"t": self.now, "from": who or "Dispatch", "text": text, "urgent": urgent})

    def new_id(self, prefix):
        self.s["ids"][prefix] = self.s["ids"].get(prefix, 0) + 1
        return f"{prefix}{self.s['ids'][prefix]}"

    # ----- state ----------------------------------------------------------
    def new_state(self):
        m = gen_map(self.cfg.get("world_seed", 4127))
        self.geometry(m)
        tx, ty = m["tower"]
        start = 6 * 60 + 30
        s = {
            "version": 1, "minute": start, "map": m, "ids": {},
            "weather_day": None, "dryness": 0.55, "speed": self.cfg.get("default_speed", 1),
            "agent": {
                "pos": [tx + 0.5, ty + 0.35], "loc": "tower_cab", "activity": "think", "arg": "",
                "start": start, "end": start, "travel_from": None,
                "thought": "First morning. Everything is quiet and very big.",
                "say": "", "say_t": -9999, "asleep": False, "wake_at": None, "diary_day": -1,
                "needs": {"energy": 85, "hunger": 35, "boredom": 15, "lonely": 20},
                "last_scan": start, "last_wx": -9999,
            },
            "interrupts": [], "inbox": [], "voice": [], "voice_in": [], "memories": [], "thoughts": [], "self_facts": [],
            "events": [], "history": [], "last_tier": {},
            "sightings": [], "reports": [], "scheduled": [],
            "fires": {}, "burned": [], "strikes": [],
            "today": [], "diary": [], "log": [], "radio": [],
            "ledger": {}, "beliefs": [], "projects": [],
            "inventory": [self.fmt_static(i) for i in C.START_INVENTORY],
            "stats": {"llm_calls": 0, "offline_calls": 0},
        }
        self.s = s
        self.map = m
        self.init_fixables()
        self.new_weather()
        self.exp(self.fmt(f"Arrived at {{lookout}} yesterday with {C.PACKER} and two mules. "
                          "Unpacked. The cab smells of old wood and mice."), "event")
        self.log("Lookout staffed for the season.")
        self.radio(self.cfg["place"]["dispatch"],
                   self.fmt("{lookout}, {dispatch}. Welcome aboard. Morning check-in at 0800, "
                            "report any smokes. Have a good season."))
        return s

    def init_fixables(self):
        fx = self.s.setdefault("fixables", {})
        seen = " ".join(x["text"] for x in self.s.get("today", []))
        for k, v in C.FIXABLES.items():
            if k not in fx:
                fx[k] = {"broken": True, "noticed": v["line"] in seen, "attempts": 0, "fixed_day": None, "times_fixed": 0}

    def match_fixable(self, text):
        t = str(text or "").lower()
        if not t:
            return None
        best = None
        for k, v in C.FIXABLES.items():
            for kw in v["keys"]:
                if kw in t and (best is None or len(kw) > best[1]):
                    best = (k, len(kw))
        return best[0] if best else None

    def finish_activity(self):
        """Called when an activity ends: resolves repairs."""
        a = self.s["agent"]
        key = a.get("fix_key")
        if not key or a["activity"] not in ("repair", "maintain"):
            return
        a["fix_key"] = None
        f = self.s["fixables"][key]
        spec = C.FIXABLES[key]
        if not f["broken"]:
            return
        spent = max(0, min(self.now, a["end"]) - a["start"])
        f["attempts"] += 1
        p = clamp((0.25 + spent * 0.005 + f["attempts"] * 0.12) / spec["difficulty"], 0.05, 0.92)
        if self.rng.random() < p:
            f["broken"], f["fixed_day"] = False, self.day()
            f["times_fixed"] += 1
            f["attempts"] = 0
            self.exp(spec["fixed"], "project")
            self.log(f"Repaired {spec['name']}.")
        else:
            self.exp(spec["failed"], "project")

    def fixables_text(self):
        fx = self.s["fixables"]
        broken = [f"- {C.FIXABLES[k]['name']}" + (f" (you've tried {v['attempts']} time{'s' if v['attempts'] != 1 else ''})" if v["attempts"] else "")
                  for k, v in fx.items() if v["broken"] and v["noticed"]]
        fixed = [f"{C.FIXABLES[k]['name']} (day {v['fixed_day'] + 1})" for k, v in fx.items() if not v["broken"]]
        out = "\n".join(broken) or "- nothing you've noticed"
        if fixed:
            out += "\nFixed so far: " + ", ".join(fixed)
        return out

    def fmt_static(self, text):
        return text.format_map(SafeDict(self.cfg["place"]))

    def save(self):
        with self.lock:
            data = json.dumps(self.s, ensure_ascii=False)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, self.path)

    # ----- weather ----------------------------------------------------------
    def new_weather(self):
        d = self.day()
        r = self.rng
        n = doy(d)
        m = cal(d).month
        storm_p = {6: 0.15, 7: 0.32, 8: 0.32, 9: 0.12}.get(m, 0.0)
        if m in (12, 1, 2):
            storm_p = 0.10  # blizzards
        rain_p = {6: 0.22, 7: 0.07, 8: 0.07, 9: 0.18, 10: 0.25, 11: 0.35, 12: 0.40, 1: 0.40,
                  2: 0.38, 3: 0.38, 4: 0.33, 5: 0.30}[m]
        high = 47 + 28 * math.cos(2 * math.pi * (n - 200) / 365) + r.uniform(-7, 7)
        roll = r.random()
        if roll < rain_p:
            kind = "rain"
        elif roll < rain_p + storm_p:
            kind = "storm"
        else:
            kind = r.choice(["clear", "clear", "partly cloudy", "hazy" if m in (8, 9) else "clear"])
        w = {
            "day": d, "kind": kind, "high": round(high), "low": round(high - r.uniform(22, 30)),
            "wind_from": r.choice([225, 225, 250, 270, 270, 300, 200, 180]),
            "wind_mph": r.randint(4, 14),
            "fog": r.random() < (0.45 if self.s.get("last_rain") else 0.15),
            "storm_start": r.randint(13, 16), "storm_len": r.randint(2, 4),
            "rain_hours": sorted(r.sample(range(7, 20), r.randint(3, 8))) if kind == "rain" else [],
        }
        if not fire_season(d):
            self.s["dryness"] = 0.3 if snow_cover(d) else max(0.3, self.s["dryness"] - 0.05)
        elif kind == "rain":
            self.s["dryness"] = max(0.3, self.s["dryness"] - 0.3)
        elif kind == "storm":
            self.s["dryness"] = max(0.3, self.s["dryness"] - 0.02)
        else:
            self.s["dryness"] = min(1.6, self.s["dryness"] + 0.05 + 0.025 * max(0, high - 75) / 5)
        self.s["last_rain"] = kind == "rain"
        self.s["weather_day"] = w

    def wx(self):
        w = self.s["weather_day"]
        h = self.tod() // 60
        temp = w["low"] + (w["high"] - w["low"]) * max(0.0, math.sin(math.pi * (h - 5) / 15))
        storm = w["kind"] == "storm" and w["storm_start"] <= h < w["storm_start"] + w["storm_len"]
        if storm and not fire_season(self.day()) and cal(self.day()).month not in (12, 1, 2):
            storm = False
        raining = (w["kind"] == "rain" and h in w["rain_hours"]) or (storm and (h + w["day"]) % 2 == 0)
        fog = w["fog"] and h < 10
        wind = w["wind_mph"] + (12 if storm else 0) + (3 if 12 <= h <= 18 else 0)
        rh = round(clamp(70 - (temp - 50) * 1.2 + (25 if raining else 0) - self.s["dryness"] * 10, 8, 100))
        cold = temp < 34
        if storm and cold:
            desc = "blizzard"
            raining = True
        elif storm:
            desc = "thunderstorm" + (", raining" if raining else ", dry lightning")
        elif raining:
            desc = "snow" if cold else "rain"
        elif fog:
            desc = "fog in the valleys"
        elif w["kind"] == "storm" and h < w["storm_start"]:
            desc = "building cumulus"
        else:
            desc = {"rain": "overcast", "storm": "partly cloudy"}.get(w["kind"], w["kind"])
        return {"desc": desc, "temp": round(temp), "wind_from": w["wind_from"], "wind_mph": wind,
                "rh": rh, "raining": raining and not cold, "snowing": raining and cold, "storm": storm,
                "fog": fog, "danger": self.danger()}

    def danger(self):
        d = self.s["dryness"]
        if not fire_season(self.day()):
            return "Off season"
        return ("Low" if d < 0.5 else "Moderate" if d < 0.8 else "High" if d < 1.1
                else "Very High" if d < 1.4 else "Extreme")

    def compass(self, deg):
        return COMPASS[int(((deg % 360) + 22.5) // 45) % 8]

    def season_turn(self):
        for fk, f in self.s["fixables"].items():
            if not f["broken"] and self.rng.random() < 0.012 * C.FIXABLES[fk]["difficulty"]:
                f["broken"] = True  # wear and tear; he'll notice when it acts up
                f["relapse"] = True
        d = cal(self.day())
        call, disp = self.cfg["place"]["lookout"], self.cfg["place"]["dispatch"]
        first = self.day() < 365
        if d.month == 9 and d.day == 26:
            if first:
                msg = (f"{call}, {disp}. Fire season's officially over. The district wants to keep the tower staffed "
                       f"this winter as caretaker: snow reports, keeping an eye on things. If you're up for it, "
                       f"{C.PACKER} will keep bringing supplies, by snowmobile once the road closes. We're counting on you.")
            else:
                msg = f"{call}, {disp}. Fire season's closed. Another winter up there. Stay warm."
            self.dispatch_says(msg, self.cfg["place"]["dispatch"])
        elif d.month == 6 and d.day == 1:
            self.dispatch_says(f"{call}, {disp}. Fire season starts today. Morning check-ins at 0800 again.",
                               self.cfg["place"]["dispatch"])
        elif doy(self.day()) == 312:
            self.exp("First real snow. It's sticking.", "weather")
        elif doy(self.day()) == 119:
            self.exp("The snow is going. Bare ground on the south side of the summit.", "weather")
            burned = self.s["burned"]
            self.rng.shuffle(burned)
            self.s["burned"] = burned[: int(len(burned) * 0.6)]  # forest slowly grows back

    # ----- events -----------------------------------------------------------
    def pick_spot(self, where):
        tx, ty = self.map["tower"]
        pool = self.map["spots"].get(where) or self.map["spots"]["forest"]
        good = [p for p in pool if 3 <= math.hypot(p[0] - tx, p[1] - ty) <= 26]
        return self.rng.choice(good or pool)

    def storyteller(self):
        h = self.tod() // 60
        wxd = self.s["weather_day"]
        d = self.day()
        for tm in self.rng.sample(C.EVENTS, len(C.EVENTS)):
            win = WINDOWS[tm["when"]]
            if h not in win:
                continue
            if tm.get("requires") == "fog" and not wxd["fog"]:
                continue
            if tm.get("seasons") and season_of(d) not in tm["seasons"]:
                continue
            if any(e["key"] == tm["key"] for e in self.s["events"]):
                continue
            last = self.s["last_tier"].get(tm["tier"], -10 ** 9)
            if d - self.day(last) < C.TIER_COOLDOWN_DAYS[tm["tier"]] and tm["tier"] != "ambient":
                continue
            p = tm["per_season"] / (SEASON_DAYS * len(win))
            if self.rng.random() < p:
                self.spawn(tm)
                if tm["tier"] != "ambient":
                    return

    def spawn(self, tm, x=None, y=None, cues=None, planted=False):
        r = self.rng
        e = {"id": self.new_id("E"), "key": tm["key"], "tier": tm["tier"], "where": tm["where"],
             "start": self.now, "revealed": 0, "sighting": None, "resolved": False,
             "acted": None, "planted": planted, "cues": [self.fmt(c) for c in (cues or tm["cues"])]}
        if tm["where"] == "summit":
            tx, ty = self.map["tower"]
            e["x"], e["y"] = tx, ty
            if tm.get("appear") == "morning":
                nxt = (self.day() + (1 if self.tod() >= 6 * 60 else 0)) * DAY + 6 * 60
                e["appear"] = [nxt]
                e["end"] = nxt + DAY
            elif tm.get("appear") == "daily16":
                first = self.day() + (0 if self.tod() < 15 * 60 else 1)
                e["appear"] = [(first + i) * DAY + 16 * 60 for i in range(len(e["cues"]))]
                e["absent_at"] = (first + len(e["cues"])) * DAY + 16 * 60 + 30
                e["end"] = e["absent_at"] + 10
            else:
                e["appear"] = [self.now + r.randint(10, 120)]
                e["end"] = e["appear"][0] + 180
            if e["cues"] == ["@VISITOR"]:
                e["cues"] = [self.fmt(r.choice(C.VISITOR_LINES))]
        elif tm["where"] == "radio":
            e["x"], e["y"] = self.map["tower"]
            e["end"] = self.now + r.randint(*tm["hours"]) * 60
        else:
            if x is None:
                x, y = self.pick_spot(tm["where"])
            e["x"], e["y"] = x, y
            lo, hi = tm.get("hours", (2, 4))
            e["end"] = self.now + int(r.uniform(lo, hi) * 60)
        self.s["events"].append(e)
        if tm["tier"] != "ambient":
            self.s["last_tier"][tm["tier"]] = self.now
        return e

    def tmpl(self, key):
        for t in C.EVENTS:
            if t["key"] == key:
                return t
        return {"key": key, "tier": "planted", "vis": 0.45, "night_vis": 0.0, "reportable": False,
                "resolution": "Ranger checked. Nothing there to explain it.", "auto_action": "note"}

    def plant(self, x, y, text):
        tx, ty = self.map["tower"]
        if math.hypot(x - tx, y - ty) <= 2.5:
            tm = {"key": "planted", "tier": "planted", "where": "summit", "cues": [text]}
        else:
            tm = {"key": "planted", "tier": "planted", "where": "forest", "hours": (30, 48), "cues": [text]}
        e = self.spawn(tm, x, y, cues=[text], planted=True)
        e["vis"] = 0.45
        return e

    # ----- fire ---------------------------------------------------------------
    def ignite(self, x, y, delay):
        fid = self.new_id("F")
        self.s["fires"][fid] = {"cells": {f"{x},{y}": {"st": "smolder", "left": 0}},
                                "x": x, "y": y, "start": self.now, "visible_at": self.now + delay,
                                "suppress_at": None, "event": None, "reported": False, "pilot": False,
                                "max": 1}
        return fid

    def fire_step(self):
        wx = self.wx()
        dry = self.s["dryness"]
        burned = set(self.s["burned"])
        snowy = snow_cover(self.day())
        for fid, f in list(self.s["fires"].items()):
            cells = f["cells"]
            new = {}
            burning = 0
            for key, c in list(cells.items()):
                x, y = map(int, key.split(","))
                if c["st"] == "smolder":
                    if self.now >= f["visible_at"]:
                        c["st"], c["left"] = "burn", self.rng.randint(60, 200)
                    continue
                burning += 1
                c["left"] -= 10
                out_p = (0.25 if f["suppress_at"] and self.now >= f["suppress_at"] else 0) + \
                        (0.12 if wx["raining"] or wx["snowing"] else 0) + (0.5 if snowy else 0)
                if c["left"] <= 0 or self.rng.random() < out_p:
                    del cells[key]
                    self.s["burned"].append(key)
                    burned.add(key)
                    continue
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nx, ny = x + dx, y + dy
                        nk = f"{nx},{ny}"
                        if (dx or dy) and self.tile(nx, ny) in BURNABLE and nk not in cells \
                                and nk not in new and nk not in burned:
                            to_deg = (wx["wind_from"] + 180) % 360
                            ang = math.degrees(math.atan2(dx, -dy))
                            align = math.cos(math.radians(ang - to_deg))
                            base = {"F": 0.016, "L": 0.012, "M": 0.009}[self.tile(nx, ny)]
                            p = base * dry * (1 + align * wx["wind_mph"] / 9)
                            if wx["raining"]:
                                p *= 0.15
                            if f["suppress_at"] and self.now >= f["suppress_at"]:
                                p *= 0.15
                            if self.rng.random() < max(0, p):
                                new[nk] = {"st": "burn", "left": self.rng.randint(60, 200)}
            cells.update(new)
            f["max"] = max(f["max"], len(cells))
            if len(cells) > 40 and not f["suppress_at"]:
                f["suppress_at"] = self.now + 60
            if burning >= 5 and not f["reported"] and not f["pilot"] and self.now - f["visible_at"] > 200:
                f["pilot"] = True
                f["suppress_at"] = f["suppress_at"] or self.now + 180
                az, mi = self.bearing(f["x"], f["y"])
                self.dispatch_says(self.fmt(f"{{lookout}}, {{dispatch}}. A pilot is reporting smoke at about "
                                            f"azimuth {az:03d} from you, {mi:.0f} miles. Do you have anything?"),
                                   urgent=True)
            if not cells:
                del self.s["fires"][fid]
                ev = self.find_event(f["event"])
                if ev:
                    ev["end"] = self.now
                if f["visible_at"] <= self.now:
                    az, mi = self.bearing(f["x"], f["y"])
                    msg = (f"Your fire at azimuth {az:03d} is out. Nice catch." if f["reported"]
                           else f"FYI, the fire at azimuth {az:03d} from you is out.")
                    self.dispatch_says(self.fmt(msg))

    def fire_cue(self, f):
        n = sum(1 for c in f["cells"].values() if c["st"] == "burn")
        to = self.compass((self.wx()["wind_from"] + 180) % 360)
        if self.is_dark():
            if n <= 2:
                return "a small orange glow in the timber"
            if n <= 8:
                return "an orange glow in the timber, flickering"
            return f"a line of fire visible in the dark, spreading {to}"
        if n <= 2:
            return "a thin wisp of blue-gray smoke rising from the timber"
        if n <= 6:
            return f"a steady column of smoke, drifting {to}"
        if n <= 15:
            return "a thick column of smoke with a dark base; it's growing"
        return f"a large smoke column with flame visible at the base, spreading {to}"

    def sync_fire_events(self):
        """Every visible fire gets an event so it can be sighted."""
        for fid, f in self.s["fires"].items():
            burning = sum(1 for c in f["cells"].values() if c["st"] == "burn")
            if burning and not f["event"]:
                e = {"id": self.new_id("E"), "key": "fire", "tier": "fire", "where": "forest",
                     "x": f["x"], "y": f["y"], "start": self.now, "end": self.now + 99 * DAY,
                     "revealed": 0, "sighting": None, "resolved": False, "acted": None,
                     "planted": False, "cues": [], "fire": fid}
                self.s["events"].append(e)
                f["event"] = e["id"]

    def lightning(self):
        tx, ty = self.map["tower"]
        n = self.rng.randint(2, 8)
        hits = []
        for _ in range(n):
            x = clamp(tx + self.rng.randint(-28, 28), 0, W - 1)
            y = clamp(ty + self.rng.randint(-18, 18), 0, H - 1)
            hits.append((x, y))
            self.s["strikes"].append({"t": self.now + self.rng.randint(0, 59), "x": x, "y": y})
            if self.tile(x, y) in BURNABLE and self.rng.random() < 0.025 * self.s["dryness"] and fire_season(self.day()):
                self.ignite(x, y, self.rng.randint(30, 36 * 60))
        self.s["strikes"] = [s for s in self.s["strikes"] if s["t"] > self.now - 120]
        a = self.s["agent"]
        if not a["asleep"] and a["loc"] in SUMMIT:
            near = min(hits, key=lambda p: math.hypot(p[0] - tx, p[1] - ty))
            az, mi = self.bearing(*near)
            self.exp(f"Lightning: {n} strikes this hour. Closest at azimuth {az:03d}, about {mi} mi.", "weather")
            self.log(f"Lightning, {n} strikes. Closest az {az:03d}, {mi} mi.")

    # ----- detection ----------------------------------------------------------
    def find_event(self, eid):
        for e in self.s["events"]:
            if e["id"] == eid:
                return e
        return None

    def find_sighting(self, sid):
        for s in self.s["sightings"]:
            if s["id"].lower() == str(sid).lower().strip():
                return s
        return None

    def event_vis(self, e):
        if e["key"] == "fire":
            f = self.s["fires"].get(e["fire"])
            if not f:
                return 0, 0
            n = sum(1 for c in f["cells"].values() if c["st"] == "burn")
            if not n:
                return 0, 0
            return min(0.9, 0.22 + 0.07 * n), min(0.9, 0.15 + 0.08 * n)
        t = self.tmpl(e["key"])
        return e.get("vis", t.get("vis", 0.3)), t.get("night_vis", 0.0)

    def sweep(self):
        a = self.s["agent"]
        if a["asleep"]:
            return
        act = ACTS.get(a["activity"], {})
        factor = LOC_SEE.get(a["loc"], 0.3) * act.get("attn", 0.4)
        if a["activity"] == "nap":
            factor = 0
        w = self.wx()
        wf = 0.35 if w["fog"] else 0.45 if w["raining"] else 0.55 if w["storm"] else 0.8 if "hazy" in w["desc"] else 1.0
        dark = self.is_dark()
        for e in self.s["events"]:
            if e["where"] in ("summit", "radio"):
                continue
            vis, nvis = self.event_vis(e)
            v = nvis if dark else vis
            if v <= 0:
                continue
            az, mi = self.bearing(e["x"], e["y"])
            distf = clamp(1.15 - mi / 18, 0.15, 1)
            sid = e["sighting"]
            if e["key"] == "fire":
                cue = self.fire_cue(self.s["fires"][e["fire"]])
                s = self.find_sighting(sid) if sid else None
                if s is None:
                    if self.rng.random() < v * distf * wf * factor * 0.6:
                        self.new_sighting(e, cue)
                elif s["cues"][-1] != cue and factor > 0.1:
                    s["cues"].append(cue)
                    self.exp(f"{s['id']} changed: {cue}.", "sighting")
                    self.interrupt(f"Sighting {s['id']} has changed: {cue}.")
                continue
            if sid is None:
                if self.rng.random() < v * distf * wf * factor * 0.5:
                    self.new_sighting(e, e["cues"][0])
            else:
                s = self.find_sighting(sid)
                if s and s["status"] == "open" and e["revealed"] < len(e["cues"]) - 1:
                    watching = a["activity"] == "watch_closely" and str(a["arg"]).upper() == s["id"]
                    p = 0.85 if watching else 0.15 * factor
                    if self.rng.random() < p:
                        e["revealed"] += 1
                        cue = e["cues"][e["revealed"]]
                        s["cues"].append(cue)
                        self.exp(f"{s['id']}: {cue}.", "sighting")
                        if not watching:
                            self.interrupt(f"New detail on sighting {s['id']}: {cue}.")

    def new_sighting(self, e, cue):
        az, mi = self.bearing(e["x"], e["y"])
        s = {"id": self.new_id("S"), "event": e["id"], "t": self.now, "az": az, "mi": mi,
             "dir": self.compass(az), "cues": [cue], "status": "open", "report": None}
        self.s["sightings"].append(s)
        e["sighting"] = s["id"]
        self.exp(f"New sighting {s['id']} at azimuth {az:03d}, ~{mi} mi {s['dir']}: {cue}.", "sighting")
        self.log(f"Sighting {s['id']}: az {az:03d}, {mi} mi. {cue}.")
        self.interrupt(f"You spotted something: {s['id']} at azimuth {az:03d}, about {mi} mi {s['dir']}: {cue}.")

    def summit_checks(self):
        a = self.s["agent"]
        if a["asleep"]:
            return
        for e in self.s["events"]:
            if e["where"] == "summit":
                tm = self.tmpl(e["key"])
                for i, t in enumerate(e.get("appear", [])):
                    if t is None or self.now < t:
                        continue
                    visible = a["loc"] in SUMMIT and (a["loc"] != "tower_cab" or tm.get("seen_from_cab"))
                    if not visible:
                        if tm.get("appear") == "daily16" and self.now > t + 45:
                            e["appear"][i] = None   # missed today's visit
                        continue
                    e["appear"][i] = None
                    cue = e["cues"][min(i, len(e["cues"]) - 1)]
                    self.exp(cue, "event")
                    self.log(f"At the lookout: {cue}")
                    if tm.get("social"):
                        a["needs"]["lonely"] = max(0, a["needs"]["lonely"] - tm["social"])
                    self.interrupt(f"Something at the lookout: {cue}")
                if e.get("absent_at") and self.now >= e["absent_at"]:
                    e["absent_at"] = None
                    self.exp(self.fmt(tm.get("absent", "")), "event")
                    self.interrupt(tm.get("absent", ""))
            elif e["where"] == "radio" and not e.get("heard_all"):
                if a["loc"] != "tower_cab" or a["activity"] == "nap":
                    continue
                p = 1.0 if a["activity"] == "listen_radio" else 0.35
                if self.rng.random() < p:
                    cue = e["cues"][e["revealed"]]
                    self.exp(cue, "event")
                    if e["revealed"] == 0:
                        self.log(f"Radio: {cue}")
                        self.interrupt(f"On the radio: {cue}")
                    e["revealed"] += 1
                    if e["revealed"] >= len(e["cues"]):
                        e["heard_all"] = True

    def end_events(self):
        keep = []
        for e in self.s["events"]:
            if self.now < e["end"]:
                keep.append(e)
                continue
            tm = self.tmpl(e["key"])
            s = self.find_sighting(e["sighting"]) if e["sighting"] else None
            if s and s["status"] == "open":
                s["status"] = "gone"
                self.exp(f"{s['id']}: whatever it was is no longer visible.", "sighting")
            if not e["resolved"] and tm.get("missed") and e["acted"] in (None, "disregard", "watch"):
                self.schedule(self.rng.randint(60, 900), "radio", text=self.fmt(tm["missed"]))
            if not e["resolved"] and tm.get("escalate") and fire_season(self.day()) and self.rng.random() < tm["escalate"]:
                fid = self.ignite(e["x"], e["y"], self.rng.randint(20, 240))
            self.s["history"].append({"id": e["id"], "key": e["key"], "start": e["start"], "end": self.now,
                                      "sighted": bool(e["sighting"]), "acted": e["acted"]})
            self.s["history"] = self.s["history"][-300:]
        self.s["events"] = keep

    # ----- reports & dispatch ------------------------------------------------------
    def schedule(self, delay, kind, **kw):
        self.s["scheduled"].append({"t": self.now + delay, "kind": kind, **kw})

    def run_scheduled(self):
        due = [x for x in self.s["scheduled"] if x["t"] <= self.now]
        if not due:
            return
        self.s["scheduled"] = [x for x in self.s["scheduled"] if x["t"] > self.now]
        for x in due:
            if x["kind"] == "radio":
                self.dispatch_says(x["text"])
            elif x["kind"] == "resolve":
                e = self.find_event(x["event"])
                if e:
                    e["resolved"] = True
                    e["end"] = min(e["end"], self.now + 5)
                s = self.find_sighting(x.get("sighting"))
                if s:
                    s["status"] = "resolved"
                self.dispatch_says(x["text"])

    def report_action(self, rid, action, by_user=True):
        """Dispatch (the user, or auto) acts on a report: crew / ranger / watch / disregard."""
        rep = next((r for r in self.s["reports"] if r["id"] == rid), None)
        if not rep or rep["status"] != "open":
            return False
        rep["status"] = "acted"
        rep["action"] = action
        e = self.find_event(rep["event"]) if rep.get("event") else None
        who = "Dispatch (you)" if by_user else "Dispatch (auto)"
        call = self.cfg["place"]["lookout"]
        if e:
            e["acted"] = action
        if action in ("crew", "ranger"):
            text = (f"{call}, copy your report. Sending an engine crew." if action == "crew"
                    else f"{call}, copy. Sending a ranger to check it out.")
            self.radio(who, text, rid)
            self.s["inbox"].append({"t": self.now, "from": "Dispatch", "text": text})
            if e and e["key"] == "fire":
                f = self.s["fires"].get(e["fire"])
                if f:
                    f["reported"] = True
                    delay = self.rng.randint(90, 200) if action == "crew" else self.rng.randint(150, 300)
                    f["suppress_at"] = min(f["suppress_at"] or 10 ** 9, self.now + delay)
                    self.schedule(delay, "radio", text=f"{call}, the crew is on scene at your smoke, "
                                                          f"azimuth {rep['az']:03d}.")
            elif e:
                tm = self.tmpl(e["key"])
                self.schedule(self.rng.randint(90, 300), "resolve", event=e["id"], sighting=rep.get("sighting"),
                              text=self.fmt(tm.get("resolution", "Ranger checked. Nothing found.")))
            else:
                self.schedule(self.rng.randint(90, 240), "radio",
                              text=f"{call}, ranger checked your report. Didn't find anything.")
        elif action == "watch":
            text = f"{call}, copy. Keep an eye on it and let us know if it changes."
            self.radio(who, text, rid)
            self.s["inbox"].append({"t": self.now, "from": "Dispatch", "text": text})
        elif action == "disregard":
            text = f"{call}, copy. We'll disregard for now."
            self.radio(who, text, rid)
            self.s["inbox"].append({"t": self.now, "from": "Dispatch", "text": text})
        return True

    def auto_dispatch(self):
        if not self.cfg.get("auto_dispatch", True):
            return
        wait = self.cfg.get("auto_dispatch_minutes", 45)
        for r in self.s["reports"]:
            if r["status"] == "open" and self.now - r["t"] >= wait:
                e = self.find_event(r["event"]) if r.get("event") else None
                if e and e["key"] == "fire":
                    act = "crew"
                elif e:
                    tm = self.tmpl(e["key"])
                    act = {"note": "watch"}.get(tm.get("auto_action", "ranger"), tm.get("auto_action", "ranger"))
                else:
                    act = "watch" if r["priority"] == "routine" else "ranger"
                self.report_action(r["id"], act, by_user=False)

    def user_radio(self, text):
        self.radio("Dispatch (you)", text)
        self.s["inbox"].append({"t": self.now, "from": "Dispatch", "text": text, "urgent": True})
        a = self.s["agent"]
        if a["asleep"] and any(w in text.lower() for w in ("urgent", "wake", "emergency")):
            a["wake_at"] = self.now

    def user_voice(self, text):
        a = self.s["agent"]
        dream = bool(a["asleep"])
        self.s["voice"].append({"t": self.now, "from": "voice", "text": text, "dream": dream})
        self.s["voice"] = self.s["voice"][-300:]
        self.s["voice_in"].append({"t": self.now, "text": text, "dream": dream})

    # ----- agent ---------------------------------------------------------------
    def interrupt(self, why):
        if why:
            self.s["interrupts"].append(why)

    def needs_tick(self):
        a = self.s["agent"]
        n = a["needs"]
        act = ACTS.get(a["activity"], {})
        if a["asleep"] or act.get("sleep"):
            n["energy"] = min(100, n["energy"] + 0.2)
            n["hunger"] = min(100, n["hunger"] + 0.03)
        else:
            moving = a.get("travel_from") is not None
            n["energy"] = max(0, n["energy"] - 0.055 * act.get("energy", 1.0) * (1.5 if moving else 1))
            n["hunger"] = min(100, n["hunger"] + 0.07)
            n["boredom"] = clamp(n["boredom"] + act.get("bored", 0.02), 0, 100)
            n["lonely"] = min(100, n["lonely"] + 0.015)

    def move_tick(self):
        a = self.s["agent"]
        tgt = self.locs.get(a["loc"])
        if not tgt:
            return
        x, y = a["pos"]
        d = math.hypot(tgt[0] - x, tgt[1] - y)
        if d < 0.01:
            a["travel_from"] = None
            return
        both_summit = d < 3.5
        speed = 0.12 if both_summit else 1 / 15  # tiles per minute
        if d <= speed:
            a["pos"] = list(tgt)
            a["travel_from"] = None
        else:
            a["pos"] = [x + (tgt[0] - x) / d * speed, y + (tgt[1] - y) / d * speed]

    def flavor_tick(self):
        a = self.s["agent"]
        if a["asleep"] or a.get("travel_from") is not None:
            return
        act = ACTS.get(a["activity"], {})
        key = act.get("flav")
        if self.is_dark() and a["loc"] in ("catwalk", "tower_cab") and a["activity"] != "listen_radio":
            key = "night"
        if not key or self.rng.random() > 0.3:
            return
        if snow_cover(self.day()) and key in ("garden", "lake", "catwalk", "cab", "cistern", "woodpile") \
                and (key in ("garden", "lake") or self.rng.random() < 0.45):
            key = "winter"
        raw = self.rng.choice(C.FLAVOR[key])
        for fk, spec in C.FIXABLES.items():
            if spec["line"] == raw:
                f = self.s["fixables"][fk]
                if not f["broken"]:
                    return
                f["noticed"] = True
                if f.pop("relapse", False):
                    raw = spec["rebroke"]
        line = self.fmt(raw)
        recent = {x["text"] for x in self.s["today"][-25:]}
        if line not in recent:
            self.exp(line)

    def supply_drop(self):
        a = self.s["agent"]
        r = self.rng
        items = []
        if r.random() < 0.7:
            pool = [self.fmt_static(i) for i in C.SUPPLY_EXTRAS]
            pool = [i for i in pool if i not in self.s["inventory"]
                    or i.startswith("a newspaper") or i.startswith("a letter")]
            if pool:
                items.append(r.choice(pool))
        for it in items:
            if not it.startswith("a newspaper") and not it.startswith("a letter"):
                self.s["inventory"].append(it)
        here = a["loc"] in SUMMIT and not a["asleep"]
        line = self.fmt(r.choice(C.DEL_LINES))
        if here:
            how = "on the snowmobile" if snow_cover(self.day()) else "with the mules"
            text = f"{C.PACKER} came up {how}: groceries, propane, mail. {line}"
            a["needs"]["lonely"] = max(0, a["needs"]["lonely"] - 20)
        else:
            text = f"{C.PACKER} came by while you were away and left groceries, propane and mail in the cab."
        if items:
            text += " Also in the box: " + ", ".join(items) + "."
        self.exp(text, "event")
        self.log("Weekly supplies delivered.")
        a["needs"]["hunger"] = max(0, a["needs"]["hunger"] - 5)
        self.interrupt(text)

    # ----- main step --------------------------------------------------------------
    def step_minute(self):
        """Advance one sim minute. Returns True when the lookout needs to decide something."""
        s = self.s
        s["minute"] += 1
        now = s["minute"]
        a = s["agent"]
        if now % DAY == 0:
            self.new_weather()
            self.season_turn()
        self.needs_tick()
        self.move_tick()
        if now % 10 == 0:
            self.fire_step()
            self.sync_fire_events()
        if now % 60 == 0:
            self.storyteller()
            if self.wx()["storm"] and fire_season(self.day()):
                self.lightning()
            if self.day() % 7 == 6 and self.tod() == 10 * 60:
                self.supply_drop()
        if now % 15 == 0:
            self.sweep()
            self.end_events()
        if now % 30 == 0:
            self.flavor_tick()
        if now % 5 == 0:
            self.summit_checks()
            self.auto_dispatch()
        self.run_scheduled()

        # sleep / wake
        if a["asleep"]:
            if now >= a["wake_at"]:
                a["asleep"] = False
                a["activity"], a["arg"] = "think", ""
                a["start"] = a["end"] = now
                self.interrupt("You just woke up. A new day.")
                return True
            return False
        if any(m.get("urgent") for m in s["inbox"]) or s["voice_in"]:
            return True
        if s["interrupts"]:
            return True
        if now >= a["end"] and a.get("travel_from") is None:
            queue = a.get("queue") or []
            if queue and not self.wants_bed():
                nxt = queue.pop(0)
                self.apply_decision({**nxt, "thought": a["thought"]}, keep_queue=True)
                return False
            return True
        if now >= a["end"] + 120:  # stuck travelling
            return True
        return False

    def wants_bed(self):
        a = self.s["agent"]
        t = self.tod()
        return (t >= 21 * 60 + 30 or t < 4 * 60) and (a["needs"]["energy"] < 45 or t >= 23 * 60 or t < 4 * 60)

    # ----- prompts ------------------------------------------------------------------
    def identity(self):
        ch, p = self.cfg["character"], self.cfg["place"]
        traits = "\n".join(f"- {t}" for t in ch["traits"])
        return f"""You are {ch['name']}, {ch['age']}, from {ch['hometown']}.
Temperament:
{traits}
Why you're here: {ch['why_here']}

You are the fire lookout at {p['lookout']} on {p['peak']} in the {p['forest']}, Montana. You live alone in the small glass cab at the top of the tower: a cot, a stove, the Osborne firefinder in the middle, windows on all four sides, a catwalk around the outside. Below are {p['lake']}, {p['creek']}, {p['road']}, {p['campground']} and the {p['trailhead']}. {p['town']} is about {p['town_miles']} miles away. {p['dispatch']} is on the radio. {C.PACKER} brings supplies up with mules once a week.

{"You arrived on June 20 of your first year. After the first fire season ended you stayed on as the winter caretaker, so you live here year-round now. " if self.day() >= 98 else "You arrived on June 20 for the fire season. "}It is now year {year_of(self.day())} here.

You are a real person living this life, not an assistant. You only know what you have experienced here and what is in your memory below. Think and speak as yourself.

How this works:
- Most days are quiet and ordinary. Do not invent drama, and do not treat every small thing as an omen.
- Your likes and dislikes are NOT decided in advance. They form only from things that actually happen to you here. Most things leave no impression at all. Only note a feeling when something genuinely strikes you, and stay consistent with feelings you've already formed unless something changes your mind.
- Your duty: watch for smoke, and for anything else that's wrong out there. Report what matters by radio in short, clear radio style ("{p['dispatch']}, {p['lookout']}." then what, azimuth, distance, details). Do not report routine things like hikers on a trail or elk grazing. If you're unsure, you can keep watching first.
- How the job actually works: a careful sweep of the horizon every 30-60 minutes during the day, plus weather observations morning and afternoon. Between sweeps your time is your own, and you still glance out the windows while you read or write in the cab, so you're never truly off watch. Lookouts have always spent most of their hours reading, writing, carving, thinking or doing nothing at all, and that's expected; nobody wants a lookout who burns out by August. Outside fire season there's even less to watch. How you spend your free time is up to you.
- Sightings have ids like S3. Use them.
- Sometimes you hear a voice in your head that is not your own thought. You don't know what it is or where it comes from. Nobody else hears it. How you treat it is up to you: answer it, question it, test it, ignore it, distrust it. It is not always right. You never mention it on the radio unless you truly decide to.
- Reply with JSON only."""

    def needs_words(self):
        n = self.s["agent"]["needs"]
        out = []
        e = n["energy"]
        out.append("rested" if e > 70 else "a little tired" if e > 45 else "tired" if e > 20 else "exhausted")
        h = n["hunger"]
        out.append("not hungry" if h < 35 else "getting hungry" if h < 60 else "hungry" if h < 80 else "very hungry")
        b = n["boredom"]
        if b > 70:
            out.append("bored out of your mind")
        elif b > 45:
            out.append("restless")
        if n["lonely"] > 70:
            out.append("lonely")
        elif n["lonely"] > 45:
            out.append("a bit lonely")
        return ", ".join(out)

    def ledger_text(self, strong_n=14, weak_n=6):
        items = sorted(self.s["ledger"].items(), key=lambda kv: -abs(kv[1]["score"]))
        strong, weak = [], []
        for k, v in items:
            sc = v["score"]
            why = f" ({v['notes'][-1]})" if v["notes"] else ""
            if sc >= 4:
                strong.append(f"- you've come to love {k}{why}")
            elif sc >= 1.5:
                strong.append(f"- you like {k}{why}")
            elif sc <= -4:
                strong.append(f"- you've come to hate {k}{why}")
            elif sc <= -1.5:
                strong.append(f"- you dislike {k}{why}")
            elif len(weak) < weak_n:
                weak.append(f"- early impression of {k}: {'slightly positive' if sc > 0 else 'slightly negative' if sc < 0 else 'mixed'}{why}")
        out = "\n".join(strong[:strong_n]) or "- nothing yet; you've only just started forming opinions"
        if weak:
            out += "\nForming:\n" + "\n".join(weak)
        if items:
            out += "\n(When a feeling is about one of these, use exactly the same name for it.)"
        return out

    def memory_block(self):
        s = self.s
        beliefs = "\n".join(f"- {b}" for b in s["beliefs"][-20:]) or "- none yet"
        projs = [p for p in s["projects"] if p["status"] == "active"]
        projects = "\n".join(f"- {p['name']} (since day {p['day'] + 1}, {p['sessions']} sessions): "
                             f"{p['notes'][-1] if p['notes'] else ''}" for p in projs) or "- none"
        done = [p["name"] for p in s["projects"] if p["status"] != "active"][-5:]
        if done:
            projects += "\nPast projects: " + ", ".join(done)
        inv = ", ".join(s["inventory"])
        last = s["diary"][-1]["text"][:700] if s["diary"] else "(no entries yet)"
        vh = "\n".join(f"- D{self.day(v['t']) + 1} {hhmm(v['t'])} {'THE VOICE' if v['from'] == 'voice' else 'YOU (in your head)'}"
                       f"{' (in a dream)' if v.get('dream') else ''}: {v['text']}" for v in s["voice"][-12:]) or "- never heard it"
        mem = s["memories"]
        older = mem[:-8]
        # thin out the far past: keep roughly one summary per month beyond the last 8 weeks
        far = [m for i, m in enumerate(older) if i % 4 == 0][-18:]
        lt = "\n".join(f"- D{m['day'] + 1} ({date_str(m['day'])}, year {year_of(m['day'])}): {m['text']}"
                        for m in far + mem[-8:]) or "- you've only just arrived"
        facts = "\n".join(f"- {f['text']}" for f in s["self_facts"]) or "- nothing established yet beyond the basics above"
        return f"""FACTS ABOUT YOUR OWN LIFE AND PAST (you've established these; stay consistent with them):
{facts}

YOUR LONG-TERM MEMORY (your own weekly summaries, oldest first):
{lt}

THE VOICE IN YOUR HEAD, PAST EXCHANGES:
{vh}

WHAT YOU'VE COME TO FEEL ABOUT THINGS HERE:
{self.ledger_text()}

THINGS YOU'VE CONCLUDED OR BELIEVE (from reflection):
{beliefs}

YOUR SIDE PROJECTS:
{projects}

YOUR THINGS: {inv}

THINGS AROUND THE LOOKOUT THAT NEED FIXING:
{self.fixables_text()}

LAST DIARY ENTRY:
{last}"""

    def situation(self):
        s, a = self.s, self.s["agent"]
        w = self.wx()
        rise, sset = self.sun()
        sights = [x for x in s["sightings"] if x["status"] == "open"][-6:]
        stext = "\n".join(
            f"- {x['id']}: azimuth {x['az']:03d}, ~{x['mi']} mi {x['dir']}, first seen {hhmm(x['t'])}. "
            f"What you've seen: {' / '.join(x['cues'])}. "
            f"{'Reported at ' + hhmm(next((r['t'] for r in s['reports'] if r['id'] == x['report']), x['t'])) if x['report'] else 'NOT reported.'}"
            for x in sights) or "- none"
        today = "\n".join(f"- {hhmm(x['t'])} {x['text']}" for x in s["today"][-16:]) or "- nothing yet"
        radio = "\n".join(f"- {hhmm(x['t'])} {x['from']}: {x['text']}" for x in s["radio"][-6:])
        act = a["activity"] + (f" ({a['arg']})" if a["arg"] else "")
        season = season_of(self.day())
        fs = "fire season" if fire_season(self.day()) else "outside fire season"
        return f"""NOW: Day {self.day() + 1} at the lookout (year {year_of(self.day())}), {date_str(self.day())}, {season}, {fs}. {hhmm(self.now)}. Sunrise {hhmm(rise)}, sunset {hhmm(sset)}. {"It's dark." if self.is_dark() else ''}
Weather: {w['desc']}, {w['temp']}°F, wind from {self.compass(w['wind_from'])} at {w['wind_mph']} mph, humidity {w['rh']}%. Fire danger: {w['danger']}.
You are {LOC_NAMES.get(a['loc'], a['loc'])}. Until now you were doing: {act}.
You feel: {self.needs_words()}.
Last full scan of the horizon: {hhmm(a['last_scan'])}. Last weather observation: {hhmm(a['last_wx']) if a['last_wx'] > 0 else 'not yet'}.

OPEN SIGHTINGS:
{stext}

TODAY SO FAR:
{today}

RECENT RADIO:
{radio}"""

    def decision_prompt(self, reasons, heard, voices=()):
        acts = "\n".join(f"- {k}: {self.fmt(v['desc'])}" for k, v in ACTS.items())
        why = "\n".join(f"- {r}" for r in reasons) or "- You finished what you were doing."
        heard_t = "\n".join(f"- {hhmm(h['t'])} {h['from']}: {h['text']}" for h in heard)
        voice_t = "\n".join(f"- {'(last night, in a dream) ' if v.get('dream') else ''}\"{v['text']}\"" for v in voices)
        voice_key = ('  "voice_reply": "what you answer the voice in your head, or empty string if you choose not to answer",\n'
                     if voices else "")
        return f"""{self.situation()}

{self.memory_block()}

WHY YOU'RE DECIDING NOW:
{why}
{('RADIO YOU JUST HEARD:' + chr(10) + heard_t) if heard else ''}
{('THE VOICE IN YOUR HEAD JUST SAID (not your own thought):' + chr(10) + voice_t) if voices else ''}

ACTIVITIES YOU CAN CHOOSE:
{acts}

Decide what you do next. JSON only, exactly these keys:
{{
  "thought": "one or two sentences of private thought, in your own voice",
  "say": "something you mutter or say out loud, or empty string",
{voice_key}  "activity": "one activity name from the list",
  "target": "book title / item / project name / sighting id, or empty",
  "minutes": 60,
  "then": [ {{"activity": "...", "target": "", "minutes": 60}} ]  (up to 3 things you mean to do after this, in order; you'll be interrupted if anything happens),
  "radio": null or {{"priority": "routine|urgent|emergency", "about": "sighting id or null", "message": "what you say on the radio"}},
  "feelings": [ {{"thing": "specific thing", "feeling": -2 to 2, "why": "short reason"}} ]  (usually empty; at most 2),
  "project": null or {{"name": "short name", "action": "start|work|finish|abandon", "note": "short note"}}
}}"""

    def night_prompt(self):
        s = self.s
        week = ""
        if (self.day() + 1) % 7 == 0:
            week = (',\n  "week_summary": "this is the end of a week: 2-4 sentences remembering what mattered this '
                    'week, for your future self. Facts, people, changes in you. This is how you will remember it '
                    'months from now."')
        today = "\n".join(f"- {hhmm(x['t'])} {x['text']}" for x in s["today"]) or "- nothing much"
        start = self.now - 20 * 60
        radio = "\n".join(f"- {hhmm(x['t'])} {x['from']}: {x['text']}" for x in s["radio"] if x["t"] >= start) or "- quiet"
        return f"""NOW: end of day {self.day() + 1} at the lookout (year {year_of(self.day())}), {date_str(self.day())}, {season_of(self.day())}, {hhmm(self.now)}. You're about to sleep.

EVERYTHING THAT HAPPENED TODAY:
{today}

RADIO TODAY:
{radio}

{self.memory_block()}

Write tonight's private diary entry and reflect on the day. The diary is honest and in your own voice. Not every day needs meaning: if today was dull, the entry can be short and plain. Don't repeat yesterday's entry. Then decide whether anything today genuinely changed how you feel about something specific, or taught you something worth holding onto.

JSON only, exactly these keys:
{{
  "diary": "the entry, 60-300 words",
  "feelings": [ {{"thing": "specific thing", "feeling": -2 to 2, "why": "short reason"}} ]  (0-3 items),
  "new_beliefs": [ "a conclusion, rule of thumb or belief you've reached" ]  (0-2 items; only if real),
  "drop_beliefs": [ "exact text of an old belief you no longer hold" ],
  "new_facts_about_me": [ "a short fact about your own life or past that you stated or revealed today and that isn't already in your facts (people, places, family, history)" ]  (0-4 items; never contradict existing facts),
  "tomorrow": "one line: what you mean to do or think about tomorrow"{week}
}}"""

    def chat_prompt(self, heard, voices):
        lines = []
        for h in heard:
            lines.append(f"- ON THE RADIO, {h['from']}: {h['text']}")
        for v in voices:
            lines.append(f"- THE VOICE IN YOUR HEAD{' (last night, in a dream)' if v.get('dream') else ''}: \"{v['text']}\"")
        a = self.s["agent"]
        act = a["activity"] + (f" ({a['arg']})" if a["arg"] else "")
        voice_rule = ("; replies to the voice happen silently inside your head and can be as honest as you like."
                      if voices else ". The voice in your head has not spoken this time.")
        voice_key = ('\n  "voice_reply": "what you answer the voice in your head, or empty string",' if voices else "")
        return f"""{self.situation()}

{self.memory_block()}

SOMEONE IS TALKING TO YOU RIGHT NOW (you're in the middle of: {act}):
{chr(10).join(lines)}

Answer naturally, as yourself. Answer on the channel they used: radio replies are spoken on an open channel (keep them radio-style and fairly short){voice_rule} You can also choose not to answer. You keep doing what you were doing, unless what you heard makes you want to act now. If you tell someone you'll do something now (check something out, look at a bearing, go somewhere), set act_now to true, because otherwise you won't actually do it.

JSON only, exactly these keys:
{{
  "radio_reply": "what you say back on the radio, or empty string",{voice_key}
  "thought": "a private thought about it, one or two sentences",
  "feelings": [ {{"thing": "specific thing", "feeling": -2 to 2, "why": "short reason"}} ]  (usually empty),
  "new_facts_about_me": [ "a short fact about your own life or past that you just stated for the first time, e.g. a friend's name, where you grew up, your family" ]  (usually empty; never contradict the facts you already have),
  "act_now": false
}}"""

    def apply_chat(self, d, had_voice=True):
        if not d:
            return
        if not had_voice:
            d = {**d, "voice_reply": ""}
        a = self.s["agent"]
        name = self.cfg["character"]["short_name"]
        rr = str(d.get("radio_reply") or "").strip()
        if rr:
            self.radio(name, rr[:600])
            a["needs"]["lonely"] = max(0, a["needs"]["lonely"] - 6)
        vr = str(d.get("voice_reply") or "").strip()
        if vr:
            self.s["voice"].append({"t": self.now, "from": "julian", "text": vr[:800]})
            self.exp(f"(to the voice) {vr[:200]}", "voice")
            a["needs"]["lonely"] = max(0, a["needs"]["lonely"] - 3)
        th = str(d.get("thought") or "").strip()
        if th:
            a["thought"] = th[:400]
            self.think_note(th, "reaction")
        self.apply_feelings(d.get("feelings"))
        self.add_facts(d.get("new_facts_about_me"))
        if d.get("act_now") in (True, "true", "yes"):
            self.interrupt("You decided to act on what you just heard.")

    # ----- applying LLM output ------------------------------------------------------------
    def apply_feelings(self, feelings):
        for f in (feelings or [])[:3]:
            try:
                thing = str(f.get("thing", "")).strip().lower()
                if thing.startswith("the "):
                    thing = thing[4:]
                if not thing or len(thing) > 80:
                    continue
                val = clamp(float(f.get("feeling", 0)), -2, 2)
                thing = self.ledger_match(thing) or thing
                ent = self.s["ledger"].setdefault(thing, {"score": 0.0, "n": 0, "notes": [], "first": self.day()})
                ent["score"] = round(clamp(ent["score"] + val, -10, 10), 2)
                ent["n"] += 1
                why = str(f.get("why", "")).strip()
                if why:
                    ent["notes"] = (ent["notes"] + [why[:140]])[-3:]
                led = self.s["ledger"]
                if len(led) > 250:  # forget the faintest impressions
                    for k in sorted(led, key=lambda k: (abs(led[k]["score"]), led[k]["n"]))[:len(led) - 250]:
                        del led[k]
            except (TypeError, ValueError, AttributeError):
                continue

    STOP = {"the", "a", "an", "of", "from", "my", "in", "to", "on", "at", "for", "and", "with", "this", "that", "his"}

    def ledger_tokens(self, name):
        words = "".join(c if c.isalnum() else " " for c in name.lower()).split()
        out = set()
        for w in words:
            if w in self.STOP or len(w) < 2:
                continue
            if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
                w = w[:-1]
            out.add(w)
        return out

    def ledger_match(self, thing):
        """Find an existing ledger entry that means the same thing, despite different wording."""
        led = self.s["ledger"]
        if thing in led:
            return thing
        tk = self.ledger_tokens(thing)
        if not tk:
            return None
        for k in led:
            kt = self.ledger_tokens(k)
            if not kt:
                continue
            small, big = (tk, kt) if len(tk) <= len(kt) else (kt, tk)
            if tk == kt or (len(small) >= 2 and small <= big):
                return k
        return None

    def merge_ledger(self):
        led = self.s["ledger"]
        for k in sorted(list(led), key=lambda k: led[k].get("first", 0)):
            if k not in led:
                continue
            for other in list(led):
                if other == k or other not in led:
                    continue
                a, b = self.ledger_tokens(k), self.ledger_tokens(other)
                small, big = (a, b) if len(a) <= len(b) else (b, a)
                if a and b and (a == b or (len(small) >= 2 and small <= big)):
                    e, o = led[k], led.pop(other)
                    e["score"] = round(clamp(e["score"] + o["score"], -10, 10), 2)
                    e["n"] += o["n"]
                    e["notes"] = (e["notes"] + o["notes"])[-3:]

    def match_inventory(self, target):
        t = str(target or "").lower().strip()
        if not t:
            return ""
        for it in self.s["inventory"]:
            if t in it.lower() or it.lower().replace("book: ", "").split(" by ")[0] in t:
                return it
        return ""

    def apply_decision(self, d, offline=False, keep_queue=False, had_voice=False):
        s, a = self.s, self.s["agent"]
        if not had_voice and d.get("voice_reply"):
            d = {**d, "voice_reply": ""}
        self.finish_activity()
        if not keep_queue:
            q = []
            for step in (d.get("then") or [])[:3] if isinstance(d.get("then"), list) else []:
                if isinstance(step, dict) and str(step.get("activity", "")).strip().lower() in ACTS:
                    q.append({"activity": str(step["activity"]).strip().lower(),
                              "target": str(step.get("target") or ""), "minutes": step.get("minutes") or 45})
            a["queue"] = q
        act = str(d.get("activity", "think")).strip().lower()
        if act not in ACTS:
            act = "think"
        target = str(d.get("target") or "").strip()
        spec = ACTS[act]
        if act == "read":
            item = self.match_inventory(target)
            books = [i for i in s["inventory"] if i.startswith("book:")]
            target = item if item.startswith("book:") else (self.rng.choice(books) if books else "")
            target = target.replace("book: ", "")
        elif act == "use_item":
            target = self.match_inventory(target) or target
        elif act in ("repair", "maintain"):
            fk = self.match_fixable(target) or (self.match_fixable(d.get("thought")) if act == "repair" else None)
            if fk and s["fixables"][fk]["broken"]:
                a["fix_key"] = fk
                target = C.FIXABLES[fk]["name"]
                act, spec = "repair", dict(ACTS["repair"], loc=C.FIXABLES[fk]["loc"])
            elif act == "repair":
                if fk:
                    self.exp(f"Checked {C.FIXABLES[fk]['name']}. It's holding up fine.", "project")
                act, spec = "maintain", ACTS["maintain"]
                a["fix_key"] = None
        elif act == "watch_closely":
            sg = self.find_sighting(target)
            if not sg:
                act, spec = "scan", ACTS["scan"]
            else:
                target = sg["id"]
        if act not in ("read", "use_item", "project", "watch_closely", "repair"):
            target = ""
        if act != "repair":
            a["fix_key"] = None
        try:
            minutes = int(d.get("minutes") or 45)
        except (TypeError, ValueError):
            minutes = 45
        minutes = clamp(minutes, 20, 240)
        if "dur" in spec:
            minutes = clamp(minutes, *spec["dur"])
        loc = spec["loc"]
        cur = a["pos"]
        dest = self.locs[loc]
        dist = math.hypot(dest[0] - cur[0], dest[1] - cur[1])
        travel = dist / (0.12 if dist < 3.5 else 1 / 15)
        if a["activity"] == "scan":
            a["last_scan"] = self.now
        if a["activity"] == "log_weather":
            w = self.wx()
            self.log(f"Wx: {w['desc']}, {w['temp']}F, wind {self.compass(w['wind_from'])} {w['wind_mph']} mph, "
                     f"RH {w['rh']}%, danger {w['danger']}.")
            a["last_wx"] = self.now
        if a["activity"] == "cook_eat":
            a["needs"]["hunger"] = max(0, a["needs"]["hunger"] - 65)
        a.update(activity=act, arg=target, loc=loc, start=self.now,
                 end=self.now + int(travel) + minutes, travel_from=list(cur) if dist > 0.01 else None)
        a["thought"] = str(d.get("thought") or "")[:400]
        if not keep_queue:
            self.think_note(a["thought"])
        say = str(d.get("say") or "").strip()
        if say:
            a["say"], a["say_t"] = say[:200], self.now
        if act == "scan":
            a["last_scan"] = self.now + int(travel) + minutes
        # radio
        r = d.get("radio")
        if isinstance(r, dict) and str(r.get("message") or "").strip():
            msg = str(r["message"]).strip()[:600]
            pr = str(r.get("priority") or "routine").lower()
            if pr not in ("routine", "urgent", "emergency"):
                pr = "routine"
            sg = self.find_sighting(r.get("about")) if r.get("about") else None
            if sg and not sg["report"]:
                rid = self.new_id("R")
                rep = {"id": rid, "t": self.now, "priority": pr, "text": msg, "sighting": sg["id"],
                       "event": sg["event"], "az": sg["az"], "mi": sg["mi"], "status": "open", "action": None}
                s["reports"].append(rep)
                sg["report"] = rid
                ev = self.find_event(sg["event"])
                if ev and ev["key"] == "fire":
                    f = s["fires"].get(ev["fire"])
                    if f:
                        f["reported"] = True
                self.radio(self.cfg["character"]["short_name"], msg, rid)
                self.log(f"Reported {sg['id']} ({pr}): {msg}")
            else:
                self.radio(self.cfg["character"]["short_name"], msg)
                a["needs"]["lonely"] = max(0, a["needs"]["lonely"] - 6)
        vr = str(d.get("voice_reply") or "").strip()
        if vr:
            self.s["voice"].append({"t": self.now, "from": "julian", "text": vr[:600]})
            self.exp(f"(to the voice) {vr[:200]}", "voice")
        self.apply_feelings(d.get("feelings"))
        p = d.get("project")
        if isinstance(p, dict) and str(p.get("name") or "").strip():
            name = str(p["name"]).strip()[:60]
            action = str(p.get("action") or "work").lower()
            proj = next((x for x in s["projects"] if x["name"].lower() == name.lower()), None)
            if proj is None and action in ("start", "work"):
                proj = {"name": name, "day": self.day(), "status": "active", "sessions": 0, "notes": []}
                s["projects"].append(proj)
                self.exp(f"Started a new project: {name}.", "project")
            if proj:
                if action == "finish":
                    proj["status"] = "finished"
                    self.exp(f"Finished project: {name}.", "project")
                elif action == "abandon":
                    proj["status"] = "abandoned"
                    self.exp(f"Gave up on project: {name}.", "project")
                else:
                    proj["sessions"] += 1
                note = str(p.get("note") or "").strip()
                if note:
                    proj["notes"] = (proj["notes"] + [note[:200]])[-5:]
        if act == "project" and target:
            proj = next((x for x in s["projects"] if x["name"].lower() == target.lower()), None)
            if proj:
                proj["sessions"] += 1
        desc = act.replace("_", " ") + (f": {target}" if target else "")
        self.exp(f"{'(on autopilot) ' if offline else ''}{desc}", "activity")

    def apply_night(self, d):
        s = self.s
        self.finish_activity()
        text = str((d or {}).get("diary") or "").strip()
        if not text:
            text = "(Too tired to write much tonight.)"
        entry = {"day": self.day(), "date": date_str(self.day()), "year": year_of(self.day()), "text": text,
                 "tomorrow": str((d or {}).get("tomorrow") or "")}
        s["diary"].append(entry)
        try:
            with open(os.path.join(self.save_dir, "diary.md"), "a", encoding="utf-8") as f:
                f.write(f"\n## Day {entry['day'] + 1} - {entry['date']}\n\n{text}\n")
        except OSError:
            pass
        if d and str(d.get("week_summary") or "").strip():
            s["memories"].append({"day": self.day(), "text": str(d["week_summary"]).strip()[:700]})
        elif (self.day() + 1) % 7 == 0:
            s["memories"].append({"day": self.day(), "text": text[:400]})
        if d:
            self.add_facts(d.get("new_facts_about_me"))
            self.apply_feelings(d.get("feelings"))
            drops = {str(x).strip().lower() for x in (d.get("drop_beliefs") or [])}
            s["beliefs"] = [b for b in s["beliefs"] if b.strip().lower() not in drops]
            for b in (d.get("new_beliefs") or [])[:2]:
                b = str(b).strip()
                if b and b not in s["beliefs"]:
                    s["beliefs"].append(b[:240])
            s["beliefs"] = s["beliefs"][-25:]
        s["today"] = []
        a = s["agent"]
        a["asleep"] = True
        a["queue"] = []
        a["activity"], a["arg"], a["loc"] = "sleep", "", "tower_cab"
        a["say"] = ""
        wake_day = self.day() + (1 if self.tod() >= 4 * 60 else 0)
        a["wake_at"] = wake_day * DAY + 5 * 60 + 20 + self.rng.randint(0, 50)
        a["start"], a["end"] = self.now, a["wake_at"]

    # ----- offline fallback (no model reachable) -------------------------------------------------
    def offline_decision(self):
        a = self.s["agent"]
        n = a["needs"]
        t = self.tod()
        opens = [x for x in self.s["sightings"] if x["status"] == "open" and not x["report"]]
        if opens and opens[-1]["cues"]:
            sg = opens[-1]
            if len(sg["cues"]) >= 2:
                return {"activity": "scan", "minutes": 30, "thought": "(autopilot)",
                        "radio": {"priority": "urgent", "about": sg["id"],
                                  "message": f"{self.cfg['place']['dispatch']}, {self.cfg['place']['lookout']}. "
                                             f"{sg['cues'][-1]}, azimuth {sg['az']:03d}, about {sg['mi']} miles."}}
            return {"activity": "watch_closely", "target": sg["id"], "minutes": 30, "thought": "(autopilot)"}
        if n["hunger"] > 65:
            return {"activity": "cook_eat", "minutes": 40, "thought": "(autopilot)"}
        if n["energy"] < 20:
            return {"activity": "nap", "minutes": 60, "thought": "(autopilot)"}
        if 8 * 60 <= t <= 8 * 60 + 40 or 14 * 60 <= t <= 14 * 60 + 40:
            return {"activity": "log_weather", "minutes": 15, "thought": "(autopilot)"}
        if self.now - a["last_scan"] > 70 and not self.is_dark():
            return {"activity": "scan", "minutes": 30, "thought": "(autopilot)"}
        hobby = self.rng.choice(["read", "birdwatch", "carve", "chess", "think", "garden", "chop_wood",
                                 "write", "listen_radio", "fetch_water"])
        return {"activity": hobby, "minutes": self.rng.randint(30, 90), "thought": "(autopilot)"}

    # ----- snapshot for the web page --------------------------------------------------------------
    def snapshot(self):
        s = self.s
        a = s["agent"]
        rise, sset = self.sun()
        return {
            "minute": self.now, "day": self.day() + 1, "date": date_str(self.day()), "time": hhmm(self.now),
            "year": year_of(self.day()), "season": season_of(self.day()), "snow": snow_cover(self.day()),
            "fire_season": fire_season(self.day()),
            "tod": self.tod(), "sunrise": rise, "sunset": sset, "dark": self.is_dark(),
            "speed": s["speed"], "thinking": self.thinking,
            "place": self.cfg["place"], "character": self.cfg["character"],
            "wx": self.wx(), "dryness": round(s["dryness"], 2),
            "agent": {**a, "loc_name": LOC_NAMES.get(a["loc"], a["loc"]), "feel": self.needs_words()},
            "locs": self.locs,
            "fires": {k: {"cells": v["cells"], "visible": v["visible_at"] <= self.now} for k, v in s["fires"].items()},
            "burned": s["burned"], "strikes": [x for x in s["strikes"] if x["t"] <= self.now],
            "events": [{"id": e["id"], "key": e["key"], "tier": e["tier"], "x": e["x"], "y": e["y"],
                        "sighting": e["sighting"], "where": e["where"]} for e in s["events"]],
            "sightings": s["sightings"][-30:], "reports": s["reports"][-30:],
            "today": s["today"][-80:], "log": s["log"][-150:], "radio": s["radio"][-150:],
            "ledger": s["ledger"], "beliefs": s["beliefs"], "projects": s["projects"],
            "inventory": s["inventory"], "diary_count": len(s["diary"]), "self_facts": s["self_facts"],
            "voice": s["voice"][-120:], "voice_pending": len(s["voice_in"]),
            "fixables": [{"name": C.FIXABLES[k]["name"], **v} for k, v in s["fixables"].items() if v["noticed"] or not v["broken"]],
            "stats": s["stats"],
        }
