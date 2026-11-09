# AI Shorts Agent

Finds today's AI news, fact-checks it, writes a 50–60 second script, adds free-licence
photos you approve, records a voiceover (AI or your own voice) and renders a
ready-to-upload **1080×1920 YouTube Short**, plus that day's YouTube, Instagram and
LinkedIn posts.

```
research ──► fact-check ──► script + audit ──► photos ──► voiceover ──► 9:16 MP4 ──► social posts
(Claude +     (fresh Claude    (verified claims   (Commons,   (Edge TTS,     (Pillow +     (YouTube,
 web search    + search, source  only; story or    Openverse;  Kokoro, or     ffmpeg, 6     Instagram +
 and fetch)    tiers per claim)  explainer)        you pick)   your own mic)  templates)    carousel, LinkedIn)
```

## How the fact-checking works

1. **Research** (Claude + web search) collects 8 candidate stories from the last 36 h and
   breaks each into atomic claims (one number / name / date per claim).
2. **Fact-check** runs in a *separate* conversation that never sees the researcher's
   reasoning. It searches again on its own and marks each claim
   `verified | corrected | unverified | false`, with evidence URLs and caveats.
3. **Code-level rules** then re-check the verdicts. Every evidence link is given a
   **source tier**: `P` primary (the company, court, regulator or paper the news is
   about), `M` major outlet (Reuters, The Verge, ... listed in `research.source_tiers`),
   `o` other, `x` blocked (aggregators). A claim only counts if it has
   - a primary source plus one independent site, or
   - two different major outlets, or
   - one major outlet plus one other independent site.

   "Other" sites alone are never enough, and the same outlet twice counts once. The
   headline claim (the story's first key claim, matched by ID) must pass, the event must
   be inside the time window, and a story needs ≥3 usable claims. Otherwise the next
   story is tried. If none pass, nothing is published that day (exit code 2).
4. **Script** may only cite usable claim IDs. Every scene lists its `claim_ids`, the code
   rejects any unverified ID, and a second model audits every sentence for unsupported or
   overstated facts (up to 3 revision rounds).
5. Every Short has a **reality-check** scene with the caveats, and each scene shows the
   outlet its facts came from (primary sources and major outlets only, never
   aggregators). `script.md` lists each claim with its evidence links for your own review.

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
python main.py --pattern explainer     # Q&A-style script instead of the story arc
python main.py --language ta           # Tamil voiceover and captions
python main.py --no-images --no-social # skip the photo step / the social posts
```

Other flags: `--template NAME`, `--brain api|subscription`, `--story ID`,
`--from-research FILE`, `--reuse-takes` (re-process your kept recordings), `--out DIR`.

Everything is filed by date, then by topic:

```
outputs/
  2026-11-05/
    research.json, briefing.md, briefing.json    <- that day's research and briefing
    google-will-let-schools-turn-off-its-ai-tools/
      short.mp4, cover.png, script.md, script.json, factcheck.json, voice/, images/, posts/
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
| `images/` | The photos you approved, with their licence and credit in `script.json` |
| `posts/` | `posts.md` (ready to paste + YouTube upload settings), `captions.srt`, `posts.json`, `carousel_01.png`… (4:5 slides) |

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
(source-tier rule, publishable rule, graphic and read-aloud checks). Subscription mode
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

## Script patterns

Before each script you're asked which pattern to use, with a recommendation for the
story's category (Enter keeps `script.pattern`, `--pattern NAME` skips the question):

- `story` – hook → setup → twist (+ up to 2 stat beats) → everyday example → stakes →
  reality check → outro. Best for news, policy and business stories.
- `explainer` – hook question → 2–4 steps → example (+ at most 1 stat) → reality check →
  outro. Best for "how does X work" and tool launches.

**How a script is written so it sounds human, not like cuts and pieces:**

1. The story is first written as **one paragraph**, the way you'd tell a friend, and
   only then split into scenes, so the scenes join back into one flowing story.
2. Code checks for choppy writing: mostly very short sentences, the same phrase in 3+
   scenes, lists of three or more features, more than 2 teases, or a tease ("here's
   the catch") that isn't paid off right after.
3. A second model **listens** to the joined voiceover and says where a 14-year-old
   would get lost or where scenes don't connect. The whole script is rewritten, not
   patched sentence by sentence.
4. Before recording, you see the script **as one paragraph**: press Enter to approve,
   or type a note ("simpler", "less dramatic", "explain the weights part") and it's
   rewritten, re-checked and shown again. Turn this off with `script.approve: false`.
   Scheduled runs skip it.

The order and scene types are enforced in code (`agent/scriptwriter.py`). Lines are
checked for speakability too: at most 18 words per sentence and 3 spoken numbers per
Short, and each scene carries reading cues (`|` pause, `*word*` stress, how to start
and end) shown on the recording sheet in `script.md`.

## Real photos

After the script is written, the agent suggests a photo for about 60% of the scenes
(`images.target_share`) and searches **Wikimedia Commons** and **Openverse** for
safe licences only (CC0, public domain, CC BY; no share-alike, no-derivatives,
non-commercial or news-agency photos). Each option opens in Preview: type `1`, or
`1,3` for a slideshow, or press Enter to skip. Credits go on screen and into the
YouTube description. No AI-generated images of real people are ever made.

## Daily social posts

After the video renders, the agent writes that day's YouTube title, description and
pinned comment, an Instagram caption with hashtags, a LinkedIn post, and a 6-slide 4:5
carousel in the Short's template, all in `posts/`. The same audit as the script
checks them, and code checks lengths, sources, clickbait and aggregator names. The
agent never posts anything: you copy from `posts.md` and post yourself. Redo them
with `python -m agent.social outputs/<date>/<topic>/script.json`.

`posts.md` also has a **YouTube Studio upload settings** table: every field in the
upload form with the value to pick for that Short (audience, the altered-content
answer for the voice used, tags, language, category, remixing, captions, visibility),
and `posts/captions.srt` to upload under Subtitles (same words and timing as the
burned-in captions, in the voiceover's language).

## Your cloned voice (no recording)

If you have the local-voice tool (`tts.clone.path`, default `/Users/kaushik/local-voice`),
pick **My cloned voice** at the voiceover question, or run `--tts clone`. Each scene is
spoken in your voice by IndexTTS 2.5 on this Mac, free and offline, so scheduled runs
can use your voice with nobody at the keyboard. About 2.5 minutes per Short.

- **Mood per scene**: from the scene's tone note ("skeptical" → serious, "warm sign-off"
  → warm), else by scene type (hook excited, stakes and reality check serious, outro warm).
- `||` beats in the reading cues become real pauses; the pronunciation guide is applied,
  so "Hy4" is sent as "high four".
- Every scene is levelled to the same loudness, and Whisper checks it: under 80% of the
  words heard clearly (`tts.clone.min_match`) and it's generated again, up to 2 times
  (`tts.clone.retries`), then flagged for you to listen to.
- Scripts are sized to the clone's own pace (measured on every clone run, kept in
  `voice_profile.json`; `tts.clone.wpm` until the first run). If one still runs long,
  it's sped up gently (at most x1.12, pitch kept).
- Long lines are split into pieces of 20 words at most, because IndexTTS turns long passages
  into noise. A scene that fails the check is retried in smaller pieces; if it still fails you
  choose (retry, AI voice for that scene, keep, stop). Unattended runs keep it and mark the
  Short for review.
- If local-voice is missing or fails, the run falls back to the AI voice.
- `posts.md` reminds you to mark the upload as **altered or synthetic content** on YouTube
  and to turn on Instagram's AI label.

The agent only starts local-voice's own worker (`.venv-index`, `app.index_worker`); it
doesn't change the tool. Re-record the reference clip in local-voice to change the voice.

## Voiceover language (English or Tamil)

Before each video you're asked which language the voiceover is in (Enter keeps
`tts.language`; `--language en|ta` skips the question). The script is always written
and fact-checked in English. For Tamil:

1. Each scene's line is translated into everyday spoken Tamil (names written as they're
   said, numbers as words), then a second model translates it back and compares the
   meaning with the English. Differences are fixed once; anything left is flagged in
   `script.md` and before recording.
2. The voice: your cloned voice through Maya 2 (local-voice; it keeps the delivery of
   your reference recording, no mood control), or the Tamil Edge voice
   (`tts.edge_voice_ta`). Recording yourself is English-only for now.
3. Captions show the Tamil words (timings estimated, since Whisper here only knows
   English). Headlines, numbers and source chips stay in English. Tamil text needs
   `brew install fribidi` (done by `setup_mac.sh`) so the vowel signs are drawn right.

Social posts stay in English.

## Customise

Everything lives in `config.json`:

- `video.theme` – `"auto"` colours each Short by story type (`theme_by_category`: green
  launches, amber policy, cyan research, red security, purple business) or name one
  theme for every video
- Animated graphics: the script writer can add bar charts, a percentage grid, a
  before/after view or a profile card with a quote to a scene. Code checks that every number,
  name and quote in a graphic is in the fact-checked claims
- `research.aggregator_domains` – digest sites (Techmeme, ...) that are blocked from
  the fact-check and never shown or cited as sources
- `research.source_tiers` – the major outlets, extra primary sites and platforms that
  are never primary (Medium, YouTube, ...)
- `video.template` – the default look; before each video you're asked which one to use
  (Enter keeps the default, `--template NAME` skips the question):
  `tutorial` (notebook page, cartoon robot teacher, flow diagrams), `terminal` (green CRT
  text typed out), `news` (navy and red, LIVE tag, ticker), `editorial` (magazine page,
  serif headlines), `chat` (the story as phone messages), `classic` (navy gradient)
- `brand` – channel name, @handle, tagline and colours (`colors` for classic,
  `terminal_colors` for terminal)
- `tts.edge_voice` – any Edge voice, e.g. `en-US-AndrewNeural`, `en-IN-PrabhatNeural`,
  `en-GB-RyanNeural` (`edge-tts --list-voices` shows all)
- `video.music_file` – drop a royalty-free track at `assets/music.mp3` and it's mixed in
  under the voice
- `models` – which Claude model does each job
- `research.window_hours`, `blocked_domains`, `avoid_repeat_days` (no repeat stories)
- Prompts are all in `agent/prompts.py`

## Cost

With the API, each run pays for web searches ($10 per 1,000) plus model tokens. Search
results and fetched pages add a lot of input tokens, so check usage in the Anthropic
Console after your first run. Interactive mode fact-checks up to 8 stories, so it costs
more than a scheduled run. Subscription mode has no API bill. Edge TTS, Kokoro, your own
voice and the photo search are free.

## Project layout

```
main.py                 pipeline / CLI
agent/llm.py            Claude API + web search (pause_turn handling, citations)
agent/research.py       stage 1
agent/factcheck.py      stage 2 (+ code-level verification rules)
agent/scriptwriter.py   stage 3 (+ validation and audit loop)
agent/images.py         free-licence photo search and picker
agent/tts.py            stage 4 (edge-tts, Kokoro, Edge as last fallback; auto-fits 50-60 s)
agent/myvoice.py        your own voice: recording, clean-up, Whisper word timings, pace
agent/clone.py          your cloned voice via the local-voice tool (mood per scene)
agent/language.py       voiceover language: translation + meaning back-check
agent/maya_worker.py    runs Maya 2 inside local-voice for Tamil
agent/briefing.py       interactive mode: fact-check all, briefing, pick stories
agent/social.py         YouTube / Instagram / LinkedIn posts and carousel
agent/render.py         stage 5 (classic 9:16 template, captions, parallel scene render)
agent/template_terminal.py  terminal / hacker template
agent/template_tutorial.py  learning-tutorial template (and the engine the next three share)
agent/template_skins.py     news, editorial and chat templates
agent/prompts.py        all prompts
tests/test_checks.py    tests for the code-level checks
assets/fonts/           Inter (SIL Open Font License 1.1)
```

## Final check before upload

After rendering, the finished video is compared with the script and the result is saved
as `qa.json`: Whisper listens to the final audio, and the template, length and caption
timing are checked. If anything fails, the run ends with **NEEDS REVIEW** instead of Done,
and the problems go at the top of `posts.md`.

The `reconstruction` template is only offered for scripts with flight-track data; anything
else is switched to the template recommended for its story.

## Tests

The code-level checks (source tiers, publishable rule, script structure, speakability,
flow, social post rules) have tests that need no network or API key:

```bash
.venv/bin/python -m unittest discover tests
```

Run them after changing any of those rules.
