#!/usr/bin/env python3
"""Assemble workflow.json and error-workflow.json from src/nodes/*.js.

Why a build script: Code-node JavaScript stays readable and reviewable in src/nodes/,
positions and connections are declared once, and the graph is validated before export.
Node types / typeVersions were checked against n8n 2.39.5 (nodes-base 2.39.3, langchain 2.39.3).

Run:  python scripts/build_workflow.py [--provider gemini|anthropic]   (default: gemini)

The chat model is one sub-node. --provider picks which one is wired in: Google Gemini (googlePalmApi credential,
temperature 0 as the spec asks) or Anthropic Claude (anthropicApi credential, adaptive thinking; the Claude 5 family
rejects sampling parameters, so no temperature there).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "nodes"
NS = uuid.UUID("6f1c2f0e-3c7a-4d7e-9c6e-1f7f2b1a9a11")  # stable ids across builds

# Placeholders substituted at deploy time (scripts/deploy.py) or by hand after a UI import.
PH = {
    "base": "__AIRTABLE_BASE_ID__",
    "leads": "__LEADS_TABLE_ID__",
    "runs": "__RUNS_TABLE_ID__",
    "chat": "__TELEGRAM_CHAT_ID__",
    "error_wf": "__ERROR_WORKFLOW_ID__",
}
CRED = {
    "airtable": {"airtableTokenApi": {"id": "__AIRTABLE_CRED_ID__", "name": "Airtable PAT (Lead to CRM)"}},
    "anthropic": {"anthropicApi": {"id": "__ANTHROPIC_CRED_ID__", "name": "Anthropic (Lead to CRM)"}},
    "gemini": {"googlePalmApi": {"id": "__GEMINI_CRED_ID__", "name": "Google Gemini (Lead to CRM)"}},
    "telegram": {"telegramApi": {"id": "__TELEGRAM_CRED_ID__", "name": "Telegram bot (Lead to CRM)"}},
    "gmail": {"gmailOAuth2": {"id": "__GMAIL_CRED_ID__", "name": "Gmail (Lead to CRM)"}},
    "webhook": {"httpHeaderAuth": {"id": "__WEBHOOK_AUTH_CRED_ID__", "name": "Lead intake header auth"}},
}

# Airtable schema (spec §4) as the resource-mapper sees it: (field, mapper type, options)
SELECT = {
    "source_channel": ["form_site", "referral", "ads_meta", "ads_google", "email_inbound", "phone"],
    "lead_type": ["thin", "thin_comment", "rich"],
    "heat": ["hot", "warm", "cold"],
    "category": ["purchase_intent", "question", "support", "other", "unclassified"],
    "status": ["New", "Hot", "Nurture", "Contacted", "Archived"],
    "run_status": ["ok", "retry", "error"],
}
LEADS_FIELDS = [
    ("dedupe_key", "string"), ("lead_id", "number"), ("created_at", "dateTime"), ("updated_at", "dateTime"),
    ("source_channel", "options"), ("lead_type", "options"), ("name", "string"), ("email", "string"),
    ("phone", "string"), ("company", "string"), ("domain", "string"), ("message", "string"),
    ("enrichment", "string"), ("score", "number"), ("heat", "options"), ("category", "options"),
    ("reason", "string"), ("draft", "string"), ("status", "options"), ("touches", "number"),
    ("needs_review", "boolean"), ("run_id", "string"),
]
RUNS_FIELDS = [
    ("run_id", "string"), ("timestamp", "dateTime"), ("trigger_source", "string"), ("lead_ref", "string"),
    ("status", "options"), ("failed_node", "string"), ("error_message", "string"), ("duration_ms", "number"),
]
READ_ONLY = {"lead_id"}

CONTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100,
                  "description": "Lead score 0-100. hot >= 70, warm 40-69, cold < 40."},
        "heat": {"type": "string", "enum": ["hot", "warm", "cold"],
                 "description": "Tier derived from the score."},
        "category": {"type": "string", "enum": ["purchase_intent", "question", "support", "other"],
                     "description": "Intent category of the message."},
        "reason": {"type": "string", "maxLength": 200,
                   "description": "One plain-English sentence for the sales manager, max 200 characters."},
        "draft": {"type": "string",
                  "description": "Reply draft. Empty string when a template reply is enough (thin leads)."},
        "needs_review": {"type": "boolean", "description": "Always false; the system sets this flag."},
    },
    "required": ["score", "heat", "category", "reason", "draft", "needs_review"],
    "additionalProperties": False,
}

FETCH_UA = "Mozilla/5.0 (compatible; lead-to-crm-demo/1.0; +https://github.com/)"


# ---------------------------------------------------------------- helpers
def uid(seed: str) -> str:
    return str(uuid.uuid5(NS, seed))


def js(name: str) -> str:
    return (SRC / f"{name}.js").read_text(encoding="utf-8")


def mapper_schema(fields, with_id: bool, select_key=None):
    schema = []
    if with_id:
        schema.append({"id": "id", "displayName": "id", "required": False, "defaultMatch": True, "display": True,
                       "type": "string", "readOnly": True, "removed": False})
    for name, typ in fields:
        entry = {"id": name, "displayName": name, "required": False, "defaultMatch": False,
                 "canBeUsedToMatch": True, "display": True, "type": typ,
                 "readOnly": name in READ_ONLY, "removed": name in READ_ONLY}
        if typ == "options":
            key = select_key(name) if select_key else name
            entry["options"] = [{"name": v, "value": v} for v in SELECT[key]]
        schema.append(entry)
    return schema


def cond(left: str, op_type: str, operation: str, right="", seed=""):
    single = operation in ("true", "false", "exists", "notExists", "empty", "notEmpty")
    operator = {"type": op_type, "operation": operation}
    if single:
        operator["singleValue"] = True
    return {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
        "conditions": [{"id": uid(f"cond:{seed}{left}{operation}{right}"), "leftValue": left,
                        "rightValue": right, "operator": operator}],
        "combinator": "and",
    }


class Graph:
    def __init__(self, name: str):
        self.name = name
        self.nodes: list[dict] = []
        self.connections: dict = {}

    def add(self, name, type_, version, params, x, y, **extra):
        if any(n["name"] == name for n in self.nodes):
            raise ValueError(f"duplicate node name {name!r}")
        node = {"parameters": params, "id": uid(f"{self.name}:{name}"), "name": name, "type": type_,
                "typeVersion": version, "position": [x, y]}
        extra.pop("notesInFlow", None)  # node notes stay in the node's settings panel, not on the canvas
        node.update(extra)
        self.nodes.append(node)
        return name

    def sticky(self, content, x, y, w, h, color=1):
        n = f"Note {len([n for n in self.nodes if n['type'] == 'n8n-nodes-base.stickyNote']) + 1}"
        return self.add(n, "n8n-nodes-base.stickyNote", 1,
                        {"content": content, "height": h, "width": w, "color": color}, x, y)

    def link(self, src, dst, out=0, inp=0, ctype="main"):
        lst = self.connections.setdefault(src, {}).setdefault(ctype, [])
        while len(lst) <= out:
            lst.append([])
        lst[out].append({"node": dst, "type": ctype, "index": inp})

    def validate(self):
        names = {n["name"] for n in self.nodes}
        for src, types in self.connections.items():
            assert src in names, f"connection from unknown node {src!r}"
            for ctype, outs in types.items():
                for out in outs:
                    for c in out:
                        assert c["node"] in names, f"{src} -> unknown node {c['node']!r}"
        return self

    def export(self, settings):
        return {"name": self.name, "nodes": self.nodes, "connections": self.connections,
                "settings": settings, "staticData": None}


def airtable(g, name, operation, table, values, matching, x, y, with_id, fields, select_key=None, notes=None):
    params = {
        "resource": "record",
        "operation": operation,
        "base": {"__rl": True, "mode": "id", "value": PH["base"]},
        "table": {"__rl": True, "mode": "id", "value": table},
        "columns": {
            "mappingMode": "defineBelow",
            "value": values,
            "matchingColumns": matching,
            "schema": mapper_schema(fields, with_id, select_key),
            "attemptToConvertTypes": False,
            "convertFieldsToString": False,
        },
        "options": {"typecast": True},
    }
    extra = {"credentials": CRED["airtable"], "onError": "continueRegularOutput", "retryOnFail": False}
    if notes:
        extra["notes"] = notes
    return g.add(name, "n8n-nodes-base.airtable", 2.2, params, x, y, **extra)


def model_nodes(g: Graph, provider: str) -> list[str]:
    """The one place the LLM vendor lives. Returns [primary, fallback]: the chain node tries the fallback
    model inside the same call when the primary throws (503 "high demand" is the usual case), and only if
    both fail does the error reach the retry loop."""
    if provider == "gemini":
        # flash-lite first: during testing gemini-3.5-flash answered 503 "high demand" on most calls while
        # flash-lite answered in under a second. Swap the two model names if your quota behaves differently.
        primary = g.add("Gemini model", "@n8n/n8n-nodes-langchain.lmChatGoogleGemini", 1.1, {
            "modelName": "models/gemini-3.5-flash-lite",
            "options": {"temperature": 0, "maxOutputTokens": 4096},
        }, 2860, 460, credentials=CRED["gemini"],
            notes="Temperature 0 (spec §7). Model is a dropdown: swap here, nothing else changes.", notesInFlow=True)
        fallback = g.add("Gemini fallback model", "@n8n/n8n-nodes-langchain.lmChatGoogleGemini", 1.1, {
            "modelName": "models/gemini-3.5-flash",
            "options": {"temperature": 0, "maxOutputTokens": 4096},
        }, 2860, 640, credentials=CRED["gemini"],
            notes="Used only when the primary model call fails (e.g. 503).", notesInFlow=True)
        return [primary, fallback]
    if provider == "anthropic":
        primary = g.add("Anthropic model", "@n8n/n8n-nodes-langchain.lmChatAnthropic", 1.6, {
            "model": {"__rl": True, "mode": "id", "value": "claude-opus-5"},
            "options": {"maxTokensToSample": 4096, "thinkingMode": "adaptive", "effort": "low"},
        }, 2860, 460, credentials=CRED["anthropic"],
            notes="Claude 5 rejects sampling params: no temperature, adaptive thinking at low effort instead.",
            notesInFlow=True)
        fallback = g.add("Anthropic fallback model", "@n8n/n8n-nodes-langchain.lmChatAnthropic", 1.6, {
            "model": {"__rl": True, "mode": "id", "value": "claude-sonnet-5"},
            "options": {"maxTokensToSample": 4096, "thinkingMode": "adaptive", "effort": "low"},
        }, 2860, 640, credentials=CRED["anthropic"],
            notes="Used only when the primary model call fails.", notesInFlow=True)
        return [primary, fallback]
    raise ValueError(f"unknown provider {provider!r}")


# ---------------------------------------------------------------- main workflow
def build_main(provider: str = "gemini") -> dict:
    g = Graph("Lead → CRM AI pipeline")

    # --- [1] triggers -------------------------------------------------------
    # Form Trigger >= 2.2 keeps the custom path under options.path (a top-level "path" is silently ignored).
    g.add("Form trigger", "n8n-nodes-base.formTrigger", 2.6, {
        "formTitle": "Contact us",
        "formDescription": "Demo form for the Lead → CRM AI pipeline. Every submission becomes a scored CRM row in about 30 seconds.",
        "formFields": {"values": [
            {"fieldLabel": "Your name", "fieldName": "name", "fieldType": "text", "placeholder": "Jane Doe"},
            {"fieldLabel": "Email", "fieldName": "email", "fieldType": "email", "placeholder": "jane@company.com"},
            {"fieldLabel": "Phone", "fieldName": "phone", "fieldType": "text", "placeholder": "+1 604 555 0100"},
            {"fieldLabel": "Company", "fieldName": "company", "fieldType": "text", "placeholder": "Company Inc."},
            {"fieldLabel": "How can we help?", "fieldName": "comment", "fieldType": "textarea",
             "placeholder": "Tell us what you need and by when"},
        ]},
        "responseMode": "onReceived",
        "options": {"path": "lead-form", "buttonLabel": "Send"},
    }, 0, 0, webhookId=uid("form-trigger"), notes="source_channel = form_site", notesInFlow=True)

    g.add("Gmail trigger", "n8n-nodes-base.gmailTrigger", 1.4, {
        "pollTimes": {"item": [{"mode": "everyMinute"}]},
        "simple": False,
        "filters": {"labelIds": ["INBOX"], "q": "-in:draft"},
        "options": {},
    }, 0, 220, credentials=CRED["gmail"],
        notes="source_channel = email_inbound. Filter -in:draft is mandatory: this workflow creates Gmail drafts, without the filter it would trigger itself forever.",
        notesInFlow=True)

    # n8n refuses to start a Form-Trigger workflow that contains a Respond to Webhook node, so both webhooks
    # answer in "last node" mode: the last executed node's first item is the response body.
    g.add("Webhook lead intake", "n8n-nodes-base.webhook", 2.1, {
        "httpMethod": "POST",
        "path": "lead-intake",
        "authentication": "headerAuth",
        "responseMode": "lastNode",
        "responseData": "firstEntryJson",
        "options": {},
    }, 0, 440, webhookId=uid("webhook-lead-intake"), credentials=CRED["webhook"],
        notes="POST JSON with source_channel + fields from the SOURCES map. Header x-webhook-secret. Answers when the run is done.",
        notesInFlow=True)

    g.add("Webhook health", "n8n-nodes-base.webhook", 2.1, {
        "httpMethod": "GET",
        "path": "lead-intake-health",
        "responseMode": "lastNode",
        "responseData": "firstEntryJson",
        "options": {},
    }, 0, 760, webhookId=uid("webhook-health"))
    g.add("Health", "n8n-nodes-base.code", 2, {
        "jsCode": "return [{ json: { status: 'ok', workflow: $workflow.name, time: new Date().toISOString() } }];",
    }, 280, 760)
    g.link("Webhook health", "Health")

    # --- [2] normalize + validate -------------------------------------------
    g.add("Normalize lead", "n8n-nodes-base.code", 2, {"jsCode": js("normalize_lead")}, 340, 220,
          notes="Canonical object + dedupe_key + lead_type. SOURCES map lives here.", notesInFlow=True)
    g.add("Validate input", "n8n-nodes-base.code", 2, {"jsCode": js("validate_input")}, 600, 220)
    g.add("Is valid", "n8n-nodes-base.if", 2.3, {
        "conditions": cond("={{ $json.validation_passed }}", "boolean", "true", seed="valid"),
        "looseTypeValidation": True, "options": {},
    }, 860, 220)
    for t in ("Form trigger", "Gmail trigger", "Webhook lead intake"):
        g.link(t, "Normalize lead")
    g.link("Normalize lead", "Validate input")
    g.link("Validate input", "Is valid")

    # --- [3] register lead (upsert) + lead state ----------------------------
    airtable(g, "Register lead", "upsert", PH["leads"], {
        "dedupe_key": "={{ $json.dedupe_key }}",
        "name": "={{ $json.name }}",
        "email": "={{ $json.email || null }}",
        "phone": "={{ $json.phone || null }}",
        "company": "={{ $json.company }}",
        "domain": "={{ $json.domain }}",
        "message": "={{ $json.message }}",
        "source_channel": "={{ $json.source_channel }}",
        "lead_type": "={{ $json.lead_type }}",
        "updated_at": "={{ $json.received_at }}",
    }, ["dedupe_key"], 1120, 220, with_id=True, fields=LEADS_FIELDS,
        notes="Create or Update by dedupe_key. Returns the stored row: previous touches / created_at come back for free.")
    g.add("Lead state", "n8n-nodes-base.code", 2, {"jsCode": js("lead_state")}, 1380, 220)
    g.link("Is valid", "Register lead", out=0)
    g.link("Register lead", "Lead state")

    # --- [4] switch by lead type --------------------------------------------
    g.add("Route by lead type", "n8n-nodes-base.switch", 3.4, {
        "rules": {"values": [
            {"conditions": cond("={{ $json.lead_type }}", "string", "equals", "thin", seed="lt"),
             "renameOutput": True, "outputKey": "thin"},
            {"conditions": cond("={{ $json.lead_type }}", "string", "equals", "thin_comment", seed="lt"),
             "renameOutput": True, "outputKey": "thin_comment"},
            {"conditions": cond("={{ $json.lead_type }}", "string", "equals", "rich", seed="lt"),
             "renameOutput": True, "outputKey": "rich"},
        ]},
        "looseTypeValidation": True,
        "options": {},
    }, 1640, 220, notes="thin → rule only · thin_comment → AI prompt A · rich → enrichment + AI prompt B",
        notesInFlow=True)
    g.link("Lead state", "Route by lead type")

    # --- [5a] thin: rule scoring --------------------------------------------
    g.add("Rule scoring", "n8n-nodes-base.code", 2, {"jsCode": js("rule_scoring")}, 1900, 0,
          notes="Spec §6. No LLM call.", notesInFlow=True)
    g.link("Route by lead type", "Rule scoring", out=0)

    # --- [5c] rich: enrichment with retry loop -------------------------------
    g.add("Fetch company site", "n8n-nodes-base.httpRequest", 4.5, {
        "method": "GET",
        "url": "={{ 'https://' + $json.domain }}",
        "sendHeaders": True,
        "specifyHeaders": "keypair",
        "headerParameters": {"parameters": [
            {"name": "User-Agent", "value": FETCH_UA},
            {"name": "Accept", "value": "text/html,application/xhtml+xml"},
        ]},
        "options": {
            "allowUnauthorizedCerts": True,
            "timeout": 10000,
            "redirect": {"redirect": {"followRedirects": True, "maxRedirects": 5}},
            "response": {"response": {"fullResponse": True, "neverError": True, "responseFormat": "text",
                                      "outputPropertyName": "body"}},
        },
    }, 1900, 460, onError="continueErrorOutput", retryOnFail=False,
        notes="Public home page only, no keys. Retries live in the loop below, not in Retry on Fail.", notesInFlow=True)
    g.add("Process fetch result", "n8n-nodes-base.code", 2, {"jsCode": js("process_fetch_result")}, 2160, 460)
    g.add("Retry fetch", "n8n-nodes-base.if", 2.3, {
        "conditions": cond("={{ $json.retry }}", "boolean", "true", seed="retry-fetch"),
        "looseTypeValidation": True, "options": {},
    }, 2420, 460)
    g.add("Wait fetch backoff", "n8n-nodes-base.wait", 1.1, {
        "resume": "timeInterval", "amount": "={{ $json.retry_delay_s }}", "unit": "seconds",
    }, 2420, 680, webhookId=uid("wait-fetch"))
    g.link("Route by lead type", "Fetch company site", out=2)
    g.link("Fetch company site", "Process fetch result", out=0)
    g.link("Fetch company site", "Process fetch result", out=1)
    g.link("Process fetch result", "Retry fetch")
    g.link("Retry fetch", "Wait fetch backoff", out=0)
    g.link("Wait fetch backoff", "Fetch company site")

    # --- [5b/5c] LLM chain with parser + retry loop -----------------------------
    g.add("Build prompt", "n8n-nodes-base.code", 2, {"jsCode": js("build_prompt")}, 2680, 220,
          notes="Prompt A (thin_comment) or B (rich). Strict suffix on retry. COMPANY block is the only thing to edit.",
          notesInFlow=True)
    g.add("AI qualify", "@n8n/n8n-nodes-langchain.chainLlm", 1.9, {
        "promptType": "define",
        "text": "={{ $json.user_prompt }}",
        "hasOutputParser": True,
        "needsFallback": True,
        "messages": {"messageValues": [
            {"type": "SystemMessagePromptTemplate", "message": "={{ $json.system_prompt }}"},
        ]},
    }, 2940, 220, onError="continueErrorOutput", retryOnFail=False,
        notes="One narrow judgment. Output = JSON contract (spec §3). Primary + fallback model.", notesInFlow=True)
    model, fallback_model = model_nodes(g, provider)
    g.add("Output schema", "@n8n/n8n-nodes-langchain.outputParserStructured", 1.3, {
        "schemaType": "manual",
        "inputSchema": json.dumps(CONTRACT_SCHEMA, indent=2),
        "autoFix": False,
    }, 3100, 460, notes="Layer 2. Auto-fix stays off on purpose (known bugs); layer 4 is the Code validator.",
        notesInFlow=True)
    g.link("Route by lead type", "Build prompt", out=1)
    g.link("Retry fetch", "Build prompt", out=1)
    g.link("Build prompt", "AI qualify")
    g.link(model, "AI qualify", ctype="ai_languageModel")
    g.link(fallback_model, "AI qualify", inp=1, ctype="ai_languageModel")
    g.link("Output schema", "AI qualify", ctype="ai_outputParser")

    g.add("Validate contract", "n8n-nodes-base.code", 2, {"jsCode": js("validate_contract")}, 3200, 120,
          notes="Layer 4, mandatory. LeadIQ-AI parser pattern.", notesInFlow=True)
    g.add("Classify AI error", "n8n-nodes-base.code", 2, {"jsCode": js("classify_ai_error")}, 3200, 340,
          notes="Retry 408/409/425/429/5xx with backoff + jitter. Never 400/401/403/404/422.", notesInFlow=True)
    g.add("Retry AI", "n8n-nodes-base.if", 2.3, {
        "conditions": cond("={{ $json.retry }}", "boolean", "true", seed="retry-ai"),
        "looseTypeValidation": True, "options": {},
    }, 3460, 220)
    g.add("Wait AI backoff", "n8n-nodes-base.wait", 1.1, {
        "resume": "timeInterval", "amount": "={{ $json.retry_delay_s }}", "unit": "seconds",
    }, 3460, 460, webhookId=uid("wait-ai"))
    g.add("Resolve contract", "n8n-nodes-base.code", 2, {"jsCode": js("resolve_contract")}, 3720, 220,
          notes="3rd failure → score 50, warm, needs_review.", notesInFlow=True)
    g.link("AI qualify", "Validate contract", out=0)
    g.link("AI qualify", "Classify AI error", out=1)
    g.link("Validate contract", "Retry AI")
    g.link("Classify AI error", "Retry AI")
    g.link("Retry AI", "Wait AI backoff", out=0)
    g.link("Wait AI backoff", "Build prompt")
    g.link("Retry AI", "Resolve contract", out=1)

    # --- [7] routing decision + save ------------------------------------------
    g.add("Prepare routing", "n8n-nodes-base.code", 2, {"jsCode": js("prepare_routing")}, 3980, 220,
          notes="Decides, never executes. Frozen ROUTES + reply templates.", notesInFlow=True)
    g.link("Rule scoring", "Prepare routing")
    g.link("Resolve contract", "Prepare routing")

    airtable(g, "Save qualification", "upsert", PH["leads"], {
        "dedupe_key": "={{ $json.airtable.dedupe_key }}",
        "lead_type": "={{ $json.airtable.lead_type }}",
        "score": "={{ $json.airtable.score }}",
        "heat": "={{ $json.airtable.heat }}",
        "category": "={{ $json.airtable.category }}",
        "reason": "={{ $json.airtable.reason }}",
        "draft": "={{ $json.airtable.draft }}",
        "status": "={{ $json.airtable.status }}",
        "touches": "={{ $json.airtable.touches }}",
        "created_at": "={{ $json.airtable.created_at }}",
        "updated_at": "={{ $json.airtable.updated_at }}",
        "needs_review": "={{ $json.airtable.needs_review }}",
        "enrichment": "={{ $json.airtable.enrichment }}",
        "run_id": "={{ $json.airtable.run_id }}",
    }, ["dedupe_key"], 4240, 220, with_id=True, fields=LEADS_FIELDS,
        notes="Second upsert by dedupe_key. Continue on Error, no retries: a retry here would mean duplicate writes.")
    g.link("Prepare routing", "Save qualification")

    # --- [8] side effects ------------------------------------------------------
    pr = "$('Prepare routing').first().json"
    g.add("Alert needed", "n8n-nodes-base.if", 2.3, {
        "conditions": cond(f"={{{{ {pr}.route.notify }}}}", "boolean", "true", seed="alert"),
        "looseTypeValidation": True, "options": {},
    }, 4500, 220)
    g.add("Telegram alert", "n8n-nodes-base.telegram", 1.2, {
        "resource": "message",
        "operation": "sendMessage",
        "chatId": PH["chat"],
        "text": f"={{{{ {pr}.telegram_text }}}}",
        "additionalFields": {"appendAttribution": False, "parse_mode": "HTML"},
    }, 4760, 80, credentials=CRED["telegram"], onError="continueRegularOutput", retryOnFail=False,
        notes="parse_mode HTML: the default Markdown breaks on underscores (ads_google).", notesInFlow=True)
    g.add("Route by heat", "n8n-nodes-base.switch", 3.4, {
        "rules": {"values": [
            {"conditions": cond(f"={{{{ {pr}.route.has_email }}}}", "boolean", "false", seed="heat"),
             "renameOutput": True, "outputKey": "no_email"},
            {"conditions": cond(f"={{{{ {pr}.contract.heat }}}}", "string", "equals", "hot", seed="heat"),
             "renameOutput": True, "outputKey": "hot"},
            {"conditions": cond(f"={{{{ {pr}.contract.heat }}}}", "string", "equals", "warm", seed="heat"),
             "renameOutput": True, "outputKey": "warm"},
            {"conditions": cond(f"={{{{ {pr}.contract.heat }}}}", "string", "equals", "cold", seed="heat"),
             "renameOutput": True, "outputKey": "cold"},
        ]},
        "looseTypeValidation": True,
        "options": {},
    }, 5020, 220, notes="Drafts only, nothing is sent. Phone-only leads skip Gmail.", notesInFlow=True)
    g.link("Save qualification", "Alert needed")
    g.link("Alert needed", "Telegram alert", out=0)
    g.link("Alert needed", "Route by heat", out=1)
    g.link("Telegram alert", "Route by heat")

    def gmail(name, y, note):
        return g.add(name, "n8n-nodes-base.gmail", 2.2, {
            "resource": "draft",
            "operation": "create",
            "subject": f"={{{{ {pr}.subject }}}}",
            "emailType": "text",
            "message": f"={{{{ {pr}.draft_text }}}}",
            "options": {
                "sendTo": f"={{{{ {pr}.gmail.to }}}}",
                "threadId": f"={{{{ {pr}.gmail.thread_id }}}}",
            },
        }, 5280, y, credentials=CRED["gmail"], onError="continueRegularOutput", retryOnFail=False,
            notes=note, notesInFlow=True)

    gmail("Gmail draft hot", 40, "Personal draft, never sent. Lands in the thread for email leads.")
    gmail("Gmail draft warm", 220, "Personal draft or nurture template.")
    gmail("Gmail draft cold", 400, "Template reply as a draft (switch to Send for a real auto-reply).")

    # --- [9] log run, respond ---------------------------------------------------
    g.add("Build run row", "n8n-nodes-base.code", 2, {"jsCode": js("build_run_row")}, 5540, 220,
          notes="Runs row on every path + HTTP response for webhook leads.", notesInFlow=True)
    g.link("Route by heat", "Build run row", out=0)
    g.link("Route by heat", "Gmail draft hot", out=1)
    g.link("Route by heat", "Gmail draft warm", out=2)
    g.link("Route by heat", "Gmail draft cold", out=3)
    for n in ("Gmail draft hot", "Gmail draft warm", "Gmail draft cold"):
        g.link(n, "Build run row")
    g.link("Is valid", "Build run row", out=1)

    airtable(g, "Log run", "create", PH["runs"], {
        "run_id": "={{ $json.run_id }}",
        "timestamp": "={{ $json.timestamp }}",
        "trigger_source": "={{ $json.trigger_source }}",
        "lead_ref": "={{ $json.lead_ref }}",
        "status": "={{ $json.status }}",
        "failed_node": "={{ $json.failed_node }}",
        "error_message": "={{ $json.error_message }}",
        "duration_ms": "={{ $json.duration_ms }}",
    }, [], 5800, 220, with_id=False, fields=RUNS_FIELDS, select_key=lambda n: "run_status",
        notes="Always written: ok / retry / error.")
    g.link("Build run row", "Log run")

    g.add("Webhook response", "n8n-nodes-base.code", 2, {
        "jsCode": "// Last node on purpose: the intake webhook answers with this item (\"last node\" response mode).\n"
                  "return [{ json: $('Build run row').first().json.response_body }];",
    }, 6060, 220, notes="Body for webhook callers: the qualification, or ok:false + errors. Unused for form / Gmail runs.",
        notesInFlow=True)
    g.link("Log run", "Webhook response")

    # --- the one sticky note the spec asks for, above the SOURCES map -----------------
    g.sticky("### Add your source here → map to canonical fields\nOne entry in `SOURCES` inside **Normalize lead** "
             "and your trigger wired into it. Missing fields are fine: the lead becomes `thin` and goes through the rule.",
             280, 20, 300, 160, color=4)

    settings = {
        "executionOrder": "v1",
        "errorWorkflow": PH["error_wf"],
        "saveDataErrorExecution": "all",
        "saveDataSuccessExecution": "all",
        "saveManualExecutions": True,
        "saveExecutionProgress": True,
        "executionTimeout": 180,
        "timezone": "America/Vancouver",
    }
    return g.validate().export(settings)


# ---------------------------------------------------------------- error workflow
def build_error() -> dict:
    g = Graph("Lead → CRM · Error workflow")
    g.add("Error trigger", "n8n-nodes-base.errorTrigger", 1, {}, 0, 0)
    g.add("Build error row", "n8n-nodes-base.code", 2, {"jsCode": js("build_error_row")}, 260, 0)
    airtable(g, "Log failed run", "create", PH["runs"], {
        "run_id": "={{ $json.run_id }}",
        "timestamp": "={{ $json.timestamp }}",
        "trigger_source": "={{ $json.trigger_source }}",
        "lead_ref": "={{ $json.lead_ref }}",
        "status": "={{ $json.status }}",
        "failed_node": "={{ $json.failed_node }}",
        "error_message": "={{ $json.error_message }}",
        "duration_ms": "={{ $json.duration_ms }}",
    }, [], 520, 0, with_id=False, fields=RUNS_FIELDS, select_key=lambda n: "run_status")
    g.add("Telegram error alert", "n8n-nodes-base.telegram", 1.2, {
        "resource": "message",
        "operation": "sendMessage",
        "chatId": PH["chat"],
        "text": "={{ $('Build error row').first().json.telegram_text }}",
        "additionalFields": {"appendAttribution": False, "parse_mode": "HTML"},
    }, 780, 0, credentials=CRED["telegram"], onError="continueRegularOutput", retryOnFail=False)
    g.link("Error trigger", "Build error row")
    g.link("Build error row", "Log failed run")
    g.link("Log failed run", "Telegram error alert")
    settings = {"executionOrder": "v1", "saveDataErrorExecution": "all", "saveDataSuccessExecution": "all"}
    return g.validate().export(settings)


def write(path: pathlib.Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    real = [n for n in data["nodes"] if n["type"] != "n8n-nodes-base.stickyNote"]
    print(f"{path.name}: {len(real)} nodes + {len(data['nodes']) - len(real)} sticky notes, "
          f"{sum(len(o) for t in data['connections'].values() for outs in t.values() for o in outs)} connections")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["gemini", "anthropic"], default="gemini")
    args = ap.parse_args()
    write(ROOT / "workflow.json", build_main(args.provider))
    write(ROOT / "error-workflow.json", build_error())
