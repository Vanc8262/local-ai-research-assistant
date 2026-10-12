def format_report(items):
    """items: list of (name, quantity, unit_price) tuples. Return a text report."""
    lines = []
    total = 0
    for name, quantity, price in items:
        line_total = quantity * price
        total += line_total
        lines.append(f"{name[:12]:<12}{quantity:>4}{line_total:>10.2f}")
    lines.append(f"{'TOTAL':<12}{'':>4}{total:>10.2f}")
    return "\n".join(lines)
