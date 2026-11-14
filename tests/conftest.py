import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))          # for fake_model
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # for bench

import pytest


@pytest.fixture(autouse=True)
def no_search_pacing(monkeypatch):
    """The search router spaces requests to each public API; tests talk to mock transports."""
    from assistant import sources
    for name, gate in sources.GATES.items():
        monkeypatch.setattr(gate, "gap_s", 0.0)
