// k6 side of `make load-compare`: one GET per iteration at a fixed arrival
// rate, identical to the other tools' scenario.
import http from 'k6/http'
import { AUTH } from './session.js'

export const options = {
  scenarios: {
    same: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.RATE || 200),
      timeUnit: '1s',
      duration: `${__ENV.DURATION || 30}s`,
      preAllocatedVUs: 50,
      maxVUs: 1000,
    },
  },
  summaryTrendStats: ['p(50)', 'p(95)', 'p(99)', 'max'],
}

export default function () {
  http.get(`${__ENV.BASE_URL || 'http://web:8080'}/api/alerts?limit=50`, { headers: AUTH })
}
