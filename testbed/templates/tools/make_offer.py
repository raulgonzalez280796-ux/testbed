"""make_offer: record a price offer (negotiation). Offers live in the trial's shared state."""

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
