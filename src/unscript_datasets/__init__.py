"""unscript-datasets: migrate script-based ``load_dataset`` calls using the Hub's current state.

``scan_paths`` and ``judge_call`` are exposed for convenience but are internal
helpers: their signatures may change between minor versions.
"""

from .extract import scan_paths
from .judge import judge_call

__version__ = "0.1.3"
__all__ = ["__version__", "scan_paths", "judge_call"]
