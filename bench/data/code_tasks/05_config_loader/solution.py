DEFAULTS = {"host": "localhost", "port": 8080, "debug": False}


def load_config(text):
    """Parse 'key = value' lines into a config dict."""
    config = dict(DEFAULTS)
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ValueError(f"line {number}: expected 'key = value'")
        key, value = (part.strip() for part in stripped.split("=", 1))
        if key not in DEFAULTS:
            raise ValueError(f"line {number}: unknown key {key!r}")
        if key == "port":
            try:
                value = int(value)
            except ValueError:
                raise ValueError(f"line {number}: port must be a whole number") from None
        elif key == "debug":
            if value.lower() not in ("true", "false"):
                raise ValueError(f"line {number}: debug must be true or false")
            value = value.lower() == "true"
        config[key] = value
    return config
