"""Offline checks of the new three-part discovery module. No real API calls."""
import datetime as dt
import unittest

from research_split import fetch_research_split


class TestSplitDiscovery(unittest.TestCase):
    def setUp(self):
        self.issue = dt.date(2026, 11, 4)

    def test_three_sections_merged(self):
        seen = []
        def fake(section, prompt):
            seen.append(section)
            self.assertIn("2026-11-03T06:00:00+09:00", prompt) if section != 'events' else None
            return {
                "domestic": {"domestic_industry": [{"title": "sample"}], "domestic_supplement": []},
                "overseas": {"overseas": [{"title": "sample2"}]},
                "events": {"events": [{"name": "event"}]},
            }[section]
        output = fetch_research_split(self.issue, query_fn=fake)
        self.assertEqual(seen, ["domestic", "overseas", "events"])
        self.assertEqual(len(output["domestic_industry"]), 1)
        self.assertEqual(len(output["overseas"]), 1)
        self.assertEqual(len(output["events"]), 1)

    def test_one_section_failure_keeps_others(self):
        def fake(section, prompt):
            if section == "overseas":
                raise ValueError("invalid JSON")
            return {"domestic": {"domestic_industry": [], "domestic_supplement": []},
                    "events": {"events": []}}[section]
        output = fetch_research_split(self.issue, query_fn=fake)
        self.assertEqual(output["overseas"], [])

    def test_all_fail_stops(self):
        def fake(section, prompt):
            raise ValueError("bad response")
        with self.assertRaisesRegex(RuntimeError, "All three searches failed"):
            fetch_research_split(self.issue, query_fn=fake)

    def test_monday_72_hours(self):
        def fake(section, prompt):
            if section == "domestic":
                self.assertIn("2026-11-06T06:00:00+09:00", prompt)
            return {}
        fetch_research_split(dt.date(2026, 11, 9), query_fn=fake)


if __name__ == "__main__":
    unittest.main()

