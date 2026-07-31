# Project instructions

`AGENT.md` is the product constitution and source of requirements for this project.

- Use Python 3.12+ and English identifiers.
- Keep domain policy independent from browser, database, API, and LLM adapters.
- Default to review mode and never submit a real application from automated tests.
- Run `python -m pytest -q` for the current verified core.
- Do not claim an external integration works until it has been exercised.
- For delegated implementation, follow the workspace Local Code Worker workflow exactly: run from `D:\OpenAIProjects\local-code-worker`, use the provider and model from its `.env` without command-line overrides, and require Codex approval plus the Worker's interactive `y/N` confirmation.
