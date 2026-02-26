"""
Serialization boundary (dict ↔ dataclass).

This is intentionally dumb. Smart logic here causes drift.
"""

from dataclasses import asdict, is_dataclass
from typing import Type, TypeVar, Any, Dict

from .errors import PayloadValidationError

T = TypeVar("T")


def payload_to_dict(payload: Any) -> Dict[str, Any]:
    """
    Convert payload dataclass to dict.

    Args:
        payload: Dataclass instance

    Returns:
        Dict representation of payload

    Raises:
        TypeError: If payload is not a dataclass
    """
    if not is_dataclass(payload):
        raise TypeError(f"payload must be a dataclass, got {type(payload)}")
    return asdict(payload)


def dict_to_payload(data: Dict[str, Any], cls: Type[T]) -> T:
    """
    Convert dict to payload dataclass.

    Args:
        data: Dictionary with payload fields
        cls: Target dataclass type

    Returns:
        Instance of cls

    Raises:
        PayloadValidationError: If data doesn't match cls schema
    """
    if not is_dataclass(cls):
        raise TypeError(f"cls must be a dataclass, got {cls}")

    try:
        return cls(**data)
    except TypeError as e:
        raise PayloadValidationError(
            f"Failed to construct {cls.__name__} from data: {e}"
        ) from e
    except ValueError as e:
        raise PayloadValidationError(
            f"Invalid data for {cls.__name__}: {e}"
        ) from e
