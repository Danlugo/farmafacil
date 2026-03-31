# FarmaFacil Review — Multi-Agent Architecture Deep Dive

Perform a comprehensive deep-dive review of the FarmaFacil codebase using a **team of 7 specialist agents** running in parallel. Each agent examines the codebase through their expert lens. You act as the **Senior Principal Architect** who synthesizes all findings into a unified, prioritized improvement plan.

This is a **review-only** skill. You do NOT implement changes. You analyze, diagnose, and plan.

**Focus area** (optional): $ARGUMENTS

## Project Info

- **Path**: `/Users/dgonzalez/Documents/workspace/farmafacil/`
- **Stack**: Python 3.12 + FastAPI + SQLAlchemy 2.0 (async) + Pillow + Anthropic SDK
- **Database**: SQLite (local dev), PostgreSQL 16 (production via Docker)
- **Source**: `src/farmafacil/` — api/, bot/, scrapers/, services/, models/, db/, config.py
- **Tests**: `tests/*.py` (pytest + pytest-asyncio, 71+ tests)
- **Docs**: `CLAUDE.md`, `docs/` (architecture.md, api-reference.md, bot-flow.md, deployment.md, troubleshooting.md, adding-pharmacies.md)
- **Production**: Docker on 10.0.0.114, ngrok tunnel for WhatsApp webhooks
- **Key external services**: Farmatodo Algolia API, WhatsApp Business Cloud API, OpenStreetMap Nominatim, Claude Haiku (Anthropic)

## Steps

### 1. Prepare

- Read `CLAUDE.md` for full project context
- Read `IMPROVEMENT-PLAN.md` (if it exists) to understand what's already been done
- Read `docs/architecture.md` for system design
- Note the current version, test count, and overall architecture
- If the user provided a focus area, include it in every agent prompt

### 2. Launch specialist agents IN PARALLEL

Spawn ALL of the following agents simultaneously. Each agent must read the actual source files — not summaries. Every agent gets the same project context block plus their domain-specific instructions.

Include this context block in every agent prompt:

```
PROJECT CONTEXT:
- Path: /Users/dgonzalez/Documents/workspace/farmafacil/
- Stack: Python 3.12 + FastAPI + SQLAlchemy 2.0 (async) + Anthropic SDK + Pillow
- Source: src/farmafacil/ — api/ (app.py, routes.py, admin.py), bot/ (handler.py, webhook.py, whatsapp.py, formatter.py), scrapers/ (base.py, farmatodo.py), services/ (intent.py, search.py, product_cache.py, users.py, geocode.py, store_backfill.py, image_grid.py, conversation_log.py, settings.py), models/ (database.py, schemas.py), db/ (session.py, seed.py), config.py
- Database: 10 tables (users, products, product_prices, search_queries, intent_keywords, pharmacy_locations, app_settings, conversation_logs, search_logs, product_cache[deprecated])
- External APIs: Farmatodo Algolia (drug search), WhatsApp Business Cloud API (Meta), OpenStreetMap Nominatim (geocoding), Claude Haiku (intent detection)
- Tests: tests/ directory, pytest + pytest-asyncio
- Production: Docker Compose (app + postgres:16) on 10.0.0.114, ngrok for HTTPS tunnel
- Admin: SQLAdmin at /admin with username/password auth
- Read CLAUDE.md first for full context
- [FOCUS AREA: {user's focus if provided, otherwise "full-spectrum review"}]

OUTPUT FORMAT — Return a structured report with:
1. Executive summary (2-3 sentences from your domain perspective)
2. Findings table with columns: Severity (P0/P1/P2/P3), Finding, File:Line, Suggested Fix, Effort (Low/Med/High)
3. Top 3 recommendations in priority order

Be specific: name files, functions, line numbers. Be actionable: say WHAT to do. Be honest: if something is well-designed, say so.
```

#### Agent 1: Security Engineer (`security-engineer`)

```
You are a senior application security engineer. Perform a security audit of FarmaFacil.

{PROJECT CONTEXT}

AUDIT CHECKLIST:
- OWASP Top 10: injection (SQL via SQLAlchemy — are raw queries anywhere?), broken access control (admin auth bypass?), security misconfiguration
- Secrets handling: Are WHATSAPP_API_TOKEN, ANTHROPIC_API_KEY, ADMIN_PASSWORD properly protected? Check config.py, logs, error output — can they leak?
- Input validation: WhatsApp messages are UNTRUSTED user input. How are they sanitized? Check handler.py (text → intent → action flow), webhook.py (payload parsing). Can crafted messages cause harm?
- Admin dashboard security: Is /admin properly protected? Can the auth be bypassed? Is the session secret key strong enough? Check admin.py AdminAuth class
- API endpoint protection: Are /api/v1/ endpoints protected? Can anyone hit /api/v1/users and see all user data?
- Algolia API keys: The search-only API key is hardcoded in farmatodo.py. Is this acceptable? (It's a public search key, but verify)
- Prompt injection: User WhatsApp messages go into Claude Haiku prompts via intent.py. Can a crafted message manipulate the LLM? Check system prompt boundaries
- Database: Are all queries parameterized (SQLAlchemy ORM)? Any raw SQL? Check for mass assignment on user updates
- Dependency risks: Check pyproject.toml for known-vulnerable packages
- WhatsApp webhook: Is the verify_token check sufficient? Can someone spoof webhook requests?
```

#### Agent 2: SRE / Reliability Engineer (`sre-engineer`)

```
You are a senior SRE reviewing FarmaFacil for production reliability.

{PROJECT CONTEXT}

REVIEW CHECKLIST:
- Crash resilience: What happens if the app crashes mid-request? Is there data loss? Check async session handling — are sessions properly committed/rolled back?
- Database connection management: Check db/session.py. Is the async engine properly configured? Connection pool settings? What happens if Postgres is down?
- External service failures: What if Algolia API is down? Nominatim times out? WhatsApp API returns 500? Claude API is unavailable? Check error handling in each service
- Startup sequence: Check app.py lifespan — init_db → seed_intents → seed_settings → backfill_stores. What if any step fails? Does the app still start?
- Memory usage: With many concurrent WhatsApp messages, product grid image generation (Pillow) could use significant memory. Are images cleaned up? Check image_grid.py temp file handling
- Docker health: Is the health check sufficient? Does it verify DB connectivity or just HTTP response?
- Data integrity: Product catalog UPSERT — are there race conditions if two searches for the same product run concurrently? Check product_cache.py for transaction isolation
- Log quality: Are error messages actionable? Do they include enough context (user phone, query, timestamp)?
- Graceful shutdown: Does the app handle SIGTERM properly? Are in-flight requests completed?
- ngrok dependency: The entire WhatsApp integration depends on ngrok. What happens if ngrok drops? Is there monitoring?
```

#### Agent 3: Performance Engineer (`performance-engineer`)

```
You are a senior performance engineer. Profile FarmaFacil for bottlenecks.

{PROJECT CONTEXT}

REVIEW CHECKLIST:
- Hot path analysis: Trace the message handling path (webhook → handler → intent → search/action → response). What's on the critical path?
- Database queries: For each service, count the number of DB queries per request. Are there N+1 queries? Can any be batched? Check users.py (get_or_create + validate + update = 3+ queries per message)
- Product catalog queries: The new product_cache.py UPSERT logic — how many queries per search? Can it be optimized with bulk operations?
- Algolia API latency: Is the scraper timeout (30s) appropriate? Are searches cached effectively with the 1-week TTL?
- Image generation: Pillow grid generation downloads product images for each search. Are images cached? Check image_grid.py — does it re-download every time?
- Intent detection: The hybrid intent flow (DB keywords → heuristic → LLM). How often does it fall through to the LLM (expensive)? Can the keyword cache be more effective?
- Geocoding: Nominatim has rate limits (1 req/sec). Is this respected? Are geocode results cached?
- Store backfill: Runs on every app startup (18 city codes × 1 API call each). Can this be skipped if data is fresh?
- WhatsApp API: Are outbound messages sent sequentially? Can they be parallelized for multi-image responses?
- SQLAlchemy session management: Is a new session created per query? Check async_session usage — should there be request-scoped sessions?
```

#### Agent 4: Code Reviewer / Code Quality (`code-reviewer`)

```
You are a senior code reviewer doing a maintainability and quality audit.

{PROJECT CONTEXT}

REVIEW CHECKLIST:
- Architecture adherence: Does the code follow the patterns in CLAUDE.md? (service classes for external interactions, config from env vars, explicit schemas)
- God file check: handler.py handles ALL message types and flows. Is it too large? What can be extracted?
- Dead code: Find unused imports, unreachable branches, commented-out code, deprecated modules (old ProductCache)
- DRY violations: Find copy-paste patterns across files. Are there repeated DB session patterns that could be abstracted?
- Type safety: Are type hints on all public functions? Any `Any` types, missing return types, or loose typing?
- Error handling consistency: Compare error handling across services. Is it consistent? Some may swallow errors, others may crash
- Naming consistency: Are naming conventions consistent? (snake_case, PascalCase for classes, etc.)
- Import organization: Are imports sorted (stdlib → third-party → local) per project rules?
- Test quality: Review test files — are they testing behavior or implementation details? Are there missing assertions?
- Configuration sprawl: config.py vs app_settings DB table vs hardcoded values (Algolia keys). Is there a clear pattern?
- Anti-patterns: Check against the anti-patterns lists in python-rules.md (bare except, mutable defaults, print instead of logging, global state)
```

#### Agent 5: Solution Architect (`solution-architect`)

```
You are a solution architect evaluating the overall system design.

{PROJECT CONTEXT}

REVIEW CHECKLIST:
- Architecture layers: config.py → models/ → db/ → services/ → bot/ → api/. Is this the right decomposition? Are there layers that add indirection without value?
- Service granularity: 9+ service files. Are some too thin? Doing too much? Should any be merged or split?
- Multi-pharmacy readiness: The system is designed for multiple pharmacy scrapers (BaseScraper pattern). How ready is it? What would adding Armirene or Locatel require?
- Product catalog design: The new products/product_prices/search_queries schema. Is the UPSERT pattern correct? Should search_queries store product IDs as JSON or use a junction table?
- Caching strategy: 1-week TTL via app_settings. Is this the right approach? Should different product categories have different TTLs?
- WhatsApp integration: The webhook → handler → whatsapp sender flow. Is the abstraction clean? What about message queuing for high volume?
- Admin dashboard: SQLAdmin with basic auth. Is this sufficient? Should there be role-based access? Audit logging?
- Intent detection: Keywords → heuristic → LLM. Is this the right architecture? Should there be a vector search / embedding layer?
- Scalability: What breaks at 100 concurrent users? 1000? Is the single-process uvicorn with sync WhatsApp responses sustainable?
- Data model: Are relationships between tables well-designed? Missing indexes? Denormalization needed?
- Future vision: Based on docs/implementation-plan.md and the docx files in docs/, what's the gap between current state and the full vision?
```

#### Agent 6: Test Engineer (`test-engineer`)

```
You are a senior test engineer auditing the test suite.

{PROJECT CONTEXT}

REVIEW CHECKLIST:
- Coverage analysis: Read every test file and every source file. For each source file, list which functions/methods have tests and which don't
- Test strategy: What's the breakdown of unit vs integration tests? Is the ratio healthy?
- Missing critical tests: What SHOULD be tested but isn't? Focus on:
  - handler.py message flow (the most complex file)
  - Product catalog UPSERT logic
  - WhatsApp sender (mocked)
  - Intent detection with LLM fallback
  - User profile validation and auto-repair
  - Admin authentication
  - Concurrent request handling
  - Error recovery paths (Algolia down, LLM down, DB down)
- Mock strategy: Are external services (Algolia, WhatsApp API, Nominatim, Claude) properly mocked?
- Edge case coverage: For each service, identify at least 3 untested edge cases
- Test infrastructure: Is conftest.py setup clean? Are fixtures well-organized? Can tests run in parallel?
- Data isolation: Do tests clean up after themselves? Can test order affect results?
- Regression safety: If someone refactors handler.py, which tests catch regressions?
```

#### Agent 7: Product / UX Reviewer (`product-manager`)

```
You are a product manager reviewing FarmaFacil for user experience and completeness.

{PROJECT CONTEXT}

The app has two user interfaces:
1. **WhatsApp chat** — users interact with the bot by sending messages in Spanish
2. **Admin dashboard** — admins manage users, intents, settings, view logs at /admin

REVIEW CHECKLIST:
- Onboarding experience: Walk through the onboarding flow (welcome → name → location → preference). Is it smooth? What if a user types something unexpected at each step?
- Error messages: When things go wrong, what does the user see? Are error messages in Spanish? Are they helpful? Check all send_text_message calls with error content
- Drug search UX: Search "losartan" → what does the user see? Is the response format clear? Are prices formatted correctly in Bolivares? Is the nearby store info useful?
- Product display: Grid image vs detail images. Are they well-designed? Do they load quickly? What if a product has no image?
- Store lookup: "Donde queda TEPUY" → is the response helpful? Does it include a Google Maps link? What about misspellings?
- Change commands: "cambiar zona", "cambiar nombre", "cambiar preferencia" — are these discoverable? Is there a help message that lists them?
- Multi-user experience: What happens when 10 users message simultaneously? Is there per-user state isolation?
- Admin dashboard: Is it usable for a non-technical admin? Are the table views clear? Can they find what they need (e.g., view all conversations for a user)?
- Missing features from the vision: Compare current features to docs/ .docx files (spec docs, strategy docs). What's the biggest gap?
- Venezuelan context: Are all messages properly in Spanish? Are prices in Bolivares? Are pharmacy names/addresses correctly displayed?
- Accessibility: Is the WhatsApp formatting (bold, italic, emojis) used effectively? Are messages too long?
```

### 3. Synthesize findings

After ALL agents return their reports:

1. **Deduplicate**: Multiple agents may flag the same issue from different angles. Merge into single findings with the highest severity
2. **Cross-reference**: Note findings that multiple agents independently identified — these are high-confidence issues
3. **Validate**: Discard findings already addressed by existing improvements
4. **Prioritize**: Apply the priority matrix:

| Priority | Criteria |
|----------|----------|
| **P0 — Critical** | Data loss risk, security vulnerability, crash in normal usage |
| **P1 — High** | Reliability issue, significant tech debt, performance bottleneck |
| **P2 — Medium** | Code quality, maintainability, moderate UX improvement |
| **P3 — Low** | Nice-to-have, polish, minor inconsistency |

| Effort | Criteria |
|--------|----------|
| **Low** | < 1 hour, 1-2 files, straightforward |
| **Medium** | 1-4 hours, 3-5 files, some complexity |
| **High** | 4+ hours, 5+ files, significant refactoring or new system |

### 4. Present the unified review report

Show the user a comprehensive report:

```
## Architecture Review — [Date]
### Reviewed by: 7 specialist agents

### Executive Summary
[3-5 sentences: overall health, biggest risks, top opportunities]

### Agent Consensus
[Findings flagged by 2+ agents — highest-confidence issues]

### Findings by Priority

#### P0 — Critical
| # | Category | Finding | Source Agent(s) | Files | Suggested Fix | Effort |
|---|----------|---------|-----------------|-------|---------------|--------|

#### P1 — High
[same table format]

#### P2 — Medium
[same table format]

#### P3 — Low
[same table format]

### Security Posture
[From security-engineer: overall security health + top 3 risks]

### Reliability Assessment
[From SRE: overall reliability + top 3 failure scenarios]

### Performance Profile
[From performance-engineer: bottlenecks + optimization opportunities]

### Code Quality & Tech Debt
[From code-reviewer: what's accumulating + sustainability]

### Architecture Assessment
[From solution-architect: simplification opportunities, scalability concerns]

### Test Suite Health
[From test-engineer: coverage gaps + quality assessment]

### UX & Product Gaps
[From product-manager: user-facing improvements + vision gap analysis]

### Recommended Next Sprint (Top 5-10)
[Ordered list of the most impactful items]
```

### 5. Wait for user approval

- Present the full report
- Ask: "Which findings should I add to the improvement plan? You can: approve all, select specific items by number, modify priorities, or reject some."
- Wait for explicit confirmation before modifying any files

### 6. Update IMPROVEMENT-PLAN.md

After user approval:

- Read `IMPROVEMENT-PLAN.md` (create if needed)
- Add approved items as new entries under the appropriate priority sections
- Use the next sequential item number
- Set status to **PENDING**
- For each item include: problem description, suggested solution, affected files, effort estimate
- Do NOT change the status of any existing items

### 7. Commit the review

- Stage only `IMPROVEMENT-PLAN.md`
- Commit with message: `docs: architecture review — N new improvement items (P0: X, P1: Y, P2: Z, P3: W)`
- Push to GitHub

### 8. Report

Print a final summary:

| Metric | Value |
|--------|-------|
| Agents deployed | 7 |
| Total findings | N (P0: X, P1: Y, P2: Z, P3: W) |
| Cross-agent consensus findings | N |
| Items added to plan | N (item numbers) |
| Recommended execution order | [list] |
| Commit hash | [hash] |
