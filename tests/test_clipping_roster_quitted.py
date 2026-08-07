"""Unit tests for setting/user ↔ setting/quitted-user archive/restore."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import clipping_roster as cr  # noqa: E402


class FakeBlob:
    def __init__(self, bucket: "FakeBucket", name: str, text: str = ""):
        self.bucket = bucket
        self.name = name
        self._text = text

    def exists(self) -> bool:
        return self.name in self.bucket.objects

    def reload(self) -> None:
        from google.api_core.exceptions import NotFound

        if self.name not in self.bucket.objects:
            raise NotFound(f"{self.name} not found")
        self._text = self.bucket.objects[self.name]

    def download_as_text(self, encoding="utf-8") -> str:  # noqa: ARG002
        return self.bucket.objects[self.name]

    def upload_from_string(self, data, content_type=None):  # noqa: ARG002
        self.bucket.objects[self.name] = (
            data.decode("utf-8") if isinstance(data, bytes) else str(data)
        )

    def delete(self) -> None:
        self.bucket.objects.pop(self.name, None)


class FakeBucket:
    def __init__(self):
        self.objects: dict[str, str] = {}

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name, self.objects.get(name, ""))

    def list_blobs(self, prefix: str = "", max_results=None):
        names = sorted(n for n in self.objects if n.startswith(prefix))
        if max_results is not None:
            names = names[:max_results]
        for name in names:
            yield FakeBlob(self, name, self.objects[name])

    def copy_blob(self, src_blob: FakeBlob, _dest_bucket, dest_name: str):
        self.objects[dest_name] = self.objects[src_blob.name]
        return FakeBlob(self, dest_name, self.objects[dest_name])


class QuittedArchiveTests(unittest.TestCase):
    def test_archive_and_restore_roundtrip(self):
        bucket = FakeBucket()
        uid = "asamiodaka"
        src = f"setting/user/{uid}/ng_word.txt"
        bucket.objects[src] = "foo\n"
        bucket.objects[f"setting/user/{uid}/.keep"] = ""

        archived = cr.archive_user_settings_to_quitted(bucket, uid)
        self.assertTrue(archived["archived"])
        self.assertNotIn(src, bucket.objects)
        self.assertIn(f"setting/quitted-user/{uid}/ng_word.txt", bucket.objects)
        self.assertIn(cr.QUITTED_ROOT_KEEP, bucket.objects)

        restored = cr.restore_user_settings_from_quitted(bucket, uid)
        self.assertTrue(restored["restored"])
        self.assertIn(src, bucket.objects)
        self.assertEqual(bucket.objects[src], "foo\n")
        self.assertNotIn(f"setting/quitted-user/{uid}/ng_word.txt", bucket.objects)

    def test_template_admin_forbidden(self):
        bucket = FakeBucket()
        with self.assertRaises(ValueError):
            cr.archive_user_settings_to_quitted(bucket, cr.TEMPLATE_USER_ID)

    def test_both_exist_archive_prefers_active_and_moves(self):
        bucket = FakeBucket()
        uid = "dupuser"
        bucket.objects[f"setting/user/{uid}/a.txt"] = "active"
        bucket.objects[f"setting/quitted-user/{uid}/a.txt"] = "old"
        result = cr.archive_user_settings_to_quitted(bucket, uid)
        self.assertTrue(result["archived"])
        self.assertEqual(result["reason"], "moved_replacing_quitted")
        self.assertNotIn(f"setting/user/{uid}/a.txt", bucket.objects)
        self.assertEqual(bucket.objects[f"setting/quitted-user/{uid}/a.txt"], "active")

    def test_both_exist_restore_prefers_active_leaves_quitted(self):
        bucket = FakeBucket()
        uid = "dupuser"
        bucket.objects[f"setting/user/{uid}/a.txt"] = "active"
        bucket.objects[f"setting/quitted-user/{uid}/a.txt"] = "old"
        result = cr.restore_user_settings_from_quitted(bucket, uid)
        self.assertFalse(result["restored"])
        self.assertEqual(result["reason"], "both_exist_prefer_active")
        self.assertEqual(bucket.objects[f"setting/user/{uid}/a.txt"], "active")
        self.assertEqual(bucket.objects[f"setting/quitted-user/{uid}/a.txt"], "old")


class RosterUpsertConfirmTests(unittest.TestCase):
    def test_confirm_is_read_only_even_when_csv_dirty(self):
        bucket = FakeBucket()
        dirty = "ユーザーID,ロール\n\n26964u,Admin\ntracaude,Normal\n"
        bucket.objects[cr.USER_LIST_OBJECT] = dirty
        self.assertTrue(
            cr._confirm_user_in_roster(
                bucket, "tracaude", role="Normal", attempts=1
            )
        )
        self.assertEqual(bucket.objects[cr.USER_LIST_OBJECT], dirty)

    def test_unparseable_nonempty_refuses_admin_reset(self):
        bucket = FakeBucket()
        bucket.objects[cr.USER_LIST_OBJECT] = "garbage-only\n"
        with self.assertRaises(RuntimeError):
            cr.load_role_map(bucket)
        self.assertEqual(bucket.objects[cr.USER_LIST_OBJECT], "garbage-only\n")

    def test_upsert_force_writes_and_confirms(self):
        from unittest.mock import patch

        bucket = FakeBucket()
        bucket.objects[cr.USER_LIST_OBJECT] = "ユーザーID,ロール\n26964u,Admin\n"
        bucket.objects[cr.QUITTED_USER_LIST_OBJECT] = "tracaude\n"
        with patch.object(cr, "clipping_storage_bucket", return_value=bucket):
            result = cr.upsert_clipping_user("tracaude@gmail.com", "Normal")
        self.assertTrue(result["confirmed_in_roster"])
        self.assertTrue(result["removed_from_quitted"])
        users = cr.parse_user_list_csv(bucket.objects[cr.USER_LIST_OBJECT])
        self.assertEqual(users.get("tracaude"), "Normal")
        self.assertNotIn(
            "tracaude",
            cr.parse_quitted_user_txt(
                bucket.objects.get(cr.QUITTED_USER_LIST_OBJECT, "")
            ),
        )


    def test_ids_and_filters_seed_from_asamiodaka_role_aware(self):
        bucket = FakeBucket()
        seed = cr.SEED_USER_ID
        bucket.objects[f"setting/user/{seed}/ids_already_got.txt"] = "m123\nm456\n"
        bucket.objects[f"setting/user/{seed}/ng_word.txt"] = "ng1\n"
        bucket.objects[f"setting/user/{seed}/replace_word.txt"] = "rp1\n"
        bucket.objects[f"setting/user/{seed}/excluded_user.txt"] = "seller1\n"
        bucket.objects[f"setting/user/{seed}/amazon_feed_template.json"] = '{"headers":[]}\n'
        bucket.objects[f"setting/user/{seed}/price.csv"] = (
            "#amazon_fee_percent:10\n"
            "メルカリ販売価格,Amazon販売価格,送料(実際)\n"
            "600,1680,180\n"
        )

        for role, uid, expect_ids in (
            ("Exclusive", "newex", "m123\nm456\n"),
            ("Admin", "newadmin", "m123\nm456\n"),
            ("Normal", "newnormal", "m123\nm456\n"),
        ):
            created = cr.ensure_user_settings_seeded(bucket, uid, role=role)
            self.assertIn(f"setting/user/{uid}/ids_already_got.txt", created)
            self.assertEqual(
                bucket.objects[f"setting/user/{uid}/ids_already_got.txt"],
                expect_ids,
            )
            self.assertEqual(
                bucket.objects[f"setting/user/{uid}/ng_word.txt"],
                "ng1\n",
            )
            self.assertEqual(
                bucket.objects[f"setting/user/{uid}/excluded_user.txt"],
                "seller1\n",
            )
            self.assertEqual(
                bucket.objects[f"setting/user/{uid}/price.csv"],
                "メルカリ販売価格,Amazon販売価格,送料(実際)\n",
            )
            self.assertEqual(
                bucket.objects[f"setting/user/{uid}/amazon-fee.txt"],
                "10\n",
            )

        self.assertEqual(
            bucket.objects["setting/template/price.csv"],
            "メルカリ販売価格,Amazon販売価格,送料(実際)\n",
        )
        self.assertIn("setting/template/amazon_feed_template.json", bucket.objects)


if __name__ == "__main__":
    unittest.main()
