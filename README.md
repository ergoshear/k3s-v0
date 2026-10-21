# k3s

## Argo CD deployment

The `Apply Argo CD` GitHub Actions workflow runs on pushes to `main` (including
merges) on the self-hosted runner labeled `k3s-control-plane-01`. It uses the
host's `/etc/rancher/k3s/k3s.yaml` kubeconfig. It applies the upstream CRDs
separately, creates the local namespace, installs Argo CD and cert-manager, and
waits for cert-manager before applying [the Argo CD Application](argocd/application.yaml).
That Application syncs `argocd/` from this repository with pruning and self-healing
enabled, managing the platform resources as well as the checked-in install
manifests. Child applications are declared in `argocd/applications/`.

## Trivy Operator

The `trivy-operator` Argo CD application installs Aqua's official Helm chart,
pinned to chart version `0.37.0`. Its values are kept in
[`apps/trivy-operator/values.yaml`](apps/trivy-operator/values.yaml). It scans
all namespaces and creates Kubernetes security report resources; it does not
provide a web dashboard. Scan jobs are limited to two concurrent jobs, and the
operator, scanner jobs, and node collector tolerate the control-plane taint so
that control-plane workloads and the node can be scanned as well.

## Frontend Website

The frontend website pulls its private image from GHCR. Argo CD manages the
Sealed Secrets controller from the pinned upstream chart and reconciles the
encrypted image-pull Secret. The controller is installed in `kube-system`.

After the controller application is synced, install the `kubeseal` CLI and
generate the encrypted manifest:

```sh
brew install kubeseal
uv run python scripts/seal_frontend_website_ghcr_secret.py
```

The script uses `~/.kube/k3s.yaml` by default; pass `--kubeconfig` to use
another file. Enter the GHCR username and a classic PAT with `read:packages`
access when prompted. It writes an encrypted SealedSecret to
`apps/frontend-website/ghcr-sealed-secret.yaml` and registers it in that
application's Kustomization. The token is not displayed or written to Git.
Commit and push the generated encrypted manifest and Kustomization change so
Argo CD can apply it. Use `--rotate` with a new token to replace the manifest.

Back up the Sealed Secrets controller's private-key Secret as part of the
cluster backup; losing that key prevents decrypting existing SealedSecrets.
Revoke any previously exposed GHCR tokens.

## Hermes Workspace

The `hermes-workspace` Argo CD application deploys Hermes Workspace with its
Hermes Agent gateway in the same pod. The UI is available at
`https://hermes.ergoshear.dev`; only the UI port is exposed through the
Traefik ingress. The dashboard binds to loopback and is reachable by the
workspace UI proxy within the pod; the agent API and dashboard are not exposed
through ingress. The UI and agent share a persistent 10 GiB claim for agent
configuration, sessions,
and memory; workspace files use a separate 10 GiB claim. Both use the
cluster's `local-path` storage class.

Before syncing the application, generate the required credentials and load the
`hermes-workspace-secrets` Secret into the cluster:

```sh
uv run python scripts/create_hermes_workspace_secret.py
```

The script uses `~/.kube/k3s.yaml` by default; pass `--kubeconfig` to use
another file. It prints the generated UI password once and does not print the
gateway API token. It refuses to overwrite an existing Secret unless run with
`--rotate`; rotation preserves any provider keys already in the Secret and
requires restarting the Deployment.

The initial Hermes config is copied into the agent data claim only when no
config exists. It selects `Qwen3-Coder-30B-A3B` from the named
`mlops-node-7` provider by default. Hermes discovers available model IDs from
both endpoints' `/v1/models` catalogs:

| Provider | In-cluster API | Tailscale target |
|----------|----------------|------------------|
| `mlops-node-7` | `http://mlops-node-7.hermes-workspace.svc.cluster.local:8080/v1` | `mlops-node-7.tailb11f97.ts.net:8080` |
| `mlops-node-9` | `http://mlops-node-9.hermes-workspace.svc.cluster.local:8080/v1` | `mlops-node-9.tailb11f97.ts.net:8080` |

Use `/model custom:mlops-node-7:<model-id>` or
`/model custom:mlops-node-9:<model-id>` in a chat to select a discovered model.
Existing configs using the direct Tailscale URL are migrated and registered as
named providers during pod initialization. If either endpoint requires
authentication, add an `OPENAI_API_KEY` entry to the Secret. Other optional
provider keys are
`ANTHROPIC_API_KEY`, `NOUS_API_KEY`, `OPENROUTER_API_KEY`, `GOOGLE_API_KEY`,
`GOOGLE_AI_STUDIO_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, and
`MINIMAX_API_KEY`.

### Tailscale egress

The `tailscale-operator` Argo CD application installs the pinned Tailscale
Kubernetes Operator chart, and `tailscale-egress` creates a two-replica egress
ProxyGroup and model endpoint Services. Before syncing these applications:

1. In Tailscale access controls, configure `tag:k8s-operator` as an owner of
   `tag:k8s`.
2. Create an OAuth client with write access to `General/Services`,
   `Devices/Core`, and `Keys/Auth Keys`, scoped to `tag:k8s-operator`.
3. Allow `tag:k8s` to reach `mlops-node-4`, `mlops-node-6`, `mlops-node-7`,
   and `mlops-node-9` on TCP port 8080 in the tailnet access policy.
4. Run `uv run python scripts/create_tailscale_operator_secret.py` to securely
   prompt for the OAuth client ID and secret and store them in the
   `tailscale/operator-oauth` Kubernetes Secret. The script creates the
   namespace if needed and never prints the credentials. Use `--rotate` to
   replace credentials already stored in the Secret.
5. Sync the Argo CD applications. The operator chart creates the CRDs used by
   the egress application.

The endpoint uses an in-cluster Service name, so cluster DNS does not need
MagicDNS forwarding or a `DNSConfig` resource.

## Lens

The [Lens Application](argocd/applications/lens.yaml) deploys the standalone
Lens UI and API at `https://lens.ergoshear.dev/ui/`, with ClickHouse and Keeper
sharing a retained 20Gi local-path volume. The upstream chart and custom image
are pinned to the same source commit in [the values](apps/lens/values.yaml);
the deployed image additionally uses its immutable registry digest.
The custom Lens image targets amd64 nodes. ClickHouse is not publicly exposed.
The private GHCR image requires the separately provisioned `lens-ghcr`
image-pull Secret in the `lens` namespace. Keep it outside Git and refresh it
when its GitHub credential expires or is rotated; prefer a dedicated credential
with only `read:packages` access for routine pulls.

Argo CD preserves the chart-generated `lens-admin` and `lens-clickhouse`
credentials during syncs. Retrieve the `admin-token` key from the `lens-admin`
Secret in the `lens` namespace using a private terminal to sign in; never
commit or share it. Back up both these credentials and the entire ClickHouse
volume, including Keeper state, together. Local-path storage does not provide
automatic cross-node failover.

The pinned LiteLLM 1.106.0 image exposes the older Lens connection contract.
This independent Lens deployment remains standalone until a compatible gateway
release is selected and tested. It does not change LiteLLM's SSO, model routing,
or automatically instrument agents. Create a Lens tracing key and configure an
agent exporter to send a first trace to the public Lens endpoint.

To rebuild the image, check out the exact Lens source revision from the
Application, build `deploy/runtime/Dockerfile` with `LENS_VERSION` set to that
revision, and publish it to the configured image repository. Do not substitute
a moving development tag or upgrade the chart without checking storage and
protocol compatibility.

## LiteLLM SSO

The [Helm values](apps/litellm/values.yaml) persist AWS IAM Identity Center
SAML settings as environment variables, independent of settings saved in the
LiteLLM admin UI. The application uses
`https://litellm.ergoshear.dev/sso/saml/metadata` as its SP entity ID and
`https://litellm.ergoshear.dev/sso/saml/callback` as its ACS URL. Configure the
Identity Center application to send the user's email as the SAML `email`
attribute (and preferably the subject/NameID). LiteLLM uses that email as the
user ID and grants explicit proxy-admin access to `christopher@agilityqa.com`
through `PROXY_ADMIN_ID`; other users are not granted admin by this setting.
The running LiteLLM image must include SAML support.

The [Argo CD Application](argocd/applications/litellm.yaml) preserves the existing
`litellm-masterkey` Secret's `data.masterkey` during sync. Helm's offline rendering
cannot look up the existing Secret and otherwise generates a new random key.
Changing that key makes previously encrypted database settings unreadable,
including UI-saved SSO settings. Back up the key securely alongside the database;
do not delete this Secret or rotate it without a recovery/migration plan.

## LiteLLM model discovery

LiteLLM explicitly registers the 17 llama.cpp router presets managed by Salt
in [the Helm values](apps/litellm/values.yaml), routing requests through the
Tailscale egress Services. Its `/v1/models` list and admin UI use concrete IDs
with the server prefixes `mlops-node-4/`, `mlops-node-6/`, `mlops-node-7/`, and
`mlops-node-9/`, rather than wildcard entries. The prefix selects the server;
the remaining ID exactly matches its Salt `models.ini` preset. Update these
entries and their tests whenever Salt adds or renames a preset. Listing models
does not load them; llama.cpp loads the selected preset when it is requested.

The same configuration adds five semantic groups: `ergoshear-fast-edit`,
`ergoshear-coder`, `ergoshear-architect`, `ergoshear-vision`, and
`ergoshear-long-context`. The router uses the `least-busy` strategy. Fast-edit
assigns a 2:1 weight to node 4 versus node 7; other groups prefer the lowest
eligible `order`. Group deployments carry the same capability and token
metadata as their direct presets. These limits describe the supplied
configuration, not independently measured model capabilities. The gateway
routes through in-cluster `hermes-workspace` Services and authenticates with
`INFERENCE_BACKEND_KEY`.

Short aliases are `fast`, `coder`, `architect`, `vision`, `long-context`, and
`default` (coder). Hidden compatibility aliases `gpt-4o`, `gpt-4o-mini`, `o3-mini`,
and `claude-3-7-sonnet-latest` select local groups, not the named hosted models.
The router enables pre-call checks, two retries, 300-second timeouts,
15-second cooldowns, and the configured cross-group fallbacks. Parameter
dropping/modification is enabled for compatibility; unsupported options may be
removed rather than rejected. Model metadata declares output budgets explicitly
with `max_output_tokens`, alongside the supplied `max_tokens` and input limits.
All routes retain the Tailscale Service addresses and existing master key;
LAN-only addresses and the attachment's alternate master-key variable are not
used.

The workspace image is pinned to upstream commit `c631425` and the agent to
`v0.22.2`. The Deployment uses `Recreate` to avoid attaching local-path claims
to overlapping pods during updates. Deleting either PVC deletes its
corresponding persistent data.

The Argo CD install overlay pins Dex to `v2.46.0` and Redis to
`8.2.10-alpine`, replacing images with critical findings in the upstream
manifest.

ExternalDNS publishes `argocd.ergoshear.dev` as a CNAME to
`k3s.ergoshear.dev`; cert-manager obtains a publicly trusted Let's Encrypt
certificate using Route 53 DNS-01. The ingress uses the
`external-dns.kubernetes.io/target` annotation supported by ExternalDNS v0.23.
The frontend ingress is for the zone apex, so its target is the k3s Elastic IP
(`18.189.255.125`) to create an A record; Route 53 cannot create a CNAME at the
apex. Update this annotation if Terraform replaces that Elastic IP.
ExternalDNS uses `sync` with its TXT ownership registry to remove obsolete
owned records, including when a record changes from A to CNAME; it does not
manage Terraform-owned records. This requires the `ergoshear.dev` Route 53
zone, AWS Route 53 permissions on the k3s instance role, IMDSv2 access from
pods, and inbound TCP ports 80 and 443 on the k3s instance. Apply the AWS
Terraform changes before expecting DNS updates or certificate issuance.

The AWS changes create a dedicated DNS-enabled instance profile for
`k3s.ergoshear.dev`; only that host's role receives Route 53 access, scoped to
the `ergoshear.dev` zone. The profile retains the SSM managed-instance policy.
The cert-manager release is pinned in `argocd/cert-manager/kustomization.yaml`.

The runner must have `kubectl` installed and passwordless `sudo` permission to
run it. The Argo CD, cert-manager, ExternalDNS, and Traefik workloads tolerate
the control-plane `NoSchedule` taint.

## AWS IAM Identity Center SSO

Argo CD uses Dex's SAML connector with the customer-managed Identity Center
application in `us-east-2`. Select **Log in via AWS IAM Identity Center** at
`https://argocd.ergoshear.dev` to start login. Start from Argo CD rather than
an unsolicited Identity Center application launch: Dex requires a matching
authentication request.

The AWS application's ACS URL and audience must both be
`https://argocd.ergoshear.dev/api/dex/callback`. Attribute mappings are:

| Attribute | Identity Center value | Format |
|-----------|-----------------------|--------|
| `Subject` | `${user:email}` | `emailAddress` |
| `email` | `${user:email}` | `unspecified` |

The user must be assigned to the application in Identity Center.
`christopher@agilityqa.com` receives Argo CD's built-in administrator role.
Other SSO users receive no permissions unless explicitly added to
[the RBAC patch](argocd/argo-cd/rbac-patch.yaml). Group claims are not assumed.
Anonymous access is disabled; local administrator login remains enabled
as a break-glass option until an actual SSO login and permissions are verified.

The public signing certificate was obtained from the application's
[AWS metadata endpoint](https://portal.sso.us-east-2.amazonaws.com/saml/metadata/MjA0NDU5MDM4Njc2X2lucy02Njg0ZDFkYTU5MDBhYmNj).
It expires on **2031-10-19 at 15:19:43 UTC**. Kustomize generates a hashed
ConfigMap and mounts the certificate read-only into Dex; changing
[the certificate](argocd/argo-cd/identity-center-signing.pem) rolls the Dex pod.
Signature checking, IdP issuer, callback URL, audience, and email NameID format
are explicitly configured. No AWS API credentials or private signing keys
are needed in the cluster or repository.

Argo CD logout does not terminate the IAM Identity Center browser session.
Dex does not provide SAML single logout, so the supplied Identity Center
logout URL is intentionally not wired into an unsupported connector setting.

**Maintenance warning:** [Dex documents its SAML connector as unmaintained
and potentially vulnerable](https://dexidp.io/docs/connectors/saml/).
This direct integration follows Argo CD's Identity Center guide but retains
that upstream limitation. A maintained SAML-to-OIDC broker, or a direct
OIDC identity provider, is preferable for a higher-assurance deployment.

Install [uv](https://docs.astral.sh/uv/), [just](https://just.systems/), and
the required system tools (`kubectl` and OpenSSL), then run the test suite:

```sh
uv sync --locked
just test
just install-hooks
```

The `.python-version` file selects Python 3.12; `uv` installs it automatically.
`just test` runs the full Python test suite in the project environment.
`just install-hooks` enables the pre-commit hook, which runs `just test`
before each commit.

Pull requests run these tests on a GitHub-hosted runner. Only pushes to
`main` deploy, and deployment waits for the Argo CD server and Dex rollouts.
An actual user login still needs to be tested interactively.

## Private management and inference boundaries

Argo CD, Hermes and Lens attach a same-namespace `tailscale-only` Traefik
middleware. LiteLLM deliberately does not; its public API still requires
its existing master/virtual-key authentication. Management DNS targets the
control-plane tailnet IPv4 address, preserving original TLS and SSO names.
DNS-01 certificate renewal stays independent of HTTP management restrictions.
Argo CD also fetches Dex signing keys through its issuer hostname when
validating SSO sessions. Only the exact `/api/dex/keys` path additionally
allows the cluster pod CIDR `10.42.0.0/16`; the UI, callbacks and other Dex
paths remain tailnet-only. Public requests to the keys endpoint remain
blocked. Update this dedicated allowlist if the cluster pod CIDR changes.
If recursive DNS still caches the former public CNAME, test with
`curl --resolve argocd.ergoshear.dev:443:100.126.105.69 https://argocd.ergoshear.dev/`
and repeat for Hermes/Lens. Update these DNS targets if the tailnet node is
replaced.

Hermes, Lens and monitoring default-deny pod ingress. Host-networked Traefik
is allowed from control-plane addresses `100.126.105.69/32` (Tailscale) and
`10.42.0.0/32` (Flannel, the actual cross-node source) to Hermes port 3000
and Lens port 4318 only; update those policy peers if those addresses change.
Lens also accepts telemetry from the LiteLLM and Hermes workload selectors.
Only Lens pods can reach ClickHouse on port 8123. Monitoring permits
intra-namespace traffic and those control-plane addresses for admission
requests to the operator on port 10250. Its host-networked node exporters still depend on host
firewall rules, not pod NetworkPolicy enforcement. Egress remains unrestricted
to preserve DNS, metrics scraping and external agent integrations.
The Lens and monitoring values sources also render their policy Kustomizations,
so the policies are reconciled by their existing Argo CD Applications.

The installed ServiceLB path source-NATs clients to a pod gateway, even with
`externalTrafficPolicy: Local`; accepting that address would expose management
publicly. Traefik therefore runs host-networked on the existing control-plane
node, binds 80/443 directly and uses a ClusterIP Service (no ingress NodePorts
or ServiceLB). Recreate updates briefly interrupt ingress. It runs UID 0
with **only** `NET_BIND_SERVICE`, all other capabilities dropped, no privilege
escalation and a read-only root filesystem; the verified nonroot variant
could not bind low host ports on this Fedora/k3s host. Metrics uses 19100
to avoid the node-exporter host port. Keep the control-plane host/cloud
firewalls limited to the existing public 80/443 and home-only 6443 rules.
Ingress status explicitly publishes the control-plane tailnet IP rather than
copying an empty ClusterIP Service status. Explicit ExternalDNS target
annotations retain private management DNS and public LiteLLM DNS.

The two authorized inference clients use `inference-backend` Secrets with
`INFERENCE_BACKEND_KEY` in their respective namespaces. LiteLLM model keys
reference that environment variable. Hermes migrates its existing private
configuration during initialization; it does not change unrelated providers.
GPU hosts require the same key file and a persistent dual-stack nftables
boundary managed by Salt. The egress-proxy ingress NetworkPolicy allows only
the actual LiteLLM/Hermes pod labels and monitoring health traffic. Its client
rule intentionally has no fixed port: the operator maps backend port 8080 to
dynamically allocated proxy targetPorts.

Publish changes before reconciling the root `argocd` Application and restoring
automatic sync. Secrets stay in Kubernetes/private GPU files; preserve them
during rollback.
