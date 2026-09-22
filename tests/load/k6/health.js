// Ceiling of the plumbing alone (nginx -> gunicorn -> FastAPI, no database,
// no model): the most requests/s this stack can ever serve on this host.
//   make load s=health
// Arrival rate ramps up until latency or errors show the knee.
import http from 'k6/http'
import { check } from 'k6'

const BASE = __ENV.BASE_URL || 'http://web:8080'
const PEAK = Number(__ENV.RATE || 3000)

export const options = {
  scenarios: {
    ramp: {
      executor: 'ramping-arrival-rate',
      startRate: 100,
      timeUnit: '1s',
      preAllocatedVUs: 200,
      maxVUs: 2000,
      stages: [
        { target: Math.round(PEAK / 3), duration: '20s' },
        { target: Math.round((PEAK * 2) / 3), duration: '20s' },
        { target: PEAK, duration: '20s' },
      ],
    },
  },
  summaryTrendStats: ['avg', 'p(50)', 'p(95)', 'p(99)', 'max'],
}

export default function () {
  check(http.get(`${BASE}/api/health`), { 'status 200': (r) => r.status === 200 })
}
