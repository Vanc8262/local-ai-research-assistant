import re

_PATTERN = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def parse_duration(text):
    """Convert a duration such as '1h30m' into seconds."""
    match = _PATTERN.fullmatch(text)
    if not text or match is None:
        raise ValueError(f"invalid duration: {text!r}")
    hours, minutes, seconds = (int(part) if part else 0 for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds
