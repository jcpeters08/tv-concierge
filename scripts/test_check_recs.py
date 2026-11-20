#!/usr/bin/env python3
"""Tests for the recommendations date lint.

Run with:
  python3 scripts/test_check_recs.py
"""

from __future__ import annotations

import datetime as dt
import unittest

import check_recs as cr

D = dt.date.fromisoformat


class WeekdayTests(unittest.TestCase):
    def test_flags_last_years_weekday_restamped_with_this_year(self) -> None:
        # Pluribus premiered Fri Nov 7, 2025; Nov 7, 2026 is a Saturday.
        hits = cr.weekday_mismatches("Episodes 1–2 dropped Fri Nov 7, 2026; weekly", 2026)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["actual"], "Sat")
        self.assertEqual(hits[0]["fits"], [2025])

    def test_correct_weekday_passes(self) -> None:
        self.assertEqual(cr.weekday_mismatches("Dropped today, Wed Nov 18, 2026", 2026), [])

    def test_yearless_date_uses_default_year(self) -> None:
        self.assertEqual(cr.weekday_mismatches("Ep 7 lands Fri Nov 20", 2026), [])
        self.assertEqual(len(cr.weekday_mismatches("Ep 7 lands Thu Nov 20", 2026)), 1)

    def test_long_weekday_names_and_ranges_without_weekdays(self) -> None:
        self.assertEqual(cr.weekday_mismatches("weekly Wednesdays Sep 16 – Oct 21", 2026), [])
        self.assertEqual(len(cr.weekday_mismatches("Thursday, Nov 13, 2026", 2026)), 1)


class TitleTests(unittest.TestCase):
    def test_strips_marketing_suffix(self) -> None:
        self.assertEqual(cr.base_title("Pluribus — New Ep 5 Live (Apple is dropping Ep 5 early)"),
                         ("Pluribus", None, None))

    def test_season_and_year(self) -> None:
        self.assertEqual(cr.base_title("The Rings of Power S3 — Now 2 Eps Deep"),
                         ("The Rings of Power", 3, None))
        self.assertEqual(cr.base_title("Silo S4 (Final Season) (LOVED LIST)"), ("Silo", 4, None))
        self.assertEqual(cr.base_title("They Will Kill You (2026)"), ("They Will Kill You", None, 2026))


class WeeklyClaimTests(unittest.TestCase):
    def test_weekly_wording(self) -> None:
        self.assertTrue(cr.claims_weekly({"title": "Pluribus — New Ep 5 Live"}))
        self.assertTrue(cr.claims_weekly({"title": "Stick S2 — Ep 4 'The Boog' (Wed Nov 18)"}))
        self.assertTrue(cr.claims_weekly({"title": "Nocturne", "format": "series — 10 eps, weekly"}))

    def test_complete_season_is_not_weekly(self) -> None:
        self.assertFalse(cr.claims_weekly({"title": "Cunk on Cinema — New on Netflix (3 eps)",
                                           "format": "series — 3 eps, complete"}))
        self.assertFalse(cr.claims_weekly({"title": "Slow Horses S6 — Complete Season, Binge-Ready"}))


class FreshnessTests(unittest.TestCase):
    AS_OF = D("2026-11-18")

    def test_year_old_season_is_stale(self) -> None:
        eps = [D("2025-11-07"), D("2025-11-14"), D("2025-12-24")]
        verdict = cr.freshness(eps, self.AS_OF, claims_weekly=True)
        self.assertEqual(verdict["status"], "stale")
        self.assertEqual(verdict["last"], D("2025-12-24"))

    def test_recently_finished_season_is_fresh(self) -> None:
        # Slow Horses S6 finale Oct 21 — binge-ready, still current.
        eps = [D("2026-09-16"), D("2026-10-21")]
        self.assertEqual(cr.freshness(eps, self.AS_OF, claims_weekly=False)["status"], "ok")

    def test_weekly_claim_on_finished_season(self) -> None:
        # Chad Powers S2 finished Sep 3 but the card said "weekly, mid-run".
        eps = [D("2026-08-06"), D("2026-09-03")]
        self.assertEqual(cr.freshness(eps, self.AS_OF, claims_weekly=True)["status"], "stale")

    def test_weekly_claim_on_season_that_just_ended(self) -> None:
        eps = [D("2026-10-01"), D("2026-11-05")]
        self.assertEqual(cr.freshness(eps, self.AS_OF, claims_weekly=True)["status"], "finished")

    def test_airing_weekly_is_ok(self) -> None:
        eps = [D("2026-10-30"), D("2026-11-13"), D("2026-11-20"), D("2026-12-25")]
        self.assertEqual(cr.freshness(eps, self.AS_OF, claims_weekly=True)["status"], "ok")

    def test_premiere_next_week_is_ok(self) -> None:
        eps = [D("2026-11-20"), D("2026-11-27")]
        self.assertEqual(cr.freshness(eps, self.AS_OF, claims_weekly=False)["status"], "ok")


if __name__ == "__main__":
    unittest.main()
