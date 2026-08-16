from typing import NoReturn

from app.domain.workflow_human_review import HumanReviewWorkflowStep


class WorkflowHumanReviewRequired(RuntimeError):
    def __init__(self, checkpoint_key: str) -> None:
        super().__init__("Workflow requires human review")
        self.checkpoint_key = checkpoint_key


def execute_human_review_workflow_step(step: HumanReviewWorkflowStep) -> NoReturn:
    raise WorkflowHumanReviewRequired(step.parameters.checkpoint_key)
