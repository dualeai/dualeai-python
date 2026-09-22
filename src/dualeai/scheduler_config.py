"""Factory for the bounded aiojobs scheduler used by cached activities."""

import aiojobs


def create_production_scheduler(
    max_jobs: int = 100,
    close_timeout: float = 5.0,
    pending_limit: int = 1000,
) -> aiojobs.Scheduler:
    """Create an ``aiojobs.Scheduler`` with explicit running/pending bounds.

    Args:
        max_jobs: Maximum concurrent jobs.
        close_timeout: Graceful shutdown timeout in seconds.
        pending_limit: Maximum pending jobs in queue.

    Returns:
        Configured ``aiojobs.Scheduler``.

    Configuration forwarding is exercised by ``tests/test_feature_caching.py``.
    """
    return aiojobs.Scheduler(
        limit=max_jobs,
        close_timeout=close_timeout,
        pending_limit=pending_limit,
    )
