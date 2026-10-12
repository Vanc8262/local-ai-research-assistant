import re


def slugify(text):
    """Lowercase `text` and join its alphanumeric words with hyphens."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(words)
