# k3s

## Argo CD deployment

The `Apply Argo CD` GitHub Actions workflow runs on pushes to `main` (including
merges) on the self-hosted runner labeled `k3s-control-plane-01`. It uses the
host's `/etc/rancher/k3s/k3s.yaml` kubeconfig and applies `argocd/` with
server-side apply and conflict forcing. The runner must have `kubectl` installed
and passwordless `sudo` permission to run it.