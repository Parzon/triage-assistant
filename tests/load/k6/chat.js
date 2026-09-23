// Concurrent streaming answers, through nginx.
//   make load s=chat VUS=100 DURATION=60s
// k6 reads each response to the end, so it measures the whole answer
// (http_req_duration) and the time to the first byte (http_req_waiting) -
// which is the `meta` event, sent before the model's first token. The
// model's own time to first token is the server-side metric
// llm_time_to_first_token_seconds (Grafana, LLM row).
import http from 'k6/http'
import { check } from 'k6'
import { Trend } from 'k6/metrics'
import { WRITE } from './session.js'

const BASE = __ENV.BASE_URL || 'http://web:8080'
const firstEvent = new Trend('time_to_first_event', true)

export const options = {
  scenarios: {
    // Closed model on purpose here: each VU is one user holding one open
    // stream, so VUS = concurrent streams - the capacity question.
    streams: {
      executor: 'constant-vus',
      vus: Number(__ENV.VUS || 50),
      duration: __ENV.DURATION || '60s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    time_to_first_event: ['p(95)<500'],
    checks: ['rate>0.99'],
  },
  summaryTrendStats: ['avg', 'p(50)', 'p(95)', 'p(99)', 'max'],
}

export default function () {
  const res = http.post(`${BASE}/api/chat/stream`, JSON.stringify({ message: 'what is on fire?' }), {
    headers: WRITE,
    timeout: '180s',
  })
  firstEvent.add(res.timings.waiting)
  check(res, {
    'status 200': (r) => r.status === 200,
    'answer completed': (r) => typeof r.body === 'string' && r.body.includes('event: done'),
  })
}
