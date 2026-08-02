from enum import StrEnum


class WorkflowStepType(StrEnum):
    NAVIGATE = "navigate"
    FILL = "fill"
    UPLOAD = "upload"
    SELECT = "select"
    CHECK = "check"
    CLICK = "click"
    WAIT = "wait"
    ASSERT = "assert"
    HUMAN_REVIEW = "human_review"
    SUBMIT = "submit"


class WorkflowStatus(StrEnum):
    DRAFT = "draft"
    TESTING = "testing"
    ACTIVE = "active"
    BROKEN = "broken"
    ARCHIVED = "archived"


PRIVILEGED_WORKFLOW_STEP_TYPES: frozenset[WorkflowStepType] = frozenset(
    {WorkflowStepType.HUMAN_REVIEW, WorkflowStepType.SUBMIT}
)

BROWSER_WORKFLOW_STEP_TYPES: frozenset[WorkflowStepType] = frozenset(
    {
        WorkflowStepType.NAVIGATE,
        WorkflowStepType.FILL,
        WorkflowStepType.UPLOAD,
        WorkflowStepType.SELECT,
        WorkflowStepType.CHECK,
        WorkflowStepType.CLICK,
        WorkflowStepType.WAIT,
        WorkflowStepType.ASSERT,
        WorkflowStepType.SUBMIT,
    }
)
