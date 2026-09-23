// Locust's workload shape in k6: VUS users, each holding its own connection
// and pacing one request per second (a closed model). Same average rate as
// compare.js at RATE=VUS, very different arrival pattern - used to show that
// the workload shape, not the tool, changes the result.
import http from 'k6/http'
import { sleep } from 'k6'
import { AUTH } from './session.js'

export const options = {
  scenarios: { users: { executor: 'constant-vus', vus: Number(__ENV.VUS || 100), duration: `${__ENV.DURATION || 20}s` } },
  summaryTrendStats: ['p(50)', 'p(95)', 'p(99)', 'max'],
}

export default function () {
  const started = Date.now()
  http.get(`${__ENV.BASE_URL || 'http://web:8080'}${__ENV.PATH_ || '/api/alerts?limit=50'}`, { headers: AUTH })
  sleep(Math.max(0, 1 - (Date.now() - started) / 1000))
}
