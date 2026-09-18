// [5c] Enrichment result handler. "Fetch company site" runs with Full Response + Never Error, so HTTP
// statuses land on the success output; network failures (timeout, reset) land on the error output.
// Both outputs come here. Retry policy (spec §8): retry 408/409/425/429/500/502/503/504, never retry
// 400/401/403/404/422, delay = min(maxDelay, base * 2^(attempt-1)) + jitter. Max 3 attempts.
const RETRY_CODES = new Set([408, 409, 425, 429, 500, 502, 503, 504]);
const MAX_ATTEMPTS = 3;
const BASE_DELAY_S = 2;
const MAX_DELAY_S = 20;
const NET_RETRY = /ETIMEDOUT|ECONNRESET|ECONNREFUSED|EAI_AGAIN|timeout|timed out|socket hang up|aborted/i;
const EXCERPT_CHARS = 600;

const j = $input.first().json;
const lead = $('Lead state').first().json;

const attempts = Number($execution.customData.get('enrich_attempts') || 0) + 1;
$execution.customData.set('enrich_attempts', String(attempts));

function decode(s) {
  return String(s)
    .replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&nbsp;/g, ' ').replace(/&#\d+;/g, ' ');
}

function extract(html, domain) {
  const pick = (re) => { const m = html.match(re); return m ? decode(m[1]).replace(/\s+/g, ' ').trim() : ''; };
  const title = pick(/<title[^>]*>([\s\S]*?)<\/title>/i);
  const description =
    pick(/<meta[^>]+name=["']description["'][^>]*content=["']([^"']*)["']/i) ||
    pick(/<meta[^>]+content=["']([^"']*)["'][^>]*name=["']description["']/i) ||
    pick(/<meta[^>]+property=["']og:description["'][^>]*content=["']([^"']*)["']/i);
  const text = decode(html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, ' ')
    .replace(/<[^>]+>/g, ' '))
    .replace(/\s+/g, ' ').trim();
  return [
    `Website: https://${domain}`,
    title && `Title: ${title}`,
    description && `Description: ${description}`,
    text && `Excerpt: ${text.slice(0, EXCERPT_CHARS)}`,
  ].filter(Boolean).join('\n');
}

let status = 'ok';
let enrichment = '';
let error = '';
let retryable = false;
const http = typeof j.statusCode === 'number' ? j.statusCode : null;

if (j.error) {
  error = typeof j.error === 'string' ? j.error : String(j.error.message || JSON.stringify(j.error));
  retryable = NET_RETRY.test(error);
  status = 'failed';
} else if (http !== null && http >= 200 && http < 300) {
  enrichment = extract(String(j.body || ''), lead.domain);
  if (!enrichment.includes('Title:') && !enrichment.includes('Excerpt:')) {
    // reachable but empty (JS-only site, bot wall): keep going, nothing to enrich
    enrichment += '\n(no readable content on the home page)';
  }
} else {
  error = `HTTP ${http === null ? 'unknown' : http}`;
  retryable = http !== null && RETRY_CODES.has(http);
  status = 'failed';
}

const retry = status === 'failed' && retryable && attempts < MAX_ATTEMPTS;
const retry_delay_s = retry
  ? Math.min(MAX_DELAY_S, BASE_DELAY_S * 2 ** (attempts - 1)) + Math.round(Math.random() * 10) / 10
  : 0;

return [{
  json: {
    ...lead,
    enrichment,
    enrichment_status: retry ? 'retry' : status,
    enrichment_error: error,
    enrichment_http: http,
    enrich_attempts: attempts,
    retry,
    retry_delay_s,
    attempt: 0,
  },
}];
