from enum import StrEnum


class ApplicationEmailOutcome(StrEnum):
    REJECTED = "rejected"
    NEXT_STAGE = "next_stage"
    UNKNOWN = "unknown"
