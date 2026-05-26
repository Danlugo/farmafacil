# FarmaFacil Group Relay — Chamo Skill

Create a new skill that makes a WhatsApp group work like a direct chat with the FarmaFacil bot. Every text message from a group member gets sent to FarmaFacil's chat API. Every response gets posted back to the group.

## Server Access

Chamo will be deployed as a Docker container on the same server as FarmaFacil.

```
Host: 10.0.0.80
User: dgonzalez
SSH key: ~/.ssh/id_ed25519
SSH command: ssh -i ~/.ssh/id_ed25519 dgonzalez@10.0.0.80
```

FarmaFacil is already running on this server:
- **Project path:** `~/workspace/farmafacil`
- **Docker Compose:** `app` (FastAPI on port 8100) + `db` (Postgres on port 5433)
- **Both ports bound to 127.0.0.1** (localhost only)

Since Chamo runs on the same machine, call FarmaFacil via localhost — no ngrok needed:
```
http://localhost:8100/api/v1/chat
```

Verify FarmaFacil is running:
```bash
curl -s http://localhost:8100/health
# Expected: {"status":"ok","version":"0.26.0"}
```

## Config

| Setting | Value |
|---------|-------|
| Group name | FarmaFacilTest |
| Invite link | https://chat.whatsapp.com/D0VbxmOsATp4YznXrcT7Sh |
| Group JID | *(Chamo: join the invite link above, then use the JID from the group)* |
| API base (local) | `http://localhost:8100` |
| API base (public) | `https://amparo-chromophoric-christia.ngrok-free.dev` |
| Endpoint | `POST /api/v1/chat` |

## Trigger

Every **text message or voice note** in the FarmaFacilTest group. No prefix, no trigger word. The FarmaFacil bot handles intent detection internally (greetings, drug searches, help commands, feedback — everything).

**Critical: ignore messages sent by Chamo itself to prevent infinite loops.**

## API Calls

### Text messages

For every group text message, make this HTTP POST:

```
POST http://localhost:8100/api/v1/chat
Content-Type: application/json

{
  "sender_id": "{sender_phone_number}",
  "sender_name": "{sender_pushname_or_display_name}",
  "text": "{message_text}"
}
```

#### Fields

- `sender_id` — the phone number of whoever sent the message in the group (e.g. `"584127006823"`). This identifies the user in FarmaFacil's database. Each person gets their own profile, search history, and preferences even though they're all in the same group.
- `sender_name` — the WhatsApp display name / pushname of the sender. Used for onboarding if it's a new user.
- `text` — the raw message text, exactly as sent.

### Voice messages (audio notes)

For every group voice note, download the audio via Baileys and POST it as `multipart/form-data`:

```
POST http://localhost:8100/api/v1/chat/voice
Content-Type: multipart/form-data

sender_id={sender_phone_number}
sender_name={sender_pushname_or_display_name}
audio={ogg_audio_bytes; filename=voice.ogg; content-type=audio/ogg}
```

FarmaFacil transcribes the audio with Whisper and processes the resulting text through the same handler as a text message. The response format is identical. Allow **60 seconds** timeout for voice (transcription takes longer than text). Rate limit: 30 requests/minute.

## Response Format

The API returns a JSON object with an array of messages to post back to the group, in order:

```json
{
  "responses": [
    {
      "type": "text",
      "body": "Buscando losartan... 💊"
    },
    {
      "type": "image",
      "url": "https://farmatodo.com/images/product.jpg",
      "caption": "🟢 *20% DCTO*\nLosartan 50mg MK..."
    },
    {
      "type": "text",
      "body": "*Losartan* cerca de *La Tahona* — 3 producto(s)\nFarmacias: _Farmatodo, Locatel_\n\n*1. Losartan 50mg MK Caja x 30 Tabletas* 📋\n   🏥 Farmatodo — Bs. 2,500.00\n   ✅ Disponible en 180 tiendas"
    }
  ]
}
```

### Response types

| type | Fields | What to do |
|------|--------|-----------|
| `text` | `body` | Send as a text message to the group |
| `image` with `url` | `url`, `caption` | Send as an image message to the group (public URL) |
| `image` with `media_id` | `media_id`, `caption` | **Skip it.** These are product cards uploaded via WhatsApp Business API — Chamo can't use them. The text summary that follows contains the same info. |
| `list` | `body`, `button`, `rows` | Send `body` as a plain text message. Interactive lists don't work in groups. |

## Rules

1. **Forward every text message** — greetings, drug searches, help commands, feedback, everything. Don't filter or pre-process.

2. **Ignore Chamo's own messages** — do NOT forward messages sent by Chamo to the API. This prevents infinite loops.

3. **Post responses in order** — the API returns an array. Send them sequentially to the group in the order received.

4. **Skip `media_id` images** — if a response has `type: "image"` with `media_id` instead of `url`, skip it silently.

5. **Flatten interactive lists** — if a response has `type: "list"`, post just the `body` field as a plain text message.

6. **Forward text, voice, and images** — forward text messages to `/api/v1/chat`, voice notes to `/api/v1/chat/voice`, and photos to `/api/v1/chat/image`. Ignore location shares, stickers, and other unsupported message types.

7. **Image relay** — when a group member sends a photo (prescription, medicine box, etc.), download the image via Baileys, then POST as `multipart/form-data` to `/api/v1/chat/image` with `sender_id`, `sender_name`, `caption` (if any), and `image` (the raw file bytes). The API analyzes the image with Claude Vision, identifies medicines, and returns search results as a `ChatResponse`. Image timeout: 60s (Vision API).

8. **Error handling** — if the API is unreachable, times out, or returns non-200, post to the group: `⚠️ FarmaFacil no está disponible en este momento. Intenta de nuevo en unos minutos.` Text timeout: 30s. Voice timeout: 60s. Image timeout: 60s.

9. **Rate limits** — text endpoint: 120 requests/minute. Voice endpoint: 30 requests/minute. Image endpoint: 30 requests/minute.

## Example Flow

A user named Jose sends "losartan" in the group:

1. Chamo sees the message, extracts: `sender_id="584127006823"`, `sender_name="Jose Miguel"`, `text="losartan"`
2. Chamo POSTs to `/api/v1/chat`
3. API returns 3 responses: a product image, another product image, and a text summary
4. Chamo posts them to the group in order (skipping any `media_id` images)
5. Jose sees the search results in the group

Another user sends "hola":

1. Chamo forwards: `sender_id="584149709707"`, `sender_name="Daniel"`, `text="hola"`
2. API returns 1 response: `{"type": "text", "body": "¡Hola Daniel! ¿En qué te puedo ayudar? 😊"}`
3. Chamo posts the greeting to the group

## Notes

- Each sender_id maps to a separate user profile in FarmaFacil. Two people in the group searching for different drugs get independent results.
- New users will go through a lightweight onboarding (the bot asks for their location via text). This happens naturally through the chat responses.
- The `/api/v1/chat` endpoint is being built on FarmaFacil. It will be available once deployed.
