#!/usr/bin/env python3
"""Deploy the Lead -> CRM pipeline to a running n8n instance through its REST API.

What it does, in order:
  1. reads .env, checks the n8n API and the Airtable base (tables Leads + Runs; --create-tables builds them)
  2. creates the n8n credentials it can create from .env (Airtable, Gemini or Anthropic, Telegram, webhook header auth)
     and remembers their ids in .deploy-state.json, so re-running never duplicates them
  3. replaces the placeholders in workflow.json / error-workflow.json (base id, table ids, chat id, credential ids)
  4. creates or updates both workflows (error workflow first, its id goes into the main workflow settings)
  5. activates the main workflow and prints the URLs

Gmail OAuth2 cannot be created through the API (Google consent screen). Create it once in the n8n UI and put its
id into GMAIL_CREDENTIAL_ID, or leave it empty: the four Gmail nodes are then left without a credential and the
Gmail trigger is disabled so activation still works. Pick the credential in the editor later.

Usage:  python scripts/deploy.py [--create-tables] [--no-activate] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys

from common import ROOT, Airtable, HttpError, N8n, load_env, load_state, require, save_state

MAIN_FILE = ROOT / "workflow.json"
ERROR_FILE = ROOT / "error-workflow.json"

# Airtable schema per spec §4, used by --create-tables (Meta API shapes)
DT = {"dateFormat": {"name": "iso"}, "timeFormat": {"name": "24hour"}, "timeZone": "America/Vancouver"}
LEADS_SPEC = {
    "name": "Leads",
    "description": "CRM table of the Lead -> CRM AI pipeline. Dedupe key: dedupe_key.",
    "fields": [
        {"name": "dedupe_key", "type": "singleLineText", "description": "normalized email or phone"},
        {"name": "lead_id", "type": "autoNumber"},
        {"name": "created_at", "type": "dateTime", "options": DT},
        {"name": "updated_at", "type": "dateTime", "options": DT},
        {"name": "source_channel", "type": "singleSelect", "options": {"choices": [
            {"name": v} for v in ["form_site", "referral", "ads_meta", "ads_google", "email_inbound", "phone"]]}},
        {"name": "lead_type", "type": "singleSelect", "options": {"choices": [
            {"name": v} for v in ["thin", "thin_comment", "rich"]]}},
        {"name": "name", "type": "singleLineText"},
        {"name": "email", "type": "email"},
        {"name": "phone", "type": "phoneNumber"},
        {"name": "company", "type": "singleLineText"},
        {"name": "domain", "type": "singleLineText"},
        {"name": "message", "type": "multilineText"},
        {"name": "enrichment", "type": "multilineText"},
        {"name": "score", "type": "number", "options": {"precision": 0}},
        {"name": "heat", "type": "singleSelect", "options": {"choices": [{"name": v} for v in ["hot", "warm", "cold"]]}},
        {"name": "category", "type": "singleSelect", "options": {"choices": [
            {"name": v} for v in ["purchase_intent", "question", "support", "other", "unclassified"]]}},
        {"name": "reason", "type": "multilineText"},
        {"name": "draft", "type": "multilineText"},
        {"name": "status", "type": "singleSelect", "options": {"choices": [
            {"name": v} for v in ["New", "Hot", "Nurture", "Contacted", "Archived"]]}},
        {"name": "touches", "type": "number", "options": {"precision": 0}},
        {"name": "needs_review", "type": "checkbox", "options": {"icon": "check", "color": "redBright"}},
        {"name": "run_id", "type": "singleLineText"},
    ],
}
RUNS_SPEC = {
    "name": "Runs",
    "description": "Run log of the Lead -> CRM AI pipeline. One row per execution, success or failure.",
    "fields": [
        {"name": "run_id", "type": "singleLineText"},
        {"name": "timestamp", "type": "dateTime", "options": DT},
        {"name": "trigger_source", "type": "singleLineText"},
        {"name": "lead_ref", "type": "singleLineText"},
        {"name": "status", "type": "singleSelect", "options": {"choices": [{"name": v} for v in ["ok", "retry", "error"]]}},
        {"name": "failed_node", "type": "singleLineText"},
        {"name": "error_message", "type": "multilineText"},
        {"name": "duration_ms", "type": "number", "options": {"precision": 0}},
    ],
}

CREDENTIALS = [
    # key in state, placeholder, n8n credential type, display name, builder(env) -> data or None if not configured
    ("airtable", "__AIRTABLE_CRED_ID__", "airtableTokenApi", "Airtable PAT (Lead to CRM)",
     lambda e: {"accessToken": e["AIRTABLE_PAT"]} if e.get("AIRTABLE_PAT") else None),
    ("anthropic", "__ANTHROPIC_CRED_ID__", "anthropicApi", "Anthropic (Lead to CRM)",
     lambda e: {"apiKey": e["ANTHROPIC_API_KEY"]} if e.get("ANTHROPIC_API_KEY") else None),
    ("gemini", "__GEMINI_CRED_ID__", "googlePalmApi", "Google Gemini (Lead to CRM)",
     lambda e: {"apiKey": e["GEMINI_API_KEY"], "host": "https://generativelanguage.googleapis.com"}
     if e.get("GEMINI_API_KEY") else None),
    ("telegram", "__TELEGRAM_CRED_ID__", "telegramApi", "Telegram bot (Lead to CRM)",
     lambda e: {"accessToken": e["TELEGRAM_BOT_TOKEN"], "baseUrl": "https://api.telegram.org"}
     if e.get("TELEGRAM_BOT_TOKEN") else None),
    ("webhook_auth", "__WEBHOOK_AUTH_CRED_ID__", "httpHeaderAuth", "Lead intake header auth",
     lambda e: {"name": "x-webhook-secret", "value": e["WEBHOOK_SECRET"]}),
]
# googlePalmApi requires `host` and the API schema carries no default for it
GEMINI_HOST = "https://generativelanguage.googleapis.com"


def find_table_ids(at: Airtable, create: bool) -> dict:
    tables = {t["name"]: t for t in at.tables()}
    ids = {}
    for spec in (LEADS_SPEC, RUNS_SPEC):
        name = spec["name"]
        if name not in tables:
            if not create:
                sys.exit(f"Airtable table '{name}' not found in base. Re-run with --create-tables "
                         f"or create it by hand (schema: README.md -> Airtable schema).")
            print(f"  creating table {name} ...")
            tables[name] = at.create_table(spec)
        ids[name] = tables[name]["id"]
        have = {f["name"] for f in tables[name]["fields"]}
        missing = [f["name"] for f in spec["fields"] if f["name"] not in have]
        if missing:
            sys.exit(f"Airtable table '{name}' is missing fields: {', '.join(missing)} (field names are case-sensitive).")
        print(f"  table {name}: {ids[name]} ({len(have)} fields)")
    return ids


def fill_defaults(n8n: N8n, cred_type: str, data: dict) -> dict:
    """Add required fields with defaults from the credential schema (e.g. telegram baseUrl)."""
    try:
        schema = n8n.credential_schema(cred_type)
    except HttpError:
        return data
    for key, prop in (schema.get("properties") or {}).items():
        if key not in data and key in (schema.get("required") or []) and "default" in prop:
            data[key] = prop["default"]
    return data


def ensure_credentials(n8n: N8n, env: dict, state: dict, dry: bool) -> dict:
    ids = {}
    creds = state.setdefault("credentials", {})
    for key, placeholder, cred_type, name, builder in CREDENTIALS:
        if creds.get(key):
            ids[placeholder] = creds[key]
            print(f"  {name}: reusing id {creds[key]}")
            continue
        data = builder(env)
        if data is None:
            print(f"  {name}: not configured in .env, node keeps the placeholder")
            continue
        if dry:
            print(f"  {name}: would create ({cred_type})")
            continue
        created = n8n.create_credential(name, cred_type, fill_defaults(n8n, cred_type, data))
        creds[key] = created["id"]
        ids[placeholder] = created["id"]
        save_state(state)
        print(f"  {name}: created id {created['id']}")
    if env.get("GMAIL_CREDENTIAL_ID"):
        ids["__GMAIL_CRED_ID__"] = env["GMAIL_CREDENTIAL_ID"]
        print(f"  Gmail (Lead to CRM): using id {env['GMAIL_CREDENTIAL_ID']} from .env")
    else:
        print("  Gmail: no GMAIL_CREDENTIAL_ID, Gmail nodes stay unassigned and the Gmail trigger is disabled")
    return ids


def substitute(wf: dict, mapping: dict) -> dict:
    text = json.dumps(wf, ensure_ascii=False)
    for k, v in mapping.items():
        text = text.replace(k, str(v))
    return json.loads(text)


def strip_unassigned_credentials(wf: dict) -> list[str]:
    """Nodes whose credential is still a placeholder are disabled: n8n 2.x refuses to activate a workflow
    with a node that misses a required credential. A disabled node passes its input through, so the
    pipeline keeps running without that side effect. Add the credential to .env (or the id for Gmail),
    re-run deploy.py, and the node comes back enabled."""
    left = []
    for node in wf["nodes"]:
        creds = node.get("credentials") or {}
        if any(str(c.get("id", "")).startswith("__") for c in creds.values()):
            node.pop("credentials", None)
            node["disabled"] = True
            left.append(node["name"])
    return left


def upsert_workflow(n8n: N8n, state: dict, key: str, wf: dict, dry: bool) -> str:
    wfs = state.setdefault("workflows", {})
    if dry:
        print(f"  {wf['name']}: would {'update ' + wfs[key] if wfs.get(key) else 'create'}")
        return wfs.get(key, "__DRY_RUN__")
    if wfs.get(key):
        try:
            n8n.update_workflow(wfs[key], wf)
            print(f"  {wf['name']}: updated {wfs[key]}")
            return wfs[key]
        except HttpError as e:
            if e.status != 404:
                raise
            print(f"  {wf['name']}: stored id {wfs[key]} no longer exists, creating a new one")
    created = n8n.create_workflow(wf)
    wfs[key] = created["id"]
    save_state(state)
    print(f"  {wf['name']}: created {created['id']}")
    return created["id"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--create-tables", action="store_true", help="create Leads/Runs in Airtable if missing")
    ap.add_argument("--no-activate", action="store_true", help="import but do not activate the main workflow")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, write nothing")
    args = ap.parse_args()

    env = load_env()
    require(env, "N8N_API_KEY", "AIRTABLE_PAT", "AIRTABLE_BASE_ID")
    main_text = MAIN_FILE.read_text(encoding="utf-8")
    if "__GEMINI_CRED_ID__" in main_text:
        require(env, "GEMINI_API_KEY")
    if "__ANTHROPIC_CRED_ID__" in main_text:
        require(env, "ANTHROPIC_API_KEY")
    state = load_state()
    if not env.get("WEBHOOK_SECRET"):
        env["WEBHOOK_SECRET"] = state.get("webhook_secret") or secrets.token_urlsafe(24)
    state["webhook_secret"] = env["WEBHOOK_SECRET"]
    if not args.dry_run:
        save_state(state)

    n8n = N8n(env.get("N8N_BASE_URL", "http://localhost:5678"), env["N8N_API_KEY"])
    print("1. n8n API")
    print(f"  {len(n8n.list_workflows())} workflows visible at {n8n.base}")

    print("2. Airtable")
    at = Airtable(env["AIRTABLE_PAT"], env["AIRTABLE_BASE_ID"])
    table_ids = find_table_ids(at, create=args.create_tables and not args.dry_run)
    state["airtable"] = {"base": env["AIRTABLE_BASE_ID"], "leads": table_ids["Leads"], "runs": table_ids["Runs"]}

    print("3. Credentials")
    cred_ids = ensure_credentials(n8n, env, state, args.dry_run)

    mapping = {
        "__AIRTABLE_BASE_ID__": env["AIRTABLE_BASE_ID"],
        "__LEADS_TABLE_ID__": table_ids["Leads"],
        "__RUNS_TABLE_ID__": table_ids["Runs"],
        "__TELEGRAM_CHAT_ID__": env.get("TELEGRAM_CHAT_ID", "") or "__TELEGRAM_CHAT_ID__",
        **cred_ids,
    }

    print("4. Workflows")
    error_wf = substitute(json.loads(ERROR_FILE.read_text(encoding="utf-8")), mapping)
    error_id = upsert_workflow(n8n, state, "error", error_wf, args.dry_run)

    main_wf = substitute(json.loads(MAIN_FILE.read_text(encoding="utf-8")), {**mapping, "__ERROR_WORKFLOW_ID__": error_id})
    left = strip_unassigned_credentials(main_wf) + strip_unassigned_credentials(error_wf)
    if left:
        print(f"  disabled until their credential exists: {', '.join(dict.fromkeys(left))}")
    main_id = upsert_workflow(n8n, state, "main", main_wf, args.dry_run)

    if args.dry_run:
        print("dry run finished, nothing written")
        return

    print("5. Activate")
    if args.no_activate:
        print("  skipped (--no-activate)")
    else:
        # n8n 2.x only runs an error workflow that is active itself
        for label, wid in (("error workflow", error_id), ("main workflow", main_id)):
            try:
                n8n.activate(wid)
                print(f"  {label} active")
            except HttpError as e:
                if "already active" in e.body.lower():
                    print(f"  {label} already active")
                else:
                    print(f"  {label} activation failed: {e}\n  Fix the credential the error names, then: python scripts/deploy.py")

    save_state(state)
    base = n8n.base
    print("\nDone.")
    print(f"  Editor:        {base}/workflow/{main_id}")
    print(f"  Error editor:  {base}/workflow/{error_id}")
    print(f"  Demo form:     {base}/form/lead-form")
    print(f"  Webhook:       POST {base}/webhook/lead-intake   (header x-webhook-secret: see .deploy-state.json)")
    print(f"  Health:        GET  {base}/webhook/lead-intake-health")
    print(f"  State file:    {ROOT / '.deploy-state.json'} (ids only, keep it out of git)")


if __name__ == "__main__":
    main()
