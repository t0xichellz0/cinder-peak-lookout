"""
The lookout's brain: tries each model in order and falls back when one is busy,
rate-limited or retired. Order: Gemini models -> Groq -> local Ollama.
If nothing answers, the simulation runs him on simple autopilot until a model is back.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request


def parse_json(text):
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b > a:
        try:
            v = json.loads(t[a:b + 1])
            return v if isinstance(v, dict) else None
        except json.JSONDecodeError:
            return None
    return None


class Brain:
    def __init__(self, cfg):
        self.cfg = cfg
        self.cool = {}
        self.dead = set()
        self.last_call = 0.0
        self.last_by = {}
        self.counts = {}
        self.status = {"model": None, "ok": None, "error": None, "calls": 0, "fails": 0, "at": None,
                       "tier": "none", "reset_at": None}
        self.gclient = None
        self.types = None
        self.extra = []
        key = (cfg.get("gemini_api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
        if key and "PASTE" in key:
            key = os.environ.get("GEMINI_API_KEY", "")
        if key:
            try:
                from google import genai
                from google.genai import types
                self.types = types
                self.gclient = genai.Client(
                    api_key=key,
                    http_options=types.HttpOptions(
                        timeout=int(cfg.get("timeout_seconds", 30)) * 1000,
                        retry_options=types.HttpRetryOptions(attempts=1)))
            except Exception as e:  # library missing or old
                self.status["error"] = f"Gemini setup failed: {e}"
            if self.gclient and cfg.get("auto_add_gemma", True):
                self.extra = self.find_gemma()
        else:
            self.status["error"] = "No Gemini key yet: paste it into config.json"

    def find_gemma(self):
        """Gemma models on the same free key have far higher daily limits than Flash: a big safety net."""
        try:
            names = [m.name.replace("models/", "") for m in self.gclient.models.list()]
        except Exception:
            return []
        gem = [n for n in names if n.startswith("gemma") and n.endswith("-it")]

        def size(n):
            for part in n.split("-"):
                if part.endswith("b") and part[:-1].isdigit():
                    return int(part[:-1])
            return 0
        gem = [n for n in gem if size(n) >= 12]
        gem.sort(key=lambda n: (-float(n.split("-")[1]) if n.split("-")[1].replace(".", "").isdigit() else 0, -size(n)))
        return gem[:2]

    def chain(self):
        out = []
        if self.gclient:
            out += [("gemini", m) for m in self.cfg.get("gemini_models", [])]
            out += [("gemini", m) for m in self.extra if m not in self.cfg.get("gemini_models", [])]
        gk = (self.cfg.get("groq_api_key") or "").strip()
        if gk and "PASTE" not in gk:
            out.append(("groq", self.cfg.get("groq_model", "openai/gpt-oss-120b")))
        if self.cfg.get("ollama_model"):
            out.append(("ollama", self.cfg["ollama_model"]))
        return out

    def describe(self):
        names = [f"{p}:{m}" for p, m in self.chain()]
        avail = [n for n in names if n not in self.dead and self.cool.get(n, 0) <= time.time()]
        main = [f"gemini:{m}" for m in self.cfg.get("gemini_models", [])] if self.gclient else []
        soon = time.time() + 3600
        # main brain is "out for the day" only if every main model is dead or cooled for a long time
        main_out = bool(main) and all(n in self.dead or self.cool.get(n, 0) > soon for n in main)
        return {**self.status, "chain": names, "available": avail, "main_out": main_out,
                "daily_counts": self.daily_counts()}

    def daily_counts(self):
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 8 * 3600))  # Google's day (Pacific)
        return self.counts.get(day, {})

    def ask(self, system, user):
        gap = float(self.cfg.get("min_seconds_between_calls", 4))
        deadline = time.time() + float(self.cfg.get("max_wait_seconds", 180))
        while True:
            for prov, model in self.chain():
                name = f"{prov}:{model}"
                if name in self.dead or self.cool.get(name, 0) > time.time():
                    continue
                if model.startswith("gemma"):  # small per-minute token budget: space these out
                    wait = self.last_by.get(name, 0) + max(gap, 15) - time.time()
                else:
                    wait = self.last_call + gap - time.time()
                if wait > 0:
                    time.sleep(wait)
                self.last_call = self.last_by[name] = time.time()
                self.status["trying"] = name
                try:
                    text = self._call(prov, model, system, user)
                    data = parse_json(text)
                    if data is None:
                        raise ValueError("model reply was not valid JSON")
                    best = prov == "gemini" and model in self.cfg.get("gemini_models", [])
                    self.status.update(model=name, ok=True, error=None, at=time.strftime("%H:%M:%S"),
                                       tier="full" if best else "backup", trying=None)
                    self.status["calls"] += 1
                    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 8 * 3600))
                    self.counts.setdefault(day, {})
                    self.counts[day][name] = self.counts[day].get(name, 0) + 1
                    return data
                except Exception as e:
                    self.note_failure(name, str(e))
            # Nothing answered. If a model is only briefly busy, wait for it instead of giving up.
            pending = [self.cool.get(f"{p}:{m}", 0) for p, m in self.chain()
                       if f"{p}:{m}" not in self.dead and self.cool.get(f"{p}:{m}", 0) > time.time()]
            soonest = min(pending) if pending else None
            if soonest and soonest <= deadline:
                self.status["trying"] = "waiting for a busy model"
                time.sleep(max(1.0, soonest - time.time() + 1))
                continue
            break
        self.status["tier"] = "autopilot"
        self.status["trying"] = None
        return None

    def note_failure(self, name, msg):
        self.status.update(ok=False, error=f"{name}: {msg[:300]}", at=time.strftime("%H:%M:%S"))
        self.status["fails"] += 1
        low = msg.lower()
        flat = low.replace(" ", "").replace("_", "")
        if "404" in msg or "not_found" in low or "no longer available" in low:
            self.dead.add(name)
        elif "429" in msg or "resource_exhausted" in low or "quota" in low:
            if "perday" in flat or "daily" in flat:
                until = self.next_reset()
                self.cool[name] = until
                self.status["reset_at"] = until
            else:
                self.cool[name] = time.time() + 65  # per-minute limit: back in about a minute
        elif "503" in msg or "unavailable" in low or "overloaded" in low:
            self.cool[name] = time.time() + 600
        elif "timed out" in low or "timeout" in low:
            self.cool[name] = time.time() + 120
        elif "401" in msg or "403" in msg or "api key" in low or "permission" in low:
            self.dead.add(name)
        elif "not valid json" in low:
            self.cool[name] = time.time() + 5
        else:
            self.cool[name] = time.time() + 60

    @staticmethod
    def next_reset():
        """Google resets free daily limits at midnight Pacific time (~08:00 UTC)."""
        now = time.time()
        day = 86400
        t = (now // day) * day + 8 * 3600
        return t if t > now else t + day

    def _call(self, prov, model, system, user):
        temp = float(self.cfg.get("temperature", 0.9))
        if prov == "gemini":
            t = self.types
            if model.startswith("gemma"):  # Gemma: no system prompt or JSON mode, so fold them into the message
                conf = t.GenerateContentConfig(temperature=temp)
                contents = system + "\n\n" + user + "\n\nReply with the JSON object only, no other text."
                r = self.gclient.models.generate_content(model=model, contents=contents, config=conf)
                return r.text
            conf = t.GenerateContentConfig(system_instruction=system, temperature=temp,
                                           response_mime_type="application/json")
            r = self.gclient.models.generate_content(model=model, contents=user, config=conf)
            return r.text
        if prov == "groq":
            body = {"model": model, "temperature": temp,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "response_format": {"type": "json_object"}}
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions", data=json.dumps(body).encode(),
                headers={"Authorization": f"Bearer {self.cfg['groq_api_key'].strip()}",
                         "Content-Type": "application/json", "User-Agent": "lookout/1.0"})
            return self._post(req)["choices"][0]["message"]["content"]
        if prov == "ollama":
            body = {"model": model, "stream": False, "format": "json", "options": {"temperature": temp},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            req = urllib.request.Request("http://localhost:11434/api/chat", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            return self._post(req, timeout=180)["message"]["content"]
        raise ValueError(f"unknown provider {prov}")

    def _post(self, req, timeout=None):
        try:
            with urllib.request.urlopen(req, timeout=timeout or int(self.cfg.get("timeout_seconds", 30))) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{e.code} {e.read().decode('utf-8', 'replace')[:200]}")
