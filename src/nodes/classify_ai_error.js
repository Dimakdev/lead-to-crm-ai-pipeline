// The model call itself failed (error output of "AI qualify"; Gemini or Claude, same handling). Classify by HTTP status (spec §8):
// retry 408/409/425/429/500/502/503/504, never retry 400/401/403/404/422,
// delay = min(maxDelay, base * 2^(attempt-1)) + jitter. Max 3 attempts, then the fallback contract.
const RETRY_CODES = [408, 409, 425, 429, 500, 502, 503, 504];
const NO_RETRY_CODES = [400, 401, 403, 404, 422];
const MAX_ATTEMPTS = 3;
const BASE_DELAY_S = 2;
const MAX_DELAY_S = 20;

const st = $('Build prompt').item.json;
const j = $input.first().json;

const msg = typeof j.error === 'string'
  ? j.error
  : (j.error && (j.error.message || j.error.description)) || JSON.stringify(j.error || 'unknown error');

// n8n replaces the vendor message with its own wording per status code; map those back to a code.
const N8N_MESSAGES = [
  [400, /bad request/i],
  [401, /authorization failed/i],
  [402, /payment required/i],
  [403, /forbidden/i],
  [404, /could not be found/i],
  [405, /method not allowed/i],
  [429, /too many requests/i],
  [500, /not able to process your request/i],
  [502, /bad gateway/i],
  [503, /service unavailable/i],
  [504, /gateway timed out/i],
];
const codeMatch = msg.match(/\b(4\d\d|5\d\d)\b/);
let code = codeMatch ? Number(codeMatch[1]) : null;
if (code === null) {
  const hit = N8N_MESSAGES.find(([, re]) => re.test(msg));
  if (hit) code = hit[0];
}

let retryable;
if (code !== null && NO_RETRY_CODES.includes(code)) retryable = false;
else if (code !== null && RETRY_CODES.includes(code)) retryable = true;
else retryable = /rate.?limit|overloaded|timeout|timed out|ETIMEDOUT|ECONNRESET|socket hang up|529|temporar/i.test(msg);

const attempt = Number(st.attempt || 0) + 1;
const retry = retryable && attempt < MAX_ATTEMPTS;
const retry_delay_s = retry
  ? Math.min(MAX_DELAY_S, BASE_DELAY_S * 2 ** (attempt - 1)) + Math.round(Math.random() * 10) / 10
  : 0;

return [{
  json: {
    ...st,
    attempt,
    contract: null,
    contract_ok: false,
    errors: [],
    retry,
    retry_delay_s,
    api_error: msg.slice(0, 500),
    api_status: code,
    api_retryable: retryable,
  },
}];
