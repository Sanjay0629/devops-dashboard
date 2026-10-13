# Mock OIDC provider — development aid

**This is not an identity provider.** It checks no password and will issue a token
for any of three synthetic accounts to whoever asks. It exists so M1.3's
authorisation-code + PKCE flow, session handling and CSRF checks can be built and
tested without a cloud account or a real IdP (PRD §11).

Passing against it proves DCP's OIDC client is **self-consistent**. It does not
prove the integration works — claim shapes, discovery quirks and redirect-URI
registration rules differ per provider (`OPEN-01`). A real-IdP round trip is still
outstanding and is recorded as such in [`PROGRESS.md`](../../../PROGRESS.md).

It runs from the backend image (no extra image to pull, no JVM), which is why it
needs no dependencies of its own: `fastapi`, `pyjwt` and `cryptography` are already
there for the backend.

## What it does implement

Enough that the security checks against it mean something:

| Behaviour | Why it matters |
|---|---|
| Discovery document at `/.well-known/openid-configuration` | DCP fetches endpoints from it and rejects any that leave the issuer's origin |
| RS256 signing, JWKS at `/jwks` with a `kid` | Exercises real signature verification and key lookup |
| PKCE `S256` **required and verified** | A client that quietly dropped PKCE fails here instead of looking fine |
| Authorisation codes are single-use and TTL-bounded | Replay is rejected |
| `nonce` echoed into the ID token | DCP's replay check has something real to compare |
| `redirect_uri` must match the one used at `/authorize` | Catches a mismatched registration |
| Client secret compared in constant time | `/token` rejects a wrong secret |

The signing key is generated at startup and never persisted, so a restart
invalidates every issued token.

## Running it

```bash
docker compose -f infra/compose/docker-compose.yml --profile core --profile mocks up -d
```

Then point the backend at it (these are already the defaults in `.env.example`):

```
DCP_OIDC_ISSUER=http://localhost:9000
DCP_OIDC_CLIENT_ID=dcp-local
DCP_OIDC_CLIENT_SECRET=placeholder-client-secret
DCP_OIDC_REDIRECT_URL=http://localhost:8000/api/v1/auth/callback
```

The issuer is `http://localhost:9000` from the **browser's** point of view and the
backend must agree with it exactly — the `iss` claim is compared literally — so the
`mock-idp` container publishes on host port 9000 and the backend reaches it through
the same host URL rather than through the Compose service name.

Visiting `/api/v1/auth/login` lands on a plain account picker: `operator`, `admin`
or `viewer`. All three start with **no roles**, so they can sign in and see nothing
until a role is granted — role administration is M1.4.
