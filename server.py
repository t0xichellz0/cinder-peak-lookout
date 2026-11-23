"""
Start the lookout: runs the world in the background and serves the web page.
Run with:  py server.py   (or double-click start.bat)
"""
import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from brain import Brain  # noqa: E402
from sim import Sim      # noqa: E402

DEFAULTS = {
    "gemini_api_key": "", "gemini_models": ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
                                             ],
    "groq_api_key": "", "groq_model": "openai/gpt-oss-120b", "ollama_model": "",
    "min_seconds_between_calls": 4, "timeout_seconds": 30, "temperature": 0.9,
    "port": 8765, "default_speed": 1, "skip_nights": True, "auto_dispatch": True,
    "auto_dispatch_minutes": 45, "world_seed": 4127, "open_browser": True,
}


def load_config():
    path = os.path.join(HERE, "config.json")
    with open(path, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    return cfg


def think(sim, brain):
    with sim.lock:
        a = sim.s["agent"]
        if a["asleep"]:
            return
        reasons = sim.s["interrupts"][:]
        heard = sim.s["inbox"][:]
        voices = sim.s["voice_in"][:]
        sim.s["interrupts"], sim.s["inbox"], sim.s["voice_in"] = [], [], []
        for v in voices:
            sim.exp(f"A voice in his head{' (in a dream)' if v.get('dream') else ''}: \"{v['text']}\"", "voice")
        for h in heard:
            sim.exp(f"Radio, {h['from']}: {h['text']}", "radio")
        urgent = any(k in r.lower() for r in reasons for k in ("sighting", "spotted", "something at"))
        chat = bool(heard or voices) and not reasons and sim.now < a["end"]
        night = sim.wants_bed() and not heard and not voices and not urgent
        system = sim.identity()
        if chat:
            prompt, mode = sim.chat_prompt(heard, voices), "chat"
        elif night:
            prompt, mode = sim.night_prompt(), "night"
        else:
            prompt, mode = sim.decision_prompt(reasons, heard, voices), "decide"
        sim.thinking = {"chat": "Listening", "night": "Writing in his diary", "decide": "Thinking"}[mode]
    data = brain.ask(system, prompt)
    with sim.lock:
        sim.thinking = False
        if mode == "chat":
            sim.apply_chat(data, had_voice=bool(voices))
        elif mode == "night":
            sim.apply_night(data)
        elif data is None:
            sim.apply_decision(sim.offline_decision(), offline=True)
            sim.s["stats"]["offline_calls"] += 1
        else:
            sim.apply_decision(data, had_voice=bool(voices))
        if data is not None:
            sim.s["stats"]["llm_calls"] += 1


def run_world(sim, brain, stop):
    acc, last, last_save = 0.0, time.time(), time.time()
    while not stop.is_set():
        time.sleep(0.1)
        t = time.time()
        dt, last = t - last, t
        with sim.lock:
            speed = float(sim.s["speed"])
            if sim.s["agent"]["asleep"] and speed > 0 and sim.cfg.get("skip_nights", True):
                speed = max(speed, 40)
        if speed <= 0:
            continue
        acc += dt * speed
        steps, acc = int(acc), acc - int(acc)
        need = False
        with sim.lock:
            for _ in range(min(steps, 300)):
                if sim.step_minute():
                    need = True
                    break
        if need:
            acc = 0.0
            try:
                think(sim, brain)
            except Exception as e:  # never let one bad reply kill the world
                print("think() error:", e)
                with sim.lock:
                    sim.thinking = False
                    sim.apply_decision(sim.offline_decision(), offline=True)
        if time.time() - last_save > 30:
            sim.save()
            last_save = time.time()


SAVE_ROOT = os.path.join(HERE, "save")
SLOTS = os.path.join(SAVE_ROOT, "slots")


def slugify(name):
    out = "".join(c.lower() if c.isalnum() else "-" for c in str(name)).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:40] or "lookout"


class Game:
    """Holds the running world. Several saved lives ("slots") can exist; one runs at a time."""

    def __init__(self):
        self.sim = self.brain = self.stop = self.thread = None
        self.slot = None
        os.makedirs(SLOTS, exist_ok=True)
        self.migrate_old_save()

    def migrate_old_save(self):
        """Older versions kept one world in save/. Move it into its own slot, after a backup copy."""
        old = os.path.join(SAVE_ROOT, "world.json")
        if not os.path.exists(old):
            return
        import shutil
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(SAVE_ROOT, f"backup-{stamp}")
        os.makedirs(backup, exist_ok=True)
        for f in ("world.json", "diary.md"):
            if os.path.exists(os.path.join(SAVE_ROOT, f)):
                shutil.copy2(os.path.join(SAVE_ROOT, f), backup)
        try:
            with open(old, encoding="utf-8") as fh:
                name = (json.load(fh).get("character") or load_config()["character"])["name"]
        except Exception:
            name = "lookout"
        dest = os.path.join(SLOTS, slugify(name))
        os.makedirs(dest, exist_ok=True)
        for f in ("world.json", "diary.md"):
            if os.path.exists(os.path.join(SAVE_ROOT, f)):
                shutil.move(os.path.join(SAVE_ROOT, f), os.path.join(dest, f))
        self.set_active(slugify(name))
        print(f"  Moved your existing save into slot '{slugify(name)}' (backup in {backup}).")

    def active(self):
        try:
            with open(os.path.join(SAVE_ROOT, "active.txt"), encoding="utf-8") as f:
                a = f.read().strip()
            if a and os.path.isdir(os.path.join(SLOTS, a)):
                return a
        except OSError:
            pass
        slots = self.list_slots()
        return slots[0]["slot"] if slots else None

    def set_active(self, slot):
        with open(os.path.join(SAVE_ROOT, "active.txt"), "w", encoding="utf-8") as f:
            f.write(slot)

    def list_slots(self):
        out = []
        for d in os.listdir(SLOTS):
            wf = os.path.join(SLOTS, d, "world.json")
            if not os.path.exists(wf):
                continue
            try:
                with open(wf, encoding="utf-8") as fh:
                    w = json.load(fh)
                from sim import date_str, year_of
                day = int(w["minute"] // 1440)
                ch = w.get("character") or {}
                out.append({"slot": d, "name": ch.get("name", d), "pronouns": ch.get("pronouns", "he"),
                            "day": day + 1, "date": date_str(day), "year": year_of(day),
                            "diary": len(w.get("diary", [])), "modified": os.path.getmtime(wf)})
            except Exception:
                continue
        return sorted(out, key=lambda x: -x["modified"])

    def stop_world(self):
        if self.sim:
            self.stop.set()
            if self.thread:
                self.thread.join(timeout=5)
            self.sim.save()

    def load(self, slot, cfg=None):
        self.stop_world()
        cfg = cfg or load_config()
        sim = Sim(cfg, os.path.join(SLOTS, slot))
        if self.brain is None:
            self.brain = Brain(cfg)
            self.brain.log_path = os.path.join(SAVE_ROOT, "brain.log")
        else:
            self.brain.cfg = cfg
        self.stop = threading.Event()
        self.thread = threading.Thread(target=run_world, args=(sim, self.brain, self.stop), daemon=True)
        self.sim, self.slot = sim, slot
        self.thread.start()
        self.set_active(slot)

    def new(self, character, new_map=False):
        cfg = load_config()
        ch = dict(cfg["character"])
        ch.update({k: v for k, v in character.items() if v not in (None, "", [])})
        ch["short_name"] = ch.get("short_name") or str(ch["name"]).split()[0]
        cfg["character"] = ch
        if new_map:
            import random
            cfg["world_seed"] = random.randint(1, 10 ** 6)
        base = slugify(ch["name"])
        slot, i = base, 2
        while os.path.exists(os.path.join(SLOTS, slot)):
            slot, i = f"{base}-{i}", i + 1
        os.makedirs(os.path.join(SLOTS, slot), exist_ok=True)
        self.load(slot, cfg)
        self.sim.save()
        return slot


def make_handler(G):
    web = os.path.join(HERE, "web")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            LAST_REQUEST[0] = time.time()
            p = self.path.split("?")[0]
            if p in ("/", "/index.html"):
                with open(os.path.join(web, "index.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html")
            if p.endswith(".js") or p.startswith("/vendor/"):
                full = os.path.normpath(os.path.join(web, p.lstrip("/")))
                if full.startswith(os.path.normpath(web)) and os.path.isfile(full):
                    with open(full, "rb") as f:
                        return self.send(200, f.read(), "text/javascript")
            if p == "/api/saves":
                return self.send(200, {"active": G.slot, "saves": G.list_slots()})
            sim = G.sim
            if p == "/api/state":
                with sim.lock:
                    snap = sim.snapshot()
                snap["brain"] = G.brain.describe()
                snap["slot"] = G.slot
                return self.send(200, snap)
            if p == "/api/map":
                with sim.lock:
                    elev = sim.map.get("elev")
                    if not elev:  # older saves: rebuild the same elevation from the world seed
                        from sim import gen_map
                        elev = sim.map["elev"] = gen_map(sim.s.get("seed", sim.cfg.get("world_seed", 4127)))["elev"]
                    return self.send(200, {"tiles": sim.map["tiles"], "tower": sim.map["tower"], "elev": elev})
            if p == "/api/thoughts":
                with sim.lock:
                    return self.send(200, sim.s.get("thoughts", []))
            if p == "/api/diary":
                with sim.lock:
                    return self.send(200, sim.s["diary"])
            return self.send(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                body = {}
            p = self.path
            if p == "/api/new":
                ch = body.get("character") or {}
                if not str(ch.get("name", "")).strip():
                    return self.send(400, {"error": "a name is required"})
                traits = ch.get("traits")
                if isinstance(traits, str):
                    ch["traits"] = [t.strip("-• ").strip() for t in traits.splitlines() if t.strip()]
                slot = G.new(ch, new_map=bool(body.get("new_map")))
                return self.send(200, {"ok": True, "slot": slot})
            if p == "/api/load":
                slot = str(body.get("slot", ""))
                if not os.path.isdir(os.path.join(SLOTS, slot)):
                    return self.send(404, {"error": "no such save"})
                G.load(slot)
                return self.send(200, {"ok": True})
            sim = G.sim
            with sim.lock:
                if p == "/api/speed":
                    sim.s["speed"] = max(0, min(120, float(body.get("speed", 1))))
                elif p == "/api/radio":
                    text = str(body.get("text", "")).strip()[:500]
                    if text:
                        sim.user_radio(text)
                elif p == "/api/voice":
                    text = str(body.get("text", "")).strip()[:500]
                    if text:
                        sim.user_voice(text)
                elif p == "/api/report":
                    sim.report_action(str(body.get("id")), str(body.get("action")))
                elif p == "/api/plant":
                    text = str(body.get("text", "")).strip()[:300]
                    if text:
                        sim.plant(int(body["x"]), int(body["y"]), text)
                else:
                    return self.send(404, {"error": "not found"})
            return self.send(200, {"ok": True})

    return H


LAST_REQUEST = [time.time()]


def start(open_browser=False):
    """Start the world and the web server in the background. Returns (url, cfg, shutdown)."""
    cfg = load_config()
    G = Game()
    slot = G.active()
    if slot:
        G.load(slot)
    else:
        G.new(cfg["character"])  # first ever run: start with the character from config.json
    port = int(cfg["port"])
    server = None
    for p in range(port, port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), make_handler(G))
            port = p
            break
        except OSError:
            continue
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://localhost:{port}"
    print(f"  {cfg['place']['lookout']} is running at {url} ({G.slot})")
    print(f"  Brain: {', '.join(G.brain.describe()['chain']) or 'NONE (autopilot only) - check config.json'}")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    def shutdown():
        G.stop_world()
        server.shutdown()
        print("  Saved. Goodbye.")
    return url, cfg, shutdown


def main():
    url, cfg, shutdown = start(open_browser=cfg_open_browser())
    print(f"  Keep this window open while {cfg['character']['short_name']} is alive. Close it to pause the world (it's saved).\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()


def cfg_open_browser():
    try:
        return bool(load_config().get("open_browser", True))
    except Exception:
        return True


if __name__ == "__main__":
    main()
