# BPEL2Orkes — Architecture & Operations

**Updated:** 2026-06-21

This document covers the deploy model, IAM/secrets handling, and infrastructure
gotchas discovered in production. It's the reference for anyone (human or AI session)
picking this project back up.

---

## Environments

| Environment | Domain | DNS | Notes |
|---|---|---|---|
| Production | `bpel2orkes.kshetra.studio` | Cloudflare, **proxied** (orange cloud) | Full Cloudflare WAF/DDoS protection |
| Staging | `staging.bpel2orkes.kshetra.studio` | Cloudflare, **DNS-only** (grey cloud) | Deliberate — see "Staging SSL" below |

Both sit behind: API Gateway (REST v1) → Lambda (ARM64 Docker image) → FastAPI (Mangum adapter).

### Staging SSL — why DNS-only

Cloudflare's free Universal SSL cert only covers **one level** of subdomain
(`*.kshetra.studio`). `staging.bpel2orkes.kshetra.studio` is two levels deep, so a
proxied (orange cloud) record there gets no valid cert and fails the TLS handshake
(`SSL routines:ST_CONNECT:sslv3 alert handshake failure`).

Fix: staging DNS record is **DNS-only**. Traffic goes straight to AWS, where ACM
issues a cert scoped exactly to `staging.bpel2orkes.kshetra.studio` (provisioned by
the CDK custom-domain construct). Production stays proxied since `bpel2orkes.kshetra.studio`
is one level deep and is covered by the wildcard.

This is permanent, not a workaround — staging doesn't need Cloudflare's protection,
and grey-cloud avoids the cert mismatch entirely.

---

## Three deploy lanes — do not conflate them

| Lane | Workflow | Trigger | What it touches | Frequency |
|---|---|---|---|---|
| **App code** | `.github/workflows/deploy.yml` | Auto on push to `main` (staging) / manual `workflow_dispatch` (production) | `docker build` + `lambda update-function-code` only | Every commit |
| **Infra** | `.github/workflows/deploy-infra.yml` | Manual `workflow_dispatch` only | `cdk diff` then `cdk deploy` — WAF, API Gateway, IAM, DynamoDB schema | Rare, deliberate |
| **Secrets** | `scripts/push-secrets.sh {env}` | Manual, local only | Reads `.env.{env}`, writes to AWS Secrets Manager directly | Very rare (rotation only) |

**Why three lanes:** app deploys should be fast and frequent; infra changes are
higher blast-radius and need explicit review (`cdk diff` before `cdk deploy`);
secrets should never pass through CI logs even encrypted-in-transit.

### Known gap (found and fixed 2026-06-19/21)

`deploy.yml` **never ran `cdk deploy`** — it only updates Lambda code. A WAF fix
(`NoUserAgent_HEADER` override) was merged to `main` and the team believed it was
"deployed," but `aws wafv2 get-web-acl` showed the live rule set never changed. This
caused a multi-session debugging detour (chasing CORS, error messages, server-card.json)
before the real root cause — infra never actually deployed — was found.

**Lesson: a merged PR + "deploy done" does not guarantee CDK changes are live.**
Always verify with `cdk diff` (should show no changes) or a direct AWS check
(e.g. `aws wafv2 get-web-acl ...`) after an infra change is supposed to have shipped.

`deploy-infra.yml` now exists specifically to close this gap.

---

## CDK specifics

- **No `cdk.json`** exists in `infra/` — the CDK app entrypoint must be passed
  explicitly on every invocation: `cdk deploy --app 'infra/.venv/bin/python3 infra/app.py' <StackName>`
- **Must run from repo root**, not `infra/`. `DockerImageCode.from_image_asset(directory=".")`
  in `infra/app.py` resolves relative to CDK's working directory — running from
  `infra/` makes it look for `infra/Dockerfile.lambda`, which doesn't exist (it's at
  repo root). This caused `RuntimeError: Cannot find file at .../infra/Dockerfile.lambda`
  the first time `deploy-infra.yml` ran.
- Stack names: `Bpel2OrkesStagingServerless`, `Bpel2OrkesProductionServerless`.

### CDK bootstrap IAM (fixed 2026-06-21)

The GitHub OIDC role (`github-actions-bpel2orkes`) was originally scoped to only
ECR push + `lambda:UpdateFunctionCode` — sufficient for app deploys, not for
`cdk deploy`. First `deploy-infra.yml` run failed:

```
AccessDeniedException: ... is not authorized to perform: ssm:GetParameter on
resource: arn:aws:ssm:.../cdk-bootstrap/hnb659fds/version
```

Fix: added a statement to the role's inline policy (`bpel2orkes-deploy`) granting:
- `sts:AssumeRole` on the 4 CDK bootstrap roles (`cdk-hnb659fds-{lookup,deploy,file-publishing,image-publishing}-role-*`)
- `ssm:GetParameter` on `/cdk-bootstrap/hnb659fds/version`

This is the standard CDK CI/CD pattern — the bootstrap roles are themselves scoped
by the CDK bootstrap stack (can only touch CloudFormation-managed resources), so
granting our CI role permission to assume them is not a broad permission grant.

---

## Secrets

Stored in AWS Secrets Manager at `bpel2orkes/{env}/oauth` (JSON: `GITHUB_CLIENT_ID`,
`GITHUB_CLIENT_SECRET`, `SESSION_SECRET`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`).

CDK wires these into Lambda env vars via `SecretValue.secrets_manager(...).to_string()`,
which becomes a CloudFormation **dynamic reference** resolved at **deploy time** —
not read live by the running Lambda.

**This means rotating a secret requires two steps, not one:**

1. `./scripts/push-secrets.sh {env}` — reads `.env.{env}`, writes new value to
   Secrets Manager. Prints the next command needed.
2. `cdk deploy --app 'infra/.venv/bin/python3 infra/app.py' Bpel2Orkes{Env}Serverless`
   (or `aws lambda update-function-configuration` for a lighter-weight refresh) —
   re-resolves the dynamic reference and pushes the new value into the **already-deployed**
   Lambda's environment.

Skipping step 2 means the Lambda keeps using the old secret value indefinitely —
Secrets Manager updates do not propagate on their own.

No "bounce"/restart needed — Lambda is serverless. Updating function config forces
all new invocations to pick up the fresh env var immediately; no manual restart step exists or is needed.

---

## WAF (AWS WAFv2, REGIONAL scope)

Associated with the API Gateway REST API stage (WAFv2 only supports REST API v1
association, not HTTP API v2 — confirmed empirically, see BACKLOG.md INF-6).

Managed rule group: `AWSManagedRulesCommonRuleSet`, with overrides (block → count):

| Rule | Why overridden |
|---|---|
| `CrossSiteScripting_BODY` | False positives on legitimate BPEL/XML bodies — XML tags resemble XSS patterns |
| `SizeRestrictions_BODY` | WAF's 8KB body inspection default blocks BPEL files >8KB (most real BPEL is tens of KB); app already enforces its own 5–10MB cap |
| `NoUserAgent_HEADER` | MCP scanner bots (Smithery, etc.) send minimal/no User-Agent strings; blocks legitimate MCP clients, not real bot threats — anything adversarial just spoofs a User-Agent anyway |

Plus a rate-based rule (`RateLimitPerIp`, 300 req/5min/IP, block) — this is the actual
DDoS/cost circuit breaker, separate from the app-level rate limiter in `src/api.py`
(20/min on `/parse`, 30/min on `/convert/diagram` for unauthenticated callers).

**Verifying WAF state live** (don't trust "merged" status alone):
```bash
aws wafv2 get-web-acl --name bpel2orkes-{env} --scope REGIONAL \
  --id $(aws wafv2 list-web-acls --scope REGIONAL --region ap-southeast-2 \
         --query 'WebACLs[?Name==`bpel2orkes-{env}`].Id' --output text) \
  --region ap-southeast-2
```

---

## CORS

`allow_origins=["*"]` in `src/api.py`. Deliberately open — this is an API
authenticated by `X-Api-Key` header + credit quota, not by browser same-origin
policy. CORS only restricts what *browsers* can do cross-origin; it does nothing
against direct API calls, server-to-server calls, or MCP clients, so a whitelist
here was security theatre, not real protection. Real protection is the WAF rate
limit + credit quota.

(Originally whitelisted to specific domains; opened up after Smithery's scanner
got rejected by CORS preflight from `smithery.ai`/`run.tools` origins — an
unrelated-but-real bug the whitelist was hiding.)

---

## MCP server (Smithery / external registries)

`server-card.json` at `/.well-known/mcp/server-card.json` advertises tools (with
full `inputSchema`), the `streamable-http` remote URL, and a `configSchema` requiring
`apiKey` with `x-header: X-Api-Key` (tells gateways to inject the key as that header).

**Open issue:** Smithery's scanner uses an `oauth4webapi` client that probes
`/.well-known/oauth-protected-resource` before connecting. We return 404 there
(we don't use OAuth — header auth only), and Smithery's scanner currently
misreports this as a connection-level 403, blocking automated publish via both
the CLI and the direct API (`PUT /servers/{name}/releases`).

Confirmed NOT the cause: WAF (verified rule-by-rule), CORS, or our app's actual
MCP behavior (curl with `SmitheryBot` UA returns 200 cleanly). A sibling project
(BPMN2AI/TwinTrack) has near-identical infra and the *same* 404 on
oauth-protected-resource, yet published successfully — so the discriminator isn't
purely architectural; likely something in how this specific publish payload
(`configSchema` + `scanCredentials`) is being interpreted by Smithery's scanner.

**Backlogged fix (not yet applied):** add a
`/.well-known/oauth-protected-resource` route returning RFC 9728 metadata with
`authorization_servers: []`, explicitly telling clients no OAuth server is required.
Low risk — new read-only GET route, doesn't touch any existing auth/conversion code path.

---

## Cost

- Idle cost near-$0 (request-billed Lambda + API Gateway, replaced always-on
  ECS Fargate + ALB which ran ~$50–70/mo).
- AWS Budget alert at $50 forecasted threshold (account-wide, not bpel2orkes-specific).
- Historical spike (2026-06-14 to 06-16, ~$2–8/day) was leftover ECS/NAT Gateway/ALB
  infra from the Fargate-era stack, torn down once the Lambda migration completed.
  Confirmed via `aws ec2 describe-nat-gateways` / `aws ecs list-clusters` / `aws elbv2
  describe-load-balancers` all returning empty after teardown. Steady-state is ~$0.50/day.
