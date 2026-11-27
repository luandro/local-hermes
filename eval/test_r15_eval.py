#!/usr/bin/env python3
"""Unit tests for R1.5 shadow veto logic + data validator (stdlib only, no probes).

Run: python3 -m unittest discover -s eval -p 'test_r15_eval.py'
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import r15_eval as r15


class VetoTests(unittest.TestCase):
    def test_high_safe_score_keeps_w1(self):
        # p_safe above cutoff -> keep W1's block
        self.assertEqual(r15.veto("disk", 0.995, 0.99), "disk")

    def test_cutoff_inclusive_keeps_w1(self):
        # p_safe == cutoff -> keep (inclusive)
        self.assertEqual(r15.veto("disk", 0.99, 0.99), "disk")

    def test_low_score_vetoes(self):
        # p_safe below cutoff -> fall through (veto)
        self.assertIsNone(r15.veto("disk", 0.5, 0.99))

    def test_none_score_falls_through(self):
        self.assertIsNone(r15.veto("disk", None, 0.99))

    def test_nan_score_falls_through(self):
        self.assertIsNone(r15.veto("disk", float("nan"), 0.99))

    def test_inf_score_falls_through(self):
        self.assertIsNone(r15.veto("disk", float("inf"), 0.99))

    def test_w1_abstain_never_promoted(self):
        # W1 returned None -> classifier can NEVER add an interception
        self.assertIsNone(r15.veto(None, 0.999, 0.99))

    def test_high_score_cannot_promote_abstention(self):
        # even p_safe=1.0 with W1 None -> None (the key safety property)
        self.assertIsNone(r15.veto(None, 1.0, 0.99))


class DataValidatorTests(unittest.TestCase):
    def _write(self, rows):
        fd, p = tempfile.mkstemp(suffix=".jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return Path(p)

    def test_valid_loads(self):
        p = self._write([
            {"id": "a", "split": "train", "lang": "en", "text": "how much disk space?",
             "expected_route": "disk", "group": "g1"},
            {"id": "b", "split": "test", "lang": "pt", "text": "limpar o disco",
             "expected_route": None, "group": "g2"},
        ])
        self.assertEqual(len(r15.load_cases(p)), 2)

    def test_bad_split_rejected(self):
        p = self._write([{"id": "a", "split": "bogus", "lang": "en",
                          "text": "x", "expected_route": None}])
        with self.assertRaises(ValueError):
            r15.load_cases(p)

    def test_bad_route_rejected(self):
        p = self._write([{"id": "a", "split": "train", "lang": "en",
                          "text": "x", "expected_route": "bogus"}])
        with self.assertRaises(ValueError):
            r15.load_cases(p)

    def test_group_cross_split_rejected(self):
        p = self._write([
            {"id": "a", "split": "train", "lang": "en", "text": "one", "group": "G"},
            {"id": "b", "split": "test", "lang": "en", "text": "two", "group": "G"},
        ])
        with self.assertRaises(ValueError):
            r15.load_cases(p)

    def test_duplicate_rejected(self):
        p = self._write([
            {"id": "a", "split": "train", "lang": "en", "text": "same text", "group": "g1"},
            {"id": "b", "split": "test", "lang": "en", "text": "  SAME   text ", "group": "g2"},
        ])
        with self.assertRaises(ValueError):
            r15.load_cases(p)


class ScoreRowsTests(unittest.TestCase):
    def _rows(self):
        return [
            {"id": "1", "lang": "en", "text": "how much disk space?", "expected_route": "disk"},
            {"id": "2", "lang": "en", "text": "clean the disk", "expected_route": None},
            {"id": "3", "lang": "pt", "text": "quanto espaço livre tem no disco?", "expected_route": "disk"},
        ]

    def test_wrong_family_counts_as_error(self):
        # predict always says safe=1.0 -> W1 kept. All 3 rows: W1 matches gold
        # (disk, fall-through on "clean", disk), so 0 unsafe interceptions, 3 correct.
        res = r15.score_rows(self._rows(), lambda t: 1.0, 0.99)
        self.assertEqual(res["unsafe_intercept"], 0)
        self.assertEqual(res["correct"], 3)
        self.assertEqual(res["accuracy"], 1.0)

    def test_wrong_family_is_counted(self):
        # a row where W1 picks the WRONG family counts as an error for the candidate
        rows = [{"id": "1", "lang": "en", "text": "how much memory is free?",
                 "expected_route": "disk"}]  # gold says disk but W1 says memory
        res = r15.score_rows(rows, lambda t: 1.0, 0.99)
        self.assertEqual(res["correct"], 0)
        self.assertEqual(res["accuracy"], 0.0)

    def test_veto_prevents_unsafe_interception(self):
        # low p_safe vetoes W1 on the unsafe row -> no unsafe interception
        res = r15.score_rows(self._rows(), lambda t: 0.0, 0.99)
        self.assertEqual(res["unsafe_intercept"], 0)
        # but it also vetoes the safe rows -> safe coverage drops
        self.assertEqual(res["safe_correct"], 0)

    def test_high_safe_score_cannot_promote_abstention(self):
        # a row W1 abstains on stays None even with p_safe=1.0
        rows = [{"id": "x", "lang": "en", "text": "tell me a joke", "expected_route": None}]
        res = r15.score_rows(rows, lambda t: 1.0, 0.99)
        self.assertEqual(res["unsafe_intercept"], 0)


if __name__ == "__main__":
    unittest.main()