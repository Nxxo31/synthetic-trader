"""pytest configuration — ensures `src` is importable from any test location."""
import sys
from pathlib import Path

# Add project root to sys.path so `import src...` works from test files
# located alongside source modules (not in a separate tests/ dir)
_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
