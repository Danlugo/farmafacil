# FarmaFacil — API Reference

> Last Updated: 2026-04-08
> Base URL (production): `https://amparo-chromophoric-christia.ngrok-free.dev`
> Base URL (local dev): `http://localhost:8000`
> Base URL (server direct): `http://10.0.0.116:8100`

## Authentication

Most endpoints are unauthenticated (internal use). The admin dashboard at `/admin` requires HTTP Basic Auth (see [deployment.md](deployment.md) for credentials).

---

## Health Check

### GET /health

Returns application health and version.

**Response 200:**
```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

---

## Drug Search

### GET /api/v1/search

Search for a drug across all active pharmacies.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| q | string | Yes | Drug name (min 2 chars, max 200) |
| city | string | No | City for localized pricing (e.g., `caracas`, `maracaibo`) |

**Example:**
```
GET /api/v1/search?q=losartan&city=caracas
```

**Response 200:**
```json
{
  "query": "losartan",
  "city": "caracas",
  "zone": null,
  "results": [
    {
      "drug_name": "COZAAR 50MG x28 TABLETAS",
      "pharmacy_name": "Farmatodo",
      "price": null,
      "price_bs": "156.50",
      "full_price_bs": "195.60",
      "discount_pct": "20%",
      "available": true,
      "url": "https://www.farmatodo.com.ve/cozaar-50mg-28-tabletas",
      "last_checked": "2026-03-30T12:00:00Z",
      "requires_prescription": true,
      "image_url": "https://cdn.farmatodo.com/...",
      "brand": "MSD",
      "drug_class": "Antihipertensivo",
      "unit_label": "Capsulas 5.59",
      "unit_count": 28,
      "description": "...",
      "stores_in_stock": 12,
      "stores_with_stock_ids": [101, 205, 318],
      "nearby_stores": []
    }
  ],
  "total": 8,
  "searched_pharmacies": ["Farmatodo"]
}
```

### POST /api/v1/search

Same as GET but accepts a JSON body.

**Request body:**
```json
{
  "query": "losartan",
  "city": "caracas"
}
```

---

## Users

### GET /api/v1/users

List registered WhatsApp users.

**Query Parameters:**

| Parameter | Type | Default | Max |
|-----------|------|---------|-----|
| limit | integer | 50 | 200 |

**Response 200:**
```json
[
  {
    "id": 1,
    "phone": "584121234567",
    "name": "Maria",
    "zone": "El Cafetal",
    "city_code": "CCS",
    "lat": 10.45,
    "lng": -66.85,
    "display_preference": "grid",
    "onboarding_step": null,
    "created": "2026-03-01T10:00:00"
  }
]
```

**Onboarding steps:**

| Value | Meaning |
|-------|---------|
| `welcome` | New user, not yet greeted |
| `awaiting_name` | Waiting for user's name |
| `awaiting_location` | Waiting for user's zone |
| `awaiting_preference` | Waiting for display preference |
| `null` | Onboarding complete |

---

## Conversations

### GET /api/v1/conversations

View WhatsApp message logs for troubleshooting.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| phone | string | null | Filter by phone number (partial match) |
| limit | integer | 50 | Max records (max 200) |

**Response 200:**
```json
[
  {
    "id": 42,
    "phone": "584121234567",
    "direction": "inbound",
    "message": "losartan",
    "type": "text",
    "wa_id": "wamid.abc123",
    "time": "2026-03-30T12:05:00"
  }
]
```

**Direction values:** `inbound` (user → bot) or `outbound` (bot → user)

---

## Intent Keywords

Intent keywords are matched against incoming messages before falling back to the LLM. They are cached in memory with a 5-minute TTL.

### GET /api/v1/intents

List all intent keywords.

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| action | string | Filter by action type |

**Response 200:**
```json
[
  {
    "id": 1,
    "action": "greeting",
    "keyword": "hola",
    "response": null,
    "is_active": true
  },
  {
    "id": 5,
    "action": "location_change",
    "keyword": "cambiar zona",
    "response": null,
    "is_active": true
  }
]
```

**Available action values:**

| Action | Trigger |
|--------|---------|
| `greeting` | Respond with welcome-back message |
| `help` | Send the help menu |
| `location_change` | Enter awaiting_location step |
| `preference_change` | Enter awaiting_preference step |
| `name_change` | Enter awaiting_name step |
| `farewell` | Send the canned response and exit |

### POST /api/v1/intents

Add a new intent keyword. Immediately invalidates the keyword cache.

**Request body:**
```json
{
  "action": "farewell",
  "keyword": "adios",
  "response": "Hasta luego! Escribe cuando necesites buscar medicamentos."
}
```

**Response 200:**
```json
{
  "id": 25,
  "action": "farewell",
  "keyword": "adios"
}
```

### DELETE /api/v1/intents/{intent_id}

Deactivate (soft delete) an intent keyword.

**Response 200:**
```json
{
  "id": 25,
  "deactivated": true
}
```

---

## Usage Statistics

### GET /api/v1/stats

Usage statistics — global totals or per-user breakdown.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| phone | string | No | Phone number for per-user stats |

**Response 200 (global — no phone param):**
```json
{
  "total_users": 42,
  "total_questions": 1250,
  "total_success": 87,
  "total_tokens_in": 523000,
  "total_tokens_out": 198000
}
```

**Response 200 (per-user — with phone param):**
```json
{
  "phone": "584121234567",
  "name": "Maria",
  "total_questions": 25,
  "total_success": 3,
  "total_tokens_in": 12500,
  "total_tokens_out": 4800
}
```

**Response 200 (user not found):**
```json
{
  "error": "user not found"
}
```

---

## WhatsApp Webhook

### GET /webhook

Endpoint used by Meta to verify the webhook URL. Responds with the `hub.challenge` value when `hub.verify_token` matches `WHATSAPP_VERIFY_TOKEN`.

**Query Parameters (set by Meta):**

| Parameter | Description |
|-----------|-------------|
| hub.mode | Always `subscribe` |
| hub.verify_token | Must match `WHATSAPP_VERIFY_TOKEN` |
| hub.challenge | Echo this back on success |

**Response:** `200 OK` with challenge string, or `403 Forbidden`.

### POST /webhook

Receives incoming WhatsApp messages. Meta delivers this as a nested JSON structure.

**Handled message types:**

| Type | Handling |
|------|---------|
| `text` | Full bot processing via handler.py |
| `location` | Logged; GPS-based onboarding (TODO) |
| `image` | Logged; sends "coming soon" reply |
| Other | Logged only |

**Response:** Always `{"status": "ok"}` (200). Errors are logged, not surfaced to Meta.

---

## Admin Dashboard

**URL:** `/admin`
**Auth:** HTTP Basic — username/password from `ADMIN_USERNAME` / `ADMIN_PASSWORD`

Built with SQLAdmin. Provides a web UI to browse and edit all database tables:
- Users
- Intent keywords
- App settings (cache TTL, etc.)
- Conversation logs
- Products and prices
- User feedback (`/bug` and `/comentario` submissions — review-only workflow)

See [deployment.md](deployment.md) for default credentials.

---

## Error Responses

The API does not use a custom error envelope. FastAPI returns standard HTTP error responses:

| Status | Meaning |
|--------|---------|
| 200 | Success |
| 422 | Validation error (e.g., missing `q` param) |
| 403 | Webhook verification failed |
| 500 | Internal server error (check logs) |

**422 example:**
```json
{
  "detail": [
    {
      "loc": ["query", "q"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

---

## Interactive Docs

FastAPI auto-generates OpenAPI docs:
- Swagger UI: `/docs`
- ReDoc: `/redoc`
- OpenAPI JSON: `/openapi.json`
