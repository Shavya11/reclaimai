"""Detectors are plugins. Each exposes detect() -> list[AtRiskRecord] and knows
nothing about diagnosis, policy or guardrails. V2's overdue-invoice detector drops
in here with no change anywhere else."""

from typing import Protocol

from ..enums import LeakType
from ..models import AtRiskRecord


class Detector(Protocol):
    leak_type: LeakType
    name: str

    def detect(self) -> list[AtRiskRecord]: ...


def detect_all(detectors: list[Detector]) -> list[AtRiskRecord]:
    out: list[AtRiskRecord] = []
    for d in detectors:
        out.extend(d.detect())
    return out
