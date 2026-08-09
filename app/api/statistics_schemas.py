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
    email_events: int = Field(ge=0)
    email_rejections: int = Field(ge=0)
    email_next_stages: int = Field(ge=0)


class ApplicationSyncResponse(BaseModel):
    checked: int = Field(ge=0)
    updated: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    skipped: int = Field(ge=0)
    failed: int = Field(ge=0)
