def summarize(rows):
    """rows: list of dicts with 'category' and 'amount' strings. Return {category: total}."""
    totals = {}
    for row in rows:
        totals[row["category"]] = float(row["amount"])
    return totals
