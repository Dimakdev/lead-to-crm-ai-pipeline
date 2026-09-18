// [5a] Rule scoring for `thin` leads (spec §6). Transparent, no LLM call.
// Channel weights live in SOURCES (Normalize lead). Bonuses and thresholds below are starting
// values, tune them per client (README -> "Make it yours").
const BONUS_BOTH_CONTACTS = 10;   // phone AND email present
const BONUS_REPEAT = 15;          // touches > 1
const BONUS_BUSINESS_HOURS = 5;   // received inside business hours
const HOT_MIN = 70;               // score >= 70  -> hot
const WARM_MIN = 40;              // 40..69       -> warm, below -> cold
const BUSINESS = { zone: 'America/Vancouver', weekdays: [1, 2, 3, 4, 5], from: 9, to: 18 };  // set your own IANA time zone

const s = $input.first().json;
const parts = [];

let score = Number(s.channel_weight || 25);
parts.push(`channel ${s.source_channel} ${score}`);

if (s.email && s.phone) { score += BONUS_BOTH_CONTACTS; parts.push(`email+phone +${BONUS_BOTH_CONTACTS}`); }
if (Number(s.touches) > 1) { score += BONUS_REPEAT; parts.push(`repeat contact +${BONUS_REPEAT}`); }

const dt = DateTime.fromISO(s.received_at, { zone: BUSINESS.zone });
const businessHours = dt.isValid && BUSINESS.weekdays.includes(dt.weekday) && dt.hour >= BUSINESS.from && dt.hour < BUSINESS.to;
if (businessHours) { score += BONUS_BUSINESS_HOURS; parts.push(`business hours +${BONUS_BUSINESS_HOURS}`); }

score = Math.max(0, Math.min(100, score));
const heat = score >= HOT_MIN ? 'hot' : score >= WARM_MIN ? 'warm' : 'cold';

const contact = s.email && s.phone ? 'email and phone' : s.email ? 'email only' : 'phone only';
const reason = `${s.source_channel} lead, ${contact}, ${Number(s.touches) > 1 ? `touch #${s.touches}` : 'first contact'}, ${businessHours ? 'business hours' : 'off-hours'}: ${score} pts → ${heat}. Rule-scored, no AI.`;

return [{
  json: {
    ...s,
    contract: { score, heat, category: 'unclassified', reason: reason.slice(0, 200), draft: '', needs_review: false },
    contract_source: 'rule',
    scoring: { parts, business_hours: businessHours, thresholds: { hot: HOT_MIN, warm: WARM_MIN } },
  },
}];
