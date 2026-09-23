// The signed-in user every load scenario runs as. `make load` mints the
// session with app.cli (a real session row, no identity provider) and passes
// it in: SESSION_COOKIE="name=value". State-changing requests also carry
// ORIGIN, the site's PUBLIC_URL, which the api's CSRF check requires.
// A fixed header rather than k6's cookie jar: identical for every virtual
// user, and nothing to parse per request.
const cookie = __ENV.SESSION_COOKIE
if (!cookie) throw new Error('SESSION_COOKIE is not set: run the scenario through `make load`')

export const AUTH = { Cookie: cookie }
export const WRITE = { ...AUTH, Origin: __ENV.ORIGIN || '', 'Content-Type': 'application/json' }
