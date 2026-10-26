"""Talk to Your Editor: the feedback-to-edit direction layer.

A creator leaves plain-language, timestamped feedback on a finished edit.
Muse Spark / Muse Wonder propose strict typed edit operations, a
deterministic verifier checks them against locked safety rules, and the
applier writes a revised cuts plan without ever mutating the original job.
"""

__version__ = "0.1.0"

from .schema import Operation, SchemaError, validate_operations
from .timeline import Timeline
from .verifier import Verdict, verify_operations

__all__ = [
    "__version__",
    "Operation",
    "SchemaError",
    "validate_operations",
    "Timeline",
    "Verdict",
    "verify_operations",
]
