#!/usr/bin/env python3
"""Encrypt GHCR credentials into a SealedSecret for the frontend website."""

import argparse
import base64
import getpass
import json
from pathlib import Path
import shutil
import subprocess


NAMESPACE = "frontend-website"
SECRET_NAME = "ghcr-registry-credentials"
CONTROLLER_NAME = "sealed-secrets-controller"
CONTROLLER_NAMESPACE = "kube-system"
SEALED_SECRET_FILE = "ghcr-sealed-secret.yaml"


def register_sealed_secret(kustomization_path):
    contents = kustomization_path.read_text()
    resource_line = f"  - {SEALED_SECRET_FILE}"
    if resource_line in contents.splitlines():
        return
    marker = "resources:\n"
    if marker not in contents:
        raise SystemExit(
            f"Could not find a resources section in {kustomization_path}."
        )
    kustomization_path.write_text(
        contents.replace(marker, f"{marker}{resource_line}\n", 1)
    )


def docker_config_data(username, token):
    auth = base64.b64encode(f"{username}:{token}".encode("utf-8")).decode("ascii")
    docker_config = json.dumps(
        {
            "auths": {
                "ghcr.io": {
                    "username": username,
                    "password": token,
                    "auth": auth,
                }
            }
        },
        separators=(",", ":"),
    )
    return base64.b64encode(docker_config.encode("utf-8")).decode("ascii")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kubeconfig",
        type=Path,
        default=Path.home() / ".kube/k3s.yaml",
        help="Kubernetes config file (default: ~/.kube/k3s.yaml)",
    )
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="Replace the existing encrypted manifest with new GHCR credentials.",
    )
    args = parser.parse_args(argv)
    kubeconfig = args.kubeconfig.expanduser()
    if not kubeconfig.is_file():
        raise SystemExit(f"Kubeconfig does not exist: {kubeconfig}")
    if shutil.which("kubeseal") is None:
        raise SystemExit(
            "kubeseal was not found on PATH. Install kubeseal and sync the "
            "sealed-secrets Argo CD application before generating this manifest."
        )

    app_directory = Path(__file__).resolve().parents[1] / "apps" / NAMESPACE
    output_path = app_directory / SEALED_SECRET_FILE
    kustomization_path = app_directory / "kustomization.yaml"
    if output_path.exists() and not args.rotate:
        raise SystemExit(
            f"{output_path} already exists; leaving it unchanged. "
            "Use --rotate to replace it."
        )

    username = input("GHCR username: ").strip()
    token = getpass.getpass("GHCR classic PAT with read:packages: ")
    if not username or not token:
        raise SystemExit("Both the GHCR username and token are required.")

    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": SECRET_NAME, "namespace": NAMESPACE},
        "type": "kubernetes.io/dockerconfigjson",
        "data": {".dockerconfigjson": docker_config_data(username, token)},
    }
    command = [
        "kubeseal",
        "--format",
        "yaml",
        "--scope",
        "strict",
        "--controller-name",
        CONTROLLER_NAME,
        "--controller-namespace",
        CONTROLLER_NAMESPACE,
        "--kubeconfig",
        str(kubeconfig),
    ]
    try:
        result = subprocess.run(
            command,
            input=json.dumps(secret),
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise SystemExit("kubeseal was not found on PATH.") from error
    if result.returncode:
        detail = result.stderr.strip()
        if detail:
            raise SystemExit(f"kubeseal failed:\n{detail}")
        raise SystemExit(f"kubeseal failed with exit code {result.returncode}.")
    if not result.stdout.strip():
        raise SystemExit("kubeseal returned an empty SealedSecret manifest.")

    output_path.write_text(result.stdout)
    output_path.chmod(0o600)
    register_sealed_secret(kustomization_path)
    print(f"Encrypted manifest written to {output_path}.")
    print("Commit and push the encrypted manifest and Kustomization change for Argo CD.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
