CINDER PEAK LOOKOUT
===================

FIRST TIME
1. Open config.json in Notepad (right-click > Open with > Notepad).
2. Replace PASTE-YOUR-GEMINI-KEY-HERE with your key. Keep the quote marks.
3. Save and close.

EVERY TIME
Double-click start.bat. A black window opens (leave it open) and your browser
opens the lookout page. Closing the black window pauses the world; it's saved
and continues next time.

THE PAGE
- Map: Forest view shows the whole area, Summit view zooms in on Julian.
  God view shows everything that's happening, including what he hasn't noticed.
- Click anywhere on the map to leave something there for him to find.
- Speed buttons: pause, 1x (a day takes 24 real minutes), 5x, 20x, 60x.
  Nights skip by fast automatically while he sleeps.
- Today: what he's done and noticed. Diary: what he writes each night.
  Logbook: his official log. Mind: what he's come to like and hate, what he's
  concluded, his side projects and his things.
- Talking to him: anything you say on the radio or as the voice gets a quick
  reply without him dropping what he's doing. If it matters, he acts on it.
- Voice: you are a voice inside his head. He hears you as something that
  isn't his own thought, and decides whether to answer, doubt or ignore you.
  If he's asleep, you reach him in a dream.
- Radio: you are dispatch. Talk to him, and answer his reports with
  Send crew / Send ranger / Keep watching / Disregard. If you don't answer
  within 45 sim-minutes, dispatch answers automatically (auto_dispatch in config).

LONG RUNS
The world runs year-round with real seasons. Fire season is June to September;
after the first one he stays on as winter caretaker (snow, short days, wolves,
avalanches, snowmobiles). Every week he writes himself a summary, and those
summaries are his long-term memory, so he still remembers year one in year three.
A run can go on as long as you like.

CHANGING THINGS (config.json)
- character: his name, age, traits, why he's here.
- place: every place name.
- gemini_models: which models he uses, tried in order. If one is busy he
  switches to the next one automatically.
- groq_api_key: optional backup brain (free key from console.groq.com).
- ollama_model: optional offline brain on your own laptop, e.g. "qwen3:4b".
If no model answers at all, he runs on simple autopilot (marked in Today)
until one comes back.

START OVER
Delete the "save" folder. A new season starts next time. His diary is also
kept as save\diary.md, so copy that first if you want to keep it.

Event content (animals, objects, rare events) lives in content.py.
