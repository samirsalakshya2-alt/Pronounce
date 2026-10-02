"""Start the Pronunciation Lab web app without installing the package.

    uv run python scripts/run_app.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pronunciation_lab.app.__main__ import main  # noqa: E402

raise SystemExit(main())
