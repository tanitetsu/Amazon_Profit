"""Tests for provision rollback + register_user whole-flow retry."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.sheets_retry import is_transient
from app.template_ops import ProvisionError, WorkbookExistsError


class TransientProvisionErrorTests(unittest.TestCase):
    def test_provision_error_follows_cause(self) -> None:
        cause = ConnectionError("connection reset")
        err = ProvisionError("wrapped", rollback={"sheet_retired": "deleted"})
        err.__cause__ = cause
        self.assertTrue(is_transient(err))

    def test_workbook_exists_not_transient(self) -> None:
        exc = WorkbookExistsError(
            gmail="a@gmail.com",
            title="t",
            spreadsheet_id="id",
            url="https://example",
        )
        self.assertFalse(is_transient(exc))


class RollbackProvisionTests(unittest.TestCase):
    def test_roster_failure_rolls_back_new_sheet(self) -> None:
        from app.template_ops import provision_from_template

        drive = MagicMock()
        sheets = MagicMock()

        with (
            patch("app.template_ops.load_users_config", return_value={
                "folder_name": "User_Acounting",
                "template_spreadsheet_id": "tmpl",
            }),
            patch("app.template_ops.uses_adc_credentials", return_value=False),
            patch("app.template_ops.load_operator_credentials", return_value=MagicMock()),
            patch("app.template_ops.drive_service", return_value=drive),
            patch("app.template_ops.sheets_service", return_value=sheets),
            patch("app.template_ops.resolve_operator_folder_id", return_value="folder"),
            patch("app.template_ops.resolve_template_spreadsheet_id", return_value="tmpl"),
            patch("app.template_ops.find_spreadsheet_in_folder", return_value=None),
            patch(
                "app.template_ops.copy_spreadsheet_in_folder",
                return_value="new-sheet",
            ),
            patch("app.template_ops.ensure_months_for_order"),
            patch("app.template_ops.apply_protections"),
            patch("app.template_ops.share_editor"),
            patch(
                "app.clipping_roster.load_role_map",
                return_value={"26964u": "Admin"},
            ),
            patch(
                "app.clipping_roster.upsert_clipping_user",
                side_effect=ConnectionError("gcs blip"),
            ),
            patch("app.iap_access.revoke_iap_access") as revoke,
            patch("app.clipping_roster.remove_clipping_user") as remove,
            patch("app.sheet_protection.unshare_user", return_value=True) as unshare,
            patch(
                "app.template_ops.retire_spreadsheet_for_overwrite",
                return_value="deleted",
            ) as retire,
        ):
            with self.assertRaises(ProvisionError) as ctx:
                provision_from_template("newbie@gmail.com", role="Normal")

        self.assertIn("gcs blip", str(ctx.exception))
        self.assertEqual(ctx.exception.rollback.get("sheet_retired"), "deleted")
        unshare.assert_called()
        retire.assert_called()
        # Roster never confirmed write → remove may still run if roster_touched;
        # here upsert raised before return so roster_touched stays False.
        remove.assert_not_called()
        revoke.assert_not_called()

    def test_roster_touched_then_iap_fail_rolls_back_roster(self) -> None:
        from app.template_ops import provision_from_template

        drive = MagicMock()
        sheets = MagicMock()

        with (
            patch("app.template_ops.load_users_config", return_value={
                "folder_name": "User_Acounting",
                "template_spreadsheet_id": "tmpl",
            }),
            patch("app.template_ops.uses_adc_credentials", return_value=False),
            patch("app.template_ops.load_operator_credentials", return_value=MagicMock()),
            patch("app.template_ops.drive_service", return_value=drive),
            patch("app.template_ops.sheets_service", return_value=sheets),
            patch("app.template_ops.resolve_operator_folder_id", return_value="folder"),
            patch("app.template_ops.resolve_template_spreadsheet_id", return_value="tmpl"),
            patch("app.template_ops.find_spreadsheet_in_folder", return_value=None),
            patch(
                "app.template_ops.copy_spreadsheet_in_folder",
                return_value="new-sheet",
            ),
            patch("app.template_ops.ensure_months_for_order"),
            patch("app.template_ops.apply_protections"),
            patch("app.template_ops.share_editor"),
            patch(
                "app.clipping_roster.load_role_map",
                return_value={"26964u": "Admin"},
            ),
            patch(
                "app.clipping_roster.upsert_clipping_user",
                return_value={
                    "user_id": "newbie",
                    "confirmed_in_roster": True,
                },
            ),
            patch(
                "app.iap_access.grant_iap_access",
                side_effect=ConnectionError("iap down"),
            ),
            patch("app.iap_access.revoke_iap_access") as revoke,
            patch("app.clipping_roster.remove_clipping_user") as remove,
            patch("app.sheet_protection.unshare_user", return_value=True),
            patch(
                "app.template_ops.retire_spreadsheet_for_overwrite",
                return_value="deleted",
            ),
        ):
            with self.assertRaises(ProvisionError):
                provision_from_template("newbie@gmail.com", role="Normal")

        remove.assert_called()
        revoke.assert_called()


class RegisterUserRetryTests(unittest.TestCase):
    def test_retries_transient_then_succeeds(self) -> None:
        from app.provision import register_user

        ok = {
            "gmail": "newbie@gmail.com",
            "url": "https://sheet",
            "title": "amazon-profit_newbie_2026.xlsx",
            "spreadsheet_id": "sid",
        }
        with (
            patch(
                "app.template_ops.provision_from_template",
                side_effect=[ConnectionError("blip"), ok],
            ),
            patch("app.gmail_oauth.has_gmail_token", return_value=False),
            patch(
                "app.consent_mail.send_gmail_consent_email",
                return_value={"message_id": "m1"},
            ),
            patch("time.sleep"),
        ):
            result = register_user("newbie@gmail.com", max_attempts=3)

        self.assertEqual(result["register_attempts"], 2)
        self.assertEqual(result["consent_email"]["message_id"], "m1")

    def test_rejects_non_gmail_domain(self) -> None:
        from app.provision import register_user

        with self.assertRaises(ValueError) as ctx:
            register_user("asamiodaka.b@gamil.com")
        self.assertIn("@gmail.com", str(ctx.exception))

    def test_linked_ingest_uses_capped_max_results(self) -> None:
        from app.provision import register_user

        ok = {
            "gmail": "newbie@gmail.com",
            "url": "https://sheet",
            "title": "amazon-profit_newbie_2026.xlsx",
            "spreadsheet_id": "sid",
        }
        with (
            patch(
                "app.template_ops.provision_from_template",
                return_value=ok,
            ),
            patch("app.gmail_oauth.has_gmail_token", return_value=True),
            patch(
                "app.mail_ingest.ingest_user_mail",
                return_value={"processed": 3},
            ) as ingest,
        ):
            result = register_user("newbie@gmail.com", max_attempts=1)

        ingest.assert_called_once_with("newbie@gmail.com", max_results=100)
        self.assertEqual(result["mail_ingest"]["processed"], 3)


if __name__ == "__main__":
    unittest.main()
