// Read load on the alert list, through nginx like real users.
//   make load s=alerts-read RATE=50 DURATION=60s
// Each iteration makes two requests: the severity-filtered list and the
// unfiltered newest-first list (the same query the chat uses for context).
import http from 'k6/http'
import { check } from 'k6'
import { AUTH } from './session.js'

const BASE = __ENV.BASE_URL || 'http://web:8080'

export const options = {
  scenarios: {
    // Open model: iterations start at a fixed rate whatever the response
    // time, like independent users. A closed model (a fixed number of VUs
    // looping) slows its own arrival rate when the server slows down, and
    // hides the very latency it is supposed to measure ("coordinated
    // omission"). maxVUs caps how many can be in flight at once.
    reads: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.RATE || 50),
      timeUnit: '1s',
      duration: __ENV.DURATION || '60s',
      preAllocatedVUs: 50,
      maxVUs: 1000,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'http_req_duration{kind:critical}': ['p(95)<200'],
    'http_req_duration{kind:recent}': ['p(95)<200'],
  },
  summaryTrendStats: ['avg', 'p(50)', 'p(95)', 'p(99)', 'max'],
}

export default function () {
  const critical = http.get(`${BASE}/api/alerts?severity=critical&limit=50`, { headers: AUTH, tags: { kind: 'critical' } })
  check(critical, { 'critical list 200': (r) => r.status === 200 })
  const recent = http.get(`${BASE}/api/alerts?limit=50`, { headers: AUTH, tags: { kind: 'recent' } })
  check(recent, { 'recent list 200': (r) => r.status === 200 })
}
