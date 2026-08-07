"""Amazon Seller Central order detail URL helpers."""

from __future__ import annotations

import re

# Same 3-7-7 shape as seller mails / mail_parser.ORDER_ID_RE
ORDER_ID_RE = re.compile(r"^\d{3}-\d{7}-\d{7}$")

# Confirmed from 注文確定 mail redirect decode:
# https://sellercentral-japan.amazon.com/orders-v3/order/{order_id}/…
SELLER_ORDER_URL_TMPL = (
    "https://sellercentral-japan.amazon.com/orders-v3/order/{order_id}"
)


def seller_order_detail_url(order_id: str | None) -> str | None:
    """Build Seller Central order page URL from order number alone when possible."""
    if not order_id:
        return None
    oid = str(order_id).strip()
    if not ORDER_ID_RE.match(oid):
        return None
    return SELLER_ORDER_URL_TMPL.format(order_id=oid)
