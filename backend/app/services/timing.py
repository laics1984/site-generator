"""
Lightweight per-stage timing for the generation pipeline.

`stage(name)` is a context manager that logs how long its block took at INFO,
so we can see which phase dominates a given generation (text-heavy sites are
GPU-bound on content generation; image-heavy sites spend their time in image
resolution / stock lookup). Zero behaviour change — purely observational.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Log the wall-clock duration of the wrapped block as ``stage <name>``."""
    start = time.perf_counter()
    try:
        yield
    finally:
        log_elapsed(name, start)


def log_elapsed(name: str, start: float) -> None:
    """Log ``stage <name> took <ms>`` for a block timed with `time.perf_counter()`.

    For spots where wrapping in `stage(...)` would force a large re-indent — pass
    the `perf_counter()` value captured before the block.
    """
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    logger.info("stage %s took %.0f ms", name, elapsed_ms)
