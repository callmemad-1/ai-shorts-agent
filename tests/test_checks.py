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
from agent.scriptwriter import check_speakability, check_structure, spoken_numbers  # noqa: E402
from agent.social import check as social_check, names_site  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
