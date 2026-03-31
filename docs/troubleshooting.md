# FarmaFacil — Troubleshooting Guide

> Last Updated: 2026-03-30

## Quick Diagnosis

```bash
# Check if the app is running
curl http://localhost:8100/health

# Check recent logs
docker compose logs --tail=100 app

# Check recent conversations
curl http://localhost:8100/api/v1/conversations?limit=20
```

---

## WhatsApp Token Expired

### Symptom
Messages arrive at the webhook but no reply is sent. Logs show:

```
HTTP 401 Unauthorized posting to WhatsApp API
```

### Root Cause
Standard WhatsApp tokens (from personal accounts in Meta Business Manager) expire every ~60 days.

### Solution (Permanent Fix)
The app now uses a **System User permanent token**, which does not expire. This was the fix applied in early 2026.

If you still see 401 errors:
1. Check `WHATSAPP_API_TOKEN` in `.env` — it should be a System User token, not a personal token
2. Verify the token in Meta Business Manager → Settings → System Users → Generate Token (needs `whatsapp_business_messaging` permission)
3. Update `.env` and restart: `docker compose restart app`

---

## ngrok CSS/Styling Not Loading in Admin Dashboard

### Symptom
The admin dashboard at `/admin` loads but looks unstyled (plain HTML, no CSS).

### Root Cause
ngrok requires the `X-Forwarded-For` and proxy headers to be trusted by the app. Without `--proxy-headers`, FastAPI serves absolute URLs pointing to `localhost` instead of the ngrok domain.

### Solution (Already Applied)
The `Dockerfile` CMD includes `--proxy-headers --forwarded-allow-ips "*"`:

```
CMD ["uvicorn", "farmafacil.api.app:app", "--host", "0.0.0.0",
     "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
```

If the issue recurs after a Dockerfile change, verify these flags are present.

---

## User Profile Corruption

### Symptom
A user is stuck in an unexpected onboarding state. For example:
- Onboarding loops (asks for name again for a known user)
- Bot asks for location when user already set it
- Bot sends "no estoy seguro" on every message

### Root Cause
A bug or interrupted request left `onboarding_step` inconsistent with the actual user data (name/location saved but step not advanced, or step null but data missing).

### Solution (Automatic)
`validate_user_profile()` runs on every message and auto-repairs these states. See [bot-flow.md](bot-flow.md) for the repair logic.

### Manual Fix via Admin
1. Go to `/admin` → Users table
2. Find the user by phone number
3. Set `onboarding_step` to `null` if all data (name, location, preference) is present
4. Or set to the correct step (`awaiting_name`, `awaiting_location`, `awaiting_preference`)

---

## Geocoding Failures

### Symptom
User provides a valid Venezuelan location but the bot replies:

> No logre ubicar esa zona en Venezuela. Intenta con el nombre de tu barrio...

### Root Cause
OpenStreetMap Nominatim returned no results for the input text.

### Diagnosis
Check logs for:
```
WARNING: Nominatim returned no results for 'xyz'
```
or:
```
ERROR: Nominatim geocode failed for 'xyz': <connection error>
```

### Solutions

**Case 1: Location name not recognized**
- Common with hyper-local neighborhood names (e.g., "Urb. La Palmita")
- Advise user to try a broader name: "Baruta", "Caracas", or the nearest municipality
- Nominatim covers Venezuela well but not every micro-neighborhood

**Case 2: Nominatim rate limiting**
- Nominatim has a 1 req/sec limit for anonymous usage
- The app currently makes one request per geocode call
- If high traffic causes 429s, consider adding a small delay or caching geocode results

**Case 3: Network error reaching Nominatim**
- Check server connectivity: `curl "https://nominatim.openstreetmap.org/search?q=Caracas&format=json"`
- Verify no firewall blocking outbound HTTPS from the Docker container

---

## LLM Model Not Found

### Symptom
Intent classification fails and logs show:

```
ERROR: LLM classification failed
anthropic.NotFoundError: model not found: claude-haiku-4-5-20251001
```

### Root Cause
The model ID in `LLM_MODEL` env var (or the default in `config.py`) is outdated.

### Solution
1. Check available models at [Anthropic docs](https://docs.anthropic.com/en/docs/about-claude/models)
2. Update `LLM_MODEL` in `.env`:
   ```
   LLM_MODEL=claude-haiku-4-5-20251001
   ```
3. Restart the app: `docker compose restart app`

**Fallback behavior:** When LLM classification fails, the service falls back to treating the message as a `drug_search` with the raw text as the query. This means the bot still functions but loses nuanced intent detection.

---

## Product Search Returns No Results

### Symptom
User sends a drug name but bot replies with "no encontre resultados" or similar.

### Diagnosis

```bash
# Test the search API directly
curl "http://localhost:8100/api/v1/search?q=losartan&city=caracas"
```

**Case 1: Algolia API unreachable**
Check logs for:
```
WARNING: Farmatodo Algolia search timed out for query: losartan
```
The Farmatodo Algolia index (`products-venezuela`) is a public, unauthenticated endpoint. Check connectivity from the server.

**Case 2: Cache is stale**
The `search_queries.searched_at` might be recent but `product_prices.refreshed_at` is old. Adjust `cache_ttl_minutes` in App Settings via `/admin`.

**Case 3: Drug actually not in Farmatodo catalog**
Some drugs may not be carried by Farmatodo. Try searching on `farmatodo.com.ve` directly to confirm.

---

## Database Connection Errors (Production)

### Symptom
App fails to start or requests fail with:
```
asyncpg.exceptions.ConnectionDoesNotExistError
```
or:
```
could not connect to server: Connection refused
```

### Diagnosis
```bash
docker compose ps           # Is db service running?
docker compose logs db      # Check PostgreSQL logs
docker compose exec db pg_isready -U farmafacil  # Health check
```

### Solution
```bash
docker compose down && docker compose up -d   # Full restart
docker compose logs -f app                     # Watch startup
```

If PostgreSQL data volume is corrupted:
```bash
docker compose down -v     # WARNING: destroys all data
docker compose up -d
```

---

## Webhook Not Receiving Messages

### Symptom
WhatsApp messages from users don't trigger any bot response and nothing appears in `conversation_logs`.

### Checklist

1. **ngrok running?**
   ```bash
   curl http://10.0.0.114:4040/api/tunnels
   ```
   Should return the active tunnel URL.

2. **Webhook URL registered in Meta?**
   - Meta Business Manager → App → WhatsApp → Configuration
   - Webhook URL must point to the current ngrok URL + `/webhook`

3. **Webhook verified?**
   - Meta shows "Verified" status next to the webhook URL
   - If not, check `WHATSAPP_VERIFY_TOKEN` matches what's in Meta settings

4. **App running and healthy?**
   ```bash
   curl http://localhost:8100/health
   ```

5. **Messages subscription active?**
   - Meta webhook must be subscribed to the `messages` field
