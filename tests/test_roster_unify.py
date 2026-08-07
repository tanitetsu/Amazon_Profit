"""Tests for roster / quitted_user.txt helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import clipping_roster as cr  # noqa: E402


class RosterTextHelpersTests(unittest.TestCase):
    def test_strip_blank_lines(self):
        raw = "a\n\n\nb\n \n"
        self.assertEqual(cr.strip_blank_lines(raw), "a\nb\n")

    def test_user_list_no_blank(self):
        text = cr.user_list_csv_payload({"z": "Normal", "a": "Admin"})
        self.assertNotIn("\n\n", text)
        self.assertTrue(text.endswith("\n"))
        parsed = cr.parse_user_list_csv(text)
        self.assertEqual(parsed["a"], "Admin")

    def test_quitted_roundtrip(self):
        payload = cr.quitted_user_txt_payload(["tracaude", "", "asamiodaka", "tracaude"])
        self.assertEqual(payload, "asamiodaka\ntracaude\n")
        self.assertEqual(cr.parse_quitted_user_txt(payload), ["asamiodaka", "tracaude"])


if __name__ == "__main__":
    unittest.main()
