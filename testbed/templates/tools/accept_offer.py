"""accept_offer: accept the other party's latest offer and end the trial (negotiation)."""

from testbed import ToolContext, ToolError, tool


@tool(file="deal.txt", op="write")
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
