import datetime as dt
import os
import tempfile
import unittest

import support  # noqa: F401  (puts scripts/ on the path)
from support import ROWS, HEADERS, write_csv, write_xlsx, write_goals
import nf_read
import nf_checks
from nf_errors import NFError

TODAY = dt.date(2026, 10, 6)


def packet_for(folder, roster_name="roster.csv", **kw):
    goals = nf_read.load_goals(os.path.join(folder, "goals.md"))
    roster = nf_read.load_roster(nf_read.find_roster(folder, roster_name))
    return nf_checks.build_packet(roster, goals, TODAY, **kw)


def by_name(packet, name, company=None):
    return [c for c in packet["contacts"] if c["name"] == name and (company is None or c["company"] == company)][0]


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        write_goals(self.tmp)

    def test_csv_columns_and_rows(self):
        write_csv(os.path.join(self.tmp, "roster.csv"))
        r = nf_read.load_roster(os.path.join(self.tmp, "roster.csv"))
        self.assertEqual(len(r["records"]), 11)  # the blank row is dropped
        self.assertEqual(r["columns_missing"], [])
        self.assertEqual(r["records"][0]["name"], "Avery Stone")

    def test_semicolon_and_cp1252_csv(self):
        rows = [list(ROWS[0])]
        rows[0][9] = "Café meeting planned"
        write_csv(os.path.join(self.tmp, "roster.csv"), rows=rows, delimiter=";", encoding="cp1252")
        r = nf_read.load_roster(os.path.join(self.tmp, "roster.csv"))
        self.assertEqual(r["records"][0]["notes"], "Café meeting planned")

    def test_xlsx_shared_and_inline_strings_match(self):
        a = nf_read.load_roster(write_xlsx(os.path.join(self.tmp, "a.xlsx"), shared=True))
        b = nf_read.load_roster(write_xlsx(os.path.join(self.tmp, "b.xlsx"), shared=False, numeric_days_as_float=True))
        strip = lambda recs: [{k: v for k, v in x.items()} for x in recs]  # noqa: E731
        self.assertEqual(strip(a["records"]), strip(b["records"]))
        self.assertEqual(a["records"][0]["days_since"], "12")
        self.assertEqual(b["records"][0]["days_since"], "12")  # 12.0 read back as 12
        self.assertEqual(a["saved"], dt.date(2026, 7, 3))

    def test_sparse_cells_keep_columns_aligned(self):
        rows = [list(ROWS[0])]
        rows[0][1] = ""  # empty Role cell is omitted from the XML
        r = nf_read.load_roster(write_xlsx(os.path.join(self.tmp, "s.xlsx"), rows=rows))
        self.assertEqual(r["records"][0]["role"], "")
        self.assertEqual(r["records"][0]["company"], "Birchwood Ventures")

    def test_missing_required_column(self):
        headers = [h for h in HEADERS if h != "Days Since Last Contact"]
        rows = [[v for i, v in enumerate(r) if i != 4] for r in ROWS]
        write_csv(os.path.join(self.tmp, "roster.csv"), rows=rows, headers=headers)
        with self.assertRaises(NFError) as e:
            nf_read.load_roster(os.path.join(self.tmp, "roster.csv"))
        self.assertEqual(e.exception.code, "NF-03")
        self.assertIn("Days Since Last Contact", e.exception.message)

    def test_header_aliases(self):
        headers = ["Full Name", "Title", "Organization", "Relationship", "Days Since Contact", "Channel", "Summary",
                   "Strength", "Labels", "Comments"]
        write_csv(os.path.join(self.tmp, "roster.csv"), headers=headers)
        r = nf_read.load_roster(os.path.join(self.tmp, "roster.csv"))
        self.assertEqual(r["columns_missing"], [])

    def test_find_roster_skips_lock_files_and_picks_newest(self):
        old = write_csv(os.path.join(self.tmp, "old.csv"))
        os.utime(old, (1, 1))
        write_csv(os.path.join(self.tmp, "new.csv"))
        open(os.path.join(self.tmp, "~$new.xlsx"), "w").close()
        self.assertTrue(nf_read.find_roster(self.tmp).endswith("new.csv"))

    def test_no_roster_and_unsupported_format(self):
        with self.assertRaises(NFError) as e:
            nf_read.find_roster(self.tmp)
        self.assertEqual(e.exception.code, "NF-01")
        open(os.path.join(self.tmp, "roster.numbers"), "w").close()
        with self.assertRaises(NFError) as e:
            nf_read.find_roster(self.tmp)
        self.assertEqual(e.exception.code, "NF-02")

    def test_goals_parse(self):
        g = nf_read.load_goals(os.path.join(self.tmp, "goals.md"))
        self.assertEqual([x["id"] for x in g["goals"]], ["series-b", "vp-sales", "climate"])
        self.assertEqual(g["goals"][0]["known_orgs"][0]["aliases"], ["Birchwood Ventures", "Birchwood"])
        self.assertEqual(g["goals"][2]["wanted_orgs"][1]["tags"], ["academic"])

    def test_blank_goals_template_is_rejected(self):
        tpl = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "goals.md")
        with self.assertRaises(NFError) as e:
            nf_read.load_goals(tpl)
        self.assertEqual(e.exception.code, "NF-05")


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        write_goals(self.tmp)
        write_csv(os.path.join(self.tmp, "roster.csv"))

    def test_flags(self):
        p = packet_for(self.tmp)
        self.assertTrue(by_name(p, "Taylor Brooks")["flags"]["thin_context"])
        self.assertIn("revisit in a month", by_name(p, "Morgan Lee", "Tidewell")["flags"]["timing_hold"])
        self.assertTrue(by_name(p, "Sam Ortiz")["flags"]["personal"])
        self.assertTrue(by_name(p, "Morgan Lee", "Quillstone")["flags"]["duplicate_name"])
        self.assertTrue(by_name(p, "Quinn Rivera")["flags"]["near_dormant"])
        self.assertTrue(by_name(p, "Casey Park")["flags"]["dormant"])
        self.assertEqual(by_name(p, "Drew Patel")["goal_ids"], ["series-b", "vp-sales"])
        self.assertNotIn(by_name(p, "Taylor Brooks")["id"], p["eligible"]["dormant"])

    def test_goal_org_checks(self):
        p = packet_for(self.tmp)
        kinds = [(c["kind"], c["message"]) for c in p["checks"]]
        self.assertTrue(any(k == "goal-org-missing" and "Harrow Capital" in m for k, m in kinds))
        self.assertTrue(any(k == "goal-org-found" and "Riley Chen" in m for k, m in kinds))
        self.assertFalse(any(k == "goal-org-missing" and "Birchwood" in m for k, m in kinds))

    def test_unresolved_reference(self):
        p = packet_for(self.tmp)
        refs = [c for c in p["checks"] if c["kind"] == "unresolved-reference"]
        self.assertEqual(len(refs), 1)
        self.assertIn("Jordan at Kestrel Capital", refs[0]["message"])
        self.assertIn("Jordan Blake", refs[0]["message"])
        self.assertIn("Drew Patel", refs[0]["message"])

    def test_no_reference_false_positives(self):
        p = packet_for(self.tmp)
        # "Met at a panel", "Coffee chat", "Shook hands at a conference" must not be read as people.
        refs = [c for c in p["checks"] if c["kind"] == "unresolved-reference"]
        self.assertTrue(all("Jordan" in c["message"] for c in refs))

    def test_weak_freshness_never_adjusts(self):
        write_xlsx(os.path.join(self.tmp, "roster.xlsx"), modified="2026-07-03T08:30:52Z")
        p = packet_for(self.tmp, "roster.xlsx")
        self.assertEqual(p["freshness"]["status"], "unconfirmed")
        self.assertEqual(by_name(p, "Avery Stone")["days_since"], 12)

    def test_dated_filename_adjusts_days(self):
        write_csv(os.path.join(self.tmp, "roster-2026-09-20.csv"))
        p = packet_for(self.tmp, "roster-2026-09-20.csv")
        self.assertEqual(p["freshness"]["adjust_days"], 16)
        self.assertEqual(by_name(p, "Quinn Rivera")["days_since"], 73)
        self.assertTrue(by_name(p, "Quinn Rivera")["flags"]["dormant"])

    def test_old_dated_roster_blocks_unless_use_as_is(self):
        write_csv(os.path.join(self.tmp, "roster-2026-07-01.csv"))
        with self.assertRaises(NFError) as e:
            packet_for(self.tmp, "roster-2026-07-01.csv")
        self.assertEqual(e.exception.code, "NF-04")
        p = packet_for(self.tmp, "roster-2026-07-01.csv", use_as_is=True)
        self.assertEqual(p["freshness"]["adjust_days"], 0)

    def test_as_of_argument_wins(self):
        p = packet_for(self.tmp, as_of=dt.date(2026, 10, 1))
        self.assertEqual(p["freshness"]["adjust_days"], 5)

    def test_week_of_is_monday(self):
        self.assertEqual(packet_for(self.tmp)["week_of"], "2026-10-05")


if __name__ == "__main__":
    unittest.main()
