import sys
from pathlib import Path

# Add service src directories to sys.path so tests can import service modules
_root = Path(__file__).parent.parent
for _service in _root.glob("services/*/src"):
    _path = str(_service)
    if _path not in sys.path:
        sys.path.insert(0, _path)
