"""Constants for testing to avoid magic number violations."""

from dualeai.constants import TimingDefaults

# Timeout constants (in seconds)
DEFAULT_TIMEOUT_SECONDS = TimingDefaults.DEFAULT_TASK_TIMEOUT_SECONDS
SHORT_TIMEOUT_SECONDS = 30  # 30 seconds
VERY_SHORT_TIMEOUT_SECONDS = 29  # 29 seconds (allows for execution time)
TEST_NETWORK_DELAY_SECONDS = 0.1  # Network delay simulation

# Task priority constants
DEFAULT_PRIORITY_LEVEL = 5
HIGH_PRIORITY_LEVEL = 7

# Accuracy constants
DEFAULT_TARGET_ACCURACY = 0.9  # 90% accuracy target

# Jitter constants
JITTER_MIN_PERCENT = -20.0  # -20% jitter
JITTER_MAX_PERCENT = 20.0  # +20% jitter

# Test counts
TYPICAL_TEST_COUNT = 3
BATCH_TEST_COUNT = 5

# String length limits
ACTION_PREVIEW_MAX_LENGTH = 100

# Connection staleness threshold
CONNECTION_STALE_THRESHOLD_SECONDS = 0.05

# Long timeout for integration tests
INTEGRATION_TIMEOUT_SECONDS = 86400  # 24 hours
INTEGRATION_TIMEOUT_MIN = 86390  # Minimum acceptable (allows for execution time)
