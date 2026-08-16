# Resume Profile Extraction Prompt

Read this resume text and extract ONLY what is explicitly stated. Do not infer skills
that are not mentioned, do not estimate seniority beyond what is stated, and do not
invent employer names or dates.

For skills, provide an exhaustive inventory of every explicitly named programming language, framework, library, database, cloud service, operating system, platform, enterprise product, protocol, standard, testing tool, DevOps tool, methodology, business or domain system, professional certification, and spoken language.
Do not merge distinct technologies into broad categories, do not omit older experience, and do not infer any skill that is absent. Keep every skill as a short standalone label.

Resume text:
{resume_text}

Respond with a single JSON object matching exactly this shape:
{{
  "skills": ["<short skill name>", ...],
  "experience_summary": "<2-4 sentence factual summary of the candidate work history>",
  "search_keywords": "<3-8 words a job search engine would use to find matching roles>",
  "years_of_experience": <number or null if not determinable>
}}
