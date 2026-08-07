"""Unit tests for IAP policy member helpers."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import iap_access as ia  # noqa: E402


class IapPolicyHelpersTests(unittest.TestCase):
    def test_add_member_idempotent(self):
        policy: dict = {"bindings": []}
        self.assertTrue(ia._add_member(policy, ia._IAP_ROLE, "user:a@x.com"))
        self.assertFalse(ia._add_member(policy, ia._IAP_ROLE, "user:a@x.com"))
        self.assertTrue(ia._policy_has_member(policy, ia._IAP_ROLE, "user:a@x.com"))

    def test_remove_member(self):
        policy = {
            "bindings": [
                {"role": ia._IAP_ROLE, "members": ["user:a@x.com", "user:b@x.com"]}
            ]
        }
        self.assertTrue(ia._remove_member(policy, ia._IAP_ROLE, "user:a@x.com"))
        self.assertEqual(policy["bindings"][0]["members"], ["user:b@x.com"])
        self.assertTrue(ia._remove_member(policy, ia._IAP_ROLE, "user:b@x.com"))
        self.assertEqual(policy.get("bindings"), [])

    def test_disabled_skips(self):
        with mock.patch.dict(os.environ, {"IAP_AUTO_GRANT": "0"}, clear=False):
            out = ia.grant_iap_access("someone@gmail.com")
        self.assertTrue(out["skipped"])

    def test_set_iap_accessor_uses_nested_v1(self):
        """googleapiclient IAP v1 exposes IAM on ``.v1()``, not the root Resource."""
        policy = {"bindings": [], "etag": "abc", "version": 1}
        iap_v1 = mock.MagicMock()
        get_req = mock.MagicMock()
        set_req = mock.MagicMock()
        iap_v1.getIamPolicy.return_value = get_req
        iap_v1.setIamPolicy.return_value = set_req
        iap_root = mock.MagicMock()
        iap_root.v1.return_value = iap_v1
        # Root must not be used for IAM (reproduces production AttributeError).
        del iap_root.getIamPolicy
        del iap_root.setIamPolicy

        with (
            mock.patch("googleapiclient.discovery.build", return_value=iap_root),
            mock.patch(
                "app.iap_access.execute_with_retry",
                side_effect=[policy, {"done": True}],
            ) as exec_mock,
        ):
            out = ia._set_iap_accessor(
                creds=mock.MagicMock(),
                project_number="123",
                region="asia-northeast1",
                service="ai-cripping-data-viewer",
                member="user:tracaude@gmail.com",
                grant=True,
            )

        self.assertTrue(out["changed"])
        iap_root.v1.assert_called_once_with()
        iap_v1.getIamPolicy.assert_called_once()
        iap_v1.setIamPolicy.assert_called_once()
        self.assertEqual(exec_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
