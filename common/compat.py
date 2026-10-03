"""Python 3.10 compatibility shims.

Ubuntu 22.04 ships Python 3.10; upstream openpilot targets 3.11+.
This module provides back-ported aliases so EOP daemons run on both.

Usage:
    from openpilot.common.compat import UTC, StrEnum
"""
from datetime import timezone

# datetime.UTC is new in 3.11; timezone.utc is available since 3.2.
UTC = timezone.utc

try:
    from enum import StrEnum  # Python 3.11+
except ImportError:
    from enum import Enum
    class StrEnum(str, Enum):  # type: ignore[no-redef]
        pass
