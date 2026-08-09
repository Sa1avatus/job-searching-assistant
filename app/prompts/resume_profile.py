from __future__ import annotations

_MAX_RESUME_CHARS = 48_000
_OMISSION_MARKER = "\n\n[... middle of resume omitted for length ...]\n\n"


def build_resume_profile_prompt(resume_text: str) -> str:
    selected_resume_text = select_resume_text(resume_text)
    return (
        "Read this resume text and extract ONLY what is explicitly stated. Do not infer skills "
        "that are not mentioned, do not estimate seniority beyond what is stated, and do not "
        "invent employer names or dates.\n\n"
        "For skills, provide an exhaustive inventory of every explicitly named programming "
        "language, framework, library, database, cloud service, operating system, platform, "
        "enterprise product, protocol, standard, testing tool, DevOps tool, methodology, "
        "business or domain system, professional certification, and spoken language. Do not "
        "merge distinct technologies into broad categories, do not omit older experience, and "
        "do not infer any skill that is absent. Keep every skill as a short standalone label.\n\n"
        f"Resume text:\n{selected_resume_text}\n\n"
        "Respond with a single JSON object matching exactly this shape:\n"
        "{\n"
        '  "skills": ["<short skill name>", ...],\n'
        '  "experience_summary": "<2-4 sentence factual summary of the candidate work history>",\n'
        '  "search_keywords": "<3-8 words a job search engine would use to find matching roles>",\n'
        '  "years_of_experience": <number or null if not determinable>\n'
        "}"
    )


def select_resume_text(resume_text: str) -> str:
    if len(resume_text) <= _MAX_RESUME_CHARS:
        return resume_text
    content_budget = _MAX_RESUME_CHARS - len(_OMISSION_MARKER)
    beginning_characters = content_budget // 2
    ending_characters = content_budget - beginning_characters
    return resume_text[:beginning_characters] + _OMISSION_MARKER + resume_text[-ending_characters:]
