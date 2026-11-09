"""Run the agent from your phone: a Telegram bot that lives on this Mac.

    .venv/bin/python -m agent.telegram_bot

Commands: /today, /status, /cancel, /help. It does what `main.py --interactive` does, but asks
with buttons and sends the finished video to the chat. Nothing is ever posted anywhere else.

Only the account that pairs with the code printed in the terminal can use it; every other
message is ignored. The token comes from TELEGRAM_BOT_TOKEN in .env and is never printed.
"""
from __future__ import annotations

import datetime as dt
import json
import mimetypes
import queue
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import remote
from .utils import load_config, load_dotenv, log, today_dir

ROOT = Path(__file__).resolve().parent.parent
OWNER_FILE = ROOT / ".telegram_owner.json"
MAX_TEXT = 3900  # Telegram's limit is 4096
MAX_UPLOAD = 50 * 1024 * 1024  # what a bot may send
MAX_ALBUM = 10
HELP = ("/today - research, fact-check and make today's Shorts\n"
        "/status - what I'm doing right now\n"
        "/cancel - stop the current job at its next step\n"
        "/help - this list\n\n"
        "This Mac has to stay on and online. Nothing is posted anywhere: you get the video here.")


class TelegramError(RuntimeError):
    pass


class Api:
    """The few Bot API calls we need, with no extra packages. Errors never include the URL,
    because the URL contains the token."""

    def __init__(self, token: str, base: str = "https://api.telegram.org"):
        self._url = f"{base}/bot{token}/"

    def _send(self, method: str, data: bytes, ctype: str, timeout: float):
        for attempt in range(4):
            req = urllib.request.Request(self._url + method, data=data, headers={"Content-Type": ctype})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read())["result"]
            except urllib.error.HTTPError as e:
                try:
                    body = json.loads(e.read())
                except ValueError:
                    body = {}
                wait = (body.get("parameters") or {}).get("retry_after")
                if e.code == 429 and wait and attempt < 3:
                    time.sleep(float(wait) + 1)
                    continue
                raise TelegramError(f"Telegram refused {method}: {body.get('description') or e.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                reason = getattr(e, "reason", None) or type(e).__name__
                if attempt == 3:
                    raise TelegramError(f"Couldn't reach Telegram for {method}: {reason}") from None
                time.sleep(2 ** attempt)

    def call(self, method: str, http_timeout: float = 40, **params):
        data = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
        return self._send(method, data, "application/json", http_timeout)

    def upload(self, method: str, files: dict[str, Path], timeout: float = 300, **params):
        """Multipart upload. `files` maps a form field to a file on disk."""
        boundary = uuid.uuid4().hex
        out = bytearray()
        for k, v in params.items():
            if v is None:
                continue
            value = v if isinstance(v, str) else json.dumps(v)
            out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{value}\r\n').encode()
        for field, path in files.items():
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'
                    f"Content-Type: {ctype}\r\n\r\n").encode()
            out += path.read_bytes() + b"\r\n"
        out += f"--{boundary}--\r\n".encode()
        return self._send(method, bytes(out), f"multipart/form-data; boundary={boundary}", timeout)


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Break a long message at line ends so each part fits in one Telegram message."""
    parts, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            parts.append(cur)
            cur = ""
        cur += line
    if cur.strip():
        parts.append(cur)
    return parts


def load_owner(path: Path = OWNER_FILE) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save_owner(state: dict, path: Path = OWNER_FILE) -> None:
    path.write_text(json.dumps(state, indent=1))
    path.chmod(0o600)


class TelegramBot:
    def __init__(self, api: Api, cfg: dict, owner_file: Path = OWNER_FILE):
        self.api, self.cfg, self.owner_file = api, cfg, owner_file
        self.state = load_owner(owner_file)
        self.pair_code = None if self.state.get("user_id") else secrets.token_hex(3)
        self.timeout = float(cfg.get("telegram", {}).get("answer_timeout_minutes", 120)) * 60
        self.inbox: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = threading.Lock()
        self.prompt_id = None
        self.prompt_takes_text = False
        self.doing = "idle"

    # ------------------------------------------------------------ messages in
    def is_owner(self, user_id, chat_id) -> bool:
        return user_id == self.state.get("user_id") and chat_id == self.state.get("chat_id")

    def handle(self, update: dict) -> None:
        tap = update.get("callback_query")
        if tap:
            msg = tap.get("message") or {}
            if not self.is_owner(tap["from"]["id"], (msg.get("chat") or {}).get("id")):
                return  # strangers get no reply at all
            pid, _, value = str(tap.get("data", "")).partition(":")
            live = pid == self.prompt_id
            self.api.call("answerCallbackQuery", callback_query_id=tap["id"], text=None if live else "That question has passed.")
            if live:
                self.inbox.put(("tap", value, msg.get("message_id")))
            return
        msg = update.get("message")
        if not msg or "text" not in msg or "from" not in msg:
            return
        user, chat, text = msg["from"]["id"], msg["chat"]["id"], msg["text"].strip()
        if not self.state.get("user_id"):
            if text.split() == ["/start", self.pair_code]:
                self.state.update(user_id=user, chat_id=chat)
                save_owner(self.state, self.owner_file)
                self.pair_code = None
                log("Telegram: paired with your account.")
                self.say("Paired. Only you can use this bot now.\n\n" + HELP)
            return
        if not self.is_owner(user, chat):
            return
        cmd = text.split("@")[0].split()[0].lower() if text.startswith("/") else ""
        if cmd in ("/start", "/help"):
            self.say(HELP)
        elif cmd == "/today":
            if not self.start_job():
                self.say(f"I'm already working: {self.doing}. Send /cancel to stop it.")
        elif cmd == "/status":
            self.say(f"Right now: {self.doing}.")
        elif cmd == "/cancel":
            if self.busy.locked():
                self.cancel.set()
                self.say("Stopping at the next step.")
            else:
                self.say("Nothing is running.")
        elif cmd:
            self.say("I don't know that command.\n\n" + HELP)
        elif self.prompt_id and self.prompt_takes_text:
            self.inbox.put(("text", text, None))
        else:
            self.say("Send /today to start, or /help.")

    # ------------------------------------------------------------ messages out
    def say(self, text: str, keyboard: list | None = None) -> int | None:
        parts = split_text(text) or ["(empty)"]
        last = None
        for i, part in enumerate(parts):
            markup = {"inline_keyboard": keyboard} if keyboard and i == len(parts) - 1 else None
            last = self.api.call("sendMessage", chat_id=self.state["chat_id"], text=part, reply_markup=markup)
        return (last or {}).get("message_id")

    def send_file(self, path: Path, caption: str | None = None) -> None:
        path = Path(path)
        if path.stat().st_size > MAX_UPLOAD:
            self.say(f"{path.name} is too big for Telegram. It's on your Mac: {path}")
            return
        self.api.upload("sendDocument", {"document": path}, chat_id=str(self.state["chat_id"]), caption=caption)

    def _album(self, paths: list[Path], captions: list[str] | None = None) -> None:
        for start in range(0, len(paths), MAX_ALBUM):
            chunk = paths[start:start + MAX_ALBUM]
            caps = (captions or [None] * len(paths))[start:start + MAX_ALBUM]
            chat = str(self.state["chat_id"])
            if len(chunk) == 1:
                self.api.upload("sendPhoto", {"photo": chunk[0]}, chat_id=chat, caption=caps[0])
                continue
            media = [{"type": "photo", "media": f"attach://f{i}", **({"caption": c} if c else {})} for i, c in enumerate(caps)]
            self.api.upload("sendMediaGroup", {f"f{i}": p for i, p in enumerate(chunk)}, chat_id=chat, media=media)

    # ------------------------------------------------------------ questions
    def check(self) -> None:
        if self.cancel.is_set():
            raise remote.Cancelled()

    def _new_prompt(self, takes_text: bool = False) -> str:
        self.check()
        while not self.inbox.empty():  # nothing tapped before this question counts as its answer
            self.inbox.get_nowait()
        self.prompt_takes_text = takes_text
        self.prompt_id = secrets.token_hex(3)
        return self.prompt_id

    def _wait(self, deadline: float | None = None, keep_open: bool = False):
        """The next answer, or None when nobody answered in time."""
        deadline = deadline or time.monotonic() + self.timeout
        try:
            while True:
                self.check()
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                try:
                    return self.inbox.get(timeout=min(left, 1.0))
                except queue.Empty:
                    continue
        finally:
            if not keep_open:
                self.prompt_id, self.prompt_takes_text = None, False

    def _clear_buttons(self, message_id) -> None:
        try:
            self.api.call("editMessageReplyMarkup", chat_id=self.state["chat_id"], message_id=message_id,
                          reply_markup={"inline_keyboard": []})
        except TelegramError:
            pass  # cosmetic

    def choose(self, question: str, options: list[tuple], default):
        pid = self._new_prompt()
        rows = [[{"text": f"{label}{'  (default)' if value == default else ''}"[:60], "callback_data": f"{pid}:{i}"}]
                for i, (value, label) in enumerate(options)]
        mid = self.say(question, rows)
        ans = self._wait()
        self._clear_buttons(mid)
        if ans is None:
            label = dict(options).get(default, default)
            self.say(f"No answer, so I'm using: {label}")
            return default
        value, label = options[int(ans[1])]
        self.say(f"✓ {label}")
        return value

    def confirm(self, question: str, default: bool = True) -> bool:
        return self.choose(question, [(True, "Yes"), (False, "No")], default)

    def pick_many(self, question: str, options: list[tuple], default: list, done_label: str = "Done"):
        """Tap to tick several, then Done. Returns the ticked values (possibly none)."""
        chosen = {i for i, (v, _) in enumerate(options) if v in default}
        pid = self._new_prompt()

        def rows():
            out = [[{"text": f"{'✅ ' if i in chosen else ''}{label}"[:60], "callback_data": f"{pid}:{i}"}]
                   for i, (_, label) in enumerate(options)]
            return out + [[{"text": done_label, "callback_data": f"{pid}:done"}]]

        mid = self.say(question, rows())
        deadline = time.monotonic() + self.timeout
        while True:
            ans = self._wait(deadline, keep_open=True)
            if ans is None or ans[1] == "done":
                self.prompt_id = None
                break
            i = int(ans[1])
            chosen.symmetric_difference_update({i})
            self.api.call("editMessageReplyMarkup", chat_id=self.state["chat_id"], message_id=mid,
                          reply_markup={"inline_keyboard": rows()})
        self._clear_buttons(mid)
        if ans is None:
            chosen = {i for i, (v, _) in enumerate(options) if v in default}
            self.say("No answer, so I'm using the default.")
        return [options[i][0] for i in sorted(chosen)]

    def pick_photos(self, title: str, thumbs: list[Path], labels: list[str]) -> str:
        """Show the options and return the picks as "1,3" (empty = skip), like the terminal."""
        self._album(thumbs, [str(i) for i in range(1, len(thumbs) + 1)])
        options = [(i, f"{i}. {label}") for i, label in enumerate(labels, 1)]
        picks = self.pick_many(title + "\n\nTap the photos to use (the number on each one), then Done. Nothing ticked = no photo.", options, [])
        return ",".join(map(str, picks))

    def approve(self, text: str, words: int, warnings: list[str]) -> str:
        """Empty string = approved; anything else is your note for a rewrite."""
        pid = self._new_prompt(takes_text=True)
        extra = "".join(f"\n⚠ Fact audit: {w}" for w in warnings)
        mid = self.say(f"The script, as it will be heard ({words} words):\n\n{text}\n{extra}\n\n"
                       "Tap Approve, or reply with what to change (for example: simpler, less dramatic).",
                       [[{"text": "Approve", "callback_data": f"{pid}:ok"}]])
        ans = self._wait()
        self._clear_buttons(mid)
        if ans is None:
            self.say("No answer, so I'm going ahead with this script.")
            return ""
        if ans[0] == "tap":
            self.say("✓ Approved")
            return ""
        self.say("Rewriting with your note...")
        return ans[1]

    def deliver(self, folder: Path, mp4: Path) -> None:
        """Send the finished Short and its pack. A send failure must not lose the video."""
        folder, mp4 = Path(folder), Path(mp4)
        try:
            script = json.loads((folder / "script.json").read_text())
            qa = json.loads((folder / "qa.json").read_text()) if (folder / "qa.json").exists() else {}
            problems = qa.get("problems", [])
            head = "⚠ NEEDS REVIEW - don't upload this yet" if problems else "✅ Passed the final check"
            caption = f"{script.get('youtube_title') or script.get('title', '')}\n{head}"
            if mp4.stat().st_size > MAX_UPLOAD:
                self.say(f"{caption}\n\nThe video is too big for Telegram. It's on your Mac: {mp4}")
            else:
                self.api.upload("sendVideo", {"video": mp4}, chat_id=str(self.state["chat_id"]), caption=caption[:1000],
                                supports_streaming="true", width="1080", height="1920")
            if problems:
                self.say("Problems found:\n" + "\n".join(f"- {p}" for p in problems))
            posts = folder / "posts"
            for f in (posts / "posts.md", posts / "captions.srt"):
                if f.exists():
                    self.send_file(f)
            slides = sorted(posts.glob("carousel_*.png"))
            if slides:
                self._album(slides)
        except Exception as e:  # noqa: BLE001
            log(f"Telegram: couldn't send the Short ({e}). It's on the Mac: {mp4}")
            try:
                self.say(f"The Short is ready but I couldn't send it here. It's on your Mac: {mp4}")
            except TelegramError:
                pass

    # ------------------------------------------------------------ jobs
    def start_job(self) -> bool:
        if not self.busy.acquire(blocking=False):
            return False
        self.cancel.clear()
        self.doing = "starting"
        threading.Thread(target=self._job, daemon=True).start()
        return True

    def run_briefing(self) -> int:
        from main import add_homebrew_to_path

        from . import briefing

        add_homebrew_to_path()
        cfg = load_config()  # fresh each job: a run changes its copy
        today = dt.date.today()
        out = today_dir(today)
        out.mkdir(parents=True, exist_ok=True)
        brain = cfg.get("telegram", {}).get("brain") or cfg.get("llm", {}).get("backend", "api")
        return briefing.run(cfg, today, out, None, brain)

    def _job(self) -> None:
        remote.active = self
        try:
            self.doing = "researching and fact-checking today's AI news"
            self.say("Starting. Research and fact-checking take several minutes; I'll message you when I need you.")
            code = self.run_briefing()
            self.say("All done." if code == 0 else "Finished, but the Shorts failed. Check the messages above or the Mac's log.")
        except remote.Cancelled:
            self.say("Stopped.")
        except (Exception, SystemExit) as e:  # noqa: BLE001 - always tell the phone what happened
            log(f"Telegram job failed: {e}")
            try:
                self.say(f"That run failed: {str(e)[:500]}")
            except TelegramError:
                pass
        finally:
            remote.active = None
            self.doing = "idle"
            self.busy.release()

    def daily_due(self, now: dt.datetime) -> bool:
        at = self.cfg.get("telegram", {}).get("daily_at")
        if not at or not self.state.get("user_id"):
            return False
        hour, minute = map(int, str(at).split(":"))
        return (now.hour, now.minute) >= (hour, minute) and self.state.get("last_daily") != now.date().isoformat()

    def tick_daily(self, now: dt.datetime) -> None:
        if self.daily_due(now) and self.start_job():
            self.state["last_daily"] = now.date().isoformat()
            save_owner(self.state, self.owner_file)

    def run_forever(self) -> None:
        if self.pair_code:
            print(f"\nOpen your bot in Telegram and send:  /start {self.pair_code}\n", flush=True)
        else:
            log("Telegram bot running. Send /today from your phone. Keep this window open.")
        offset, failures = None, 0
        while True:
            try:
                updates = self.api.call("getUpdates", http_timeout=40, offset=offset, timeout=25,
                                        allowed_updates=["message", "callback_query"])
                failures = 0
            except TelegramError as e:
                failures += 1
                log(f"Telegram: {e}")
                time.sleep(min(60, 2 ** failures))
                continue
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    self.handle(u)
                except Exception as e:  # noqa: BLE001 - one bad message must not stop the bot
                    log(f"Telegram: couldn't handle a message ({e})")
            self.tick_daily(dt.datetime.now())


def main() -> int:
    import os

    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Add TELEGRAM_BOT_TOKEN=... to your .env file first (the token comes from @BotFather).")
        return 1
    TelegramBot(Api(token), load_config()).run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
