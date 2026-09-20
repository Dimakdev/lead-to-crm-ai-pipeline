#!/usr/bin/env python3
"""Drive the five test cases from spec §9 through the webhook and print what happened.

For every case: POST sample-payloads/NN-*.json -> wait for the response (the workflow answers when it is done)
-> read the execution from the n8n API -> read the Leads and Runs rows from Airtable -> one table row.

Case 5 swaps the Anthropic credential of the model node for a broken key, runs, then restores it.

Usage:  python scripts/run_tests.py [--only 2] [--reset] [--keep-broken]
  --reset   delete the test leads from Airtable first, so touches counts start from 1
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from common import ROOT, Airtable, HttpError, N8n, http, load_env, load_state, require

PAYLOADS = sorted((ROOT / "sample-payloads").glob("0*.json"))
BROKEN_CRED_NAME = "BROKEN KEY (test 5, safe to delete)"
MODEL_NODES = {  # node type -> (credential type, state key, restored credential name)
    "@n8n/n8n-nodes-langchain.lmChatGoogleGemini": ("googlePalmApi", "gemini", "Google Gemini (Lead to CRM)"),
    "@n8n/n8n-nodes-langchain.lmChatAnthropic": ("anthropicApi", "anthropic", "Anthropic (Lead to CRM)"),
}


def send(env: dict, state: dict, payload: dict, timeout: int = 240):
    url = f"{env.get('N8N_BASE_URL', 'http://localhost:5678').rstrip('/')}/webhook/lead-intake"
    headers = {"x-webhook-secret": env.get("WEBHOOK_SECRET") or state.get("webhook_secret", "")}
    t0 = time.time()
    try:
        status, body = http("POST", url, headers, payload, timeout=timeout)
    except HttpError as e:
        status, body = e.status, e.body
    return status, body, round(time.time() - t0, 1)


FORM_CASE = {  # case 6: the demo form, submitted the way the form page does it (multipart, field-0..field-4)
    "field-0": "Maria Form Test",
    "field-1": "maria.formtest.demo@gmail.com",
    "field-2": "+1 604 555 0177",
    "field-3": "",
    "field-4": "Do you offer a monthly plan? We need help automating our booking confirmations, ideally starting next month.",
}


def submit_form(env: dict):
    import urllib.request
    boundary = "----lead-to-crm-test"
    parts = []
    for k, v in FORM_CASE.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n")
    body = ("".join(parts) + f"--{boundary}--\r\n").encode("utf-8")
    url = f"{env.get('N8N_BASE_URL', 'http://localhost:5678').rstrip('/')}/form/lead-form"
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.read().decode("utf-8", "replace")[:200], round(time.time() - t0, 1)


def wait_for_execution(n8n: N8n, wf_id: str, trigger_node: str, after_ts: float, max_wait: int = 420):
    """Wait for a finished execution of wf_id started after after_ts whose first node is trigger_node."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        for ex in n8n.executions(wf_id, limit=5, include_data=True):
            started = _iso_to_ts(ex.get("startedAt", ""))
            run_data = ((ex.get("data") or {}).get("resultData") or {}).get("runData") or {}
            if started >= after_ts - 5 and trigger_node in run_data and ex.get("status") not in ("running", "waiting", "new"):
                return ex
        time.sleep(4)
    return None


def run_rows_for(at, ex_id):
    """Runs rows for one execution, newest first.

    run_id is `<utc date>-<execution id>`, written by the workflow rather than by this script, so
    matching on the ending is what stays true when the clock has just ticked past midnight between
    the run and this lookup. Rows written before that format existed do not match, which is honest:
    they belong to a different n8n whose execution numbering started over.
    """
    if not ex_id:
        return []
    suffix = f"-{ex_id}"
    rows = at.records("Runs", f"RIGHT({{run_id}}, {len(suffix)}) = '{suffix}'")
    return sorted(rows, key=lambda r: r["fields"].get("timestamp", ""), reverse=True)


def node_summary(execution: dict) -> dict:
    run_data = (((execution.get("data") or {}).get("resultData") or {}).get("runData") or {})
    out = {}
    for name, runs in run_data.items():
        last = runs[-1] if runs else {}
        items = ((last.get("data") or {}).get("main") or [[]])
        first = None
        for branch in items:
            if branch:
                first = branch[0].get("json")
                break
        err = last.get("error", {}).get("message") if last.get("error") else None
        if first and isinstance(first, dict) and first.get("error"):
            err = first["error"] if isinstance(first["error"], str) else first["error"].get("message", str(first["error"]))
        out[name] = {"runs": len(runs), "error": err, "first": first}
    return out


def latest_execution(n8n: N8n, wf_id: str, after_ts: float, tries: int = 20):
    for _ in range(tries):
        for ex in n8n.executions(wf_id, limit=3, include_data=True):
            started = ex.get("startedAt", "")
            if started and _iso_to_ts(started) >= after_ts - 5 and ex.get("status") not in ("running", "waiting", "new"):
                return ex
        time.sleep(3)
    return None


def _iso_to_ts(s: str) -> float:
    from datetime import datetime, timezone
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def model_node_info(n8n: N8n, wf_id: str):
    """Which chat-model sub-node is wired in and which credential type it uses."""
    wf = n8n.get_workflow(wf_id)
    for node in wf["nodes"]:
        if node["type"] in MODEL_NODES:
            return wf, node["type"], MODEL_NODES[node["type"]]
    sys.exit("No chat model node found in the deployed workflow.")


def swap_model_credential(n8n: N8n, wf_id: str, cred_id: str, cred_name: str) -> None:
    wf, node_type, (cred_type, _, _) = model_node_info(n8n, wf_id)
    for node in wf["nodes"]:
        if node["type"] == node_type:
            node["credentials"] = {cred_type: {"id": cred_id, "name": cred_name}}
    n8n.update_workflow(wf_id, wf)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", type=int, nargs="+", help="run only these cases, e.g. --only 2 3 (6 = demo form, 7 = wait for a Gmail-triggered run)")
    ap.add_argument("--reset", action="store_true", help="delete test leads from Airtable before running")
    ap.add_argument("--keep-broken", action="store_true", help="do not delete the broken credential after case 5")
    args = ap.parse_args()

    env = load_env()
    state = load_state()
    require(env, "N8N_API_KEY", "AIRTABLE_PAT", "AIRTABLE_BASE_ID")
    wf_id = (state.get("workflows") or {}).get("main")
    if not wf_id:
        sys.exit("No deployed workflow in .deploy-state.json. Run scripts/deploy.py first.")
    n8n = N8n(env.get("N8N_BASE_URL", "http://localhost:5678"), env["N8N_API_KEY"])
    at = Airtable(env["AIRTABLE_PAT"], env["AIRTABLE_BASE_ID"])
    deployed_wf, _, (model_cred_type, model_state_key, model_cred_name) = model_node_info(n8n, wf_id)
    model_cred = (state.get("credentials") or {}).get(model_state_key)
    disabled_nodes = {n["name"] for n in deployed_wf["nodes"] if n.get("disabled")}

    cases = [(i + 1, p) for i, p in enumerate(PAYLOADS)] + [(6, None), (7, None)]
    if args.only:
        cases = [c for c in cases if c[0] in args.only]
    else:
        cases = [c for c in cases if c[0] != 7]  # the Gmail case needs a human to send an email: opt in with --only 7

    if args.reset:
        keys = []
        for num, p in cases:
            if p is None:
                if num == 6:
                    keys.append(FORM_CASE["field-1"].lower())
                continue
            body = json.loads(p.read_text(encoding="utf-8"))
            email = (body.get("email") or body.get("from") or "").strip().lower()
            phone = "".join(ch for ch in (body.get("tel") or body.get("phone") or "") if ch.isdigit())
            if email:
                keys.append(email)
            if phone:
                keys.append("+" + (phone if len(phone) != 10 else "1" + phone))
        if keys:
            formula = "OR(" + ",".join(f"{{dedupe_key}}='{k}'" for k in keys) + ")"
            recs = at.records("Leads", formula)
            if recs:
                at.delete("Leads", [r["id"] for r in recs])
            print(f"reset: deleted {len(recs)} test lead(s)")

    rows = []
    for num, path in cases:
        if num in (6, 7):
            t0 = time.time()
            if num == 6:
                print(f"\n== case 6: demo form POST /form/lead-form")
                status, text, secs = submit_form(env)
                print(f"   HTTP {status} in {secs}s: {text}")
                ex = wait_for_execution(n8n, wf_id, "Form trigger", t0)
                dedupe = FORM_CASE["field-1"].lower()
            else:
                print("\n== case 7: waiting up to 7 minutes for an execution started by the Gmail trigger.")
                print("   Send an email to the connected Gmail inbox now (the trigger polls every minute).")
                status, secs = "n/a", None
                ex = wait_for_execution(n8n, wf_id, "Gmail trigger", t0)
                dedupe = None
                if ex:
                    norm = ((ex["data"]["resultData"].get("runData") or {}).get("Normalize lead") or [{}])[-1]
                    items = (norm.get("data") or {}).get("main") or [[]]
                    dedupe = items[0][0]["json"].get("dedupe_key") if items and items[0] else None
            nodes = node_summary(ex) if ex else {}
            ex_id = ex.get("id") if ex else None
            lead_rows = at.records("Leads", f"{{dedupe_key}}='{dedupe}'") if dedupe else []
            run_rows = run_rows_for(at, ex_id)
            lead = lead_rows[0]["fields"] if lead_rows else {}
            run = run_rows[0]["fields"] if run_rows else {"status": "NO ROW"}
            rows.append({
                "case": "6 form" if num == 6 else "7 gmail", "http": status,
                "exec": f"{ex_id} {ex.get('status') if ex else 'not found'}",
                "lead_type": lead.get("lead_type"), "score": lead.get("score"), "heat": lead.get("heat"),
                "category": lead.get("category"), "status": lead.get("status"), "touches": lead.get("touches"),
                "review": lead.get("needs_review", False),
                "ai": f"{nodes['AI qualify']['runs']}x" if nodes.get("AI qualify") else "no call",
                "runs": f"{run.get('status', '?')} {run.get('failed_node', '') or ''}".strip(),
                "telegram": "ok" if nodes.get("Telegram alert") and not nodes["Telegram alert"]["error"] and "Telegram alert" not in disabled_nodes else ("off (no credential)" if "Telegram alert" in disabled_nodes else ("—" if not nodes.get("Telegram alert") else f"ERR {nodes['Telegram alert']['error'][:60]}")),
                "gmail": next((("off (no credential)" if n in disabled_nodes else ("ok" if not nodes[n]["error"] else f"ERR {nodes[n]['error'][:60]}")) for n in ("Gmail draft hot", "Gmail draft warm", "Gmail draft cold") if nodes.get(n)), "—"),
            })
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        broken_id = None
        if num == 5:
            if not model_cred:
                print(f"case 5 needs the '{model_state_key}' credential id in .deploy-state.json, skipping")
                continue
            broken_data = {"apiKey": "BROKEN-KEY-for-test-5"}
            if model_cred_type == "googlePalmApi":
                broken_data["host"] = "https://generativelanguage.googleapis.com"
            broken = n8n.create_credential(BROKEN_CRED_NAME, model_cred_type, broken_data)
            broken_id = broken["id"]
            swap_model_credential(n8n, wf_id, broken_id, BROKEN_CRED_NAME)
            print("case 5: model node switched to the broken key")

        print(f"\n== case {num}: {path.name}")
        t0 = time.time()
        status, body, secs = send(env, state, payload)
        print(f"   HTTP {status} in {secs}s: {json.dumps(body, ensure_ascii=False)[:300]}")

        ex = latest_execution(n8n, wf_id, t0)
        nodes = node_summary(ex) if ex else {}
        ex_status = ex.get("status") if ex else "not found"
        ex_id = ex.get("id") if ex else None

        if num == 5:
            swap_model_credential(n8n, wf_id, model_cred, model_cred_name)
            print("case 5: credential restored")
            if broken_id and not args.keep_broken:
                try:
                    n8n.delete_credential(broken_id)
                except HttpError:
                    print(f"case 5: this n8n version does not allow deleting credentials through the API, "
                          f"remove '{BROKEN_CRED_NAME}' in Credentials by hand (it is harmless)")

        resp = body if isinstance(body, dict) else {}
        dedupe = resp.get("lead")
        lead_rows, run_rows = [], []
        for _ in range(4):  # Airtable search can lag a second or two behind a fresh write
            lead_rows = at.records("Leads", f"{{dedupe_key}}='{dedupe}'") if dedupe else []
            run_rows = run_rows_for(at, ex_id)
            if run_rows and (lead_rows or not dedupe):
                break
            time.sleep(2)
        lead = lead_rows[0]["fields"] if lead_rows else {}
        run = run_rows[0]["fields"] if run_rows else {"status": "NO ROW"}

        def side(name):
            n = nodes.get(name)
            if not n:
                return "—"
            if name in disabled_nodes:
                return "off (no credential)"
            if n["error"]:
                return f"ERR {n['error'][:60]}"
            return "ok"

        rows.append({
            "case": num,
            "http": status,
            "exec": f"{ex_id} {ex_status}",
            "lead_type": lead.get("lead_type", resp.get("lead_type")),
            "score": lead.get("score", resp.get("score")),
            "heat": lead.get("heat", resp.get("heat")),
            "category": lead.get("category"),
            "status": lead.get("status"),
            "touches": lead.get("touches"),
            "review": lead.get("needs_review", False),
            "ai": f"{nodes['AI qualify']['runs']}x" if nodes.get("AI qualify") else "no call",
            "runs": f"{run.get('status', '?')} {run.get('failed_node', '') or ''}".strip(),
            "telegram": side("Telegram alert"),
            "gmail": next((side(n) for n in ("Gmail draft hot", "Gmail draft warm", "Gmail draft cold") if nodes.get(n)), "—"),
        })

    cols = ["case", "http", "exec", "lead_type", "score", "heat", "category", "status", "touches", "review",
            "ai", "runs", "telegram", "gmail"]
    print("\n| " + " | ".join(cols) + " |")
    print("|" + "|".join("---" for _ in cols) + "|")
    for r in rows:
        print("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")


if __name__ == "__main__":
    main()
