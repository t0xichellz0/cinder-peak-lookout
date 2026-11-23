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


def make_handler(sim, brain):
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
            p = self.path.split("?")[0]
            if p in ("/", "/index.html"):
                with open(os.path.join(web, "index.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html")
            if p == "/api/state":
                with sim.lock:
                    snap = sim.snapshot()
                snap["brain"] = brain.describe()
                return self.send(200, snap)
            if p == "/api/map":
                with sim.lock:
                    elev = sim.map.get("elev")
                    if not elev:  # older saves: rebuild the same elevation from the world seed
                        from sim import gen_map
                        elev = sim.map["elev"] = gen_map(sim.cfg.get("world_seed", 4127))["elev"]
                    return self.send(200, {"tiles": sim.map["tiles"], "tower": sim.map["tower"], "elev": elev})
            if p.endswith(".js") or p.startswith("/vendor/"):
                full = os.path.normpath(os.path.join(web, p.lstrip("/")))
                if full.startswith(os.path.normpath(web)) and os.path.isfile(full):
                    with open(full, "rb") as f:
                        return self.send(200, f.read(), "text/javascript")
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


def main():
    cfg = load_config()
    sim = Sim(cfg, os.path.join(HERE, "save"))
    brain = Brain(cfg)
    stop = threading.Event()
    threading.Thread(target=run_world, args=(sim, brain, stop), daemon=True).start()
    port = int(cfg["port"])
    server = None
    for p in range(port, port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), make_handler(sim, brain))
            port = p
            break
        except OSError:
            continue
    url = f"http://localhost:{port}"
    name = cfg["character"]["short_name"]
    print(f"\n  {cfg['place']['lookout']} is running at {url}")
    print(f"  Brain: {', '.join(brain.describe()['chain']) or 'NONE (autopilot only) - check config.json'}")
    print(f"  Keep this window open while {name} is alive. Close it to pause the world (it's saved).\n")
    if cfg.get("open_browser", True):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        sim.save()
        print("Saved. Goodbye.")


if __name__ == "__main__":
    main()
