// [9] Build the Runs row. Reached on every path: valid, invalid input, AI fallback, side-effect errors.
// Also prepares the HTTP response for leads that came in through the webhook.
const SIDE_EFFECT_NODES = ['Register lead', 'Save qualification', 'Telegram alert', 'Gmail draft hot', 'Gmail draft warm', 'Gmail draft cold'];

const norm = $('Normalize lead').first().json;
const val = $('Validate input').first().json;

const ran = (name) => { try { return $(name).isExecuted === true; } catch (e) { return false; } };
const out = (name) => { try { return $(name).first().json || {}; } catch (e) { return {}; } };
const errorOf = (name) => {
  if (!ran(name)) return null;
  const o = out(name);
  return o && o.error ? String(o.error.message || o.error) : null;
};

const routing = ran('Prepare routing') ? out('Prepare routing') : null;
const c = routing ? routing.contract : null;
const problems = [];

if (!val.validation_passed) problems.push({ node: 'Validate input', msg: (val.errors || []).join(' ') });
if (routing && routing.contract_source === 'fallback') problems.push({ node: 'AI qualify', msg: routing.fallback_reason });
if (routing && routing.enrichment_status === 'failed') problems.push({ node: 'Fetch company site', msg: routing.enrichment_error || 'enrichment failed' });
for (const name of SIDE_EFFECT_NODES) {
  const e = errorOf(name);
  if (e) problems.push({ node: name, msg: e });
}

const retried = !!routing && (Number(routing.attempt) > 0 || Number(routing.enrich_attempts) > 1);
const status = problems.length ? 'error' : retried ? 'retry' : 'ok';

const row = {
  run_id: norm.run_id,
  timestamp: DateTime.utc().toISO(),
  trigger_source: `${norm.trigger}:${norm.source_channel}`,
  lead_ref: norm.dedupe_key || '(none)',
  status,
  failed_node: problems.map((p) => p.node).join(', '),
  error_message: problems.map((p) => `${p.node}: ${p.msg}`).join('\n').slice(0, 2000),
  duration_ms: Date.now() - Number(norm.run_started_at || Date.now()),
};

const response_body = val.validation_passed
  ? {
      ok: true,
      run_id: row.run_id,
      lead: norm.dedupe_key,
      lead_type: norm.lead_type,
      score: c ? c.score : null,
      heat: c ? c.heat : null,
      category: c ? c.category : null,
      status: routing && routing.route ? routing.route.status : null,
      needs_review: c ? c.needs_review : null,
      run_status: status,
    }
  : { ok: false, code: 400, message: 'Validation failed.', errors: val.errors || [] };

return [{
  json: {
    ...row,
    from_webhook: norm.trigger === 'webhook',
    http_status: val.validation_passed ? 200 : 400,
    response_body,
  },
}];
