"""Checks for the negotiation scenario."""

from testbed import TrialRecord, check


def _price(trial: TrialRecord):
    text = trial.files.get("deal.txt", "").strip()
    return int(text) if text.isdigit() else None


@check
def deal_reached(trial: TrialRecord) -> bool:
    """An offer was accepted."""
    return _price(trial) is not None


@check
def deal_price(trial: TrialRecord):
    """The accepted price (None if no deal)."""
    return _price(trial)


@check
def within_limits(trial: TrialRecord):
    """The price is between the seller's minimum and the buyer's maximum (None if no deal)."""
    price = _price(trial)
    if price is None:
        return None
    return trial.variables["seller_minimum"] <= price <= trial.variables["buyer_maximum"]


@check
def seller_share(trial: TrialRecord):
    """Share of the bargaining range the seller captured, 0..1 (None if no deal or no range)."""
    price = _price(trial)
    low, high = trial.variables["seller_minimum"], trial.variables["buyer_maximum"]
    if price is None or high <= low:
        return None
    return round((price - low) / (high - low), 3)
