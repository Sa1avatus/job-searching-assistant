from pydantic import BaseModel, Field


class ApplicationStatisticsResponse(BaseModel):
    total: int = Field(ge=0)
    draft: int = Field(ge=0)
    saved: int = Field(ge=0)
    awaiting_review: int = Field(ge=0)
    approved: int = Field(ge=0)
    rejected: int = Field(ge=0)
    skipped: int = Field(ge=0)
    submitted: int = Field(ge=0)
    interview: int = Field(ge=0)
