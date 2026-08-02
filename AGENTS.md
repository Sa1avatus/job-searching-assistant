# Project instructions

`AGENT.md` is the product constitution and source of requirements for this project.

- Use Python 3.12+ and English identifiers.
- Keep domain policy independent from browser, database, API, and LLM adapters.
- Default to review mode and never submit a real application from automated tests.
- Run `python -m pytest -q` for the current verified core.
- Do not claim an external integration works until it has been exercised.
- For delegated implementation, follow the workspace container-only Local Code Worker workflow exactly: use the wrappers in `D:\OpenAIProjects\scripts`, let the container's `/data/.env` select the provider and model, launch generation only through the wrapper's `run --codex` flow, and obtain approval in Codex chat before applying. Never use an interactive terminal `y/N`, `--yes`, or the host virtual environment.
- When a Worker proposal has only minor, localized errors, Codex must fix those errors directly and verify the result instead of sending a corrective rerun to the model. Reserve Worker reruns for substantial contract, architecture, security, or behavioral failures. This project-specific rule follows the user's explicit instruction and overrides the workspace default for minor proposal defects.
