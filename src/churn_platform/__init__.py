"""Customer behavior feature and churn prediction platform."""

from .generator import GeneratorConfig, generate_events
from .validation import validate_event

__all__ = ["GeneratorConfig", "generate_events", "validate_event"]
