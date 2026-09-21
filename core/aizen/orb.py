"""Orb vocabulary: the UI's visual state, owned by the backend (§27).

Leaf module on purpose: both the session state machine and the tool contracts
need it, and neither may pull in the other's package.
"""

from __future__ import annotations

from enum import StrEnum


class OrbState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SEARCHING = "SEARCHING"
    READING_FILE = "READING_FILE"
    CALCULATING = "CALCULATING"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"
