from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def build_resume_profile_prompt(resume_text: str) -> str:
    template = _load_prompt("resume_profile.md")
    selected_resume_text = select_resume_text(resume_text)
    return template.replace("{resume_text}", selected_resume_text)


_MAX_RESUME_CHARS = 48_000
_OMISSION_MARKER = "\n\n[... middle of resume omitted for length ...]\n\n"


def select_resume_text(resume_text: str) -> str:
    if len(resume_text) <= _MAX_RESUME_CHARS:
        return resume_text
    content_budget = _MAX_RESUME_CHARS - len(_OMISSION_MARKER)
    beginning_characters = content_budget // 2
    ending_characters = content_budget - beginning_characters
    return resume_text[:beginning_characters] + _OMISSION_MARKER + resume_text[-ending_characters:]
