# FarmaFacil New Feature — Full-Cycle Feature Implementation Workflow

Implement a new feature described by the user, add it to `IMPROVEMENT-PLAN.md`, and run the full mandatory release pipeline. Execute every step in order. Every step is mandatory — do not skip any. Stop and report if any step fails.

**Feature request**: $ARGUMENTS

## Project Info

- **Path**: `/Users/dgonzalez/Documents/workspace/farmafacil/`
- **Stack**: Python 3.12 + FastAPI + SQLAlchemy 2.0 (async) + Pillow + Anthropic SDK
- **Database**: SQLite (local dev), PostgreSQL 16 (production via Docker)
- **Bot**: WhatsApp Business Cloud API (Meta Graph API v22.0)
- **Drug search**: Farmatodo Algolia API
- **Geocoding**: OpenStreetMap Nominatim
- **LLM**: Claude Haiku for intent detection
- **Admin**: SQLAdmin dashboard at /admin (auth-protected)
- **Tests**: pytest + pytest-asyncio (`.venv/bin/python -m pytest`)
- **Production server**: 10.0.0.114 (Docker Compose, ports: app=8100, postgres=5433)
- **ngrok tunnel**: amparo-chromophoric-christia.ngrok-free.dev
- **Version**: read from `src/farmafacil/__init__.py` → `__version__`

## Steps

### 1. Capture feature request

- Read the feature description from the prompt above
- If the description is empty or unclear, stop and ask the user for a feature description before proceeding
- Summarize the feature request back to the user for confirmation

### 2. Add to IMPROVEMENT-PLAN.md

- Read `IMPROVEMENT-PLAN.md` in the project root (create it if it doesn't exist)
- Determine the next sequential item number (scan all existing items across all priority sections)
- Add the new feature as a new entry under the appropriate priority section (default: P2 — Medium, unless the user specified a priority)
- Set status to **IN PROGRESS** with today's date
- Include the user's description as the Problem line

### 3. Plan the implementation

- Read `CLAUDE.md` for project context
- Explore the codebase to understand the relevant files, patterns, and dependencies
- Read relevant docs in `docs/` (architecture.md, bot-flow.md, api-reference.md)
- Design the implementation approach
- Show the plan to the user with: files to modify, approach, potential risks
- Wait for user approval before writing any code

### 4. Implement

- Write all code changes following the approved plan
- Follow existing patterns and conventions in the codebase
- Use async/await for all DB and HTTP operations
- Use service classes for external interactions (not raw connections)
- Add type hints on all public functions
- Write tests for the new/changed functionality

### 5. Run tests

- Run `.venv/bin/python -m pytest tests/ -v -k "not integration"`
- ALL tests must pass — if any fail, fix them before continuing
- Report test count and pass/fail status

### 6. Code review

- Use the `code-reviewer` agent to review all modified files
- Check for: correctness, SQL injection, input validation, race conditions, async issues
- If any BLOCKER or MAJOR issues are found: fix them, then re-run step 5

### 7. Update documentation

- Read `IMPROVEMENT-PLAN.md` and update the new item:
  - Change status from IN PROGRESS to **DONE** with today's date
  - Fill in implementation details: files modified, solution summary
- Update `CLAUDE.md` if new endpoints, tables, services, or key paths were added
- Update relevant docs in `docs/` (architecture.md, api-reference.md, bot-flow.md, etc.)
- If no other docs need updating, explicitly confirm "No other docs affected"

### 8. Bump version

- Read current version from `src/farmafacil/__init__.py`
- Determine the increment:
  - **Patch** (x.y.Z): bug fixes, small improvements
  - **Minor** (x.Y.0): new features, new endpoints, significant enhancements
  - **Major** (X.0.0): breaking changes, major architecture shifts
- Update `__version__` in `src/farmafacil/__init__.py`
- Update `version` in `pyproject.toml` to match

### 9. Commit & push

- Stage specific changed files (do NOT use `git add -A` or `git add .`)
- Do NOT stage `.env`, `farmafacil.db`, `__pycache__/`, `.pytest_cache/`
- Create a descriptive commit message summarizing all changes
- Push to GitHub (remote: origin, branch: main)

### 10. Deploy to production

```bash
ssh -i ~/.ssh/id_ed25519 dgonzalez@10.0.0.114 \
  "cd ~/workspace/farmafacil && git pull && docker compose build --no-cache && docker compose down && docker compose up -d"
```

### 11. Verify deployment

```bash
# Health check
ssh -i ~/.ssh/id_ed25519 dgonzalez@10.0.0.114 "curl -s http://localhost:8100/health"

# Check logs for errors
ssh -i ~/.ssh/id_ed25519 dgonzalez@10.0.0.114 "cd ~/workspace/farmafacil && docker compose logs --tail=20 app"
```

- Health endpoint must return `{"status":"ok"}`
- Logs must show no errors during startup
- If deployment fails, stop and investigate

### 12. Update memory

- Update the project's auto-memory `MEMORY.md` file with:
  - New version number
  - Summary of what changed (the new feature that was implemented)
  - Updated improvement plan status counts (done/pending)

### 13. Report

Print a summary table with:
- Version (old → new)
- Feature implemented (title and description)
- Files changed (count and list)
- Test results (count, pass/fail)
- Deployment status (success/failure)
- Commit hash
- Admin URL: https://amparo-chromophoric-christia.ngrok-free.dev/admin/
