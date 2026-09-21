"""Production aiojobs scheduler factory."""

import aiojobs


def create_production_scheduler(
    max_jobs: int = 100,
    close_timeout: float = 5.0,
    pending_limit: int = 1000,
) -> aiojobs.Scheduler:
    """Create a production-ready aiojobs scheduler.

    Args:
        max_jobs: Maximum concurrent jobs.
        close_timeout: Graceful shutdown timeout in seconds.
        pending_limit: Maximum pending jobs in queue.

    Returns:
        Configured ``aiojobs.Scheduler`` for production use.
    """
    return aiojobs.Scheduler(
        limit=max_jobs,
        close_timeout=close_timeout,
        pending_limit=pending_limit,
    )
