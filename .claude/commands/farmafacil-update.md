# FarmaFacil Update — Full-Cycle Improvement Workflow

Read `IMPROVEMENT-PLAN.md`, pick the next pending item, implement it, and run the full mandatory release pipeline. Execute every step in order. Every step is mandatory — do not skip any. Stop and report if any step fails.

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
- **Production server**: 10.0.0.116 (Docker Compose, ports: app=8100, postgres=5433)
- **ngrok tunnel**: amparo-chromophoric-christia.ngrok-free.dev
- **Version**: read from `src/farmafacil/__init__.py` → `__version__`

## Steps

### 1. Read IMPROVEMENT-PLAN.md

- Read `IMPROVEMENT-PLAN.md` in the project root
- Find all items with status **PENDING** or **IN PROGRESS**
- Pick the highest priority item: P0 first, then P1, P2, P3. Within the same priority, pick the lowest item number
- Display the selected item to the user (number, title, description, priority)
- Wait for user confirmation before proceeding

### 2. Plan the implementation

- Read `CLAUDE.md` for project context
- Read relevant docs in `docs/` (architecture.md, bot-flow.md, api-reference.md, etc.)
- Explore the codebase to understand the relevant files, patterns, and dependencies
- Design the implementation approach
- Show the plan to the user with: files to modify, approach, potential risks
- Wait for user approval before writing any code

### 3. Implement

- Write all code changes following the approved plan
- Follow existing patterns and conventions in the codebase
- Use async/await for all DB and HTTP operations
- Use service classes for external interactions (not raw connections)
- Add type hints on all public functions
- Write tests for the new/changed functionality

### 4. Run tests

- Run `.venv/bin/python -m pytest tests/ -v -k "not integration"`
- ALL tests must pass — if any fail, fix them before continuing
- Report test count and pass/fail status

### 5. Code review

- Use the `code-reviewer` agent to review all modified files
- Check for: correctness, SQL injection, input validation, race conditions, async issues
- If any BLOCKER or MAJOR issues are found: fix them, then re-run step 4

### 6. Update documentation

- Read `IMPROVEMENT-PLAN.md` and update it:
  - Mark the completed item as **DONE** with today's date
  - Add implementation details: files modified, solution summary
  - Add any new items discovered during implementation
- Update `CLAUDE.md` if new endpoints, tables, services, or key paths were added
- Update relevant docs in `docs/` (architecture.md, api-reference.md, bot-flow.md, etc.)
- If no other docs need updating, explicitly confirm "No other docs affected"

### 7. Bump version

- Read current version from `src/farmafacil/__init__.py`
- Determine the increment:
  - **Patch** (x.y.Z): bug fixes, small improvements
  - **Minor** (x.Y.0): new features, new endpoints, significant enhancements
  - **Major** (X.0.0): breaking changes, major architecture shifts
- Update `__version__` in `src/farmafacil/__init__.py`
- Update `version` in `pyproject.toml` to match

### 8. Commit & push

- Stage specific changed files (do NOT use `git add -A` or `git add .`)
- Do NOT stage `.env`, `farmafacil.db`, `__pycache__/`, `.pytest_cache/`
- Create a descriptive commit message summarizing all changes
- Push to GitHub (remote: origin, branch: main)

### 9. Deploy to production

```bash
ssh -i ~/.ssh/id_ed25519 dgonzalez@10.0.0.116 \
  "cd ~/workspace/farmafacil && git pull && docker compose build --no-cache && docker compose down && docker compose up -d"
```

### 10. Verify deployment

```bash
# Health check
ssh -i ~/.ssh/id_ed25519 dgonzalez@10.0.0.116 "curl -s http://localhost:8100/health"

# Check logs for errors
ssh -i ~/.ssh/id_ed25519 dgonzalez@10.0.0.116 "cd ~/workspace/farmafacil && docker compose logs --tail=20 app"
```

- Health endpoint must return `{"status":"ok"}`
- Logs must show no errors during startup

### 11. Update memory

- Update the project's auto-memory `MEMORY.md` file with:
  - New version number
  - Summary of what changed (which improvement plan item was completed)
  - Updated improvement plan status counts (done/pending)

### 12. Report

Print a summary table with:
- Version (old → new)
- Improvement plan item completed (number and title)
- Files changed (count and list)
- Test results (count, pass/fail)
- Deployment status (success/failure)
- Commit hash
