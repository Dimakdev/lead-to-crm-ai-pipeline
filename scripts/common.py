"""Shared helpers for deploy.py and run_tests.py: .env loading, n8n API, Airtable API, state file.
Standard library only (urllib), Python 3.10+.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / ".deploy-state.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_env(path: pathlib.Path = ROOT / ".env") -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in os.environ.items():
        env.setdefault(k, v)
    return env


def require(env: dict, *keys: str) -> None:
    missing = [k for k in keys if not env.get(k)]
    if missing:
        sys.exit(f"Missing in .env: {', '.join(missing)}")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


class HttpError(Exception):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} for {url}: {body[:800]}")
        self.status = status
        self.body = body


def http(method: str, url: str, headers: dict | None = None, body=None, timeout: int = 60):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        raise HttpError(e.code, raw, url) from None


class N8n:
    def __init__(self, base_url: str, api_key: str):
        self.base = base_url.rstrip("/")
        self.headers = {"X-N8N-API-KEY": api_key, "Accept": "application/json"}

    def call(self, method: str, path: str, body=None, timeout: int = 60):
        return http(method, f"{self.base}/api/v1{path}", self.headers, body, timeout)[1]

    # workflows
    def list_workflows(self):
        return self.call("GET", "/workflows?limit=250").get("data", [])

    def get_workflow(self, wf_id: str):
        return self.call("GET", f"/workflows/{wf_id}")

    def create_workflow(self, wf: dict):
        return self.call("POST", "/workflows", body=api_body(wf))

    def update_workflow(self, wf_id: str, wf: dict):
        return self.call("PUT", f"/workflows/{wf_id}", body=api_body(wf))

    def activate(self, wf_id: str):
        return self.call("POST", f"/workflows/{wf_id}/activate")

    def deactivate(self, wf_id: str):
        return self.call("POST", f"/workflows/{wf_id}/deactivate")

    # credentials
    def credential_schema(self, cred_type: str):
        return self.call("GET", f"/credentials/schema/{cred_type}")

    def create_credential(self, name: str, cred_type: str, data: dict):
        return self.call("POST", "/credentials", body={"name": name, "type": cred_type, "data": data})

    def delete_credential(self, cred_id: str):
        return self.call("DELETE", f"/credentials/{cred_id}")

    # executions
    def executions(self, wf_id: str, limit: int = 5, include_data: bool = True):
        q = f"?workflowId={wf_id}&limit={limit}&includeData={'true' if include_data else 'false'}"
        return self.call("GET", f"/executions{q}").get("data", [])


def api_body(wf: dict) -> dict:
    """The public API rejects unknown top-level keys: keep only what it accepts."""
    allowed_settings = {"saveExecutionProgress", "saveManualExecutions", "saveDataErrorExecution",
                        "saveDataSuccessExecution", "executionTimeout", "errorWorkflow", "timezone",
                        "executionOrder"}
    return {
        "name": wf["name"],
        "nodes": wf["nodes"],
        "connections": wf["connections"],
        "settings": {k: v for k, v in wf.get("settings", {}).items() if k in allowed_settings},
        "staticData": wf.get("staticData"),
    }


class Airtable:
    META = "https://api.airtable.com/v0/meta/bases"
    DATA = "https://api.airtable.com/v0"

    def __init__(self, pat: str, base_id: str):
        self.base_id = base_id
        self.headers = {"Authorization": f"Bearer {pat}"}

    def tables(self) -> list[dict]:
        return http("GET", f"{self.META}/{self.base_id}/tables", self.headers)[1]["tables"]

    def create_table(self, spec: dict) -> dict:
        return http("POST", f"{self.META}/{self.base_id}/tables", self.headers, spec)[1]

    def records(self, table: str, formula: str | None = None, max_records: int = 50) -> list[dict]:
        q = {"maxRecords": str(max_records)}
        if formula:
            q["filterByFormula"] = formula
        url = f"{self.DATA}/{self.base_id}/{urllib.parse.quote(table)}?{urllib.parse.urlencode(q)}"
        return http("GET", url, self.headers)[1].get("records", [])

    def delete(self, table: str, record_ids: list[str]) -> None:
        for i in range(0, len(record_ids), 10):
            chunk = record_ids[i:i + 10]
            qs = "&".join(f"records[]={urllib.parse.quote(r)}" for r in chunk)
            http("DELETE", f"{self.DATA}/{self.base_id}/{urllib.parse.quote(table)}?{qs}", self.headers)
