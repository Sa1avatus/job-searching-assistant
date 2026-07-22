from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class VacancyRequest(BaseModel):
    source_url: HttpUrl
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=300)
    required_skills: set[str] = set()
    preferred_skills: set[str] = set()


class ProfileFactRequest(BaseModel):
    category: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1)
    is_verified: bool = False


class AssessmentRequest(BaseModel):
    vacancy: VacancyRequest
    profile_facts: list[ProfileFactRequest]


class AssessmentResponse(BaseModel):
    score: int
    matched_required_skills: list[str]
    missing_required_skills: list[str]
    matched_preferred_skills: list[str]
    recommendation: str


class HealthResponse(BaseModel):
    status: str
    submission_mode: str


class ConnectorCapabilityResponse(BaseModel):
    name: str
    vacancy_extraction: str
    authentication_requirement: str
    form_discovery: str
    file_upload_behavior: str
    validation_detection: str
    submission_detection: str
    confirmation_extraction: str
    submission_supported: bool
    known_limitations: list[str]


class BrowserHandoffRequest(BaseModel):
    source_url: HttpUrl


class BrowserHandoffResponse(BaseModel):
    platform: str
    canonical_url: str
    mode: str
    automated_actions_supported: bool
    instructions: list[str]


class UserRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)


class UserResponse(UserRequest):
    id: str


class ProfileFactResponse(ProfileFactRequest):
    id: str
    user_id: str


class CvFileResponse(BaseModel):
    id: str
    user_id: str
    original_filename: str
    content_type: str
    sha256: str
    size_bytes: int


class VacancyResponse(BaseModel):
    id: str
    source_url: str
    title: str
    company: str
    required_skills: list[str]
    preferred_skills: list[str]


class GreenhouseImportRequest(BaseModel):
    source_url: HttpUrl


class HeadHunterImportRequest(BaseModel):
    source_url: HttpUrl


class LinkedInReferenceImportRequest(BaseModel):
    source_url: HttpUrl
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=300)
    location: str = Field(default="", max_length=300)
    description_text: str = ""


class ImportedVacancyResponse(VacancyResponse):
    location: str
    description_text: str
    adapter_name: str
    source_evidence_url: str
    application_fields: list[dict[str, object]]
    requires_sensitive_review: bool


class PrepareApplicationRequest(BaseModel):
    user_id: str
    vacancy_id: str
    cv_file_id: str | None = None


class BrowserReviewRequest(BaseModel):
    confirmation: Literal["prepare_without_submission"]


class BrowserApplySubmitRequest(BaseModel):
    """Distinct from BrowserReviewRequest on purpose: this confirmation causes a real submit."""

    confirmation: Literal["submit_real_application_i_understand_the_platform_tos_risk"]


class ApplicationResponse(BaseModel):
    id: str
    user_id: str
    vacancy_id: str
    selected_cv_file_id: str | None
    status: str
    match_score: int
    warnings: list[str]


class TaskTransitionResponse(BaseModel):
    previous_state: str
    new_state: str
    reason: str
    worker: str
    attempt_number: int
    evidence: list[str]


class WorkflowTaskResponse(BaseModel):
    id: str
    application_id: str | None
    idempotency_key: str
    queue_name: str
    state: str
    attempt_number: int
    priority: int
    transitions: list[TaskTransitionResponse]


class ReviewDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject|skip)$")


class ScreeningAnswerResponse(BaseModel):
    field_id: str
    label: str
    semantic_category: str
    is_required: bool
    answer: str | None
    answer_source: str
    source_fact_name: str | None
    requires_review: bool
    warning: str | None


class ScreeningAnswerEditRequest(BaseModel):
    field_id: str = Field(min_length=1, max_length=300)
    answer: str | None = Field(default=None, max_length=10_000)


class ApplicationMaterialsUpdateRequest(BaseModel):
    cover_letter_text: str = Field(default="", max_length=20_000)
    screening_answers: list[ScreeningAnswerEditRequest] = Field(
        default_factory=list, max_length=100
    )


class ApplicationMaterialsResponse(BaseModel):
    application_id: str
    cover_letter_text: str
    screening_answers: list[ScreeningAnswerResponse]
    missing_facts: list[str]
    requested_legal_declarations: list[str]


class EvidenceArtifactResponse(BaseModel):
    id: str
    kind: str
    content_type: str
    size_bytes: int
    url: str


class HumanActionCheckpointResponse(BaseModel):
    id: str
    kind: str
    status: str
    instructions: str
    evidence: list[str]
    artifacts: list[EvidenceArtifactResponse]


class HumanActionResumeRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=500)


class ReviewItemResponse(ApplicationResponse):
    company: str
    vacancy_title: str
    source_url: str
    adapter_name: str
    selected_cv_filename: str | None
    current_workflow_state: str
    cover_letter_text: str
    screening_answers: list[ScreeningAnswerResponse]
    missing_facts: list[str]
    requested_legal_declarations: list[str]
    active_human_action: HumanActionCheckpointResponse | None


class ExtractedProfileResponse(BaseModel):
    skills: list[str]
    experience_summary: str
    search_keywords: str
    years_of_experience: float | None


class ConfirmProfileFactsRequest(BaseModel):
    skills: list[str] = Field(default_factory=list, max_length=60)
    experience_summary: str = Field(default="", max_length=2_000)


class ConfirmedProfileFactResponse(BaseModel):
    id: str
    category: str
    name: str
    value: str
    is_verified: bool


class DiscoverHeadHunterVacanciesRequest(BaseModel):
    locations: list[str] = Field(
        default_factory=list,
        description="Free-text hh.ru location names, e.g. ['Москва']. Empty = anywhere.",
        max_length=20,
    )
    limit: int = Field(default=15, ge=1, le=50)
    search_text: str | None = Field(default=None, max_length=300)


class DiscoveryOutcomeResponse(BaseModel):
    application_id: str
    vacancy_id: str
    title: str
    company: str
    source_url: str
    match_score: int
    status: str


class DiscoverLinkedInVacanciesRequest(BaseModel):
    locations: list[str] = Field(
        default_factory=list,
        description="Free-text LinkedIn location, e.g. ['Berlin, Germany']. Empty = anywhere.",
        max_length=5,
    )
    limit: int = Field(default=15, ge=1, le=50)
    search_text: str | None = Field(default=None, max_length=300)
