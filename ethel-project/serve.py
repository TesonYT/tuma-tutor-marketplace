"""Start the tutor from anywhere: `python path/to/ai-teacher/serve.py`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ethel.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
