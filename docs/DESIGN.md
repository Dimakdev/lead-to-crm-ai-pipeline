# Design document

Written before the build, on 2026-09-14, as the spec the workflow was implemented against. It is kept here
because the decisions matter more than the node list. Where the build ended up deviating, the last section
says so; the as-built description is in [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. What the case has to prove

That an inbound lead turns into a scored CRM row and a ready reply draft within thirty seconds, with no
manual copying and no manual sorting. And that the system does not fall over when an external service
returns an error.

What it does not claim: writing a CRM from scratch, machine-learning models, a custom backend. This is
workflow automation plus AI integration.

## 2. The main architectural decision

One workflow, three processing branches, one shared output contract.

The fork is not the label "B2B" or "B2C". It is how much data actually arrived:

| Type | Signal | Who scores |
|---|---|---|
| `thin` | only a contact, a channel and a time | a rule, the AI is not called |
| `thin_comment` | a short comment or request text | the AI, one narrow task: warmth and intent |
| `rich` | a company or a corporate domain | the AI, full qualification and a draft |

All three branches return the same JSON. Downstream there is one table, one routing, one story.

The principle, stated in the case and on a call: the model makes one narrow judgment, n8n does everything
else. Triggers, retries, credentials, run history are the engine's job, not the model's.

### The canonical object

Everything from any source is reduced to this. It is the input contract:

```json
{
  "source_channel": "form_site",
  "received_at": "2026-09-14T10:02:00Z",
  "name": "",
  "email": "",
  "phone": "",
  "company": "",
  "domain": "",
  "message": "",
  "raw": {}
}
```

Required: `source_channel`, `received_at`, and at least one of `email` / `phone`. Everything else may be
empty; the lead simply becomes `thin`.

### The source map

Normalization has no if/else per channel. It reads one map:

```js
const SOURCES = {
  form_site:     { weight: 45, map: { name: 'name', email: 'email', message: 'comment' } },
  referral:      { weight: 60, map: { name: 'full_name', phone: 'tel' } },
  email_inbound: { weight: 45, map: { name: 'from_name', email: 'from', message: 'body' } },
  // add your channel: one entry here + your trigger wired into the normalize node
};
```

A new channel is one map entry plus a trigger. Nothing after that changes. A sticky note above the
normalize node says: *Add your source here → map to canonical fields*.

## 3. The output contract (the same for every branch)

```json
{
  "score": 0,
  "heat": "hot | warm | cold",
  "category": "string",
  "reason": "string, up to 200 characters",
  "draft": "string, may be empty",
  "needs_review": false
}
```

Rules: all fields are mandatory, even when empty; `reason` is written in human language, it is what the
manager reads in the CRM; `draft` is always empty for `thin` (a template reply goes there, not a personal
one); `needs_review` is true when validation failed or enrichment fell over.

## 4. Data in Airtable

### `Leads`

| Field | Type | Notes |
|---|---|---|
| lead_id | Autonumber | |
| created_at | Date and time | time of first contact |
| updated_at | Date and time | |
| source_channel | Single select | form_site, referral, ads_meta, ads_google, email_inbound, phone |
| lead_type | Single select | thin, thin_comment, rich |
| name | Text | |
| email | Email | |
| phone | Phone | |
| company | Text | empty for B2C |
| domain | Text | empty for B2C |
| message | Long text | the request text |
| enrichment | Long text | what was learned about the company |
| score | Number | 0 to 100 |
| heat | Single select | hot, warm, cold |
| category | Single select | |
| reason | Long text | why this score |
| draft | Long text | the reply draft |
| status | Single select | New, Hot, Nurture, Contacted, Archived |
| touches | Number | how many times the contact reached out |
| dedupe_key | Text | normalized email or phone |
| needs_review | Checkbox | |
| run_id | Text | link to the log |

### `Runs` (the run log)

run_id · timestamp · trigger_source · lead_ref · status (ok / retry / error) · failed_node ·
error_message · duration_ms

The log is written **always**, on success and on failure. It is half of what this case sells.

## 5. The node chain

```
[1] Triggers (two inputs)
     ├─ Form trigger (demo form)
     └─ Gmail trigger (inbound email)
                │
[2] Normalize (Code)
     canonical object + dedupe_key + lead_type
                │
[3] Airtable: upsert by dedupe_key
     found     → touches +1, update
     not found → create
                │
[4] Switch by lead_type
     ├─ thin         → [5a] rule scoring (Code)
     ├─ thin_comment → [5b] model: warmth + intent
     └─ rich         → [5c] enrichment (HTTP) → model: full qualification + draft
                │
[6] Validate the contract (Code)
     ├─ ok        → continue
     ├─ fail 1-2  → retry with a stricter prompt
     └─ fail 3    → fallback: score 50, heat warm, needs_review = true
                │
[7] Airtable: upsert into Leads
                │
[8] Switch by heat
     ├─ hot   → Telegram alert + Gmail draft (NOT sent)
     ├─ warm  → Gmail draft + status Nurture
     └─ cold  → template auto-reply + Archived
                │
[9] Airtable: row in Runs
```

On top of it all: an **error workflow** for the whole scenario, and retries with backoff on the HTTP and
model nodes.

## 6. The scoring rule for `thin` (no AI)

A starting table, tuned per client:

| Factor | Points |
|---|---|
| Channel: referral | 60 |
| Channel: website form (organic) | 45 |
| Channel: paid traffic | 35 |
| Channel: cold contact | 25 |
| Both phone and email present | +10 |
| Repeat contact (touches > 1) | +15 |
| Received in business hours | +5 |

Thresholds: 70 and up is hot, 40 to 69 warm, below 40 cold.

Deliberately simple and transparent. On a call it is a strong argument: the client sees the AI placed where
it is needed, not everywhere.

## 7. The prompts (two, both narrow)

**Prompt A, `thin_comment`.** Input: the comment text and the channel. Task: warmth and intent. Output:
score, heat, category, reason. Draft empty. Three or four categories, no more.

**Prompt B, `rich`.** Input: contact, company, enrichment, the request text. Task: rate the fit against the
ideal customer profile, explain the score in one sentence, write a reply draft in the company's tone.
Output: the full contract.

Both prompts: JSON only, no preamble, no markdown fence. Temperature 0. The prompt carries an example of the
exact output format; the JSON schema carries the field descriptions.

### Layers of protection around the output

Relying on one mechanism is not an option. In order:

1. the format instruction in the system prompt, plus an example;
2. a Structured Output Parser (schema from a JSON example or by hand; `$ref` is not supported);
3. an Auto-fixing Parser, **with caution**: it has known bugs, it breaks on triple backticks inside JSON
   strings and loses the fixed JSON when the model answers in content blocks;
4. **our own Code validator**, mandatory rather than a fallback. Strips fences, checks that every contract
   field is present, checks types and the `score` range.

## 8. Reliability

### Retry policy

Retry: 408, 409, 425, 429, 500, 502, 503, 504.
Do not retry: 400, 401, 403, 404, 422.
Delay: `min(maxDelay, baseDelay × 2^(attempt-1))` plus random jitter.

**Retry on fail only goes on nodes without side effects**, the enrichment HTTP call and the model call.
Nodes that write (Airtable create, Gmail draft) get no retry: three attempts would give three records or
three drafts. There it is *continue on error* plus an existence check before writing, which for us is
`dedupe_key`.

### The error workflow

A separate workflow with an Error Trigger, shared across the instance. It receives the workflow id, the
execution id and the error text, writes a row into `Runs` and sends a notification.

Environment variable `EXECUTIONS_DATA_SAVE_ON_ERROR=all`, so the data of a failed execution is kept and the
run can be restarted from the UI.

## 9. Test cases

1. A B2C request, phone only, channel referral → rule, 60 points, warm, the nurture flow. The AI was not
   called.
2. A B2C request with the comment "how much does it cost, I need it by next week" → the AI, hot, a Telegram
   alert.
3. A B2B request from a corporate domain → enrichment, full qualification, a personal draft in Gmail.
4. A repeat request with the same email → the row is updated, no duplicate is created, touches = 2.
5. A deliberately broken API key → the workflow does not fall over, the error is in `Runs`, a notification,
   `needs_review`.

## 10. Adapting it to your business

Whoever takes the workflow does three things:

1. **Their own channel.** Connect their trigger (Facebook Lead Ads, Instagram DM, WhatsApp, Telegram, their
   own form) to the input of the normalize node. Platform permissions are on the side of whoever adapts;
   the workflow does not require any.
2. **An entry in the source map.** Add a key to `SOURCES`: the channel weight and the mapping of incoming
   fields to the canonical object. No fields? The lead becomes `thin` and goes through the rule, nothing
   breaks.
3. **Their own weights and thresholds.** Section 6: channel weights and the 70/40 thresholds are starting
   numbers, not the truth.

What does not need changing: the output contract, the Airtable schema, the routing, the prompts.

## 11. Open source: what is borrowed, what is written here

The rule: look, take an idea or a single block, write your own. Your own JSON goes into your own
repository.

### Borrowed

| Element | What was found | How it is used |
|---|---|---|
| [3] and [7], Airtable | the Airtable node has a native **Create or Update (upsert)** operation; there is a template for batch upsert with `fieldsToMergeOn` | drop the separate Search + IF, upsert by `dedupe_key`; two nodes fewer |
| [6], validation | Structured Output Parser (schema from a JSON example; `$ref` not supported) plus the **Robust JSON Parser** template (a sub-workflow that cleans backticks, repairs quotes and trailing commas) | the parser as layer 2, the robust parser as layer 4 instead of code from scratch |
| retries | a reusable retry-handler template: backoff, jitter, status-code classification | take the formula and the code lists, not the workflow (it drags in Slack and SMTP) |
| the error workflow | an error logging and alerts template (a log in Sheets, an email with the workflow name, the failed node and a link to the execution) | take the structure of the log row, write to Airtable `Runs` instead of Sheets |
| [8], Gmail draft | the Gmail AI Auto-Responder template: converts text to HTML and puts a draft into the thread as a reply to the first message | take the way the draft is placed into the thread |
| enrichment | a template that enriches company leads through Firecrawl (positioned as a free self-hosted alternative to Apollo or Clay) | a candidate if real enrichment is done |

### Written here

Normalization with the source map, the rule scoring for `thin`, both prompts, the Airtable schema, the
routing by heat, the output contract.

### Gotchas that cost hours

1. **The Gmail trigger "On Message Received" also fires when a draft is created.** We have Gmail as an input
   and a Gmail draft as an output: a ready-made infinite loop. Cured with a filter: query `-in:draft` or
   excluding the `DRAFT` label.
2. **Gmail "Create Draft" does not always create a real reply inside the thread**, a known community
   complaint even with the thread id passed correctly. Check it on the first test.
3. **The Auto-fixing Parser** has two open bugs (section 7). Do not rely on it.
4. **Retry on fail on writing nodes** produces duplicate records and emails. Only continue on error plus an
   existence check.
5. **Airtable silently ignores a non-existent field** when creating a record: no error, just no data.
   Field names are case-sensitive.

### Not found

A free API for B2B contact enrichment without strings attached. In the n8n community this is an open
question. For the demo it is not a blocker: enrichment can be shown on the domain's public data or marked
as an optional step.

### Sources on GitHub

| Repository | License | What was taken |
|---|---|---|
| `Tukdify/LeadIQ-AI` | MIT | the closest analogue: 55 nodes, webhook → validation → normalization → LLM → parser with a strict schema → routing → log → Slack/Gmail. Taken: the parser, the router and the input validation |
| `Awaisali36/50k-lead-generation-system` | MIT | n8n + Airtable + enrichment, scoring at volume. A candidate for the enrichment block |
| `EtienneLescot/n8n-as-code` | MIT | 537 nodes with full schemas and Git-style sync. A tool, if `workflow.json` is generated by code |
| `Danitilahun/n8n-workflow-templates` | none | a catalogue of 2053 workflows: 20 with Airtable, 41 with error handling. For studying patterns only |
| `christinec-dev/n8n-Audit-Workflow` | none | a workflow auditor for security, error handling and readability. Run your JSON through it before publishing |
| `AviVAvi/AI-Lead-Qualification-Sales-Automation` | none | 12 nodes, a simple analogue. Look, do not copy |

**The license rule:** MIT can be reused with attribution. A repository without a license file means all
rights reserved by default: look, learn, write your own code.

### Patterns taken from LeadIQ-AI

1. **The LLM-output parser**: `JSON.parse` first, on failure a regex extraction of `{...}`, then required
   fields, an enum check on the priority and a score-versus-tier check. A ready replacement for our layer 4.
2. **Separating "prepare the decision" from "execute"**: a separate node builds the routing object
   (`queue`, `notify_sales`, `send_email`, `archive`) with no side effects at all, and only then do the nodes
   act. The routes are a frozen constant.
3. **Input validation with a 400 response**: an invalid request returns a structured error instead of a
   failed execution.
4. **A webhook secret check** as a separate node before anything else.
5. **A health-check endpoint** as a second webhook: cheap, and it looks good in a case.
6. **Snapshot and merge after every channel**: each channel's delivery status is written separately, so it
   is visible what exactly worked.
7. **`LIMITATIONS.md` in the repository**: an honest list of limits. A strong move for a portfolio; the
   practice itself is copied.

**Not taken from LeadIQ-AI:** Supabase as a dependency for deduplication (we have the Airtable upsert) and
the 55-node scale itself.

## What changed in the build

- **The model.** The spec assumed Claude. The shipped workflow runs Google Gemini, and the vendor is a
  single sub-node: `--provider anthropic` rebuilds it with Claude. Temperature 0 is honoured with Gemini;
  the Claude 5 family rejects sampling parameters, so that variant runs with adaptive thinking instead.
- **A fallback model** was added inside the chain node after Gemini's bigger flash models answered 503
  "high demand" on most calls during testing.
- **A third input**, the intake webhook with a header secret. It is where the test cases go in, and the
  way any channel without its own n8n trigger connects.
- **No Respond-to-Webhook nodes.** n8n refuses to start a workflow from the Form Trigger if the workflow
  contains one, so the intake webhook answers in "last node" mode: a structured body, always HTTP 200.
- **The health check** is a webhook plus a Code node for the same reason. The webhook-secret check is n8n's
  own header-auth credential rather than a separate node.
- **Retries** are explicit loops rather than the node setting, because n8n's "retry on fail" waits a fixed
  time and retries every error, which the policy in section 8 does not allow.
- **`category`** got its values: `purchase_intent`, `question`, `support`, `other`, and `unclassified` for
  rule-scored leads.
- **`dedupe_key` is the primary field** of `Leads`; Airtable does not allow an autonumber as primary.
- The Robust JSON Parser sub-workflow was not used; the Code validator in `validate_contract.js` covers
  layer 4 on its own.
