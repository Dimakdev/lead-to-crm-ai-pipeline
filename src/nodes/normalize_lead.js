// [2] Normalize — any source -> canonical lead object (input contract, spec §2).
//
// Add your source here -> map to canonical fields.
// One SOURCES entry per channel: `weight` feeds the rule scoring (spec §6),
// `map` translates incoming keys to canonical fields (canonical -> incoming).
// Wire your trigger into this node; nothing below the map changes.
const SOURCES = {
  form_site:     { weight: 45, map: { name: 'name', email: 'email', phone: 'phone', company: 'company', domain: 'website', message: 'comment' } },
  referral:      { weight: 60, map: { name: 'full_name', phone: 'tel', email: 'email', company: 'company', message: 'note' } },
  email_inbound: { weight: 45, map: { name: 'from_name', email: 'from', message: 'body' } },
  ads_meta:      { weight: 35, map: { name: 'full_name', email: 'email', phone: 'phone_number', message: 'message' } },
  ads_google:    { weight: 35, map: { name: 'name', email: 'email', phone: 'phone', message: 'message' } },
  phone:         { weight: 45, map: { name: 'caller_name', phone: 'caller_number', message: 'call_notes' } },
  // your_channel: { weight: 40, map: { name: '...', email: '...', phone: '...', company: '...', message: '...' } },
};

// Which channel a trigger node stands for. Wire a new trigger into this node and name it here;
// a trigger that is not listed falls back to `source_channel` inside its payload (that is how the webhook works).
const TRIGGER_CHANNEL = {
  'Form trigger': 'form_site',
  'Gmail trigger': 'email_inbound',
  'Webhook lead intake': null,          // channel comes from the JSON body
  // 'Facebook Lead Ads Trigger': 'ads_meta',
  // 'Telegram Trigger': 'telegram_bot',
};

const DEFAULT_WEIGHT = 25;          // unknown channel is treated as a cold contact
const MIN_COMMENT_CHARS = 8;        // shorter text counts as "no message" -> thin lead
const DEFAULT_COUNTRY_CODE = '+1';  // 10-digit numbers without a country code get this prefix
const FREE_MAIL = new Set([
  'gmail.com', 'googlemail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'live.com', 'msn.com',
  'icloud.com', 'me.com', 'proton.me', 'protonmail.com', 'aol.com', 'gmx.com', 'zoho.com',
  'ukr.net', 'i.ua', 'meta.ua', 'mail.ru', 'yandex.ru', 'yandex.com', 'example.com',
]);

// ---------------------------------------------------------------------------
const input = $input.first().json;
const trigger = $prevNode.name;

let channel = '';
let payload = {};
let meta = {};

if (trigger === 'Form trigger') {
  channel = 'form_site';
  payload = input;
  meta = { submitted_at: input.submittedAt || '', form_mode: input.formMode || '' };
} else if (trigger === 'Gmail trigger') {
  channel = 'email_inbound';
  const from = (input.from && input.from.value && input.from.value[0]) || {};
  payload = {
    from_name: from.name || '',
    from: from.address || (input.from && input.from.text) || '',
    body: input.text || stripHtml(input.html || '') || input.snippet || '',
  };
  meta = {
    gmail_id: input.id || '',
    thread_id: input.threadId || '',
    subject: input.subject || '',
    date: input.date || '',
  };
} else {
  // Webhook lead intake (partner form, ads connector, CRM export, ...) or any trigger you add:
  // the payload is the webhook body when there is one, otherwise the trigger's own output.
  payload = flatten((input.body && typeof input.body === 'object') ? input.body : input);
  channel = String(payload.source_channel || TRIGGER_CHANNEL[trigger] || '').trim();
  meta = { webhook_path: (input.params && input.params.path) || '' };
}

const src = SOURCES[channel];
const map = src ? src.map : {};

// map values may be dotted paths into nested payloads, e.g. 'from.first_name' or 'message.text'
function getPath(obj, pathStr) {
  return String(pathStr).split('.').reduce((acc, k) => (acc != null && typeof acc === 'object' ? acc[k] : undefined), obj);
}

function pick(field) {
  const key = map[field];
  let v = key != null ? getPath(payload, key) : undefined;
  if (v == null || v === '') v = payload[field]; // canonical name is always accepted
  return v == null ? '' : String(v);
}

// Common nested shapes become flat key/value pairs, so a SOURCES map can use plain names:
//   Facebook Lead Ads: { field_data: [{ name, values: [..] }] }
//   Tally / Fillout / Jotform-style: { data: { fields: [{ label|key, value }] } } or { fields: [...] }
function flatten(p) {
  const out = { ...p };
  const rows = Array.isArray(p.field_data) ? p.field_data
    : Array.isArray(p.fields) ? p.fields
    : (p.data && Array.isArray(p.data.fields)) ? p.data.fields
    : null;
  if (rows) {
    for (const r of rows) {
      if (!r || typeof r !== 'object') continue;
      const key = String(r.name || r.key || r.label || '').trim().toLowerCase().replace(/\s+/g, '_');
      const val = Array.isArray(r.values) ? r.values[0] : r.value;
      if (key && out[key] == null) out[key] = val == null ? '' : val;
    }
  }
  return out;
}

function normEmail(v) {
  return v.trim().toLowerCase().replace(/^mailto:/, '');
}

function normPhone(v) {
  const raw = v.trim();
  if (!raw) return '';
  const digits = raw.replace(/\D/g, '');
  if (!digits) return '';
  if (raw.startsWith('+')) return '+' + digits;
  if (digits.length === 10) return DEFAULT_COUNTRY_CODE + digits;
  if (digits.length === 11 && digits.startsWith('1')) return '+' + digits;
  return '+' + digits;
}

function normDomain(v) {
  return v.trim().toLowerCase()
    .replace(/^https?:\/\//, '')
    .replace(/^www\./, '')
    .split(/[/?#]/)[0]
    .replace(/[^a-z0-9.-]/g, '');
}

function stripHtml(html) {
  return String(html)
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function toIso(value) {
  if (!value) return null;
  const dt = DateTime.fromISO(String(value));
  if (dt.isValid) return dt.toUTC().toISO();
  const js = DateTime.fromJSDate(new Date(value));
  return js.isValid ? js.toUTC().toISO() : null;
}

const email = normEmail(pick('email'));
const phone = normPhone(pick('phone'));
const emailDomain = email.includes('@') ? email.split('@')[1] : '';
let domain = normDomain(pick('domain'));
if (!domain && emailDomain && !FREE_MAIL.has(emailDomain)) domain = emailDomain;

const company = pick('company').trim();
const message = pick('message').trim();
const name = pick('name').trim();

const lead_type = (company || domain) ? 'rich'
  : (message.length >= MIN_COMMENT_CHARS ? 'thin_comment' : 'thin');

const received_at = toIso(meta.submitted_at) || toIso(meta.date) || DateTime.utc().toISO();

// keep raw small: drop html, cap long text
const rawPayload = {};
for (const [k, v] of Object.entries(payload)) {
  if (k === 'html') continue;
  rawPayload[k] = typeof v === 'string' && v.length > 2000 ? v.slice(0, 2000) + '…' : v;
}

return [{
  json: {
    // canonical object (spec §2)
    source_channel: channel || 'unknown',
    received_at,
    name,
    email,
    phone,
    company,
    domain,
    message,
    raw: { trigger, channel_known: !!src, payload: rawPayload, ...meta },
    // derived
    lead_type,
    dedupe_key: email || phone || '',
    channel_weight: src ? src.weight : DEFAULT_WEIGHT,
    trigger: trigger === 'Webhook lead intake' ? 'webhook' : trigger === 'Form trigger' ? 'form' : 'gmail',
    // n8n numbers executions from 1 again whenever it is rebuilt from an empty database, so on an
    // Airtable base that already holds history two unrelated runs can both call themselves run 3.
    // The UTC date in front makes the id unique in practice and still readable: 20260920-41.
    run_id: `${DateTime.utc().toFormat('yyyyLLdd')}-${$execution.id}`,
    run_started_at: Date.now(),
  },
}];
