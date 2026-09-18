# Limitations

An honest list of what this workflow does not do, or does only in a simplified way. Most items are deliberate
scope decisions for a demo; the ones worth fixing for production say so.

## Scope

* **One lead per execution.** Each trigger event is one lead. The Code nodes read cross-node state with
  `$('Node').first()`, so a batch of several leads in one execution is not supported. Batch inputs need a
  *Loop Over Items* wrapper or a Split In Batches step before `Normalize lead`.
* **Enrichment is the public home page only.** A GET of `https://<domain>` and a text extraction of title,
  meta description and the first 600 characters. JavaScript-only sites, bot walls and redirects to app
  stores yield little or nothing. No third-party data provider is called; there is no free one without
  strings attached, which the spec also notes. Swap `Fetch company site` + `Process fetch result` for Firecrawl,
  Clearbit or Apollo if the client has a budget.
* **Nothing is sent to the lead.** Every branch, including cold "auto-reply", creates a Gmail *draft*. This is a
  safety choice for a demo. A real auto-reply is one operation change in `Gmail draft cold` and a decision the
  client has to make consciously.
* **Categories are four plus one.** `purchase_intent`, `question`, `support`, `other`, and `unclassified` for
  rule-scored leads. Enough for routing, not a taxonomy.
* **Business hours are one time zone.** `Rule scoring` has one `BUSINESS` constant (America/Vancouver,
  Mon–Fri 9–18); set your own zone there. Multi-region teams need a per-channel or per-lead time zone.
* **The free-mail list is a heuristic.** `Normalize lead` decides "corporate domain" with a fixed list of
  free-mail providers. An unlisted free-mail domain will be treated as a company domain and the lead becomes
  `rich`; harmless, just a wasted enrichment call.

## Data

* **Dedupe is email or phone, not both.** `dedupe_key` is the normalized email, or the phone if there is no
  email. The same person writing once by email and once by phone becomes two rows. Merging identities is a
  CRM feature, not a pipeline feature.
* **Status is overwritten on a repeat touch.** A lead marked `Contacted` by a human who writes again gets the
  status of the new run (Hot / Nurture / Archived). `Prepare routing` has the previous status
  (`prev_status`) if you want to protect manual states.
* **`touches` and `created_at` come from the upsert response.** Airtable returns the stored record on
  upsert, which is what makes the "no Search node" design work. Verified in test case 4; if Airtable ever
  changes that behaviour, `Lead state` degrades to `touches = 1`.
* **Airtable typecast is on.** Unknown single-select values (a new `source_channel` from your map, a typo) create
  a new option instead of failing. Convenient for adaptation, worth watching in production.
* **Concurrency.** Two identical leads arriving within the same second race on the upsert and can produce two
  rows. Airtable has no unique constraint. A queue or a lock is out of scope here.

## Reliability

* **Retry classification of LLM errors reads the message text.** The chain node exposes the error message,
  not a structured status code, so the classifier parses the code out of the message and falls back to
  keywords (rate limit, overloaded, timeout). It errs on the side of "do not retry".
* **Retry budget is small on purpose.** Three attempts, backoff capped at 20 seconds, so a whole run stays under
  the webhook timeout and Wait nodes stay in memory (under 65 seconds). Long outages go to the fallback
  contract and `needs_review`, not to a queue.
* **The Error Workflow catches crashes, not slow failures.** A downstream API that silently returns nonsense is
  caught by the contract validator (for the model) or not at all (for Airtable). `Runs` rows are the audit trail.
* **Health endpoint is shallow.** `GET /webhook/lead-intake-health` proves n8n is up and the workflow is active.
  It does not probe Airtable, the LLM, Gmail or Telegram.
* **Webhook callers always get HTTP 200.** n8n refuses to start a workflow from the Form Trigger if the workflow
  contains a *Respond to Webhook* node, so the intake webhook answers in "last node" mode instead: the body is
  still structured (`ok`, `code`, `errors` for invalid input; the qualification for valid input), but the HTTP
  status cannot be 400. A separate intake workflow would restore real status codes at the cost of a second
  workflow to maintain.
* **Webhook protection is a shared secret.** Header auth with one secret, no signature, no replay protection,
  no rate limiting. Fine behind a partner form or an internal connector, not for a public endpoint.

## Setup

* **Gmail OAuth2 is manual.** Google's consent screen cannot be scripted; the credential is created once in the
  n8n UI and referenced by id. Until then `deploy.py` imports the four Gmail nodes disabled (n8n 2.x refuses to
  activate a workflow with a node that lacks its credential), so the pipeline runs without drafts. Google
  keeps the OAuth app in "testing" mode until you publish it: refresh tokens then expire after 7 days and the
  sign-in has to be repeated.
* **Draft-in-thread depends on Gmail.** Replies for email leads pass the original `threadId`; the n8n community
  has reported cases where Gmail does not attach the draft to the thread. Verified working on 2026-09-18
  (draft landed in the sender's thread with the right recipient), but check it on your own account once.
* **The Telegram chat id is baked into the instance.** It is a placeholder in `workflow.json` and gets
  substituted at deploy. Changing the recipient means re-running `deploy.py` or editing two nodes.
* **Model choice is a parameter, not a benchmark.** The shipped workflow uses Google Gemini
  (`gemini-3.5-flash-lite` first, `gemini-3.5-flash` as fallback, temperature 0, output budget 4096 tokens
  because Gemini's thinking counts against it). Google retires model names quickly (`gemini-2.5-flash`
  already returns 404 for new keys) and the bigger flash models answered 503 "high demand" on most calls
  during testing, so check the dropdown in the model nodes if calls fail or take more than 30 seconds.
  The lite model is also the less careful one: it once filed a clear pricing request as `other` because the
  company was outside the ICP, which is why the prompt now separates intent (category) from fit (score).
* **The Claude variant is built, not battle-tested.** `--provider anthropic` wires `claude-opus-5` with
  `claude-sonnet-5` as fallback, adaptive thinking at low effort and no sampling parameters (the Claude 5
  family rejects them). It was not exercised in the test run above. No formal evaluation of scoring quality
  was run for either vendor; the test cases are smoke tests.
* **Prompts and templates are English.** Leads in other languages are qualified fine, but the reply drafts
  follow the `COMPANY.tone` instruction, which is English until you change it.
