// The bundled identity provider's demo users (infra/keycloak/triage-realm.json):
//   alice  responder in payments, viewer in platform
//   bob    admin in platform, viewer in default
// `make e2e` passes the password from .env.
export const PASSWORD = process.env.DEMO_USER_PASSWORD ?? 'change-me-demo'
