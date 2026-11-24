"""ramen robotics: governed task dispatch for robots, built on ramen foundry."""

from .gate import GateDecision, Outcome, RoboticsGate, verify_audit_file

__version__ = "0.1.0"

__all__ = ["GateDecision", "Outcome", "RoboticsGate", "__version__", "verify_audit_file"]
