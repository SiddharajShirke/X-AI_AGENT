from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.cli import main


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the complete offline prototype lifecycle")
    parser.add_argument("--database-path", default="")
    args = parser.parse_args()
    if args.database_path:
        path = args.database_path
    else:
        path = str(Path(tempfile.mkdtemp(prefix="x-agent-demo-")) / "demo.db")
    raise SystemExit(main(["--database-path", path, "demo-flow"]))
