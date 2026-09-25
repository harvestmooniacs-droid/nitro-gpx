"""Double-click launcher for Nitro GFX (no console window on Windows)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nitrokit.gui.app import main  # noqa: E402

main()
