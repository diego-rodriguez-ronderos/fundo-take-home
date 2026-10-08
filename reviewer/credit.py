"""Features and the offer rule. Pure functions of (transactions, labels).

labels: {transaction_id: {"group": str, "is_business": bool}}
"""
from datetime import date, timedelta

from . import groups as G

NSF_DECLINE_ABOVE = 5


def _direction(t):
    return "debit" if t["amount"] > 0 else "credit"  # Plaid: positive = outflow


def business_days(start: date, end: date) -> int:
    n, d = 0, start
    while d <= end:
        n += d.weekday() < 5
        d += timedelta(days=1)
    return n


def features(biz: dict, labels: dict, assume_days: int | None = None) -> dict:
    """assume_days: force the window length (simulates a pipeline hard-coded to 90 days)."""
    txns = biz["transactions"]
    dates = [date.fromisoformat(t["date"]) for t in txns]
    start, end = min(dates), max(dates)
    days = assume_days or (end - start).days + 1
    if assume_days:
        start = end - timedelta(days=assume_days - 1)
    deposits = debits = revenue = hr_debits = funder_debits = 0.0
    nsf = od = 0
    for t in txns:
        lab = labels[t["transaction_id"]]
        g, d, amt = lab["group"], _direction(t), abs(t["amount"])
        if d == "credit":
            deposits += amt
            if G.is_revenue(d, g, lab["is_business"]):
                revenue += amt
        else:
            debits += amt
            if g in G.HIGH_RISK:
                hr_debits += amt
            if g == G.ACTIVE_ADVANCE:
                funder_debits += amt
        nsf += g == G.NSF
        od += g == G.OVERDRAFT
    months = days / 30.0
    amr = revenue / months
    funder_daily = funder_debits / business_days(start, end)
    offer = 0.0 if nsf > NSF_DECLINE_ABOVE else max(0.0, 1.2 * amr - 20 * funder_daily)
    return {
        "days": days,
        "avg_monthly_revenue": round(amr, 2),
        "revenue_share_of_deposits": round(revenue / deposits, 4) if deposits else 0.0,
        "nsf_count": nsf,
        "overdraft_count": od,
        "high_risk_share_of_debits": round(hr_debits / debits, 4) if debits else 0.0,
        "funder_daily_payment": round(funder_daily, 2),
        "offer": round(offer, 2),
    }
