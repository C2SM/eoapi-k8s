# CSCS eoAPI Dev Deployment

This deploys eoAPI to the `eoapi-dev` namespace on the `c2sm-tds` cluster with Traefik ingress, TLS, IP allowlisting, and retained NVMe-backed Crunchy Postgres storage.

Apply the Traefik middleware first:

```bash
kubectl apply -f deploy/cscs/traefik-ipallowlist.yaml
```

Install or upgrade the release (a first install follows the order in
`deploy/cscs/auth-keycloak/README.md`, "Deploy"):

```bash
helm upgrade --install eoapi ./charts/eoapi \
  -n eoapi-dev \
  --create-namespace \
  -f charts/eoapi/profiles/development.yaml \
  -f deploy/cscs/values-cscs-dev.yaml \
  -f deploy/cscs/values-cscs-dev-stac-auth-proxy.yaml \
  -f deploy/cscs/values-cscs-dev-auth-keycloak.yaml \
  --set gitSha=$(git rev-parse HEAD | cut -c1-10)
```

Render locally before applying:

```bash
helm template eoapi ./charts/eoapi \
  -n eoapi-dev \
  -f charts/eoapi/profiles/development.yaml \
  -f deploy/cscs/values-cscs-dev.yaml \
  -f deploy/cscs/values-cscs-dev-stac-auth-proxy.yaml \
  -f deploy/cscs/values-cscs-dev-auth-keycloak.yaml \
  --set gitSha=$(git rev-parse HEAD | cut -c1-10)
```

The ingress middleware annotation uses Traefik's Kubernetes CRD reference form:

```text
eoapi-dev-eoapi-dev-ipallowlist@kubernetescrd
```

## Namespace security

Two things outside the chart, applied with kubectl.

**Pod Security `restricted`**, enforced since 2026-11-12. The namespace
refuses any new pod that may run as root, can escalate privileges, keeps Linux
capabilities or has no seccomp profile. `warn` makes kubectl and helm print the
violation, and `audit` logs it. Every pod of the chart, of PGO and of phegos
complies, the chart's through the security contexts in `values-cscs-dev.yaml`.
A refused pod never starts: a Deployment keeps its old pods, a Helm hook Job
makes `helm upgrade` wait until it times out, and ad-hoc `kubectl run` or
`kubectl debug` pods need a security context too. Running pods are never
affected.

```bash
kubectl label --overwrite ns eoapi-dev \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/warn=restricted \
  pod-security.kubernetes.io/audit=restricted

# Before changing the level: lists the running pods it would refuse.
kubectl label --dry-run=server --overwrite ns eoapi-dev \
  pod-security.kubernetes.io/enforce=restricted

# Undo.
kubectl label ns eoapi-dev pod-security.kubernetes.io/enforce- \
  pod-security.kubernetes.io/warn- pod-security.kubernetes.io/audit-
```

There is deliberately no Namespace manifest for this: `kubectl delete -f` on
one would delete the namespace.

**NetworkPolicies** for the two Postgres clusters, in `network-policies.yaml`;
its header lists who may connect. A new database client needs a line there, or
its connections time out.

## Hardening decisions

**Read-only root filesystems: not pursued.** The Postgres operator's
containers already have one. For the other twelve (the nginx front ends, the
Python APIs, stac-auth-proxy, oauth2-proxy and phegos), every path an image
writes to would first have to be found and given an `emptyDir`. A missed path
only shows up as a failure at runtime, so each service would need its own round
of testing, and another after every image upgrade. The gain is small: every
container already runs as a non-root user with no capabilities and no privilege
escalation, under Pod Security `restricted`, and a restart discards whatever
was written. The squeeze isn't worth the juice. Worth revisiting if a service
starts running untrusted code or handling uploads.

**PodDisruptionBudgets: for production, together with replicas.** With one
replica per service a budget can only block node drains (kured reboots a node
on weekdays and forces it after 10 minutes), so there are none. When a
production deployment runs two or more replicas of a service:

- Give each scaled-out service a PodDisruptionBudget with `maxUnavailable: 1`,
  so a drain evicts one pod at a time. The chart has no template for it yet:
  either add one (an upstream PR candidate) or keep a manifest next to the
  deployment's values.
- Spread the replicas over nodes with the service's `settings.affinity` (a
  preferred pod anti-affinity on its `app` label), so one node going down does
  not take them all.
- Postgres needs nothing extra: PGO creates its own budgets, `minAvailable: 1`,
  as soon as `instances[].replicas` or `pgBouncerConfig.replicas` is above one.
- `profiles/production.yaml` already runs stac, raster and vector with at
  least two replicas (`minReplicas: 2`) and the browser with two.

## CSCS Keycloak Auth

`deploy/cscs/auth-keycloak/` is the auth path, live since 2026-10-30
(`v1.3.0-prometheus`). CSCS's production Keycloak (`auth.cscs.ch`, realm
`cscs`, client `prometheus-tds`) is the OIDC provider and the shared SSO
session; oauth2-proxy, using that confidential client, is the Traefik
ForwardAuth gate for `/raster`, `/vector` and `/multidim`. `/stac`
authenticates itself via stac-auth-proxy, `/browser` is public static UI, the
`/` landing page is public, and narthex sits outside the gate as its own OIDC
client. The IP allowlist stays on everything.

Every IdP-facing value is in `values-cscs-dev-auth-keycloak.yaml`, endpoints
and client ID in its `keycloak:` section; the gate's client secret is the
`oauth2-proxy-keycloak-secret` Secret.

`deploy/cscs/auth-keycloak/README.md` has what is known about the realm, the
registered redirect URIs, the network precondition, the deploy order, the logs
to watch and the rollback.

All four files, every time: omitting the Keycloak one fails the render,
omitting the stac-auth-proxy one silently disables group filtering and the
browser login button, and omitting the development profile silently drops
every app pod's resource requests and limits. The profile comes first so the
CSCS values can override it.
