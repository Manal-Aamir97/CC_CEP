"""Rule-based security validator for the simulated cloud console.

The validator never calls the LLM and never reads its output: it scores only the
configuration JSON the participant submits, against the task's scoring rules.
"""
from validator.schema import ConfigError, check_config, config_problems, submission_problems
from validator.validator import Finding, ValidationResult, load_task, validate

__all__ = [
    "ConfigError", "check_config", "config_problems", "submission_problems",
    "Finding", "ValidationResult", "load_task", "validate",
]
