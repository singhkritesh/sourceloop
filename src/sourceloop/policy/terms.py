"""Payment-term options: what a longer or shorter payment term does to the unit price. Pure functions, `Decimal` only.

The agreed price is quoted at `base_days`. Paying later costs the supplier financing, so the price rises by `rate_pct` per year for each
extra day (and falls for fewer days). The option list is built by trusted code and only carries options that stay inside BOTH sides' limits;
callers see a price per term, never the limit that removed an option."""
from __future__ import annotations

from decimal import Decimal

TERM_MENU: tuple[int, ...] = (30, 45, 60, 90)
Q4 = Decimal("0.0001")


def adjusted_price(price: Decimal, base_days: int, days: int, rate_pct: Decimal) -> Decimal:
    """Price at `days` when `price` is quoted at `base_days`: price * (1 + rate * (days - base) / 365)."""
    return (price * (1 + rate_pct / 100 * Decimal(days - base_days) / 365)).quantize(Q4)


def term_options(price: Decimal, base_days: int, rate_pct: Decimal, *, supplier_floor: Decimal, buyer_max: Decimal,
                 menu: tuple[int, ...] = TERM_MENU) -> list[dict[str, str | int]]:
    """Every term on the menu whose adjusted price is at or above the supplier's own floor and at or below the buyer's own maximum.
    The base term is always listed first (it is the agreed deal). A term that breaks either limit is left out without saying which."""
    out: list[dict[str, str | int]] = [{"days": base_days, "price": str(price.quantize(Q4))}]
    for d in sorted(set(menu) - {base_days}):
        p = adjusted_price(price, base_days, d, rate_pct)
        if supplier_floor <= p <= buyer_max:
            out.append({"days": d, "price": str(p)})
    return out
