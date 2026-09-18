// Final contract for the AI branches: the validated model output, or the safe fallback after the
// third failure (spec §5 [6]: score 50, heat warm, needs_review = true). Enrichment failure also flags review.
const j = $input.first().json;

let contract;
let contract_source;

if (j.contract_ok && j.contract) {
  contract = { ...j.contract };
  contract_source = 'llm';
} else {
  const why = j.api_error
    ? `AI call failed (${j.api_status || 'no status'}): ${j.api_error}`
    : `AI output failed validation ${j.attempt}x: ${(j.errors || []).join(' ')}`;
  contract = {
    score: 50,
    heat: 'warm',
    category: 'unclassified',
    reason: `Needs human review. ${why}`.slice(0, 200),
    draft: '',
    needs_review: true,
  };
  contract_source = 'fallback';
}

if (j.enrichment_status === 'failed') contract.needs_review = true;

return [{
  json: {
    ...j,
    contract,
    contract_source,
    fallback_reason: contract_source === 'fallback' ? contract.reason : '',
  },
}];
