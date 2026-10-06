import copy
import datetime as dt
import json
import os
import tempfile
import unittest

import support  # noqa: F401
from support import write_csv, write_goals, good_brief
import nf_read
import nf_checks
import nf_validate
import nf_render
from nf_errors import EXIT_OK, EXIT_CHECK_FAILED, EXIT_BLOCKED, read_text


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)

TODAY = dt.date(2026, 10, 6)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        write_goals(cls.tmp)
        write_csv(os.path.join(cls.tmp, "roster.csv"))
        goals = nf_read.load_goals(os.path.join(cls.tmp, "goals.md"))
        roster = nf_read.load_roster(os.path.join(cls.tmp, "roster.csv"))
        cls.packet = nf_checks.build_packet(roster, goals, TODAY)

    def errors(self, brief):
        return nf_validate.check(brief, self.packet)[0]

    def brief(self):
        return copy.deepcopy(good_brief(self.packet["week_of"]))


class ValidateTests(Base):
    def test_good_brief_passes(self):
        errors, warnings = nf_validate.check(self.brief(), self.packet)
        self.assertEqual(errors, [])

    def test_fabricated_quote_is_rejected(self):
        b = self.brief()
        b["top_picks"][1]["evidence"] = [{"field": "notes", "quote": "committed to lead the round"}]
        self.assertTrue(any("not in any single field" in e for e in self.errors(b)))

    def test_quote_spanning_two_fields_is_rejected(self):
        b = self.brief()
        b["top_picks"][1]["evidence"] = [{"field": "summary", "quote": "after a warm call. Intro from Jordan"}]
        self.assertTrue(any("not in any single field" in e for e in self.errors(b)))

    def test_quote_normalisation_and_ellipsis(self):
        b = self.brief()
        b["top_picks"][1]["evidence"] = [{"field": "summary", "quote": "“ASKED for the latest ... after a warm call.”"}]
        self.assertEqual(self.errors(b), [])

    def test_invented_person_is_rejected(self):
        b = self.brief()
        b["top_picks"][0]["move"]["action"] = "Ask Margaret Thornton for two intros by Friday."
        self.assertTrue(any("Margaret Thornton" in e for e in self.errors(b)))

    def test_wrong_day_count_is_rejected(self):
        b = self.brief()
        b["top_picks"][1]["why"] = "Partner who asked for the latest metrics deck 40 days ago."
        self.assertTrue(any("40 days ago" in e for e in self.errors(b)))

    def test_qualified_day_count_is_allowed(self):
        b = self.brief()
        b["dormant"][0]["why"] = "Good Series B fit, quiet for 60+ days since the seed."
        self.assertEqual(self.errors(b), [])

    def test_timing_hold_blocks_a_pitch(self):
        b = self.brief()
        b["top_picks"][2]["move"]["type"] = "email"
        self.assertTrue(any("asked for timing" in e for e in self.errors(b)))

    def test_weak_tie_needs_a_low_commitment_move(self):
        b = self.brief()
        thin = [c for c in self.packet["contacts"] if c["name"] == "Taylor Brooks"][0]["id"]
        b["top_picks"][3] = {"id": thin, "goals": ["series-b"], "why": "A famous CEO who could open doors for the round.",
                             "move": {"type": "email", "action": "Email to ask for an intro to an investor."},
                             "risk": "A missed chance at a big name.", "evidence": [{"field": "summary", "quote": "Shook hands at a conference"}]}
        errs = self.errors(b)
        self.assertTrue(any("weak or thin" in e for e in errs))

    def test_personal_contact_is_rejected(self):
        b = self.brief()
        sam = [c for c in self.packet["contacts"] if c["name"] == "Sam Ortiz"][0]["id"]
        b["holding"] = []
        b["top_picks"].append({"id": sam, "goals": ["series-b"], "why": "Close friend.",
                               "move": {"type": "call", "action": "Call this weekend."}, "risk": "Drift.",
                               "evidence": [{"field": "notes", "quote": "Old friend from school"}]})
        self.assertTrue(any("personal relationship" in e for e in self.errors(b)))

    def test_dormant_needs_sixty_days(self):
        b = self.brief()
        b["dormant"][1]["id"] = [c for c in self.packet["contacts"] if c["name"] == "Quinn Rivera"][0]["id"]
        b["dormant"][1]["evidence"] = [{"field": "notes", "quote": "Lukewarm but open"}]
        self.assertTrue(any("dormant needs 60+" in e for e in self.errors(b)))

    def test_person_twice_is_rejected(self):
        b = self.brief()
        b["holding"] = [{"id": b["top_picks"][0]["id"], "reason": "Waiting."}]
        self.assertTrue(any("already in top_picks" in e for e in self.errors(b)))

    def test_goal_coverage(self):
        b = self.brief()
        b["top_picks"] = [p for p in b["top_picks"] if "climate" not in p["goals"]]
        b["dormant"] = [d for d in b["dormant"] if "climate" not in d["goals"]]
        b["dormant"].append({"id": "C04", "goals": ["series-b"], "why": "Founder met at a panel.",
                             "move": {"type": "email", "action": "Send the follow-up she promised."},
                             "evidence": [{"field": "summary", "quote": "Met at a panel on grid data"}]})
        self.assertTrue(any("goal 'climate'" in e for e in self.errors(b)))

    def test_questions_required_when_checks_ask(self):
        b = self.brief()
        b["ceo_questions"] = []
        self.assertTrue(any("ceo_questions" in e for e in self.errors(b)))

    def test_length_cap(self):
        b = self.brief()
        b["top_picks"][0]["why"] = "word " * 60
        self.assertTrue(any("the limit is" in e for e in self.errors(b)))


class RunTests(Base):
    def run_dir(self, brief_obj=None, raw=None):
        d = tempfile.mkdtemp()
        rd = os.path.join(d, "run-files")
        os.makedirs(rd)
        pk = dict(self.packet, run_dir=rd)
        write_json(os.path.join(rd, "packet.json"), pk)
        with open(os.path.join(rd, "brief.json"), "w", encoding="utf-8") as f:
            f.write(raw if raw is not None else json.dumps(brief_obj or self.brief()))
        return rd

    def test_invalid_json_fails_check(self):
        rd = self.run_dir(raw="Here is the brief: {")
        self.assertEqual(nf_validate.main(rd, lambda s: None), EXIT_CHECK_FAILED)

    def test_render_refuses_a_brief_changed_after_validation(self):
        rd = self.run_dir()
        self.assertEqual(nf_validate.main(rd, lambda s: None), EXIT_OK)
        b = self.brief()
        b["headline"] = "Edited after the check."
        write_json(os.path.join(rd, "brief.json"), b)
        self.assertEqual(nf_render.main(rd, lambda s: None), EXIT_BLOCKED)

    def test_render_html_and_one_page_pdf(self):
        rd = self.run_dir()
        self.assertEqual(nf_validate.main(rd, lambda s: None), EXIT_OK)
        code = nf_render.main(rd, lambda s: None)
        week = os.path.dirname(rd)
        html_path = os.path.join(week, "network-focus-brief-%s.html" % self.packet["week_of"])
        self.assertTrue(os.path.isfile(html_path))
        html_text = read_text(html_path)
        self.assertIn("Drew Patel", html_text)          # name printed from the row id
        self.assertIn("Head of Talent, Kestrel Capital", html_text)
        self.assertTrue(os.path.isfile(os.path.join(week, "run-log.md")))
        if nf_render.find_browser():
            self.assertEqual(code, EXIT_OK)
            pdf = os.path.join(week, "network-focus-brief-%s.pdf" % self.packet["week_of"])
            self.assertEqual(nf_render.count_pages(pdf), 1)
        else:
            self.assertEqual(code, EXIT_BLOCKED)  # NF-06, HTML still written

    def worst_case(self, scale=1.0):
        """Every section full and every field at its character cap (times scale), plus the review banner."""
        L = {k: int(v * scale) for k, v in nf_validate.LIMITS.items()}
        words = "Offered to share three sales leader profiles and is still waiting for a reply from the whole team "

        def fill(n):
            return (words * 6)[:n]
        b = self.brief()
        b["headline"] = fill(L["headline"])
        for t in b["top_picks"]:
            t["why"], t["move"]["action"], t["risk"] = fill(L["why"]), fill(L["action"]), fill(L["risk"])
        extra = copy.deepcopy(b["top_picks"][0])
        extra.update(id="C02", goals=["vp-sales"], evidence=[{"field": "summary", "quote": "Compared notes on hiring"}])
        b["top_picks"].append(extra)
        for d in b["dormant"]:
            d["why"], d["move"]["action"] = fill(L["dormant_why"]), fill(L["dormant_action"])
        extra = copy.deepcopy(b["dormant"][0])
        extra.update(id="C04", goals=["climate"], evidence=[{"field": "summary", "quote": "Met at a panel on grid data"}])
        b["dormant"].append(extra)
        b["holding"] = [{"id": "C10", "reason": fill(L["reason"])}, {"id": "C08", "reason": fill(L["reason"])}]
        b["ceo_questions"] = [fill(L["question"])] * 3
        return b

    @unittest.skipUnless(nf_render.find_browser(), "needs Chrome or Edge")
    def test_a_brief_at_every_cap_still_prints_on_one_page(self):
        b = self.worst_case()
        self.assertEqual(self.errors(b), [])
        d = tempfile.mkdtemp()
        html_path, pdf_path = os.path.join(d, "w.html"), os.path.join(d, "w.pdf")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(nf_render.build_html(b, self.packet, {"sha256": "0" * 12}, 9.0, "An unresolved reviewer issue."))
        self.assertTrue(nf_render.print_pdf(nf_render.find_browser(), html_path, pdf_path))
        self.assertEqual(nf_render.count_pages(pdf_path), 1)

    @unittest.skipUnless(nf_render.find_browser(), "needs Chrome or Edge")
    def test_page_fit_is_part_of_the_fact_check(self):
        b = self.worst_case(scale=1.6)  # over every cap: fails on length and, separately, on one page
        nf_validate.LIMITS, saved = {k: 10 ** 4 for k in nf_validate.LIMITS}, nf_validate.LIMITS
        try:
            rd = self.run_dir(b)
            self.assertEqual(nf_validate.main(rd, lambda s: None), EXIT_CHECK_FAILED)
            self.assertIn("TOO LONG", read_text(os.path.join(rd, "validation.json")))
        finally:
            nf_validate.LIMITS = saved

    def test_banner_when_review_unresolved(self):
        rd = self.run_dir()
        nf_validate.main(rd, lambda s: None)
        nf_render.main(rd, lambda s: None, audit_unresolved="Check the second pick's timing.")
        html_text = read_text(os.path.join(os.path.dirname(rd), "network-focus-brief-%s.html" % self.packet["week_of"]))
        self.assertIn("Needs a human check", html_text)


if __name__ == "__main__":
    unittest.main()
