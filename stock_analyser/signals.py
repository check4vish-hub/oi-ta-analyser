"""Combine technical and OI signals into one score and verdict."""
from __future__ import annotations


def score(signals: list[dict]) -> int:
    """-100 (all bearish) .. +100 (all bullish)."""
    if not signals:
        return 0
    return round(100 * sum(s["score"] for s in signals) / len(signals))


def verdict(sc: int) -> tuple[str, str]:
    """(label, colour) for a -100..100 score."""
    if sc >= 50:
        return "Bullish", "green"
    if sc >= 20:
        return "Mildly bullish", "green"
    if sc > -20:
        return "Neutral", "amber"
    if sc > -50:
        return "Mildly bearish", "red"
    return "Bearish", "red"


def combine(tech: list[dict], oi: list[dict], tech_weight: float = 0.6) -> dict:
    t, o = score(tech), score(oi) if oi else None
    overall = t if o is None else round(tech_weight * t + (1 - tech_weight) * o)
    agree = None
    if o is not None:
        agree = (t > 0) == (o > 0) and abs(t) >= 20 and abs(o) >= 20
    return {"technical": t, "oi": o, "overall": overall,
            "verdict": verdict(overall), "aligned": agree}
