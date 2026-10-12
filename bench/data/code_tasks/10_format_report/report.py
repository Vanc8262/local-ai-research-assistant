def format_report(items):
    """items: list of (name, quantity, unit_price) tuples. Return a text report."""
    lines = []
    for name, quantity, price in items:
        lines.append(name + " " + str(quantity * price))
    return "\n".join(lines)
