import re


def slugify(text, sep="-", max_words=None):
    """Lowercase `text` and join its alphanumeric words with `sep`."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    if max_words is not None:
        words = words[:max_words]
    return sep.join(words)
