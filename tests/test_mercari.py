"""Tests for Mercari SKU helpers and price fetch."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.mercari import (
    fetch_mercari_price,
    mercari_item_id_from_sku,
    mercari_item_url,
)


class MercariSkuTests(unittest.TestCase):
    def test_item_id_from_sku(self) -> None:
        self.assertEqual(mercari_item_id_from_sku("m_m42688576128"), "m42688576128")
        self.assertIsNone(mercari_item_id_from_sku("ms_abc"))
        self.assertIsNone(mercari_item_id_from_sku("z_1"))
        self.assertIsNone(mercari_item_id_from_sku(""))
        self.assertIsNone(mercari_item_id_from_sku(None))

    def test_item_url(self) -> None:
        self.assertEqual(
            mercari_item_url("m_m42688576128"),
            "https://jp.mercari.com/item/m42688576128",
        )
        self.assertIsNone(mercari_item_url("r_x"))


class MercariPriceFetchTests(unittest.TestCase):
    def test_non_target_sku_skips_http(self) -> None:
        with patch("app.mercari.httpx.Client") as client_cls:
            self.assertIsNone(fetch_mercari_price("ms_not_mercari"))
            client_cls.assert_not_called()

    def test_price_from_items_get(self) -> None:
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {"data": {"price": 3200, "id": "m42688576128"}}
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.get.return_value = mock_res

        with patch("app.mercari.httpx.Client", return_value=mock_client):
            self.assertEqual(fetch_mercari_price("m_m42688576128"), 3200)

        args, kwargs = mock_client.get.call_args
        self.assertIn("id=m42688576128", args[0])
        self.assertIn("DPoP", kwargs["headers"])

    def test_http_503_retries_then_succeeds(self) -> None:
        bad = MagicMock()
        bad.status_code = 503
        bad.text = "unavailable"
        good = MagicMock()
        good.status_code = 200
        good.json.return_value = {"data": {"price": 1000}}
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.get.side_effect = [bad, good]

        with patch("app.mercari.httpx.Client", return_value=mock_client):
            with patch("app.mercari.time.sleep"):
                self.assertEqual(fetch_mercari_price("m_m123"), 1000)
        self.assertEqual(mock_client.get.call_count, 2)

    def test_http_404_returns_none(self) -> None:
        mock_res = MagicMock()
        mock_res.status_code = 404
        mock_res.text = "not found"
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.get.return_value = mock_res

        with patch("app.mercari.httpx.Client", return_value=mock_client):
            self.assertIsNone(fetch_mercari_price("m_m99999999999"))

    def test_bad_price_returns_none(self) -> None:
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {"data": {"price": None}}
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.get.return_value = mock_res

        with patch("app.mercari.httpx.Client", return_value=mock_client):
            self.assertIsNone(fetch_mercari_price("m_m11111111111"))


if __name__ == "__main__":
    unittest.main()
