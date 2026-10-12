DEFAULTS = {"host": "localhost", "port": 8080, "debug": False}


def load_config(text):
    """Parse 'key = value' lines into a config dict."""
    config = {}
    for line in text.splitlines():
        key, value = line.split("=")
        config[key.strip()] = value.strip()
    return config
