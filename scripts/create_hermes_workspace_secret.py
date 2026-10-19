#!/usr/bin/env python3
"""Generate and apply the Hermes Workspace credentials to the K3s cluster."""

import argparse
import base64
import json
from pathlib import Path
import secrets
import subprocess
import sys


NAMESPACE = "hermes-workspace"
SECRET_NAME = "hermes-workspace-secrets"
FIELD_MANAGER = "hermes-workspace-secret-setup"


def run_kubectl(kubeconfig, *arguments, input_text=None):
    command = [
        "kubectl",
        "--kubeconfig",
        str(kubeconfig),
        *arguments,
    ]
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


def server_side_apply(kubeconfig, document):
    return run_kubectl(
        kubeconfig,
        "apply",
        "--server-side",
        f"--field-manager={FIELD_MANAGER}",
        "-f",
        "-",
        input_text=json.dumps(document),
    )


def encode_secret_value(value):
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


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
        help=(
            "Replace the generated password and API token. Existing provider "
            "keys are preserved; restart the Deployment afterward."
        ),
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
        applied_namespace = server_side_apply(
            kubeconfig,
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": NAMESPACE},
            },
        )
        if applied_namespace.stdout:
            print(applied_namespace.stdout, end="")
        if applied_namespace.stderr:
            print(applied_namespace.stderr, end="", file=sys.stderr)

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
    existing = json.loads(current_secret.stdout) if current_secret.stdout.strip() else None
    if existing and not args.rotate:
        raise SystemExit(
            f"Secret {NAMESPACE}/{SECRET_NAME} already exists; leaving it unchanged. "
            "Use --rotate to generate new required credentials."
        )

    password = secrets.token_urlsafe(32)
    api_server_key = secrets.token_urlsafe(32)
    data = dict(existing.get("data", {})) if existing else {}
    data.update(
        {
            "HERMES_PASSWORD": encode_secret_value(password),
            "API_SERVER_KEY": encode_secret_value(api_server_key),
        }
    )
    metadata = {
        "name": SECRET_NAME,
        "namespace": NAMESPACE,
    }
    if existing:
        existing_metadata = existing.get("metadata", {})
        if "labels" in existing_metadata:
            metadata["labels"] = existing_metadata["labels"]
        annotations = {
            key: value
            for key, value in existing_metadata.get("annotations", {}).items()
            if key != "kubectl.kubernetes.io/last-applied-configuration"
        }
        if annotations:
            metadata["annotations"] = annotations
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": metadata,
        "type": existing.get("type", "Opaque") if existing else "Opaque",
        "data": data,
    }
    applied_secret = server_side_apply(kubeconfig, secret)
    if applied_secret.stdout:
        print(applied_secret.stdout, end="")
    if applied_secret.stderr:
        print(applied_secret.stderr, end="", file=sys.stderr)

    print("Workspace login password (save it now; it is displayed only once):")
    print(password)
    print("API_SERVER_KEY was generated and stored in the cluster; it is not displayed.")
    if args.rotate:
        print(
            "Credentials were rotated. Restart the Deployment with "
            "`kubectl -n hermes-workspace rollout restart deploy/hermes-workspace`."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
