"""Tests for SA-safe workbook overwrite retirement."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from googleapiclient.errors import HttpError  # noqa: E402

from app.google_clients import retire_spreadsheet_for_overwrite  # noqa: E402


def _http_error(status: int) -> HttpError:
    resp = SimpleNamespace(status=status, reason="forbidden")
    return HttpError(resp, b'{"error":{"message":"insufficient"}}')


class RetireSpreadsheetTests(unittest.TestCase):
    def test_delete_when_allowed(self):
        drive = MagicMock()
        drive.files.return_value.delete.return_value.execute.return_value = None
        mode = retire_spreadsheet_for_overwrite(drive, "fid", "amazon-profit_u_2026.xlsx")
        self.assertEqual(mode, "deleted")
        drive.files.return_value.delete.assert_called_once()

    def test_rename_trash_on_403(self):
        drive = MagicMock()
        delete = drive.files.return_value.delete.return_value
        delete.execute.side_effect = _http_error(403)
        update = drive.files.return_value.update.return_value
        update.execute.side_effect = [
            {"id": "fid", "name": "x.retired"},
            {"id": "fid", "trashed": True},
        ]
        mode = retire_spreadsheet_for_overwrite(drive, "fid", "amazon-profit_u_2026.xlsx")
        self.assertEqual(mode, "renamed_trashed")
        self.assertEqual(drive.files.return_value.update.call_count, 2)

    def test_rename_only_when_trash_forbidden(self):
        drive = MagicMock()
        delete = drive.files.return_value.delete.return_value
        delete.execute.side_effect = _http_error(403)
        update = drive.files.return_value.update.return_value
        update.execute.side_effect = [
            {"id": "fid", "name": "x.retired"},
            _http_error(403),
        ]
        mode = retire_spreadsheet_for_overwrite(drive, "fid", "amazon-profit_u_2026.xlsx")
        self.assertEqual(mode, "renamed")


if __name__ == "__main__":
    unittest.main()
