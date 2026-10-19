#!/usr/bin/env python3
"""Prompt for Tailscale OAuth credentials and apply the operator Secret."""

import argparse
import base64
import json
from pathlib import Path
import getpass
import subprocess
import sys


NAMESPACE = "tailscale"
SECRET_NAME = "operator-oauth"
FIELD_MANAGER = "tailscale-operator-secret-setup"


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
        help="Replace the OAuth client credentials in the existing Secret.",
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
    if current_secret.stdout.strip() and not args.rotate:
        raise SystemExit(
            f"Secret {NAMESPACE}/{SECRET_NAME} already exists; leaving it unchanged. "
            "Use --rotate to replace its OAuth credentials."
        )

    client_id = getpass.getpass("Tailscale OAuth client ID: ")
    client_secret = getpass.getpass("Tailscale OAuth client secret: ")
    if not client_id or not client_secret:
        raise SystemExit("Both Tailscale OAuth credentials are required.")

    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": SECRET_NAME, "namespace": NAMESPACE},
        "type": "Opaque",
        "data": {
            "client_id": encode_secret_value(client_id),
            "client_secret": encode_secret_value(client_secret),
        },
    }
    applied_secret = server_side_apply(kubeconfig, secret)
    if applied_secret.stdout:
        print(applied_secret.stdout, end="")
    if applied_secret.stderr:
        print(applied_secret.stderr, end="", file=sys.stderr)
    print("Tailscale operator OAuth credentials were stored in the cluster.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
