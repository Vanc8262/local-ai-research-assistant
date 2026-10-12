def add_tag(tag, tags=None):
    """Return the tags with `tag` added at the end."""
    result = list(tags) if tags is not None else []
    result.append(tag)
    return result
