#!/usr/bin/env python3
"""Prompt for GHCR credentials and apply the frontend image-pull Secret."""

import argparse
import base64
import getpass
import json
from pathlib import Path
import subprocess
import sys


NAMESPACE = "frontend-website"
SECRET_NAME = "ghcr-registry-credentials"
FIELD_MANAGER = "frontend-website-ghcr-secret-setup"


def run_kubectl(kubeconfig, *arguments, input_text=None):
    command = ["kubectl", "--kubeconfig", str(kubeconfig), *arguments]
    try:
        result = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise SystemExit("kubectl was not found on PATH.") from error

    if result.returncode:
        detail = result.stderr.strip()
        if detail:
            raise SystemExit(f"kubectl {' '.join(arguments)} failed:\n{detail}")
        raise SystemExit(
            f"kubectl {' '.join(arguments)} failed with exit code {result.returncode}."
        )
    return result


def encode_docker_config(username, token):
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
        help="Replace the existing GHCR credentials.",
    )
    args = parser.parse_args(argv)
    kubeconfig = args.kubeconfig.expanduser()
    if not kubeconfig.is_file():
        raise SystemExit(f"Kubeconfig does not exist: {kubeconfig}")

    namespace = run_kubectl(
        kubeconfig,
        "get",
        "namespace",
        NAMESPACE,
        "--ignore-not-found",
        "-o",
        "name",
    )
    if not namespace.stdout.strip():
        raise SystemExit(
            f"Namespace {NAMESPACE} does not exist; sync the frontend-website "
            "Argo CD application before creating its registry Secret."
        )

    current_secret = run_kubectl(
        kubeconfig,
        "-n",
        NAMESPACE,
        "get",
        "secret",
        SECRET_NAME,
        "--ignore-not-found",
        "-o",
        "json",
    )
    if current_secret.stdout.strip() and not args.rotate:
        raise SystemExit(
            f"Secret {NAMESPACE}/{SECRET_NAME} already exists; leaving it unchanged. "
            "Use --rotate to replace its GHCR credentials."
        )

    username = input("GHCR username: ").strip()
    token = getpass.getpass("GHCR token with read:packages access: ")
    if not username or not token:
        raise SystemExit("Both the GHCR username and token are required.")

    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": SECRET_NAME, "namespace": NAMESPACE},
        "type": "kubernetes.io/dockerconfigjson",
        "data": {".dockerconfigjson": encode_docker_config(username, token)},
    }
    applied_secret = run_kubectl(
        kubeconfig,
        "apply",
        "--server-side",
        "--force-conflicts",
        f"--field-manager={FIELD_MANAGER}",
        "-f",
        "-",
        input_text=json.dumps(secret),
    )
    if applied_secret.stdout:
        print(applied_secret.stdout, end="")
    if applied_secret.stderr:
        print(applied_secret.stderr, end="", file=sys.stderr)
    print(f"GHCR pull Secret {NAMESPACE}/{SECRET_NAME} was applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
