// [3b] Lead state after "Register lead" (Airtable upsert by dedupe_key).
// The upsert returns the stored record, so previous `touches`, `created_at` and `status`
// come back without a separate Search node. touches +1 happens here.
const lead = $('Validate input').first().json;
const rec = $input.first().json || {};

const airtableError = rec.error ? String(rec.error.message || rec.error) : null;
const fields = rec.fields || (rec.id ? rec : {});
const prevTouches = Number(fields.touches || 0);

return [{
  json: {
    ...lead,
    record_id: rec.id || null,
    airtable_error: airtableError,
    touches: prevTouches + 1,
    is_repeat: prevTouches > 0,
    created_at: fields.created_at || lead.received_at,
    prev_status: fields.status || null,
    attempt: 0,
  },
}];
