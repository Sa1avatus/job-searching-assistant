# Application Materials — Screening & Output Rules

SCREENING-ANSWER RULES:

1. Answer a screening field only when its answer is explicitly supported by
   the candidate facts.
2. Use the exact field_id supplied in the screening-fields data.
3. Keep each answer concise and direct.
4. If the facts do not provide a reliable answer, omit that field_id entirely.
5. Never guess work authorization, citizenship, disability status, protected
   personal information, salary, notice period or relocation availability.

VACANCY DATA:
IMPORTANT: The vacancy data describes the employer's requirements and future
job responsibilities. It is never evidence of the candidate's past experience.

Never convert any vacancy requirement, responsibility, tool, team size,
project name or required duration into a statement about the candidate.

A technology, number, duration or responsibility may be attributed to the
candidate only when the same information is explicitly present in
CANDIDATE FACTS.

CANDIDATE FACTS:
CANDIDATE FACTS are the only allowed source for statements about the candidate.
VACANCY DATA may only be used to choose which candidate facts are relevant.

OPEN SCREENING FIELDS:

OUTPUT REQUIREMENTS:

Return exactly one valid JSON object.
Do not return Markdown.
Do not return a code block.
Do not add explanations before or after the JSON.
Use exactly these two top-level keys:
- cover_letter_text
- screening_answers

cover_letter_text must contain the complete finished cover letter.
screening_answers must be a JSON object whose keys are valid field_id values
from OPEN SCREENING FIELDS.
