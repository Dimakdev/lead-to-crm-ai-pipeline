# Sample payloads (spec §9 test cases)

Each file is a request body for `POST /webhook/lead-intake`. The webhook expects the header `x-webhook-secret`.

| # | File | Expected |
|---|------|----------|
| 1 | 01-b2c-referral-phone-only.json | `thin` → rule only (60 pts, +5 in business hours) → warm, no AI call, template draft skipped (no email) |
| 2 | 02-b2c-form-with-comment.json | `thin_comment` → LLM prompt A → hot → Telegram alert + Gmail draft |
| 3 | 03-b2b-corporate-domain.json | `rich` → enrichment (shopify.com) → LLM prompt B → personal draft in Gmail |
| 4 | 04-repeat-same-email.json | same email as #2 → row updated, no duplicate, `touches` = 2 |
| 5 | 05-broken-api-key.json | run with a broken Anthropic key → fallback (50 / warm / needs_review), `Runs` = error, Telegram alert |

Example:

```bash
curl -s -X POST "$N8N_BASE_URL/webhook/lead-intake" \
  -H "Content-Type: application/json" \
  -H "x-webhook-secret: $WEBHOOK_SECRET" \
  --data @sample-payloads/02-b2c-form-with-comment.json
```

`scripts/run_tests.py` sends all five in order and prints the result table.
