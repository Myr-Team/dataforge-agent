# DeepSeek routing and Entra identity production release

Date: 2026-08-09 (Asia/Shanghai)

## Outcome

The governed DeepSeek routing and trusted Entra identity update is active in
the new DataForge production environment. The public entry point remains:

`https://ca-dataforge-web.grayground-b382bfb9.eastus2.azurecontainerapps.io/`

The release keeps production FinOps actions disabled. DeepSeek credentials are
write-only, provider routing is tenant-scoped, and a model is selectable only
after the provider is connected, officially priced, and explicitly admitted to
the routing catalog.

## Production state

- Backend revision: `ca-dataforge-backend--dse9135641`, Healthy, Running,
  100% traffic.
- Web revision: `ca-dataforge-web--dse9135641`, Healthy, Running, 100% traffic.
- Backend image digest:
  `sha256:9404566bff5f5d645233ce83c06d24be4dc0e48283af9cf5a2c77ddb83a8d353`.
- Web image digest:
  `sha256:508588fac239ee5e49cc6cf63578ac917a61d3e790fa6f9f86980e034c1d0a8e`.
- Rollback backend: `ca-dataforge-backend--4efe9e0`, Healthy, 0% traffic.
- Rollback web: `ca-dataforge-web--dsai0809`, Healthy, 0% traffic.
- `DF_PROVIDER_CONNECTORS_ENABLED=1`.
- `DF_EXTERNAL_PROVIDER_ROUTING_ENABLED=1`.
- `DF_PROVIDER_APIM_ENABLED=0`, so the current external-provider path uses the
  configured provider endpoint directly.
- `DF_FINOPS_ACTIONS_ENABLED=0`.

## Functional scope

- The application exposes a trusted session view derived from Easy Auth claims;
  production no longer silently labels an unavailable identity as `local`.
- Settings shows the current Entra user, login address, workspace role, and the
  authorization source.
- Provider settings uses an audited lifecycle: credentials, connectivity,
  official model pricing, and explicit routing governance.
- The model picker groups Azure and DeepSeek models and explains why an
  unavailable route cannot be selected.
- DeepSeek V4 Flash shows the official three-part estimate: cached input,
  uncached input, and output. Request cost is calculated from the observed
  provider/model route and token categories; unmatched models remain unpriced.
- Runtime fallback is bounded to transport, throttling, and upstream 5xx
  failures before output begins. Authentication, balance, and invalid-request
  failures do not silently fall back.
- The demo-role handoff procedure is recorded in
  `docs/validation/2026-08-09-entra-demo-role-handoff.md`; it uses a second real
  tenant account and a private browser window, not a simulated front-end role.

## Verification evidence

- Python: `1813 passed, 1 skipped`.
- Node: `302 passed`.
- Vite production build: passed. The existing bundle-size warning remains
  non-blocking.
- Playwright: `63 passed` on an isolated preview port.
- Focused provider/pricing backend checks: `22 passed`.
- `git diff --check`: clean for the committed release changes.
- Secret scan found no GitHub token, AWS access key, private key, storage-account
  key, JWT, or real provider credential. The only secret-shaped test value is
  an intentional fake audit-redaction canary.
- The stable production URL redirects anonymous users into Easy Auth.
- The production web revision successfully proxied `/api/health` to the new
  backend. Foundry, Search, MCP, Speech, Blob, and Content Safety probes were
  healthy.
- Post-cutover backend and web logs contained zero matches for the selected
  critical-error patterns.

## Remaining human acceptance

The Azure CLI identity does not have delegated consent to impersonate the web
application, so no auth setting was changed to bypass that boundary. With an
already signed-in tenant user, confirm these read-only items in the browser:

1. Settings displays `Microsoft Entra ID`, the expected user, and the expected
   workspace role rather than `local`.
2. The DeepSeek provider card shows the configured connection state and the
   routing-governance action.
3. After governance is active, the agent model picker lists DeepSeek in its own
   provider group and the cost view shows cached-input, uncached-input, and
   output pricing.

Provider secret persistence is configured for Key Vault but its read probe is
reported as `configured_unverified` until a provider-secret operation exercises
that path. Treat the connection test and the three browser checks above as the
final credential-specific acceptance gate.
