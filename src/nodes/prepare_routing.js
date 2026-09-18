// [8a] Prepare routing. Pattern from LeadIQ-AI "Node 07 - Prepare Routing" (MIT): this node DECIDES
// and has no side effects. Routes are a frozen constant; the nodes after it only execute.
//   hot  -> status Hot,      Telegram alert + personal Gmail draft (never sent)
//   warm -> status Nurture,  Gmail draft (personal if the model wrote one, nurture template otherwise)
//   cold -> status Archived, template reply as a Gmail draft
//   needs_review -> Telegram alert regardless of heat
const COMPANY_NAME = 'Acme Automation';
const SENDER = 'Dmytro';

const ROUTES = Object.freeze({
  hot:  Object.freeze({ status: 'Hot',      notify: true,  draft_kind: 'personal' }),
  warm: Object.freeze({ status: 'Nurture',  notify: false, draft_kind: 'nurture'  }),
  cold: Object.freeze({ status: 'Archived', notify: false, draft_kind: 'template' }),
});

const firstName = (s) => (s.name || '').trim().split(/\s+/)[0] || 'there';

const TEMPLATES = {
  personal: (s) => `Hi ${firstName(s)},\n\nThanks for reaching out to ${COMPANY_NAME}. I'd like to understand your situation a bit better: would a 15-minute call this week work? Reply with a time that suits you and I'll send an invite.\n\nBest,\n${SENDER}`,
  nurture:  (s) => `Hi ${firstName(s)},\n\nThanks for getting in touch with ${COMPANY_NAME}. Here is a short overview of how we work and what a typical first project looks like: [link]. If any of it resonates, just reply to this email and we'll take it from there.\n\nBest,\n${SENDER}`,
  template: (s) => `Hi ${firstName(s)},\n\nThanks for your message to ${COMPANY_NAME}. We've received it and will reach out if it's a fit for what we do. In the meantime, our FAQ covers the most common questions: [link].\n\nBest,\n${SENDER}`,
};

const j = $input.first().json;
const c = j.contract;

const route = { ...ROUTES[c.heat], heat: c.heat, has_email: !!j.email };
route.notify = route.notify || c.needs_review === true;

const draft_text = (c.draft && c.draft.trim()) ? c.draft.trim() : TEMPLATES[route.draft_kind](j);
const subjectIn = j.raw && j.raw.subject ? String(j.raw.subject).trim() : '';
const subject = subjectIn ? (/^re:/i.test(subjectIn) ? subjectIn : `Re: ${subjectIn}`) : `Re: your inquiry to ${COMPANY_NAME}`;

// Telegram node sends with parse_mode HTML: escape user-supplied text so a "<" or "&" in a name never breaks the alert
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const who = esc(`${j.name || 'Unknown'} · ${j.email || j.phone}${j.company ? ` · ${j.company}` : ''}`);
const telegram_text = c.needs_review
  ? `⚠️ <b>Lead needs review</b>\n${who}\nChannel: ${esc(j.source_channel)} · score ${c.score} · ${c.heat}\n${esc(c.reason)}`
  : c.heat === 'hot'
    ? `🔥 <b>HOT lead</b>\n${who}\nChannel: ${esc(j.source_channel)} · score ${c.score} · ${esc(c.category)}\n${esc(c.reason)}\nDraft reply is waiting in Gmail.`
    : `ℹ️ ${c.heat} lead\n${who}\nChannel: ${esc(j.source_channel)} · score ${c.score} · ${esc(c.category)}\n${esc(c.reason)}`;

const now = DateTime.utc().toISO();
const airtable = {
  dedupe_key: j.dedupe_key,
  lead_type: j.lead_type,
  score: c.score,
  heat: c.heat,
  category: c.category,
  reason: c.reason,
  draft: draft_text,
  status: route.status,
  touches: j.touches,
  created_at: j.created_at,
  updated_at: now,
  needs_review: c.needs_review === true,
  enrichment: j.enrichment || '',
  run_id: j.run_id,
};

return [{
  json: {
    ...j,
    route,
    draft_text,
    subject,
    telegram_text,
    airtable,
    gmail: { to: j.email || '', thread_id: (j.raw && j.raw.thread_id) || '' },
  },
}];
