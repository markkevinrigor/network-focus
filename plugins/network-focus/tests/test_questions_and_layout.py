"""v0.1.2: questions for the CEO are chosen in code, and the page layout. Synthetic data only.

In rehearsal the strategist spent a question slot on data freshness (already in the header) and a goal/roster
contradiction never reached the page. These tests pin the rules that stop that.
"""

import copy
import datetime as dt
import os
import tempfile
import unittest

import support  # noqa: F401
from support import write_csv, write_goals, write_xlsx, good_brief
import nf_read
import nf_checks
import nf_validate
import nf_render
from nf_errors import EXIT_OK, read_text

TODAY = dt.date(2026, 10, 6)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        write_goals(cls.tmp)
        write_csv(os.path.join(cls.tmp, "roster.csv"))
        cls.goals = nf_read.load_goals(os.path.join(cls.tmp, "goals.md"))
        roster = nf_read.load_roster(os.path.join(cls.tmp, "roster.csv"))
        cls.packet = nf_checks.build_packet(roster, cls.goals, TODAY)

    def brief(self):
        return copy.deepcopy(good_brief(self.packet["week_of"]))

    def check(self, brief, packet=None):
        return nf_validate.check(brief, packet or self.packet)


class QuestionTests(Base):
    def test_related_checks_become_one_group_and_goal_problems_come_first(self):
        groups = self.packet["question_groups"]
        self.assertEqual([g["id"] for g in groups],
                         ["missing-org:series-b", "contradiction:climate", "who:jordan-kestrel-capital", "duplicate:morgan-lee"])
        self.assertEqual(len(groups[1]["messages"]), 2)  # Climate Lab and "university climate groups": one question

    def test_freshness_is_never_a_question_for_the_ceo(self):
        d = tempfile.mkdtemp()
        write_goals(d)
        write_xlsx(os.path.join(d, "roster.xlsx"), modified="2026-07-03T08:30:52Z")
        packet = nf_checks.build_packet(nf_read.load_roster(os.path.join(d, "roster.xlsx")), self.goals, TODAY)
        fresh = [c for c in packet["checks"] if c["kind"] == "freshness"][0]
        self.assertEqual(packet["freshness"]["status"], "unconfirmed")
        self.assertEqual((fresh["severity"], fresh["ask"]), ("info", None))
        self.assertIn("as-of=", fresh["operator_action"])
        self.assertFalse(any("fresh" in g["id"] for g in packet["question_groups"]))

    def test_the_top_three_groups_must_be_asked(self):
        b = self.brief()
        del b["ceo_questions"][1]  # the goal/roster contradiction, as in the rehearsal
        errors = self.check(b)[0]
        self.assertTrue(any("contradiction:climate" in e and "must be asked" in e for e in errors), errors)

    def test_a_slot_spent_on_something_else_cannot_cover_a_group(self):
        b = self.brief()
        b["ceo_questions"][1] = {"question": "When was this roster exported?", "covers": ["freshness"]}
        errors = self.check(b)[0]
        self.assertTrue(any("'freshness'" in e for e in errors))
        self.assertTrue(any("contradiction:climate" in e for e in errors))

    def test_one_question_may_cover_two_related_groups(self):
        b = self.brief()
        b["ceo_questions"] = [
            {"question": "Is Harrow Capital still a warm path, and are the goals out of date now Riley Chen at Climate Lab is on file?",
             "covers": ["missing-org:series-b", "contradiction:climate"]},
            b["ceo_questions"][2]]
        self.assertEqual(self.check(b)[0], [])

    def test_malformed_questions_are_errors(self):
        for bad in ["Who is your Harrow Capital contact?",
                    {"question": "Who is your Harrow Capital contact?"},
                    {"question": "Who is your Harrow Capital contact?", "covers": []},
                    {"question": "Who is your Harrow Capital contact?", "covers": ["missing-org:series-b", "missing-org:series-b"]},
                    {"question": "Who is your Harrow Capital contact?", "covers": ["missing-org:nope"]}]:
            b = self.brief()
            b["ceo_questions"][0] = bad
            self.assertTrue(any(e.startswith("ceo_questions[1]") for e in self.check(b)[0]), bad)

    def test_no_questions_when_no_data_problem_needs_the_ceo(self):
        packet = dict(self.packet, question_groups=[])
        self.assertTrue(any("leave ceo_questions empty" in e for e in self.check(self.brief(), packet)[0]))
        b = self.brief()
        b["ceo_questions"] = []
        self.assertFalse(any("ceo_questions" in e for e in self.check(b, packet)[0]))

    def test_a_question_that_never_names_its_problem_is_a_warning_not_an_error(self):
        b = self.brief()
        b["ceo_questions"][0]["question"] = "Who is the contact for this one? Nobody from there is in the roster."
        errors, warnings = self.check(b)
        self.assertEqual(errors, [])
        self.assertTrue(any("missing-org:series-b" in w and "Harrow" in w for w in warnings))

    def test_anchors_accept_aliases_and_the_matched_roster_company(self):
        self.assertTrue(nf_validate.has_anchor("Is ARC's work relevant?", "ARC"))
        self.assertFalse(nf_validate.has_anchor("Search the roster again?", "ARC"))
        contradiction = self.packet["question_groups"][1]
        self.assertIn("Climate Lab", contradiction["anchors"][0])
        self.assertIn("academic", contradiction["anchors"][1])  # the goal's tag, matched by a researcher's row

    def test_ambiguous_names_never_push_a_goal_contradiction_off_the_page(self):
        checks = [{"kind": "unresolved-reference", "severity": "ask", "person": p, "org": "Kestrel", "message": p, "ask": "Who?",
                   "anchors": [p]} for p in ("Ana", "Ben", "Cy", "Dee")]
        checks.append({"kind": "goal-org-found", "severity": "ask", "goal": "climate", "message": "Contradiction.",
                       "ask": "Out of date?", "anchors": ["Climate Lab"]})
        groups = nf_checks.question_groups(checks, self.goals["goals"])
        self.assertEqual(groups[0]["id"], "contradiction:climate")
        self.assertEqual(len(groups), 5)


class LayoutTests(Base):
    def render(self, brief, packet=None, **kw):
        return nf_render.build_html(brief, packet or self.packet, {"sha256": "ab" * 32}, 9.5, kw.pop("unresolved", []), **kw)

    def test_left_over_groups_are_counted_on_the_page_and_listed_in_the_run_log(self):
        html = self.render(self.brief())
        self.assertIn("+1 more data question in run-log.md", html)
        d = tempfile.mkdtemp()
        rd = os.path.join(d, "run-files")
        os.makedirs(rd)
        log = os.path.join(d, "run-log.md")
        nf_render.write_run_log(log, self.brief(), dict(self.packet, run_dir=rd), {"warnings": [], "sha256": "ab" * 32},
                                None, [log], [])
        text = read_text(log)
        self.assertIn("answers [contradiction:climate]", text)
        self.assertIn("Not on the page (lower priority): [duplicate:morgan-lee]", text)

    def test_goal_strip_names_come_from_the_roster(self):
        html = self.render(self.brief())
        self.assertIn('<div class="gl">VP Sales</div><div class="gp"><b>1</b> Drew Patel, <b>3</b> Morgan Lee</div>', html)
        self.assertIn("1 question for you", html)

    def test_goal_tile_counts_questions_not_groups(self):
        packet = copy.deepcopy(self.packet)
        for g in packet["question_groups"]:
            if g["id"] == "contradiction:climate":
                g["goal"] = "series-b"  # two problems for one goal...
        b = self.brief()
        b["ceo_questions"] = [{"question": "Harrow Capital and Climate Lab: are the goals out of date?",
                               "covers": ["missing-org:series-b", "contradiction:climate"]}]  # ...asked as one question
        html = self.render(b, packet)
        self.assertIn("1 question for you", html)
        self.assertNotIn("2 questions for you", html)

    def test_goal_with_nobody_eligible_says_so(self):
        packet = copy.deepcopy(self.packet)
        packet["goals"].append({"id": "board", "title": "Add a director", "label": "Board", "what": "x", "tags": ["board"],
                                "known_orgs": [], "wanted_orgs": []})
        b = self.brief()
        b["uncovered_goals"] = [{"goal": "board", "reason": "No one on file sits on boards."}]
        html = self.render(b, packet)
        self.assertIn("No eligible contact this week", html)
        self.assertIn("No one on file can serve the Board goal", html)

    def test_footer_shows_version_fingerprint_and_reviewer(self):
        html = self.render(self.brief(), reviewer="pass")
        self.assertIn("Network Focus v%s" % nf_render.plugin_version(), html)
        self.assertIn("brief fingerprint abababababab", html)
        self.assertIn("reviewer: pass", html)
        self.assertNotIn("every name, number and claim", html)

    def test_reviewer_line(self):
        self.assertEqual(nf_render.reviewer_line({"verdict": "pass"}, []), "pass")
        self.assertEqual(nf_render.reviewer_line({"verdict": "revise"}, []), "must-fix issues addressed in one revision")
        self.assertEqual(nf_render.reviewer_line({"verdict": "revise"}, ["a", "b"]), "2 issues open (see banner)")
        self.assertEqual(nf_render.reviewer_line(None, []), "not run")

    def test_compact_layout_drops_only_the_goal_strip(self):
        normal = self.render(self.brief())
        compact = self.render(self.brief(), compact=True)
        self.assertIn('<body class="compact">', compact)
        self.assertIn('<body class="">', normal)
        for name in ("Drew Patel", "Casey Park", "Harrow Capital"):
            self.assertIn(name, compact)

    @unittest.skipUnless(nf_render.find_browser(), "needs Chrome or Edge")
    def test_render_uses_the_largest_layout_that_fits(self):
        d = tempfile.mkdtemp()
        rd = os.path.join(d, "run-files")
        os.makedirs(rd)
        import json
        with open(os.path.join(rd, "packet.json"), "w", encoding="utf-8") as f:
            json.dump(dict(self.packet, run_dir=rd), f)
        with open(os.path.join(rd, "brief.json"), "w", encoding="utf-8") as f:
            json.dump(self.brief(), f)
        self.assertEqual(nf_validate.main(rd, lambda s: None), EXIT_OK)
        self.assertEqual(nf_render.main(rd, lambda s: None), EXIT_OK)
        html = read_text(os.path.join(d, "network-focus-brief-%s.html" % self.packet["week_of"]))
        self.assertIn("font: 10pt/", html)  # a short brief gets the roomiest layout


if __name__ == "__main__":
    unittest.main()
