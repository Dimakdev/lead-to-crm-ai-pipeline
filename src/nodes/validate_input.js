// [2b] Validate input contract (spec §2): source_channel, received_at, and at least one of email / phone.
// Invalid input never crashes the execution: it becomes a structured 400 (pattern from LeadIQ-AI
// "Validate — Required Fields", MIT) and a row in Runs.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const KNOWN_CHANNELS = ['form_site', 'referral', 'ads_meta', 'ads_google', 'email_inbound', 'phone'];

const lead = $input.first().json;
const errors = [];
const warnings = [];

if (!lead.source_channel || lead.source_channel === 'unknown') {
  errors.push(`'source_channel' is required. Known values: ${KNOWN_CHANNELS.join(', ')} (or your own key from SOURCES).`);
} else if (!lead.raw.channel_known) {
  warnings.push(`Unknown source_channel "${lead.source_channel}": scored with the default weight ${lead.channel_weight}.`);
}
if (!lead.received_at) errors.push("'received_at' is missing or not an ISO date-time.");
if (!lead.email && !lead.phone) errors.push("At least one of 'email' / 'phone' is required.");
if (lead.email && !EMAIL_RE.test(lead.email)) errors.push(`'email' value "${lead.email}" is not a valid address.`);
if (lead.phone && lead.phone.replace(/\D/g, '').length < 7) errors.push(`'phone' value "${lead.phone}" is too short.`);

return [{
  json: {
    ...lead,
    validation_passed: errors.length === 0,
    errors,
    warnings,
  },
}];
