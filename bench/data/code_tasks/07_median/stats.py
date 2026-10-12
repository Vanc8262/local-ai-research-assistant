def median(values):
    """Return the median of a list of numbers."""
    values.sort()
    return values[len(values) // 2]
