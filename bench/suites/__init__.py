"""The Stage 0 suites, in the order they run."""
from . import code_editing, headroom, research, speed, tool_calling, vision

SUITES = {
    "tools": tool_calling.run,
    "code": code_editing.run,
    "speed": speed.run,
    "research": research.run,
    "vision": vision.run,
    "headroom": headroom.run,
}
# `code` runs model-written code on this PC without a sandbox, so it runs only when asked for.
DEFAULT_SUITES = [name for name in SUITES if name != "code"]
