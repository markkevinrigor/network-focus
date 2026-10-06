"""One test per finding from the Codex code review of 2026-10-06, so each fix stays fixed."""

import copy
import datetime as dt
import json
import os
import tempfile
import unittest

import support  # noqa: F401  (puts scripts/ on the path)
from support import ROWS, HEADERS, write_csv, write_goals, good_brief
import nf_read
import nf_checks
import nf_validate
import nf_render
import nf
from nf_errors import EXIT_OK, EXIT_BLOCKED, read_text

TODAY = dt.date(2026, 10, 6)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def packet_from(folder, roster_name):
    return nf_checks.build_packet(nf_read.load_roster(os.path.join(folder, roster_name)),
                                  nf_read.load_goals(os.path.join(folder, "goals.md")), TODAY)


def run_dir_for(packet, brief):
    d = tempfile.mkdtemp()
    rd = os.path.join(d, "run-files")
    os.makedirs(rd)
    write_json(os.path.join(rd, "packet.json"), dict(packet, run_dir=rd))
    write_json(os.path.join(rd, "brief.json"), brief)
    return rd


class ValidatorFindings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        write_goals(cls.tmp)
        write_csv(os.path.join(cls.tmp, "roster.csv"))
        cls.packet = packet_from(cls.tmp, "roster.csv")

    def brief(self):
        return copy.deepcopy(good_brief(self.packet["week_of"]))

    def test_mixed_first_and_last_name_is_rejected(self):
        b = self.brief()
        b["top_picks"][1]["move"]["action"] = "Send the deck and copy Avery Blake on the note."  # Avery Stone + Jordan Blake
        errors = nf_validate.check(b, self.packet)[0]
        self.assertTrue(any("Avery Blake" in e for e in errors), errors)

    def test_lone_unknown_name_is_warned(self):
        b = self.brief()
        b["top_picks"][1]["move"]["action"] = "Send the deck and ask whether Margaret should join the call."
        errors, warnings = nf_validate.check(b, self.packet)
        self.assertEqual(errors, [])
        self.assertTrue(any("Margaret" in w for w in warnings), warnings)

    def test_stale_reported_days_contradict_the_card(self):
        tmp = tempfile.mkdtemp()
        write_goals(tmp)
        write_csv(os.path.join(tmp, "roster-2026-09-20.csv"))
        packet = packet_from(tmp, "roster-2026-09-20.csv")
        b = copy.deepcopy(good_brief(packet["week_of"]))
        b["top_picks"][1]["why"] = "Partner who asked for the latest metrics deck 12 days ago."  # the card says 28
        self.assertTrue(any("12 days ago" in e for e in nf_validate.check(b, packet)[0]))

    def test_dormant_strength_one_needs_a_low_commitment_move(self):
        rows = [list(r) for r in ROWS if any(r)]
        rows.append(["Robin Hale", "Partner", "Harrow Capital", "investor", "70", "email",
                     "Long thread about the market and a promised follow-up on the raise timeline.", "1", "investor",
                     "Met twice and exchanged several notes about the Series B; she said to stay in touch."])
        tmp = tempfile.mkdtemp()
        write_goals(tmp)
        write_csv(os.path.join(tmp, "roster.csv"), rows=rows, headers=HEADERS)
        packet = packet_from(tmp, "roster.csv")
        robin = [c for c in packet["contacts"] if c["name"] == "Robin Hale"][0]
        self.assertFalse(robin["flags"]["thin_context"])
        b = copy.deepcopy(good_brief(packet["week_of"]))
        b["dormant"][1] = {"id": robin["id"], "goals": ["series-b"],
                           "why": "Harrow Capital is a goal firm and she said to stay in touch.",
                           "move": {"type": "call", "action": "Call her about the raise."},
                           "evidence": [{"field": "notes", "quote": "she said to stay in touch"}]}
        self.assertTrue(any("weak or thin" in e for e in nf_validate.check(b, packet)[0]))
        b["dormant"][1]["move"]["type"] = "light-touch"
        self.assertFalse(any("weak or thin" in e for e in nf_validate.check(b, packet)[0]))

    def test_render_refuses_a_packet_changed_after_validation(self):
        rd = run_dir_for(self.packet, self.brief())
        self.assertEqual(nf_validate.main(rd, lambda s: None), EXIT_OK)
        p = os.path.join(rd, "packet.json")
        pk = json.loads(read_text(p))
        pk["contacts"][0]["name"] = "Someone Else"
        write_json(p, pk)
        self.assertEqual(nf_render.main(rd, lambda s: None), EXIT_BLOCKED)

    def test_every_unresolved_issue_is_shown(self):
        rd = run_dir_for(self.packet, self.brief())
        nf_validate.main(rd, lambda s: None)
        nf_render.main(rd, lambda s: None, audit_unresolved=["First open issue.", "Second open issue."])
        week = os.path.dirname(rd)
        html_text = read_text(os.path.join(week, "network-focus-brief-%s.html" % self.packet["week_of"]))
        log = read_text(os.path.join(week, "run-log.md"))
        for issue in ("First open issue.", "Second open issue."):
            self.assertIn(issue, html_text)
            self.assertIn(issue, log)

    def test_uncovered_goal_is_shown_not_dropped(self):
        b = self.brief()
        b["uncovered_goals"] = [{"goal": "climate", "reason": "Nobody on file works in climate research yet."}]
        html_text = nf_render.build_html(b, self.packet, {"sha256": "0" * 12}, 9.5, [])
        self.assertIn("No one on file can serve the Climate board goal", html_text)


class PrepareAndDoctorFindings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        write_goals(self.tmp)

    def test_absolute_dates_are_not_adjusted_twice(self):
        headers = ["Name", "Company", "Days Since Last Contact", "Last Contact Date", "Relationship Strength (1-5)", "Tags",
                   "Last Contact Summary", "Notes"]
        rows = [["Avery Stone", "Birchwood Ventures", "", "2026-09-10", "4", "investor", ROWS[0][6], ROWS[0][9]]]
        write_csv(os.path.join(self.tmp, "roster-2026-09-20.csv"), rows=rows, headers=headers)
        packet = packet_from(self.tmp, "roster-2026-09-20.csv")
        self.assertEqual(packet["freshness"]["adjust_days"], 16)
        self.assertEqual(packet["contacts"][0]["days_since"], 26)  # 10 Sep to 6 Oct, not 26 + 16

    def test_rerun_moves_the_earlier_outputs_aside(self):
        write_csv(os.path.join(self.tmp, "roster.csv"))
        self.assertEqual(nf.main(["prepare", "--folder", self.tmp, "--today", "2026-10-06"]), EXIT_OK)
        week = os.path.join(self.tmp, "briefs", "2026-10-05")
        with open(os.path.join(week, "network-focus-brief-2026-10-05.pdf"), "w") as f:
            f.write("an earlier run")
        self.assertEqual(nf.main(["prepare", "--folder", self.tmp, "--today", "2026-10-06"]), EXIT_OK)
        self.assertFalse(os.path.exists(os.path.join(week, "network-focus-brief-2026-10-05.pdf")))
        earlier = os.listdir(os.path.join(week, "earlier-runs"))
        self.assertEqual(len(earlier), 1)
        self.assertTrue(os.path.exists(os.path.join(week, "earlier-runs", earlier[0], "network-focus-brief-2026-10-05.pdf")))

    def test_doctor_quick_does_not_block_without_a_browser(self):
        saved = nf_render.find_browser
        nf_render.find_browser = lambda: None
        try:
            self.assertEqual(nf.main(["doctor", "--quick"]), EXIT_OK)
        finally:
            nf_render.find_browser = saved

    def test_render_without_a_browser_still_makes_the_web_page(self):
        write_csv(os.path.join(self.tmp, "roster.csv"))
        packet = packet_from(self.tmp, "roster.csv")
        rd = run_dir_for(packet, good_brief(packet["week_of"]))
        saved = nf_render.find_browser
        nf_render.find_browser = lambda: None
        try:
            self.assertEqual(nf_validate.main(rd, lambda s: None), EXIT_OK)
            lines = []
            self.assertEqual(nf_render.main(rd, lines.append), EXIT_BLOCKED)
        finally:
            nf_render.find_browser = saved
        text = "\n".join(lines)
        self.assertIn("NF-06", text)
        self.assertIn("SUMMARY", text)
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(rd), "network-focus-brief-%s.html" % packet["week_of"])))


if __name__ == "__main__":
    unittest.main()
