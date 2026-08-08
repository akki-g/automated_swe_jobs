from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SendResult:
    success: bool
    provider: str
    ambiguous: bool = False  # True on timeout/unclear response — never safe to retry via failover
    error: str | None = None


class SmsProvider(ABC):
    name: str

    @abstractmethod
    async def send(self, to: str, body: str) -> SendResult:
        ...
