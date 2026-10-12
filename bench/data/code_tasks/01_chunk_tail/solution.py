def chunk(items, size):
    """Split items into consecutive lists of length `size`.

    The last chunk may be shorter than `size`.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[i:i + size] for i in range(0, len(items), size)]
