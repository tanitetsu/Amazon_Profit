"""Range protection is off for every role; auto-fill does not lock cells."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.buyer_cancel import lock_cancel_checkbox
from app.schema import HINT_ROW, HINT_ROW_TEXT
from app.sheet_protection import apply_protections, unlock_workbook


def _meta(sheets: list[dict]) -> dict:
    return {"sheets": sheets}


def _sheet(
    title: str,
    sheet_id: int = 1,
    *,
    protected: list[dict] | None = None,
) -> dict:
    return {
        "properties": {"title": title, "sheetId": sheet_id},
        "protectedRanges": protected or [],
    }


class ApplyProtectionsTests(unittest.TestCase):
    def test_normal_role_deletes_locks_and_never_adds(self) -> None:
        sheets_api = MagicMock()
        meta = _meta(
            [
                _sheet(
                    "2026-08",
                    11,
                    protected=[
                        {
                            "protectedRangeId": 101,
                            "description": "apv:month-lock:0",
                        },
                        {
                            "protectedRangeId": 102,
                            "description": "apv:buyer-cancel:R6",
                        },
                    ],
                ),
                _sheet(
                    "ダッシュボード",
                    22,
                    protected=[
                        {"protectedRangeId": 201, "description": "apv:summary"}
                    ],
                ),
                _sheet("月次テンプレート", 33),
            ]
        )
        with (
            patch(
                "app.sheet_protection.execute_with_retry", return_value=meta
            ) as execute,
            patch("app.sheet_protection.batch_update") as batch,
            patch("app.sheet_protection.values_batch_update") as values,
        ):
            apply_protections(sheets_api, "sid", role="Normal")

        self.assertTrue(execute.called)
        delete_ids = []
        for call in batch.call_args_list:
            for req in call.args[2]:
                self.assertIn("deleteProtectedRange", req)
                self.assertNotIn("addProtectedRange", req)
                delete_ids.append(req["deleteProtectedRange"]["protectedRangeId"])
        self.assertEqual(sorted(delete_ids), [101, 102, 201])
        hint_ranges = {u["range"] for u in values.call_args.args[2]}
        self.assertEqual(
            hint_ranges,
            {f"'2026-08'!A{HINT_ROW}", f"'月次テンプレート'!A{HINT_ROW}"},
        )
        self.assertEqual(values.call_args.args[2][0]["values"], [[HINT_ROW_TEXT]])

    def test_admin_and_exclusive_take_the_same_unlock_path(self) -> None:
        sheets_api = MagicMock()
        meta = _meta(
            [_sheet("2026-01", protected=[{"protectedRangeId": 7}])]
        )
        for role in ("Admin", "Exclusive", None):
            with (
                patch(
                    "app.sheet_protection.execute_with_retry", return_value=meta
                ),
                patch("app.sheet_protection.batch_update") as batch,
                patch("app.sheet_protection.values_batch_update"),
            ):
                apply_protections(sheets_api, "sid", role=role)
            self.assertTrue(batch.called, role)
            self.assertIn("deleteProtectedRange", batch.call_args.args[2][0])

    def test_skip_if_present_skips_only_when_already_unlocked(self) -> None:
        sheets_api = MagicMock()
        empty = _meta([_sheet("2026-08")])
        locked = _meta(
            [_sheet("2026-08", protected=[{"protectedRangeId": 9}])]
        )
        with (
            patch(
                "app.sheet_protection.execute_with_retry", return_value=empty
            ),
            patch("app.sheet_protection.batch_update") as batch,
            patch("app.sheet_protection.values_batch_update") as values,
        ):
            apply_protections(sheets_api, "sid", skip_if_present=True)
        batch.assert_not_called()
        values.assert_not_called()

        with (
            patch(
                "app.sheet_protection.execute_with_retry", return_value=locked
            ),
            patch("app.sheet_protection.batch_update") as batch,
            patch("app.sheet_protection.values_batch_update") as values,
        ):
            apply_protections(sheets_api, "sid", skip_if_present=True)
        batch.assert_called()
        values.assert_called()

    def test_unlock_workbook_can_skip_hints(self) -> None:
        sheets_api = MagicMock()
        meta = _meta(
            [_sheet("2026-08", protected=[{"protectedRangeId": 3}])]
        )
        with (
            patch(
                "app.sheet_protection.execute_with_retry", return_value=meta
            ),
            patch("app.sheet_protection.batch_update") as batch,
            patch("app.sheet_protection.values_batch_update") as values,
        ):
            result = unlock_workbook(sheets_api, "sid", update_hints=False)
        self.assertEqual(result["deleted_protections"], 1)
        self.assertEqual(result["hints_updated"], 0)
        batch.assert_called()
        values.assert_not_called()


class LockCancelCheckboxTests(unittest.TestCase):
    def test_auto_fill_lock_is_noop(self) -> None:
        sheets_api = MagicMock()
        lock_cancel_checkbox(sheets_api, "sid", 1, "2026-08", [6, 7])
        sheets_api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
