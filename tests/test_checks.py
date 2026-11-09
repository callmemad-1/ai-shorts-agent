"""Tests for the code-level safety checks: the rules that decide what's allowed on air.

No network, no API key, no extra packages. Run from the project folder:

    .venv/bin/python -m unittest discover tests
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.factcheck import enforce_rules, evidence_ok, source_tier  # noqa: E402
from agent.scriptwriter import check_flow, check_speakability, check_structure, spoken_numbers  # noqa: E402
from agent.social import check as social_check, markdown as posts_md, names_site  # noqa: E402

CFG = json.loads((ROOT / "config.json").read_text())
TIERS = {**CFG["research"]["source_tiers"], "blocked": CFG["research"]["aggregator_domains"]}


def ev(*urls):
    return [{"url": u} for u in urls]


class SourceTiers(unittest.TestCase):
    def test_major_outlet(self):
        self.assertEqual(source_tier("https://www.reuters.com/tech/x", "", TIERS), "M")

    def test_government_is_primary(self):
        self.assertEqual(source_tier("https://www.ftc.gov/news/x", "", TIERS), "P")

    def test_company_own_site_is_primary_when_named(self):
        self.assertEqual(source_tier("https://openai.com/index/x", "OpenAI launches a model", TIERS), "P")

    def test_company_site_not_primary_when_not_named(self):
        self.assertEqual(source_tier("https://openai.com/index/x", "Google launches a model", TIERS), "o")

    def test_aggregator_is_blocked(self):
        self.assertEqual(source_tier("https://www.techmeme.com/x", "", TIERS), "x")

    def test_platforms_are_never_primary(self):
        self.assertEqual(source_tier("https://medium.com/@openai/x", "OpenAI says", TIERS), "o")


class BalancedRule(unittest.TestCase):
    def test_primary_plus_one_independent(self):
        self.assertTrue(evidence_ok([("P", "openai.com"), ("o", "someblog.com")]))

    def test_two_major_outlets(self):
        self.assertTrue(evidence_ok([("M", "reuters.com"), ("M", "theverge.com")]))

    def test_one_major_plus_other(self):
        self.assertTrue(evidence_ok([("M", "reuters.com"), ("o", "someblog.com")]))

    def test_others_only_is_not_enough(self):
        self.assertFalse(evidence_ok([("o", "a.com"), ("o", "b.com"), ("o", "c.com")]))

    def test_same_outlet_twice_is_one_source(self):
        self.assertFalse(evidence_ok([("M", "reuters.com"), ("M", "reuters.com")]))

    def test_primary_alone_is_not_enough(self):
        self.assertFalse(evidence_ok([("P", "openai.com")]))

    def test_blocked_sources_do_not_count(self):
        self.assertFalse(evidence_ok([("M", "reuters.com"), ("x", "techmeme.com")]))


class Publishable(unittest.TestCase):
    GOOD = ev("https://www.reuters.com/a", "https://www.theverge.com/b")

    def fc(self, headline_verdict="verified", order=None):
        claims = [{"id": f"s1c{i}", "verdict": "verified", "evidence": copy.deepcopy(self.GOOD)} for i in (1, 2, 3, 4)]
        claims[0]["verdict"] = headline_verdict
        if order:
            claims = [claims[i] for i in order]
        return {"overall": "publishable", "event_in_window": True, "claims": claims}

    STORY = {"title": "t", "key_claims": [{"id": "s1c1"}]}

    def test_good_story_is_publishable(self):
        self.assertTrue(enforce_rules(self.fc(), CFG, self.STORY)["publishable"])

    def test_unverified_headline_blocks_even_if_listed_last(self):
        fc = enforce_rules(self.fc("unverified", order=[1, 2, 3, 0]), CFG, self.STORY)
        self.assertFalse(fc["publishable"])

    def test_out_of_window_is_not_publishable(self):
        fc = self.fc()
        fc["event_in_window"] = False
        self.assertFalse(enforce_rules(fc, CFG, self.STORY)["publishable"])

    def test_needs_three_usable_claims(self):
        fc = self.fc()
        fc["claims"] = fc["claims"][:2]
        self.assertFalse(enforce_rules(fc, CFG, self.STORY)["publishable"])

    def test_weak_evidence_is_downgraded(self):
        fc = self.fc()
        fc["claims"][1]["evidence"] = ev("https://a-blog.com/x", "https://b-blog.com/y")
        out = enforce_rules(fc, CFG, self.STORY)
        self.assertEqual(out["claims"][1]["verdict"], "unverified")
        self.assertNotIn("s1c2", [c["id"] for c in out["usable_claims"]])

    def test_aggregator_evidence_does_not_count(self):
        fc = self.fc()
        fc["claims"][1]["evidence"] = ev("https://www.reuters.com/a", "https://www.techmeme.com/b")
        out = enforce_rules(fc, CFG, self.STORY)
        # Reuters with only an aggregator beside it is one source: downgraded
        self.assertEqual(out["claims"][1]["verdict"], "unverified")
        self.assertNotIn("s1c2", [c["id"] for c in out["usable_claims"]])


class SpokenNumbers(unittest.TestCase):
    def test_counts_numbers_after_sentence_starts_and_months(self):
        found = spoken_numbers("In 2027, Google will spend 40 billion. By 2030, on November 4 it ends.")
        self.assertEqual(found, ["2027", "40 billion", "2030", "4"])

    def test_product_names_are_not_numbers(self):
        self.assertEqual(spoken_numbers("Hut 8 raised money. GPT 5 is out."), [])

    def test_spelled_out_numbers(self):
        self.assertEqual(spoken_numbers("It grew twenty-three percent."), ["twenty-three percent"])

    def test_too_many_numbers_flagged(self):
        scenes = [{"id": 1, "type": "setup", "voiceover": "In 2027 it costs 5 dollars, then 7, then 9."}]
        self.assertTrue(any("numbers" in p for p in check_speakability(scenes, 3)))

    def test_long_sentence_flagged(self):
        scenes = [{"id": 1, "type": "setup", "voiceover": " ".join(["word"] * 25) + "."}]
        self.assertTrue(any("one breath" in p for p in check_speakability(scenes, 3)))


class CitedSources(unittest.TestCase):
    def test_only_primary_and_major_outlets_cited(self):
        from agent.scriptwriter import cited_sources
        usable = [{"evidence": [{"url": "https://tracker.example/list", "publisher": "Tracker", "tier": "o"},
                                {"url": "https://www.reuters.com/a", "publisher": "Reuters", "tier": "M"},
                                {"url": "https://openai.com/b", "publisher": "OpenAI", "tier": "P"}]}]
        self.assertEqual([x["publisher"] for x in cited_sources(usable, CFG)], ["OpenAI", "Reuters"])

    def test_falls_back_to_others_when_nothing_better(self):
        from agent.scriptwriter import cited_sources
        usable = [{"evidence": [{"url": "https://blog.example/x", "publisher": "Blog", "tier": "o"},
                                {"url": "https://www.techmeme.com/y", "publisher": "Techmeme", "tier": "x"}]}]
        self.assertEqual([x["publisher"] for x in cited_sources(usable, CFG)], ["Blog"])


class Structure(unittest.TestCase):
    STORY = ["hook", "setup", "twist", "example", "stakes", "verify", "outro"]
    EXPLAINER = ["hook", "step", "step", "example", "verify", "outro"]

    def test_story_arc_passes(self):
        self.assertEqual(check_structure(self.STORY, "story"), [])

    def test_story_needs_verify(self):
        self.assertTrue(check_structure([t for t in self.STORY if t != "verify"], "story"))

    def test_story_out_of_order(self):
        self.assertTrue(check_structure(["hook", "stakes", "setup", "twist", "example", "verify", "outro"], "story"))

    def test_explainer_passes(self):
        self.assertEqual(check_structure(self.EXPLAINER, "explainer"), [])

    def test_explainer_needs_two_steps(self):
        self.assertTrue(check_structure(["hook", "step", "example", "verify", "outro"], "explainer"))

    def test_explainer_rejects_story_types(self):
        self.assertTrue(check_structure(["hook", "twist", "step", "step", "example", "verify", "outro"], "explainer"))


def scenes(*lines, types=("hook", "setup", "twist", "example", "stakes", "verify", "outro")):
    return [{"id": i + 1, "type": t, "voiceover": v} for i, (t, v) in enumerate(zip(types, lines))]


FLOWING = scenes(
    "Tencent just announced a new AI called Hy4, and it's being pitched as open-source. But you can't download it yet.",
    "Hy4 is a large language model, the kind of AI behind chatbots, and it's the full version of an August preview.",
    "It's picked up two new skills. It can understand images now, and for the first time it can use a computer.",
    "Here's the catch, though. Open-source means anyone can download the files and run them, and those aren't out yet.",
    "Tencent says they're coming in the next few days. So right now, open is a promise, not something you can check.",
    "Same with better: that's Tencent's own testing, and nobody outside has checked it yet.",
    "If those files land this week, the story changes. Follow for daily AI news.",
)


class Flow(unittest.TestCase):
    def test_flowing_script_passes(self):
        self.assertEqual(check_flow(FLOWING), [])

    def test_choppy_script_flagged(self):
        choppy = scenes("Big news. Tencent has Hy4.", "It's an AI. It chats. It's new.", "It sees images. It uses computers.",
                        "Think of a menu. Kitchen closed.", "So it's a promise. Not a fact.", "Self-reported. Unchecked.",
                        "Follow for daily AI news.")
        self.assertTrue(any("choppy" in p for p in check_flow(choppy)))

    def test_feature_list_flagged(self):
        s = copy.deepcopy(FLOWING)
        s[2]["voiceover"] = "Tencent says it's better at instructions, long documents, reasoning and multi-step tasks."
        self.assertTrue(any("three or more things" in p for p in check_flow(s)))

    def test_clause_commas_are_not_a_list(self):
        self.assertFalse(any("three or more" in p for p in check_flow(FLOWING)))

    def test_tease_before_comparison_flagged(self):
        s = copy.deepcopy(FLOWING)
        s[2]["voiceover"] += " But here's the catch."
        self.assertTrue(any("payoff" in p for p in check_flow(s)))

    def test_repeated_point_flagged(self):
        s = copy.deepcopy(FLOWING)
        for i in (1, 3, 4):
            s[i]["voiceover"] += " It isn't open-source yet."
        self.assertTrue(any("comes up in" in p for p in check_flow(s)))

    def test_same_attribution_in_three_scenes_flagged(self):
        s = copy.deepcopy(FLOWING)
        for i in (1, 2, 4):
            s[i]["voiceover"] = "Tencent says " + s[i]["voiceover"][0].lower() + s[i]["voiceover"][1:]
        probs = check_flow(s)
        self.assertTrue(any("'Tencent says' comes up in" in p for p in probs))
        self.assertFalse(any("The phrase 'tencent says" in p for p in probs))  # one message, not two

    def test_varied_attribution_passes(self):
        s = copy.deepcopy(FLOWING)
        s[1]["voiceover"] = "Tencent says " + s[1]["voiceover"]
        s[2]["voiceover"] = "The company says " + s[2]["voiceover"]
        s[4]["voiceover"] = "According to Tencent, " + s[4]["voiceover"]
        self.assertFalse(any("says' comes up" in p for p in check_flow(s)))

    def test_too_many_teases_flagged(self):
        s = copy.deepcopy(FLOWING)
        s[1]["voiceover"] += " Here's why that matters."
        s[4]["voiceover"] = "But here's the thing. " + s[4]["voiceover"]
        self.assertTrue(any("teases" in p for p in check_flow(s)))


def good_posts():
    linkedin = " ".join(["Regulators asked new questions about how the model was tested before release."] * 12)
    return {
        "youtube": {"title": "Google's new AI tutor lands in schools", "description": "Sources: https://www.reuters.com/a",
                    "pinned_comment": "Would you use it?"},
        "instagram": {"caption": "Google's AI tutor is coming to classrooms.\n\nSources: Reuters, The Verge",
                      "hashtags": ["#ai", "#google", "#edtech", "#technews", "#schools", "#aitools"]},
        "linkedin": {"post": linkedin + "\n\nSources: Reuters, The Verge"},
    }


class SocialPosts(unittest.TestCase):
    def test_good_posts_pass(self):
        self.assertEqual(social_check(good_posts(), CFG), [])

    def test_aggregator_name_flagged(self):
        posts = good_posts()
        posts["instagram"]["caption"] = posts["instagram"]["caption"].replace("Reuters", "Techmeme")
        self.assertTrue(any("aggregator" in p for p in social_check(posts, CFG)))

    def test_hashtag_is_not_an_aggregator(self):
        self.assertFalse(names_site("Big week #AIWeekly", "aiweekly.co"))
        self.assertTrue(names_site("seen on aiweekly.co", "aiweekly.co"))

    def test_missing_sources_flagged(self):
        posts = good_posts()
        posts["linkedin"]["post"] = posts["linkedin"]["post"].replace("Sources", "Thanks")
        self.assertTrue(any("sources" in p for p in social_check(posts, CFG)))

    def test_long_youtube_title_flagged(self):
        posts = good_posts()
        posts["youtube"]["title"] = "x" * 71
        self.assertTrue(any("YouTube title" in p for p in social_check(posts, CFG)))


class AIVoiceRetries(unittest.TestCase):
    def test_refused_request_is_retried(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            from unittest import mock
            from agent import tts
            calls = iter([RuntimeError("No audio was received"), b"", b"audio"])

            async def fake(text, voice, rate, path):
                r = next(calls)
                if isinstance(r, Exception):
                    raise r
                path.write_bytes(r)
                return [{"word": text}]

            with mock.patch.object(tts, "_edge", fake), mock.patch("time.sleep"):
                self.assertEqual(tts._edge_with_retries("Hi", "v", "+0%", Path(d) / "a.mp3", waits=[1, 1]), [{"word": "Hi"}])

    def test_gives_up_and_leaves_no_empty_file(self):
        import tempfile
        from unittest import mock
        from agent import tts

        async def fake(text, voice, rate, path):
            path.write_bytes(b"")
            return []

        with tempfile.TemporaryDirectory() as d, mock.patch.object(tts, "_edge", fake), mock.patch("time.sleep"):
            mp3 = Path(d) / "a.mp3"
            with self.assertRaises(RuntimeError):
                tts._edge_with_retries("Hi", "v", "+0%", mp3, waits=[1])
            self.assertFalse(mp3.exists())


class CloneMadeShortsDisclosure(unittest.TestCase):
    """Shorts already made with the (now removed) cloned voice must still be disclosed."""
    def test_disclosure_reminder_only_for_cloned_voice(self):
        posts = good_posts()
        self.assertIn("Altered or synthetic content", posts_md(posts, [], True))
        self.assertNotIn("Altered or synthetic content", posts_md(posts, [], False))


class UploadSettings(unittest.TestCase):
    VOICE = {"engine": "edge", "clips": [
        {"duration": 2.0, "words": [{"word": "Hello", "start": 0.1, "end": 0.5}, {"word": "there.", "start": 0.6, "end": 1.0}]},
        {"duration": 1.5, "words": [{"word": "Bye.", "start": 0.2, "end": 0.6}]}]}

    def test_srt_cues_follow_scene_offsets(self):
        from agent.social import srt
        out = srt(CFG, self.VOICE)
        self.assertIn("00:00:00,100 --> 00:00:01,000\nHello there.", out)
        gap = CFG["tts"]["scene_gap_seconds"]
        start = round((2.0 + gap) * CFG["video"]["fps"]) / CFG["video"]["fps"] + 0.2
        self.assertIn(f"00:00:0{int(start)},{round(start % 1 * 1000):03d}", out)

    def test_cloned_voice_is_disclosed_and_not_remixable(self):
        from agent.social import upload_settings
        rows = "\n".join(upload_settings(CFG, {"language": "ta"}, good_posts(), {"engine": "clone"}, None))
        self.assertIn("**Yes** - the voiceover is your cloned", rows)
        self.assertIn("Don't allow remixing", rows)
        self.assertIn("| Language (video and title/description) | Tamil |", rows)
        self.assertIn("not made for kids", rows)

    def test_tags_have_no_hash_and_fit(self):
        from agent.social import tags_for
        posts = good_posts()
        posts["youtube"]["hashtags"] = ["#AI", "#Shorts", "#Google"]
        posts["youtube"]["tags"] = ["Google AI tutor"] + [f"term {i}" for i in range(80)]
        tags = tags_for(posts, CFG)
        self.assertEqual(tags[0], "Google AI tutor")
        self.assertNotIn("Shorts", tags)
        self.assertTrue(all("#" not in t for t in tags))
        self.assertLessEqual(sum(len(t) + 1 for t in tags), 450)


class WrongVideoGuards(unittest.TestCase):
    """From the 7 Nov Nasdaq Short: an unbalanced script and a broken video that nothing stopped."""
    NASDAQ_HOOK = ("Why did the Nasdaq 100 drop? That's a basket of big stocks. NBC News says it had its worst day "
                   "since April 2025, falling about six point three percent and wiping out roughly two point three "
                   "trillion dollars.")

    def test_overlong_hook_flagged(self):
        from agent.scriptwriter import check_balance
        self.assertTrue(any("hook" in p for p in check_balance([{"id": 1, "type": "hook", "voiceover": self.NASDAQ_HOOK}])))

    def test_fragment_comparison_flagged(self):
        from agent.scriptwriter import check_balance
        bad = "Chips are ovens, data centers bakeries. Bakeries need ovens, so limit them, fewer sell."
        good = "Think of chips as ovens and data centers as bakeries, because every new bakery has to buy ovens."
        self.assertTrue(check_balance([{"id": 3, "type": "example", "voiceover": bad}]))
        self.assertEqual(check_balance([{"id": 3, "type": "example", "voiceover": good}]), [])

    def test_tiny_reality_check_flagged(self):
        from agent.scriptwriter import check_balance
        self.assertTrue(check_balance([{"id": 5, "type": "verify", "voiceover": "Whether the fear lasts is unknown."}]))

    def test_final_check_catches_squeezed_captions_and_unclear_voice(self):
        from agent.qa import caption_problems
        words = [{"word": "w", "start": i * 0.01, "end": i * 0.01 + 0.01} for i in range(10)]
        self.assertTrue(caption_problems({"clips": [{"scene_id": 1, "words": words}]}))
        fine = [{"word": "w", "start": i * 0.4, "end": i * 0.4 + 0.3} for i in range(10)]
        self.assertEqual(caption_problems({"clips": [{"scene_id": 1, "words": fine}]}), [])



class VoiceLanguage(unittest.TestCase):
    def test_spoken_line_wins_over_english(self):
        from agent.language import spoken
        self.assertEqual(spoken({"voiceover": "Hello.", "spoken": "வணக்கம்."}), "வணக்கம்.")
        self.assertEqual(spoken({"voiceover": "Hello."}), "Hello.")

    def test_english_clears_old_translation(self):
        from agent.language import translate
        script = {"language": "ta", "scenes": [{"id": 1, "voiceover": "Hi.", "spoken": "வணக்கம்."}]}
        out = translate(None, CFG, script, "en")
        self.assertEqual(out["language"], "en")
        self.assertNotIn("spoken", out["scenes"][0])

    def test_tamil_edge_voice_used(self):
        from unittest import mock
        from agent import tts
        seen = []
        script = {"language": "ta", "scenes": [{"id": 1, "voiceover": "Hi.", "spoken": "வணக்கம்."}]}
        with mock.patch.dict(tts.ENGINES, {"edge": lambda text, wav, cfg, speed: seen.append((text, cfg["tts"]["edge_voice"])) or (1.0, [])}):
            tts._synth_all(script, CFG, Path("/tmp"), "edge", 1.0)
        self.assertEqual(seen, [("வணக்கம்.", CFG["tts"]["edge_voice_ta"])])

    def test_tamil_text_gets_a_tamil_font(self):
        from PIL import ImageFont
        from agent.render import script_font
        latin = ImageFont.truetype(str(ROOT / "assets/fonts/Inter-Regular.otf"), 40)
        self.assertIs(script_font("Hello", latin), latin)
        if Path("/System/Library/Fonts/Supplemental/Tamil Sangam MN.ttc").exists():
            self.assertIn("Tamil", script_font("வணக்கம்", latin).getname()[0])


if __name__ == "__main__":
    unittest.main()
