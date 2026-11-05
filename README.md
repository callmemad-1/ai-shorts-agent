# AI Shorts Agent

Finds today's AI news, fact-checks it, writes a 50–60 second script, records a free
AI voiceover and renders a ready-to-upload **1080×1920 YouTube Short**.

```
research ──► fact-check ──► script + audit ──► voiceover ──► 9:16 MP4
(Claude +     (fresh Claude    (verified claims   (Edge TTS,     (Pillow + ffmpeg,
 web search)   + web search,    only, 2nd model    free; offline   animated template,
               ≥2 independent   checks every       Kokoro          word-synced captions)
               sources/claim)   sentence)          fallback)
```

## How the fact-checking works

1. **Research** (Claude + web search) collects 8 candidate stories from the last 36 h and
   breaks each into atomic claims (one number / name / date per claim).
2. **Fact-check** runs in a *separate* conversation that never sees the researcher's
   reasoning. It searches again on its own and marks each claim
   `verified | corrected | unverified | false`, with evidence URLs and caveats.
3. **Code-level rules** then re-check the verdicts: a claim only counts if it has evidence
   from **at least 2 different domains**, the headline claim must pass, the event must be
   inside the time window, and a story needs ≥3 usable claims. Otherwise the next story is
   tried. If none pass, nothing is published that day (exit code 2).
4. **Script** may only cite usable claim IDs. Every scene lists its `claim_ids`, the code
   rejects any unverified ID, and a second model audits every sentence for unsupported or
   overstated facts (up to 3 revision rounds).
5. Every Short includes an on-screen **"FACT-CHECKED"** caveat beat and a sources line,
   and `script.md` lists each claim with its evidence links for your own review.

> Fact-checking by AI greatly reduces errors but is not perfect. Skim `script.md`
> (takes a minute) before you upload.

## Setup

Requires **Python 3.10+** and **ffmpeg** (`brew install ffmpeg`, `winget install ffmpeg`,
or `sudo apt install ffmpeg`).

```bash
cd ai_shorts_agent
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then paste your Anthropic API key into .env
```

Get an API key at https://console.anthropic.com. Optional offline voice (≈350 MB):

```bash
python setup_offline_voice.py
```

## Run

```bash
python main.py                         # full run -> outputs/YYYY-MM-DD/<topic>/
python main.py --stop-after script     # research + fact-check + script only
python main.py --from-script outputs/<date>/<topic>/script.json   # re-render a script in its folder
python main.py --tts kokoro            # force the offline voice
```

Everything is filed by date, then by topic:

```
outputs/
  2026-11-05/
    research.json, briefing.md, briefing.json    <- that day's research and briefing
    google-will-let-schools-turn-off-its-ai-tools/
      short.mp4, cover.png, script.md, script.json, factcheck.json, voice/
    openais-ai-pipeline-finds-948-linux-kernel-privilege/
      ...
```

Each topic folder contains:

| File | What it is |
|---|---|
| `short.mp4` | The finished 9:16 Short (H.264 + AAC, loudness-normalised to −14 LUFS) |
| `cover.png` | A frame from the hook, usable as a thumbnail |
| `script.md` | Script, every claim with evidence links, YouTube title/description/hashtags |
| `factcheck.json`, `script.json` | Full machine-readable trail for this story |
| `voice/` | Per-scene audio + word timings (`voice/mine/` holds your own recordings) |

## Run it daily on your computer

**macOS / Linux** – `crontab -e` and add (8:00 every day):
```
0 8 * * * cd /path/to/ai_shorts_agent && ./run_daily.sh >> outputs/cron.log 2>&1
```

**Windows** – Task Scheduler → Create Basic Task → Daily → Action "Start a program":
`C:\path\to\ai_shorts_agent\run_daily.bat`

## Pick your own stories (interactive mode)

Double-click **Make Shorts.command**, or run:

```bash
.venv/bin/python main.py --interactive
```

It researches today's AI news and fact-checks **every** candidate story in parallel.
Then it prints a briefing: verified facts, caveats, sources, and the producer's verdict
for each story (★ make / ? maybe / ✗ skip) with the angle it would lead with. You pick
the stories by number, or press Enter for the producer's pick, and each one gets its
own folder (`outputs/<date>/<topic>/`) with the script, fact-check, voiceover, video and cover.
Stories that failed the fact-check can't be picked. The full briefing, with every link,
is saved as `briefing.md`. Running it again the same day reuses the briefing, so you
don't pay for research and fact-checking twice.

## API key or Claude subscription

Before research starts you're asked who does the research, fact-checking and writing:

1. **API key** - pay per use, no usage limits, works for scheduled runs.
2. **Claude subscription** - runs through Claude Code (bundled with the Claude desktop
   app) on your plan, so there's no API bill; it counts against your plan's usage limits.

Enter keeps `llm.backend` from `config.json`; `--brain api` or `--brain subscription`
skips the question. Both use the same prompts and the same code-level checks
(two-source rule, publishable rule, graphic and read-aloud checks). Subscription mode
removes `ANTHROPIC_API_KEY` from Claude Code's environment so it can never bill the API,
and runs at most 2 fact-checks at once to go easy on plan limits. Rendering and voices
never use either.

## Use your own voice

```bash
.venv/bin/python main.py --interactive        # answer "y" to "record your own voice?"
.venv/bin/python main.py --from-script outputs/<date>/<topic>/script.json --tts mine
```

The terminal shows each scene's line. Press Enter, read it, and press Enter to stop.
Then keep it, redo it, or play it back. Takes are cleaned up (rumble filter, silences
trimmed) and Whisper times every word so the captions stay in sync. If what you said
differs a lot from the fact-checked line, you're warned before it's used. Your voice
isn't sped up, so if you read slowly the Short runs a little longer.

Prefer your phone or QuickTime? Save the takes as `voice/mine/scene_01.m4a`,
`scene_02.m4a`, ... in the story's output folder and they're picked up automatically.
Set `tts.engine` to `"mine"` to make it the default, and `tts.mic_device` to choose
another mic (`ffmpeg -f avfoundation -list_devices true -i ""` lists them). The first
recording asks macOS for microphone access, so allow it for Terminal.

## Customise

Everything lives in `config.json`:

- `video.theme` – `"auto"` colours each Short by story type (`theme_by_category`: green
  launches, amber policy, cyan research, red security, purple business) or name one
  theme for every video
- Animated graphics: the script writer can add bar charts, a percentage grid, a
  before/after view or a profile card with a quote to a scene. Code checks that every number,
  name and quote in a graphic is in the fact-checked claims
- `research.aggregator_domains` – sites that still count as fact-check evidence but are
  never shown or cited as sources
- `video.template` – the default look; before each video you're asked which one to use
  (Enter keeps the default, `--template NAME` skips the question):
  `tutorial` (notebook page, cartoon robot teacher, flow diagrams), `terminal` (green CRT
  text typed out), `news` (navy and red, LIVE tag, ticker), `editorial` (magazine page,
  serif headlines), `chat` (the story as phone messages), `classic` (navy gradient)
- `brand` – channel name, @handle, tagline and colours (`colors` for classic,
  `terminal_colors` for terminal)
- Scripts follow a story arc: hook → setup → twist (+ optional stat beats) → stakes →
  reality check → outro. The order is enforced in `agent/scriptwriter.py`
- `tts.edge_voice` – any Edge voice, e.g. `en-US-AndrewNeural`, `en-IN-PrabhatNeural`,
  `en-GB-RyanNeural` (`edge-tts --list-voices` shows all)
- `video.music_file` – drop a royalty-free track at `assets/music.mp3` and it's mixed in
  under the voice
- `models` – which Claude model does each job (fact-check uses the strongest by default)
- `research.window_hours`, `blocked_domains`, `avoid_repeat_days` (no repeat stories)
- Prompts are all in `agent/prompts.py`

## Cost

Per run, up to ~40 web searches ($10 per 1,000 searches, so ≈$0.40) plus model tokens.
Search results add a lot of input tokens, so token cost depends mostly on the models you
pick – check usage in the Anthropic Console after your first run, and switch
`models.factcheck` to `claude-sonnet-5-5` if you want it cheaper. Edge TTS and Kokoro are free.

## Project layout

```
main.py                 pipeline / CLI
agent/llm.py            Claude API + web search (pause_turn handling, citations)
agent/research.py       stage 1
agent/factcheck.py      stage 2 (+ code-level verification rules)
agent/scriptwriter.py   stage 3 (+ validation and audit loop)
agent/tts.py            stage 4 (edge-tts, Kokoro fallback, auto-fits 50-60 s)
agent/render.py         stage 5 (classic 9:16 template, captions, parallel scene render)
agent/template_terminal.py  terminal / hacker template
agent/template_tutorial.py  learning-tutorial template (and the engine the next three share)
agent/template_skins.py     news, editorial and chat templates
agent/prompts.py        all prompts
assets/fonts/           Inter (SIL Open Font License 1.1)
```
