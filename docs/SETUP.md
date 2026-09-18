# Setup

Everything the workflow needs, where to get it, and how to plug in your own lead sources.

## What you need

| Thing | Why | Where to get it |
|---|---|---|
| n8n 2.39 or newer | runs the workflow | `docker compose up -d` in this folder, or your own instance |
| n8n API key | `deploy.py` imports and activates through the API | n8n: Settings → n8n API → Create an API key |
| Airtable base + personal access token | the CRM | airtable.com/create/tokens, scopes `data.records:read`, `data.records:write`, `schema.bases:read`, and `schema.bases:write` if you want the script to create the tables |
| Google Gemini API key | the model | aistudio.google.com/apikey |
| Telegram bot token + chat id (optional) | alerts for hot leads, reviews and crashes | @BotFather for the token; `python scripts/telegram_chat_id.py` prints the chat id after you message the bot |
| Gmail OAuth2 credential (optional) | inbound email as a source, reply drafts | created in the n8n UI, see below |
| Python 3.10 or newer | the scripts | standard library only, nothing to install |

Copy `.env.example` to `.env` and fill it in. `.env` is git-ignored, and so is `.deploy-state.json`, where
the script keeps the ids it created.

## The Gmail credential

Google's consent screen cannot be scripted, so this one is done by hand, once:

1. In Google Cloud Console pick or create a project, enable the **Gmail API**.
2. **OAuth consent screen**: external, any app name, your email as support and developer contact. Add the
   Gmail address you will connect under **Test users**.
3. **Credentials → Create credentials → OAuth client ID**, type *Web application*, redirect URI
   `http://localhost:5678/rest/oauth2-credential/callback` (adjust host and port to your n8n). Copy the
   client id and secret.
4. In n8n: **Credentials → Create → Gmail OAuth2 API**, paste both, *Sign in with Google*, allow.
5. The credential's id is the last part of its URL in n8n (`/home/credentials/<id>`). Put it into `.env`
   as `GMAIL_CREDENTIAL_ID` and run `python scripts/deploy.py` again.

While the OAuth app stays in Google's "testing" mode, its refresh tokens expire after seven days and the
sign-in has to be repeated. Publishing the app (no verification needed for your own use) removes that.

## What `deploy.py` does

1. Checks the n8n API and the Airtable base. With `--create-tables` it creates `Leads` and `Runs` if they
   are missing, exactly as described in [ARCHITECTURE.md](ARCHITECTURE.md#airtable).
2. Creates the n8n credentials it can create from `.env`: Airtable, Gemini (or Anthropic), Telegram, and a
   header-auth credential for the intake webhook with a generated secret. Their ids go to
   `.deploy-state.json`, so a second run reuses them instead of duplicating.
3. Replaces the placeholders in the two workflow files (base id, table ids, chat id, credential ids).
4. Imports the error workflow, wires its id into the main workflow's settings, imports the main workflow.
   Nodes whose credential is still missing are imported disabled, so activation works without them.
5. Activates both workflows and prints the URLs.

Run it again after any change to `.env` or after rebuilding the workflow; it updates in place.

## Manual import

If you would rather not use the script: import `error-workflow.json` first, then `workflow.json`
(*Workflows → Create → Import from file*), and set these by hand.

| Where | What to set |
|---|---|
| `Register lead`, `Save qualification`, `Log run`, and `Log failed run` in the error workflow | Airtable credential; base and table (switch the fields to "From list" and pick `Leads` / `Runs`) |
| `Gemini model`, `Gemini fallback model` | Google Gemini (PaLM) API credential |
| `Webhook lead intake` | a Header Auth credential: name `x-webhook-secret`, value of your choice |
| `Telegram alert`, and `Telegram error alert` in the error workflow | Telegram credential; replace `__TELEGRAM_CHAT_ID__` with your chat id |
| `Gmail trigger`, `Gmail draft hot`, `Gmail draft warm`, `Gmail draft cold` | Gmail OAuth2 credential |
| main workflow, *Settings → Error workflow* | the imported error workflow |

Activate the error workflow first, then the main one.

## Editing the workflow

The Code nodes exist in two places: as files in `src/nodes/` and inside the imported workflow. Pick one
place to edit. If you edit in the n8n editor, export the workflow when you want the change in git. If you
edit the files, run `python scripts/build_workflow.py` and then `python scripts/deploy.py`.

## Connect your own source

`Normalize lead` takes any trigger's output. It finds the channel either in `source_channel` inside the
payload or in the `TRIGGER_CHANNEL` table (trigger node name → channel), flattens the usual nested shapes
(`field_data` from Facebook, `fields` or `data.fields` from form builders), and the field map in `SOURCES`
may use dotted paths for nested values such as `from.first_name`.

Adding a channel is always the same two steps: wire the trigger into `Normalize lead`, add one entry to
`SOURCES` with the channel's weight and the mapping of its field names to the canonical lead
(`name`, `email`, `phone`, `company`, `domain`, `message`). A field you do not have is simply left out;
the lead becomes `thin` and goes through the rule.

**Facebook or Instagram Lead Ads.** Add n8n's *Facebook Lead Ads Trigger*, connect it to `Normalize lead`,
add `'Facebook Lead Ads Trigger': 'ads_meta'` to `TRIGGER_CHANNEL`. The `ads_meta` entry in `SOURCES`
already maps `full_name`, `email`, `phone_number` and `message`; rename the keys to match your form's
question names. Meta's permissions (page access, app review) are yours to obtain.

**Tally, Typeform, Fillout, Webflow, Jotform, any form builder with a webhook.** Point its webhook at
`POST /webhook/lead-intake`, add the header `x-webhook-secret` with the value from `.deploy-state.json`,
and pass `source_channel` in a hidden field or in the payload mapping. Arrays such as
`{ "fields": [{ "label": "Email", "value": "…" }] }` are flattened to `email`, `name` and so on. Anything
else gets its own `SOURCES` entry with the real keys.

**A Telegram bot as an intake channel.** Add a *Telegram Trigger* for message events, connect it to
`Normalize lead`, add `'Telegram Trigger': 'telegram_bot'` to `TRIGGER_CHANNEL` and one `SOURCES` entry:

```js
telegram_bot: { weight: 45, map: { name: 'message.from.first_name', message: 'message.text' } },
```

Ask for a phone number or an email in the bot conversation; without one the lead has no `dedupe_key` and
is rejected by validation.

The same two steps cover CRM exports, telephony webhooks and a Zapier or Make bridge.

## Testing

```bash
python scripts/run_tests.py --reset          # webhook cases 1 to 5 and the form
python scripts/run_tests.py --only 3         # one case
python scripts/run_tests.py --only 7         # wait for an email you send to the connected inbox
```

`--reset` deletes the test leads from Airtable first so the repeat-contact case starts from a clean slate.
Case 5 swaps the model's credential for a broken key and puts it back afterwards; the broken credential it
creates cannot be deleted through the n8n API and can be removed by hand.
