"""
Invariant registry loader (pure data).

No logic. No branching. No enforcement.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, Optional
import yaml


SeverityType = Literal["fatal", "warn", "soft"]
ActionType = Literal["halt_router", "continue", "annotate_only"]


@dataclass(frozen=True)
class Invariant:
    """
    Invariant definition.

    This is pure data, not policy.
    """

    invariant_id: str
    severity: SeverityType
    applies_to: str
    description: str
    action: ActionType
    notes: Optional[str] = None


class InvariantRegistry:
    """
    Load and query invariant definitions.

    No execution. No side effects. Pure data access.
    """

    def __init__(self, registry_path: Optional[Path] = None):
        """
        Load invariant registry from YAML.

        Args:
            registry_path: Path to registry.yaml. If None, uses default.
        """
        if registry_path is None:
            # Default: adjacent to this module
            registry_path = Path(__file__).parent.parent / "invariants" / "registry.yaml"

        self._registry_path = registry_path
        self._invariants: Dict[str, Invariant] = {}
        self._load()

    def _load(self) -> None:
        """Load invariants from YAML file."""
        if not self._registry_path.exists():
            # Empty registry is valid (no invariants defined)
            return

        with self._registry_path.open("r") as f:
            raw = yaml.safe_load(f) or {}

        for invariant_id, spec in raw.items():
            if not isinstance(spec, dict):
                continue  # Skip malformed entries
            if invariant_id.startswith("#"):
                continue  # Skip comments

            self._invariants[invariant_id] = Invariant(
                invariant_id=invariant_id,
                severity=spec.get("severity", "warn"),
                applies_to=spec.get("applies_to", "unknown"),
                description=spec.get("description", ""),
                action=spec.get("action", "continue"),
                notes=spec.get("notes"),
            )

    def get_invariant(self, invariant_id: str) -> Optional[Invariant]:
        """
        Get invariant definition by ID.

        Args:
            invariant_id: Invariant identifier

        Returns:
            Invariant definition or None if not found
        """
        return self._invariants.get(invariant_id)

    def list_invariants(self) -> List[Invariant]:
        """
        List all registered invariants.

        Returns:
            List of invariant definitions
        """
        return list(self._invariants.values())

    def has_invariant(self, invariant_id: str) -> bool:
        """
        Check if invariant is registered.

        Args:
            invariant_id: Invariant identifier

        Returns:
            True if registered, False otherwise
        """
        return invariant_id in self._invariants


__all__ = ["Invariant", "InvariantRegistry", "SeverityType", "ActionType"]
