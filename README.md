# k3s

## Argo CD deployment

The `Apply Argo CD` GitHub Actions workflow runs on pushes to `main` (including
merges) on the self-hosted runner labeled `k3s-control-plane-01`. It uses the
host's `/etc/rancher/k3s/k3s.yaml` kubeconfig. It applies the upstream CRDs
separately, creates the local namespace, installs Argo CD and cert-manager, and
waits for cert-manager before applying [the Argo CD Application](argocd/application.yaml).
That Application syncs `argocd/` from this repository with pruning and self-healing
enabled, managing the platform resources as well as the checked-in install
manifests.

ExternalDNS publishes `argocd.ergoshear.dev` as a CNAME to
`k3s.ergoshear.dev`; cert-manager obtains a publicly trusted Let's Encrypt
certificate using Route 53 DNS-01. The ingress uses the
`external-dns.kubernetes.io/target` annotation supported by ExternalDNS v0.23.
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