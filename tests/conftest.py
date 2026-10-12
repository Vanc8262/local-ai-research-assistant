import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))          # for fake_model
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # for bench
