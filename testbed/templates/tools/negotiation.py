"""Tools for the negotiation scenario. Offers live in the trial's shared state."""

from typing import Annotated

from testbed import ToolContext, ToolError, tool


@tool
def make_offer(ctx: ToolContext, price: Annotated[int, "Your offer, in whole dollars."]) -> str:
    """Make a price offer to the other party."""
    if price <= 0:
        raise ToolError("The price must be a positive number of dollars.")
    ctx.state.setdefault("offers", {})[ctx.agent_id] = price
    ctx.note("offer", price=price)
    return f"Your offer of ${price} is recorded."


@tool
def accept_offer(ctx: ToolContext) -> str:
    """Accept the other party's latest offer. This closes the deal."""
    offers = {agent: price for agent, price in ctx.state.get("offers", {}).items() if agent != ctx.agent_id}
    if not offers:
        raise ToolError("The other party has not made an offer yet.")
    price = next(iter(offers.values()))
    ctx.files.write("deal.txt", str(price))
    ctx.note("deal", price=price)
    ctx.stop_trial("deal")
    return f"Deal closed at ${price}."
