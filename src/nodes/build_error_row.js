// Error Workflow: one Runs row + one alert per crashed execution. Input = n8n Error Trigger output.
const e = $input.first().json;
const ex = e.execution || {};
const wf = e.workflow || {};
const err = ex.error || (e.trigger && e.trigger.error) || {};

const row = {
  run_id: ex.id ? `${DateTime.utc().toFormat('yyyyLLdd')}-${ex.id}` : 'n/a',   // see normalize_lead
  timestamp: DateTime.utc().toISO(),
  trigger_source: `error_workflow:${wf.name || 'unknown workflow'}`,
  lead_ref: '',
  status: 'error',
  failed_node: ex.lastNodeExecuted || 'unknown',
  error_message: `${err.message || 'unknown error'}${ex.url ? `\n${ex.url}` : ''}`.slice(0, 2000),
  duration_ms: null,
};

const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const telegram_text = `🛑 <b>Workflow crashed</b>: ${esc(wf.name || 'unknown')}\nNode: ${esc(row.failed_node)}\n${esc(String(err.message || '').slice(0, 300))}${ex.url ? `\n${esc(ex.url)}` : ''}`;

return [{ json: { ...row, telegram_text } }];
