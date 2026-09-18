# Lead → CRM AI pipeline

A self-hosted n8n workflow that turns an inbound lead into a scored CRM row and a reply draft in about
half a minute, and keeps working when one of the services on the way fails.

It was built as a portfolio piece, so it is made to be forked: no secrets in the files, one script to
deploy, one map to plug in your own lead sources, and an honest list of what it does not do.

## What happens to a lead

1. A lead comes in: through the built-in form, as an email to your Gmail inbox, or as a POST to the intake
   webhook. The webhook is how you connect everything else: Facebook Lead Ads, Tally, Typeform, a CRM
   export, a phone system.
2. The workflow normalizes it into one shape and looks at how much data actually arrived:
   - a contact and a channel, nothing more: a transparent scoring rule, no AI call;
   - a contact plus a message: the model judges warmth and intent;
   - a contact plus a company or a corporate email domain: a look at the company's public website, then a
     full qualification and a personal reply draft.
3. Every branch returns the same six fields: `score`, `heat`, `category`, `reason`, `draft`, `needs_review`.
4. The row lands in Airtable (create or update, never a duplicate), a hot lead pings Telegram, a draft reply
   appears in Gmail (never sent), and the run is logged, success or failure.

Reliability is part of the design rather than an afterthought. A second model steps in when the first is
overloaded. Calls without side effects are retried with backoff; writes never are. Whatever still crashes
lands in a separate error workflow that logs it and sends an alert.

## What is where

| Path | What it is |
|---|---|
| `workflow.json` | the main workflow, 37 nodes, import-ready, no secrets inside |
| `error-workflow.json` | the error handler: one log row and one Telegram alert per crash |
| `src/nodes/` | the twelve Code nodes as plain JavaScript files, the part worth reading |
| `scripts/deploy.py` | imports both workflows into n8n, creates the credentials and Airtable tables, activates |
| `scripts/run_tests.py` | sends the test cases and prints what happened |
| `scripts/build_workflow.py` | rebuilds the two JSON files from `src/nodes/` after you edit a node |
| `sample-payloads/` | one request body per test case |
| `docs/ARCHITECTURE.md` | branches, output contract, Airtable schema, retry policy, test results |
| `docs/SETUP.md` | every key and credential, manual import, how to connect your own source |
| `docs/DESIGN.md` | the design document written before the build, and what changed on the way |
| `LIMITATIONS.md` | what this workflow does not do |
| `docker-compose.yml` | a minimal n8n, if you do not have one running |

## Get it running

You need Docker (or an existing n8n 2.39 or newer), Python 3.10 or newer, an Airtable base with a
personal access token, and a Google Gemini API key. Telegram and Gmail are optional: without them the
workflow still runs, it just does not alert or draft.

```bash
git clone https://github.com/Dimakdev/lead-to-crm-ai-pipeline.git
cd lead-to-crm-ai-pipeline
docker compose up -d                       # n8n at http://localhost:5678, create the owner account once
cp .env.example .env                       # fill in the keys, docs/SETUP.md says where each one comes from
python scripts/deploy.py --create-tables   # tables, credentials, both workflows, activation
```

The script prints the form URL, the webhook URL and a health-check URL. Gmail is the one credential that
needs a browser, because of Google's consent screen: create it once in the n8n UI, paste its id into
`.env`, run the script again. Details in [docs/SETUP.md](docs/SETUP.md).

## Try it

```bash
python scripts/run_tests.py --reset
```

This sends five leads through the webhook and one through the form, waits for each run, and prints a
table with the score, the CRM status, the number of model calls, the log row, and whether Telegram and
Gmail did their part. The cases are the ones from the spec: a phone-only referral (rule only), a "how much
does it cost, I need it next week" (hot), a B2B lead from a corporate domain (enrichment plus draft), the
same email again (updated row, no duplicate), and a deliberately broken API key (no crash, flagged for
review). Case 7 waits for an email you send to the connected inbox.

Then open the form at `http://localhost:5678/form/lead-form` and send a lead yourself.

## Make it yours

Everything that is specific to a business sits in three places, each a short block at the top of a Code
node:

- `SOURCES` in **Normalize lead**: your channels, their weight for the scoring rule, and how their field
  names map to the canonical lead.
- `COMPANY` in **Build prompt**: who you are, what you sell, who your ideal customer is, the tone of the
  drafts, the signature.
- `TEMPLATES` in **Prepare routing**: the three reply templates for leads that do not get a personal draft.

The scoring rule (channel weights, the 70 / 40 thresholds, business hours) lives in **Rule scoring**. The
model is a sub-node under **AI qualify**: swap it for another Gemini model, or rebuild with
`--provider anthropic` to use Claude. Prompts, parser, validator and routing stay the same.

Connecting a new source is one trigger node wired into **Normalize lead** plus one entry in `SOURCES`.
Recipes for Facebook Lead Ads, form builders and a Telegram bot are in
[docs/SETUP.md](docs/SETUP.md#connect-your-own-source).

## Why it is built this way

- The model makes one narrow judgment. Triggers, retries, credentials and run history are the workflow
  engine's job, and the engine is better at them.
- AI sits where there is something to judge. A phone number and a channel name are scored by a rule
  anyone can read.
- One output contract for all branches means one table, one routing table and one story to tell.
- Airtable's native upsert replaces the usual search-then-branch, and the row it returns carries the
  previous state, so a repeat contact costs no extra read.
- Retries are explicit loops with status-code classification and backoff, not the engine's blanket
  "retry on fail". Writes are never retried: three attempts would mean three rows or three drafts.

The longer version, with the schema and the numbers from the test run, is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Credits and license

Patterns borrowed from MIT-licensed work, with thanks: the LLM-output parser, the "decide first, then act"
routing split, input validation and the health check come from
[Tukdify/LeadIQ-AI](https://github.com/Tukdify/LeadIQ-AI);
[Awaisali36/50k-lead-generation-system](https://github.com/Awaisali36/50k-lead-generation-system) served as
a reference for the Airtable node; [EtienneLescot/n8n-as-code](https://github.com/EtienneLescot/n8n-as-code)
for node schemas. The workflow itself is written from scratch.

MIT license, see [LICENSE](LICENSE).
