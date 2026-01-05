"""Helper functions for Hikvision integration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HikvisionChannel:
    """Represents a video input channel on a Hikvision device."""

    id: int
    name: str
    enabled: bool = True
