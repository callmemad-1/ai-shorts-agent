"""The Telegram bot, against a fake Telegram: no network and no token."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import remote  # noqa: E402
from agent.telegram_bot import Api, TelegramBot, TelegramError, split_text  # noqa: E402

CFG = json.loads((ROOT / "config.json").read_text())
ME, CHAT, STRANGER = 111, 222, 999


class FakeApi:
    def __init__(self):
        self.calls, self.uploads = [], []

    def call(self, method, http_timeout=40, **p):
        self.calls.append((method, p))
        return {"message_id": len(self.calls)}

    def upload(self, method, files, timeout=300, **p):
        self.uploads.append((method, list(files), p))
        return {}

    def texts(self):
        return [p["text"] for m, p in self.calls if m == "sendMessage"]

    def last_keyboard(self):
        return [p["reply_markup"]["inline_keyboard"] for m, p in self.calls if p.get("reply_markup")][-1]


def text(t, user=ME, chat=CHAT):
    return {"message": {"text": t, "from": {"id": user}, "chat": {"id": chat}}}


def tap(data, user=ME, chat=CHAT):
    return {"callback_query": {"id": "c", "data": data, "from": {"id": user}, "message": {"message_id": 5, "chat": {"id": chat}}}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner = Path(self.tmp.name) / "owner.json"
        self.owner.write_text(json.dumps({"user_id": ME, "chat_id": CHAT}))
        self.api = FakeApi()
        self.bot = TelegramBot(self.api, CFG, self.owner)
        self.bot.timeout = 5

    def answer(self, *events, wait_for_prompts=1):
        """Answer the question once the bot has asked it."""
        def go():
            import time
            for _ in range(400):
                if self.bot.prompt_id:
                    break
                time.sleep(0.01)
            for e in events:
                self.bot.handle(e(self.bot.prompt_id) if callable(e) else e)
        t = threading.Thread(target=go)
        t.start()
        self.addCleanup(t.join)


class Pairing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner = Path(self.tmp.name) / "owner.json"
        self.api = FakeApi()
        self.bot = TelegramBot(self.api, CFG, self.owner)

    def test_wrong_code_gets_a_hint_but_no_access(self):
        self.bot.handle(text("/start nope", STRANGER, STRANGER))
        self.bot.handle(text("/start", STRANGER, STRANGER))
        self.assertTrue(all("Not paired yet" in t for t in self.api.texts()))
        self.assertEqual(len(self.api.texts()), 2)
        self.assertFalse(self.owner.exists())

    def test_code_alone_or_in_capitals_pairs(self):
        self.bot.handle(text(self.bot.pair_code.upper()))
        self.assertEqual(json.loads(self.owner.read_text())["user_id"], ME)

    def test_right_code_pairs_and_file_is_private(self):
        self.bot.handle(text(f"/start {self.bot.pair_code}"))
        self.assertEqual(json.loads(self.owner.read_text()), {"user_id": ME, "chat_id": CHAT})
        self.assertEqual(self.owner.stat().st_mode & 0o777, 0o600)
        self.assertIn("Paired", self.api.texts()[0])

    def test_code_is_needed_again_never_after_pairing(self):
        code = self.bot.pair_code
        self.bot.handle(text(f"/start {code}"))
        self.bot.handle(text(f"/start {code}", STRANGER, STRANGER))
        self.assertEqual(json.loads(self.owner.read_text())["user_id"], ME)


class Access(Base):
    def test_strangers_are_ignored(self):
        with mock.patch.object(self.bot, "start_job") as job:
            self.bot.handle(text("/today", STRANGER, STRANGER))
            self.bot.handle(text("/today", STRANGER, CHAT))
            self.bot.handle(text("/today", ME, STRANGER))
        job.assert_not_called()
        self.assertEqual(self.api.calls, [])

    def test_stranger_cannot_answer_a_question(self):
        self.bot.prompt_id = "abc"
        self.bot.handle(tap("abc:0", STRANGER))
        self.assertTrue(self.bot.inbox.empty())

    def test_today_starts_a_job(self):
        with mock.patch.object(self.bot, "start_job", return_value=True) as job:
            self.bot.handle(text("/today"))
        job.assert_called_once()

    def test_second_today_is_refused_while_busy(self):
        self.bot.busy.acquire()
        self.addCleanup(self.bot.busy.release)
        self.bot.handle(text("/today"))
        self.assertIn("already working", self.api.texts()[0])

    def test_cancel_stops_at_next_question(self):
        self.bot.busy.acquire()
        self.addCleanup(self.bot.busy.release)
        self.bot.handle(text("/cancel"))
        with self.assertRaises(remote.Cancelled):
            self.bot.choose("Q", [("a", "A")], "a")

    def test_cancelled_gets_past_except_exception(self):
        self.assertFalse(issubclass(remote.Cancelled, Exception))


class Questions(Base):
    OPTS = [("en", "English"), ("ta", "Tamil")]

    def test_tap_picks_that_option(self):
        self.answer(lambda pid: tap(f"{pid}:1"))
        self.assertEqual(self.bot.choose("Language?", self.OPTS, "en"), "ta")

    def test_no_answer_uses_the_default(self):
        self.bot.timeout = 0.05
        self.assertEqual(self.bot.choose("Language?", self.OPTS, "en"), "en")
        self.assertIn("No answer", self.api.texts()[-1])

    def test_old_button_does_not_answer_a_new_question(self):
        self.bot.prompt_id = "new"
        self.bot.handle(tap("old:1"))
        self.assertTrue(self.bot.inbox.empty())

    def test_pick_many_toggles_then_done(self):
        self.answer(lambda pid: tap(f"{pid}:0"), lambda pid: tap(f"{pid}:2"), lambda pid: tap(f"{pid}:done"))
        out = self.bot.pick_many("Which?", [(1, "a"), (2, "b"), (3, "c")], [1, 2])
        self.assertEqual(out, [2, 3])

    def test_pick_many_can_choose_nothing(self):
        self.answer(lambda pid: tap(f"{pid}:0"), lambda pid: tap(f"{pid}:done"))
        self.assertEqual(self.bot.pick_many("Which?", [(1, "a")], [1]), [])

    def test_pick_many_timeout_uses_default(self):
        self.bot.timeout = 0.05
        self.assertEqual(self.bot.pick_many("Which?", [(1, "a"), (2, "b")], [2]), [2])

    def test_approve_button(self):
        self.answer(lambda pid: tap(f"{pid}:ok"))
        self.assertEqual(self.bot.approve("Script.", 1, []), "")

    def test_reply_is_a_rewrite_note(self):
        self.answer(text("make it simpler"))
        self.assertEqual(self.bot.approve("Script.", 1, []), "make it simpler")

    def test_audit_warnings_are_shown(self):
        self.bot.timeout = 0.05
        self.bot.approve("Script.", 1, ["Scene 3 overstates"])
        self.assertTrue(any("Scene 3 overstates" in t for t in self.api.texts()))

    def test_a_note_means_nothing_when_no_script_is_waiting(self):
        self.bot.handle(text("hello"))
        self.assertTrue(self.bot.inbox.empty())
        self.assertIn("/today", self.api.texts()[-1])

    def test_photo_picks_come_back_like_the_terminal(self):
        p = Path(self.tmp.name) / "a.jpg"
        p.write_bytes(b"x")
        self.answer(lambda pid: tap(f"{pid}:0"), lambda pid: tap(f"{pid}:1"), lambda pid: tap(f"{pid}:done"))
        self.assertEqual(self.bot.pick_photos("Scene 1", [p, p], ["a", "b"]), "1,2")
        self.assertEqual(self.api.uploads[0][0], "sendMediaGroup")


class Delivery(Base):
    def folder(self, problems=()):
        f = Path(self.tmp.name) / "short"
        (f / "posts").mkdir(parents=True)
        (f / "script.json").write_text(json.dumps({"youtube_title": "A title"}))
        (f / "qa.json").write_text(json.dumps({"problems": list(problems)}))
        (f / "short.mp4").write_bytes(b"v")
        (f / "posts" / "posts.md").write_text("p")
        (f / "posts" / "captions.srt").write_text("c")
        return f

    def test_video_and_pack_are_sent(self):
        f = self.folder()
        self.bot.deliver(f, f / "short.mp4")
        self.assertEqual([u[0] for u in self.api.uploads], ["sendVideo", "sendDocument", "sendDocument"])
        self.assertIn("Passed", self.api.uploads[0][2]["caption"])

    def test_failed_check_is_said_plainly(self):
        f = self.folder(["Scene 1 unclear"])
        self.bot.deliver(f, f / "short.mp4")
        self.assertIn("NEEDS REVIEW", self.api.uploads[0][2]["caption"])
        self.assertTrue(any("Scene 1 unclear" in t for t in self.api.texts()))

    def test_oversize_video_is_left_on_the_mac(self):
        f = self.folder()
        with mock.patch("agent.telegram_bot.MAX_UPLOAD", 0):
            self.bot.deliver(f, f / "short.mp4")
        self.assertNotIn("sendVideo", [u[0] for u in self.api.uploads])
        self.assertTrue(any("on your Mac" in t for t in self.api.texts()))

    def test_send_failure_does_not_raise(self):
        f = self.folder()
        with mock.patch.object(self.api, "upload", side_effect=TelegramError("down")):
            self.bot.deliver(f, f / "short.mp4")


class Schedule(Base):
    def test_daily_runs_once_a_day_after_its_time(self):
        import datetime as dt
        cfg = copy.deepcopy(CFG)
        cfg["telegram"]["daily_at"] = "08:00"
        bot = TelegramBot(self.api, cfg, self.owner)
        before, after = dt.datetime(2026, 11, 9, 7, 59), dt.datetime(2026, 11, 9, 8, 0)
        self.assertFalse(bot.daily_due(before))
        self.assertTrue(bot.daily_due(after))
        with mock.patch.object(bot, "start_job", return_value=True) as job:
            bot.tick_daily(after)
            bot.tick_daily(after)
        job.assert_called_once()

    def test_off_by_default(self):
        import datetime as dt
        self.assertFalse(self.bot.daily_due(dt.datetime(2026, 11, 9, 23, 0)))


class Helpers(unittest.TestCase):
    def test_long_text_splits_within_limit(self):
        parts = split_text("line\n" * 2000, 100)
        self.assertTrue(all(len(p) <= 100 for p in parts))
        self.assertEqual("".join(parts).count("line"), 2000)

    def test_one_huge_line_still_splits(self):
        self.assertTrue(all(len(p) <= 100 for p in split_text("x" * 1000, 100)))

    def test_errors_never_contain_the_token(self):
        import urllib.error
        api = Api("SECRET-TOKEN")
        err = urllib.error.URLError("no route")
        with mock.patch("urllib.request.urlopen", side_effect=err), mock.patch("time.sleep"):
            with self.assertRaises(TelegramError) as c:
                api.call("getMe")
        self.assertNotIn("SECRET-TOKEN", str(c.exception))


class Routing(unittest.TestCase):
    """With a bot active the menus use it; without one they don't touch it."""

    def setUp(self):
        self.bot = mock.Mock()
        self.bot.choose.side_effect = lambda q, options, default: default
        remote.active = self.bot
        self.addCleanup(setattr, remote, "active", None)
        self.cfg = copy.deepcopy(CFG)

    def test_pattern_language_use_the_bot(self):
        from agent.language import ask_language
        from agent.scriptwriter import ask_pattern
        ask_pattern(self.cfg, "research")
        ask_language(self.cfg)
        self.assertEqual(self.bot.choose.call_count, 2)

    def test_voice_is_ai_on_telegram(self):
        from agent.briefing import ask_voice
        self.cfg["tts"]["engine"] = "mine"
        self.assertEqual(ask_voice(self.cfg), "edge")

    def test_failed_stories_are_not_offered(self):
        from agent.briefing import ask_choice
        self.bot.pick_many.side_effect = lambda q, options, default, done_label: [v for v, _ in options]
        b = {"stories": [{"id": "s1", "title": "Good"}, {"id": "s2", "title": "Bad"}],
             "checks": [{"publishable": True}, {"publishable": False}],
             "recommendation": {"recommended": ["s1"], "notes": [{"story_id": "s1", "verdict": "make"}]}}
        self.assertEqual(ask_choice(b), [1])

    def test_no_bot_means_no_remote_calls(self):
        from agent.scriptwriter import ask_pattern
        remote.active = None
        with mock.patch("sys.stdin.isatty", return_value=False):
            self.assertEqual(ask_pattern(self.cfg), self.cfg["script"]["pattern"])
        self.bot.choose.assert_not_called()


if __name__ == "__main__":
    unittest.main()
