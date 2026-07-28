from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.application_status import APPLICATION_STATUSES, ApplicationStatus
from app.domain.models import ApplicationQuestion, ProfileFact, TaskState, Vacancy
from app.domain.policy import SENSITIVE_CATEGORIES, assess_vacancy, prepare_answers
from app.domain.vacancy_attributes import EMPLOYMENT_TYPE_ORDER, EmploymentType
from app.storage.documents import SavedDocument
from app.storage.tables import (
    ApplicationAnswerRow,
    ApplicationRow,
    BrowserSessionRow,
    CvFileRow,
    EvidenceArtifactRow,
    HumanActionCheckpointRow,
    ProfileFactRow,
    TaskTransitionRow,
    UserRow,
    VacancyRow,
    WorkflowTaskRow,
)


class EntityNotFoundError(LookupError):
    pass


class DuplicateEntityError(ValueError):
    pass


class RecruitmentService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_user(self, display_name: str) -> UserRow:
        user = UserRow(display_name=display_name)
        self._session.add(user)
        self._session.commit()
        return user

    def add_profile_fact(
        self,
        user_id: str,
        *,
        category: str,
        name: str,
        value: str,
        is_verified: bool,
    ) -> ProfileFactRow:
        self._require_user(user_id)
        fact = ProfileFactRow(
            user_id=user_id,
            category=category,
            name=name,
            value=value,
            is_verified=is_verified,
        )
        self._session.add(fact)
        try:
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            raise DuplicateEntityError("Profile fact already exists") from error
        return fact

    def add_cv_file(self, user_id: str, document: SavedDocument) -> CvFileRow:
        self._require_user(user_id)
        existing = self._session.scalar(
            select(CvFileRow).where(
                CvFileRow.user_id == user_id, CvFileRow.sha256 == document.sha256
            )
        )
        if existing is not None:
            # Re-uploading byte-for-byte the same resume is a normal thing to do (retry after a
            # failed analysis, re-selecting the same file, etc.) — treat it as "already have it"
            # rather than an error, and let the caller (e.g. the dashboard) just proceed to
            # analyze the existing record instead of failing the whole flow.
            return existing
        cv_file = CvFileRow(
            user_id=user_id,
            original_filename=document.original_filename,
            storage_path=str(document.storage_path),
            content_type=document.content_type,
            sha256=document.sha256,
            size_bytes=document.size_bytes,
        )
        self._session.add(cv_file)
        try:
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            raise DuplicateEntityError("This CV file was already uploaded") from error
        return cv_file

    def list_cv_files(self, user_id: str) -> list[CvFileRow]:
        self._require_user(user_id)
        return list(
            self._session.scalars(
                select(CvFileRow)
                .where(CvFileRow.user_id == user_id)
                .order_by(CvFileRow.created_at.desc(), CvFileRow.id.desc())
            )
        )

    def get_cv_file(self, user_id: str, cv_file_id: str) -> CvFileRow:
        cv_file = self._session.get(CvFileRow, cv_file_id)
        if cv_file is None or cv_file.user_id != user_id:
            raise EntityNotFoundError("CV file not found for this user")
        return cv_file

    def active_cv_file(self, user_id: str, cv_file_id: str | None = None) -> CvFileRow | None:
        user = self._require_user(user_id)
        selected_cv_file_id = cv_file_id or user.active_cv_file_id
        if selected_cv_file_id is None:
            return None
        return self.get_cv_file(user_id, selected_cv_file_id)

    def set_active_cv_file(self, user_id: str, cv_file_id: str) -> CvFileRow:
        user = self._require_user(user_id)
        cv_file = self.get_cv_file(user_id, cv_file_id)
        user.active_cv_file_id = cv_file.id
        self._session.commit()
        return cv_file

    def delete_cv_file(self, user_id: str, cv_file_id: str) -> Path:
        user = self._require_user(user_id)
        cv_file = self.get_cv_file(user_id, cv_file_id)
        stored_path = Path(cv_file.storage_path)
        self._session.execute(
            update(ApplicationRow)
            .where(ApplicationRow.selected_cv_file_id == cv_file.id)
            .values(selected_cv_file_id=None)
        )
        replacement = self._session.scalar(
            select(CvFileRow)
            .where(CvFileRow.user_id == user_id, CvFileRow.id != cv_file.id)
            .order_by(CvFileRow.created_at.desc(), CvFileRow.id.desc())
        )
        if user.active_cv_file_id == cv_file.id:
            user.active_cv_file_id = replacement.id if replacement is not None else None
        self._session.delete(cv_file)
        self._session.commit()
        return stored_path

    def save_cv_profile(
        self,
        user_id: str,
        cv_file_id: str,
        *,
        skills: list[str],
        experience_summary: str,
        search_keywords: str,
        years_of_experience: float | None,
    ) -> CvFileRow:
        user = self._require_user(user_id)
        cv_file = self.get_cv_file(user_id, cv_file_id)
        cv_file.skills = list(dict.fromkeys(skill.strip() for skill in skills if skill.strip()))
        cv_file.experience_summary = experience_summary.strip()
        cv_file.search_keywords = search_keywords.strip()
        cv_file.years_of_experience = years_of_experience
        cv_file.analyzed_at = datetime.now(UTC)
        user.active_cv_file_id = cv_file.id
        self._session.commit()
        return cv_file

    def verified_profile_facts(
        self, user_id: str, cv_file_id: str | None = None
    ) -> list[ProfileFact]:
        user = self._require_user(user_id)
        cv_file = self.active_cv_file(user_id, cv_file_id)
        excluded_resume_categories = (
            {"skill", "experience_summary"}
            if cv_file is not None and cv_file.analyzed_at is not None
            else set()
        )
        facts = [
            ProfileFact(fact.category, fact.name, fact.value, fact.is_verified)
            for fact in user.facts
            if fact.is_verified and fact.category not in excluded_resume_categories
        ]
        if cv_file is not None and cv_file.analyzed_at is not None:
            facts.extend(ProfileFact("skill", skill, "", True) for skill in cv_file.skills)
            if cv_file.experience_summary:
                facts.append(
                    ProfileFact(
                        "experience_summary",
                        "experience_summary",
                        cv_file.experience_summary,
                        True,
                    )
                )
        return facts

    def create_vacancy(
        self,
        *,
        source_url: str,
        title: str,
        company: str,
        required_skills: list[str],
        preferred_skills: list[str],
        location: str = "",
        description_text: str = "",
        adapter_name: str = "generic",
        source_evidence_url: str | None = None,
        application_fields: list[dict[str, object]] | None = None,
        requires_sensitive_review: bool = False,
        published_at: datetime | None = None,
        salary_text: str = "",
        work_format: str = "unspecified",
        employment_types: tuple[EmploymentType, ...] | list[EmploymentType] = (),
    ) -> VacancyRow:
        # Validate work_format
        valid_work_formats = {"remote", "hybrid", "office", "unspecified"}
        if work_format not in valid_work_formats:
            raise ValueError(
                f"Invalid work_format: {work_format}. Must be one of {valid_work_formats}"
            )

        # Normalize salary_text
        normalized_salary = salary_text.strip()

        # Canonicalize employment_types
        normalized_types = (
            tuple(employment_types) if isinstance(employment_types, list) else employment_types
        )

        # Validate employment types and preserve order from EMPLOYMENT_TYPE_ORDER
        valid_set = set(EMPLOYMENT_TYPE_ORDER)
        for et in normalized_types:
            if et not in valid_set:
                raise ValueError(
                    f"Invalid employment type: {et}. Must be one of {EMPLOYMENT_TYPE_ORDER}"
                )

        # Remove duplicates and maintain order from EMPLOYMENT_TYPE_ORDER
        seen = set()
        ordered_unique = []
        for et in EMPLOYMENT_TYPE_ORDER:
            if et in normalized_types and et not in seen:
                ordered_unique.append(et)
                seen.add(et)
        canonical_employment_types = tuple(ordered_unique)

        vacancy = VacancyRow(
            source_url=source_url,
            title=title,
            company=company,
            required_skills=required_skills,
            preferred_skills=preferred_skills,
            location=location,
            description_text=description_text,
            adapter_name=adapter_name,
            source_evidence_url=source_evidence_url,
            application_fields=application_fields or [],
            requires_sensitive_review=requires_sensitive_review,
            published_at=published_at,
            salary_text=normalized_salary,
            work_format=work_format,
            employment_types=canonical_employment_types,
        )
        self._session.add(vacancy)
        try:
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            raise DuplicateEntityError("Vacancy source URL already exists") from error
        return vacancy

    def prepare_application(
        self, user_id: str, vacancy_id: str, cv_file_id: str | None = None
    ) -> ApplicationRow:
        self._require_user(user_id)
        vacancy_row = self._session.get(VacancyRow, vacancy_id)
        if vacancy_row is None:
            raise EntityNotFoundError("Vacancy not found")
        if cv_file_id is not None:
            cv_file = self._session.get(CvFileRow, cv_file_id)
            if cv_file is None:
                raise EntityNotFoundError("CV file not found")
            if cv_file.user_id != user_id:
                raise EntityNotFoundError("CV file not found for this user")
        profile_facts = self.verified_profile_facts(user_id, cv_file_id)
        assessment = assess_vacancy(
            Vacancy(
                source_url=vacancy_row.source_url,
                title=vacancy_row.title,
                company=vacancy_row.company,
                required_skills=frozenset(vacancy_row.required_skills),
                preferred_skills=frozenset(vacancy_row.preferred_skills),
            ),
            profile_facts,
        )
        warnings = [
            f"Missing required skill: {skill}" for skill in assessment.missing_required_skills
        ]
        if vacancy_row.requires_sensitive_review:
            warnings.append("Sensitive application fields require human review")
        if cv_file_id is None:
            warnings.append("No CV selected")
        application = ApplicationRow(
            user_id=user_id,
            vacancy_id=vacancy_id,
            selected_cv_file_id=cv_file_id,
            status="awaiting_review",
            match_score=assessment.score,
            warnings=warnings,
        )
        questions = self._application_questions(vacancy_row.application_fields)
        prepared_answers = prepare_answers(questions, profile_facts)
        question_by_id = {question.field_id: question for question in questions}
        for answer in prepared_answers:
            question = question_by_id[answer.field_id]
            application.answers.append(
                ApplicationAnswerRow(
                    field_id=answer.field_id,
                    label=question.label,
                    semantic_category=question.semantic_category,
                    is_required=question.is_required,
                    answer=answer.answer,
                    answer_source="profile_fact" if answer.answer is not None else "missing",
                    source_fact_name=answer.source_fact_name,
                    requires_review=answer.requires_review,
                    warning=answer.warning,
                )
            )
        self._session.add(application)
        try:
            self._session.flush()
            workflow_task = WorkflowTaskRow(
                application_id=application.id,
                idempotency_key=f"application-review:{application.id}",
                state=TaskState.SCHEDULED.value,
                priority=10,
            )
            workflow_task.transitions.append(
                TaskTransitionRow(
                    previous_state=TaskState.PENDING.value,
                    new_state=TaskState.SCHEDULED.value,
                    reason="application queued for durable review checkpoint",
                    worker="api",
                    attempt_number=0,
                    evidence=[f"application:{application.id}"],
                )
            )
            self._session.add(workflow_task)
            self._session.commit()
        except IntegrityError as error:
            self._session.rollback()
            raise DuplicateEntityError("Application already exists") from error
        return application

    def list_review_queue(
        self,
        application_id: str | None = None,
    ) -> list[tuple[ApplicationRow, VacancyRow, CvFileRow | None, WorkflowTaskRow]]:
        statement = (
            select(ApplicationRow, VacancyRow, CvFileRow, WorkflowTaskRow)
            .join(VacancyRow, VacancyRow.id == ApplicationRow.vacancy_id)
            .outerjoin(CvFileRow, CvFileRow.id == ApplicationRow.selected_cv_file_id)
            .join(WorkflowTaskRow, WorkflowTaskRow.application_id == ApplicationRow.id)
            .order_by(ApplicationRow.created_at)
        )
        if application_id is None:
            statement = statement.where(ApplicationRow.status == "awaiting_review")
        else:
            statement = statement.where(ApplicationRow.id == application_id)
        return list(self._session.execute(statement).tuples())

    def schedule_greenhouse_browser_review(self, application_id: str) -> WorkflowTaskRow:
        application = self._session.scalar(
            select(ApplicationRow).where(ApplicationRow.id == application_id).with_for_update()
        )
        if application is None:
            raise EntityNotFoundError("Application not found")
        if application.status != "awaiting_review":
            raise DuplicateEntityError("Application is no longer awaiting review")
        vacancy = self._session.get(VacancyRow, application.vacancy_id)
        if vacancy is None:
            raise EntityNotFoundError("Vacancy not found")
        if vacancy.adapter_name != "greenhouse":
            raise DuplicateEntityError(
                "Browser review is supported only for Greenhouse applications"
            )
        task = self._session.scalar(
            select(WorkflowTaskRow)
            .where(WorkflowTaskRow.application_id == application_id)
            .with_for_update()
        )
        if task is None:
            raise EntityNotFoundError("Workflow task not found")
        if task.state not in {TaskState.SCHEDULED.value, TaskState.WAITING_FOR_USER.value}:
            raise DuplicateEntityError(f"Browser review cannot be scheduled from {task.state}")

        missing_required_answers = sorted(
            answer.label
            for answer in application.answers
            if answer.is_required and not answer.answer
        )
        unreviewed_sensitive_answers = sorted(
            answer.label
            for answer in application.answers
            if answer.answer
            and answer.semantic_category in SENSITIVE_CATEGORIES
            and answer.answer_source != "human_review"
        )
        field_definitions = vacancy.application_fields
        requires_resume = any(
            str(field.get("field_type") or "") == "file"
            and str(field.get("semantic_category") or "") == "resume"
            and bool(field.get("is_required", False))
            for field in field_definitions
        )
        if requires_resume and application.selected_cv_file_id is None:
            missing_required_answers.append("Resume")
        if missing_required_answers:
            raise DuplicateEntityError(
                f"Required review answers are missing: {', '.join(missing_required_answers)}"
            )
        if unreviewed_sensitive_answers:
            raise DuplicateEntityError(
                "Sensitive answers require explicit human review: "
                + ", ".join(unreviewed_sensitive_answers)
            )

        previous_state = task.state
        task.queue_name = "browser"
        task.task_payload = {"workflow": "greenhouse_review"}
        task.state = TaskState.SCHEDULED.value
        task.scheduled_for = datetime.now(UTC)
        task.transitions.append(
            TaskTransitionRow(
                previous_state=previous_state,
                new_state=TaskState.SCHEDULED.value,
                reason="human requested non-submitting Greenhouse browser preparation",
                worker="api",
                attempt_number=task.attempt_number,
                evidence=[f"application:{application_id}", "submission:false"],
            )
        )
        self._session.commit()
        return task

    def schedule_real_submission_apply(
        self, application_id: str, *, adapter_name: str, workflow: str, site_key: str
    ) -> WorkflowTaskRow:
        """Schedule a real, irreversible application submission via browser automation.

        Unlike :meth:`schedule_greenhouse_browser_review`, this results in an actual submit
        action on the target site (hh.ru or LinkedIn) once the worker picks up the task. Callers
        (the API layer) must have already validated an explicit, distinct confirmation from the
        user before calling this — see ``BrowserApplySubmitRequest`` in app/api/schemas.py.
        """
        application = self._session.scalar(
            select(ApplicationRow).where(ApplicationRow.id == application_id).with_for_update()
        )
        if application is None:
            raise EntityNotFoundError("Application not found")
        if application.status != "awaiting_review":
            raise DuplicateEntityError("Application is no longer awaiting review")
        vacancy = self._session.get(VacancyRow, application.vacancy_id)
        if vacancy is None:
            raise EntityNotFoundError("Vacancy not found")
        if vacancy.adapter_name != adapter_name:
            raise DuplicateEntityError(
                f"Real-submission apply is supported only for {adapter_name} applications"
            )
        task = self._session.scalar(
            select(WorkflowTaskRow)
            .where(WorkflowTaskRow.application_id == application_id)
            .with_for_update()
        )
        if task is None:
            raise EntityNotFoundError("Workflow task not found")
        if task.state not in {TaskState.SCHEDULED.value, TaskState.WAITING_FOR_USER.value}:
            raise DuplicateEntityError(f"Apply cannot be scheduled from {task.state}")

        # hh.ru's "resume" field always comes back as required from the vacancy API, but the
        # browser Easy-Response flow attaches whichever resume is already selected in the user's
        # hh.ru account UI — it is not something this adapter uploads or chooses, so it cannot be
        # satisfied through a text answer and must not block scheduling. This is specific to the
        # headhunter_apply workflow; Greenhouse's separate review path still requires a selected
        # CV file, and hh.ru's own cover-letter field (when the vacancy demands one) still blocks
        # as normal below.
        ignored_field_ids = {"resume"} if workflow == "headhunter_apply" else set()
        missing_required_answers = sorted(
            answer.label
            for answer in application.answers
            if answer.is_required and not answer.answer and answer.field_id not in ignored_field_ids
        )
        unreviewed_sensitive_answers = sorted(
            answer.label
            for answer in application.answers
            if answer.answer
            and answer.semantic_category in SENSITIVE_CATEGORIES
            and answer.answer_source != "human_review"
        )
        if missing_required_answers:
            raise DuplicateEntityError(
                f"Required review answers are missing: {', '.join(missing_required_answers)}"
            )
        if unreviewed_sensitive_answers:
            raise DuplicateEntityError(
                "Sensitive answers require explicit human review: "
                + ", ".join(unreviewed_sensitive_answers)
            )

        previous_state = task.state
        task.queue_name = "browser"
        task.task_payload = {"workflow": workflow}
        task.state = TaskState.SCHEDULED.value
        task.scheduled_for = datetime.now(UTC)
        task.transitions.append(
            TaskTransitionRow(
                previous_state=previous_state,
                new_state=TaskState.SCHEDULED.value,
                reason=f"human explicitly confirmed a real {site_key} application submission",
                worker="api",
                attempt_number=task.attempt_number,
                evidence=[
                    f"application:{application_id}",
                    "submission:pending",
                    f"site:{site_key}",
                ],
            )
        )
        self._session.commit()
        return task

    def retry_application_task(self, application_id: str) -> WorkflowTaskRow:
        task = self._session.scalar(
            select(WorkflowTaskRow)
            .where(WorkflowTaskRow.application_id == application_id)
            .with_for_update()
        )
        if task is None:
            raise EntityNotFoundError("Workflow task not found")
        retryable_states = {
            TaskState.FAILED.value,
            TaskState.INTERRUPTED.value,
            TaskState.CANCELLED.value,
            TaskState.WAITING_FOR_EXTERNAL_SYSTEM.value,
        }
        if task.state not in retryable_states:
            raise DuplicateEntityError(f"Workflow task cannot be retried from {task.state}")
        previous_state = task.state
        task.state = TaskState.SCHEDULED.value
        task.scheduled_for = datetime.now(UTC)
        task.transitions.append(
            TaskTransitionRow(
                previous_state=previous_state,
                new_state=TaskState.SCHEDULED.value,
                reason="human requested workflow retry",
                worker="api",
                attempt_number=task.attempt_number,
                evidence=[f"application:{application_id}", "action:retry"],
            )
        )
        self._session.commit()
        return task

    def request_human_action(
        self,
        application_id: str,
        *,
        kind: str,
        instructions: str,
        evidence: tuple[str, ...] = (),
    ) -> HumanActionCheckpointRow:
        task = self._session.scalar(
            select(WorkflowTaskRow)
            .where(WorkflowTaskRow.application_id == application_id)
            .with_for_update()
        )
        if task is None:
            raise EntityNotFoundError("Workflow task not found")
        if task.state != TaskState.RUNNING.value:
            raise DuplicateEntityError(f"Human action cannot be requested from {task.state}")
        active_checkpoint = self._session.scalar(
            select(HumanActionCheckpointRow).where(
                HumanActionCheckpointRow.task_id == task.id,
                HumanActionCheckpointRow.status == "waiting",
            )
        )
        if active_checkpoint is not None:
            raise DuplicateEntityError("A human action is already waiting")
        checkpoint = HumanActionCheckpointRow(
            task_id=task.id,
            kind=kind,
            instructions=instructions,
            evidence=list(evidence),
        )
        self._session.add(checkpoint)
        self._session.flush()
        task.state = TaskState.WAITING_FOR_USER.value
        task.transitions.append(
            TaskTransitionRow(
                previous_state=TaskState.RUNNING.value,
                new_state=TaskState.WAITING_FOR_USER.value,
                reason=f"human action required: {kind}",
                worker="browser-worker",
                attempt_number=task.attempt_number,
                evidence=[f"checkpoint:{checkpoint.id}", *evidence],
            )
        )
        self._session.commit()
        return checkpoint

    def resume_human_action(
        self, application_id: str, *, resolution_evidence: str
    ) -> WorkflowTaskRow:
        task = self._session.scalar(
            select(WorkflowTaskRow)
            .where(WorkflowTaskRow.application_id == application_id)
            .with_for_update()
        )
        if task is None:
            raise EntityNotFoundError("Workflow task not found")
        checkpoint = self._session.scalar(
            select(HumanActionCheckpointRow)
            .where(
                HumanActionCheckpointRow.task_id == task.id,
                HumanActionCheckpointRow.status == "waiting",
            )
            .order_by(HumanActionCheckpointRow.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        if checkpoint is None:
            raise EntityNotFoundError("No human action is waiting")
        if task.state != TaskState.WAITING_FOR_USER.value:
            raise DuplicateEntityError(f"Human action cannot resume a task in {task.state}")
        if checkpoint.kind == "application_review":
            raise DuplicateEntityError(
                "Application review checkpoints must be resolved with approve, reject, or skip"
            )
        checkpoint.status = "resumed"
        checkpoint.resolution_evidence = [resolution_evidence]
        checkpoint.resolved_at = datetime.now(UTC)
        task.state = TaskState.SCHEDULED.value
        task.scheduled_for = datetime.now(UTC)
        task.transitions.append(
            TaskTransitionRow(
                previous_state=TaskState.WAITING_FOR_USER.value,
                new_state=TaskState.SCHEDULED.value,
                reason=f"human action completed: {checkpoint.kind}",
                worker="api",
                attempt_number=task.attempt_number,
                evidence=[
                    f"checkpoint:{checkpoint.id}",
                    f"confirmation:{resolution_evidence}",
                ],
            )
        )
        self._session.commit()
        return task

    def update_application_materials(
        self,
        application_id: str,
        *,
        cover_letter_text: str,
        screening_answers: dict[str, str | None],
    ) -> ApplicationRow:
        application = self._session.scalar(
            select(ApplicationRow).where(ApplicationRow.id == application_id).with_for_update()
        )
        if application is None:
            raise EntityNotFoundError("Application not found")
        if application.status != "awaiting_review":
            raise DuplicateEntityError("Application materials are no longer editable")
        answers_by_id = {answer.field_id: answer for answer in application.answers}
        unknown_fields = sorted(set(screening_answers) - set(answers_by_id))
        if unknown_fields:
            raise EntityNotFoundError(f"Application fields not found: {', '.join(unknown_fields)}")
        application.cover_letter_text = cover_letter_text.strip()
        for field_id, supplied_answer in screening_answers.items():
            answer = answers_by_id[field_id]
            normalized_answer = supplied_answer.strip() if supplied_answer else None
            if normalized_answer == answer.answer:
                continue
            answer.answer = normalized_answer
            answer.answer_source = "human_review" if normalized_answer is not None else "missing"
            answer.source_fact_name = None
            if answer.semantic_category in SENSITIVE_CATEGORIES:
                answer.warning = "Sensitive declaration reviewed by user"
            else:
                answer.warning = (
                    "Missing verified profile fact"
                    if answer.is_required and normalized_answer is None
                    else None
                )
        self._session.commit()
        return application

    def decide_application(self, application_id: str, decision: str) -> ApplicationRow:
        application = self._session.scalar(
            select(ApplicationRow).where(ApplicationRow.id == application_id).with_for_update()
        )
        if application is None:
            raise EntityNotFoundError("Application not found")
        if application.status != "awaiting_review":
            raise DuplicateEntityError("Application is no longer awaiting review")
        application.status = {"approve": "approved", "reject": "rejected", "skip": "skipped"}[
            decision
        ]
        workflow_task = self._session.scalar(
            select(WorkflowTaskRow)
            .where(WorkflowTaskRow.application_id == application_id)
            .with_for_update()
        )
        if workflow_task is not None and workflow_task.state in {
            TaskState.SCHEDULED.value,
            TaskState.RUNNING.value,
            TaskState.WAITING_FOR_USER.value,
        }:
            previous_state = workflow_task.state
            workflow_task.state = TaskState.COMPLETED.value
            workflow_task.transitions.append(
                TaskTransitionRow(
                    previous_state=previous_state,
                    new_state=TaskState.COMPLETED.value,
                    reason=f"human review decision recorded: {decision}",
                    worker="api",
                    attempt_number=workflow_task.attempt_number,
                    evidence=[f"application:{application_id}", f"decision:{decision}"],
                )
            )
            for checkpoint in workflow_task.human_actions:
                if checkpoint.status == "waiting":
                    checkpoint.status = "resolved"
                    checkpoint.resolved_at = datetime.now(UTC)
                    checkpoint.resolution_evidence = [f"decision:{decision}"]
        self._session.commit()
        return application

    def update_application_status(
        self, application_id: str, status: ApplicationStatus
    ) -> ApplicationRow:
        application = self._session.scalar(
            select(ApplicationRow).where(ApplicationRow.id == application_id).with_for_update()
        )
        if application is None:
            raise EntityNotFoundError("Application not found")
        if status not in APPLICATION_STATUSES:
            raise ValueError("Unsupported application status")
        application.status = status
        self._session.commit()
        return application

    def delete_user(self, user_id: str) -> tuple[list[Path], list[str], list[str]]:
        user = self._require_user(user_id)
        stored_paths = self._session.scalars(
            select(CvFileRow.storage_path).where(CvFileRow.user_id == user_id)
        ).all()
        browser_state_paths = list(
            self._session.scalars(
                select(BrowserSessionRow.encrypted_state_path).where(
                    BrowserSessionRow.user_id == user_id
                )
            ).all()
        )
        application_ids = self._session.scalars(
            select(ApplicationRow.id).where(ApplicationRow.user_id == user_id)
        ).all()
        evidence_paths: list[str] = []
        if application_ids:
            evidence_paths = list(
                self._session.scalars(
                    select(EvidenceArtifactRow.relative_path)
                    .join(
                        HumanActionCheckpointRow,
                        HumanActionCheckpointRow.id == EvidenceArtifactRow.checkpoint_id,
                    )
                    .join(
                        WorkflowTaskRow,
                        WorkflowTaskRow.id == HumanActionCheckpointRow.task_id,
                    )
                    .where(WorkflowTaskRow.application_id.in_(application_ids))
                ).all()
            )
            self._session.execute(
                delete(WorkflowTaskRow).where(WorkflowTaskRow.application_id.in_(application_ids))
            )
            self._session.execute(delete(ApplicationRow).where(ApplicationRow.user_id == user_id))
        self._session.delete(user)
        self._session.commit()
        return (
            [Path(stored_path) for stored_path in stored_paths],
            evidence_paths,
            browser_state_paths,
        )

    def _require_user(self, user_id: str) -> UserRow:
        user = self._session.get(UserRow, user_id)
        if user is None:
            raise EntityNotFoundError("User not found")
        return user

    @staticmethod
    def _application_questions(
        application_fields: list[dict[str, object]],
    ) -> tuple[ApplicationQuestion, ...]:
        questions: list[ApplicationQuestion] = []
        for field in application_fields:
            field_id = str(field.get("field_id") or "").strip()
            semantic_category = str(field.get("semantic_category") or "custom")
            field_type = str(field.get("field_type") or "")
            if not field_id or semantic_category in {"resume", "cover_letter"}:
                continue
            if field_type == "file":
                continue
            questions.append(
                ApplicationQuestion(
                    field_id=field_id,
                    label=str(field.get("label") or field_id),
                    semantic_category=semantic_category,
                    is_required=bool(field.get("is_required", False)),
                )
            )
        return tuple(questions)
