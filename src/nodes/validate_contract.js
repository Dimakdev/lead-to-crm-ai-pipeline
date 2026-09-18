// [6] Own contract validator, layer 4 of the output defence (spec §7). Mandatory, not a fallback.
// Pattern adapted from LeadIQ-AI "Node 06 - Parse JSON Response" (MIT): direct JSON.parse -> regex
// extraction on failure -> required fields -> enum checks -> score is authoritative for the tier.
const MAX_ATTEMPTS = 3;
const KEYS = ['score', 'heat', 'category', 'reason', 'draft', 'needs_review'];
const HEATS = ['hot', 'warm', 'cold'];
const CATEGORIES = ['purchase_intent', 'question', 'support', 'other'];

const st = $('Build prompt').item.json;
const incoming = $input.first().json;

function parse(raw) {
  if (raw && typeof raw === 'object') return raw;
  let text = String(raw ?? '').trim()
    .replace(/^```(?:json)?\s*/i, '')
    .replace(/\s*```$/, '')
    .trim();
  try { return JSON.parse(text); } catch (e) { /* fall through */ }
  const m = text.match(/\{[\s\S]*\}/);
  if (m) { try { return JSON.parse(m[0]); } catch (e) { /* fall through */ } }
  return null;
}

const parsed = parse(incoming.output ?? incoming.text ?? incoming);
const errors = [];
let extra = [];
let contract = null;

if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
  errors.push('Model output is not a JSON object.');
} else {
  for (const k of KEYS) if (!(k in parsed)) errors.push(`Missing field "${k}".`);
  extra = Object.keys(parsed).filter((k) => !KEYS.includes(k));
  const score = Number(parsed.score);
  if (!Number.isInteger(score) || score < 0 || score > 100) errors.push(`score must be an integer 0-100, got ${JSON.stringify(parsed.score)}.`);
  if (!HEATS.includes(parsed.heat)) errors.push(`heat must be hot|warm|cold, got ${JSON.stringify(parsed.heat)}.`);
  if (!CATEGORIES.includes(parsed.category)) errors.push(`category must be one of ${CATEGORIES.join('|')}, got ${JSON.stringify(parsed.category)}.`);
  if (typeof parsed.reason !== 'string' || parsed.reason.trim().length < 5) errors.push('reason must be a non-empty sentence.');
  if (typeof parsed.draft !== 'string') errors.push('draft must be a string (empty string allowed).');
  if (typeof parsed.needs_review !== 'boolean') errors.push('needs_review must be a boolean.');

  if (errors.length === 0) {
    const heatFromScore = score >= 70 ? 'hot' : score >= 40 ? 'warm' : 'cold';
    contract = {
      score,
      heat: heatFromScore,                                   // score is authoritative, tier is recomputed
      category: parsed.category,
      reason: parsed.reason.trim().slice(0, 200),
      draft: st.prompt_kind === 'A' ? '' : String(parsed.draft).trim(),   // thin_comment never gets a personal draft
      needs_review: false,                                   // the system sets this flag, not the model
    };
  }
}

if (contract) {
  return [{
    json: {
      ...st,
      contract,
      contract_ok: true,
      retry: false,
      retry_delay_s: 0,
      errors: [],
      llm_raw: parsed,
      tier_reconciled: parsed.heat !== contract.heat,
      stripped_fields: extra,
    },
  }];
}

const attempt = Number(st.attempt || 0) + 1;
return [{
  json: {
    ...st,
    attempt,
    contract: null,
    contract_ok: false,
    errors,
    retry: attempt < MAX_ATTEMPTS,
    retry_delay_s: 0,
    llm_raw: incoming,
  },
}];
