#!/usr/bin/env python3
"""Encrypt GHCR credentials into a namespace-bound SealedSecret."""

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
    parser.add_argument(
        "--namespace",
        default=NAMESPACE,
        choices=[NAMESPACE, "ghcr-registry"],
        help="Secret namespace and application directory (default: frontend-website).",
    )
    parser.add_argument(
        "--from-secret",
        metavar="NAMESPACE/NAME",
        help="Seal an existing cluster Secret instead of prompting for credentials.",
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

    app_directory = Path(__file__).resolve().parents[1] / "apps" / args.namespace
    output_path = app_directory / SEALED_SECRET_FILE
    kustomization_path = app_directory / "kustomization.yaml"
    if output_path.exists() and not args.rotate:
        raise SystemExit(
            f"{output_path} already exists; leaving it unchanged. "
            "Use --rotate to replace it."
        )

    if args.from_secret:
        parts = args.from_secret.split("/")
        if len(parts) != 2 or not all(parts):
            raise SystemExit("--from-secret must be NAMESPACE/NAME.")
        if shutil.which("kubectl") is None:
            raise SystemExit("kubectl was not found on PATH.")
        source = subprocess.run(
            [
                "kubectl", "--kubeconfig", str(kubeconfig),
                "--namespace", parts[0], "get", "secret", parts[1], "-o", "json",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if source.returncode:
            raise SystemExit(
                f"Could not read the source Secret (exit code {source.returncode})."
            )
        try:
            source_secret = json.loads(source.stdout)
            config_data = source_secret["data"][".dockerconfigjson"]
            config = json.loads(base64.b64decode(config_data, validate=True))
            if (
                source_secret["type"] != "kubernetes.io/dockerconfigjson"
                or not config.get("auths", {}).get("ghcr.io")
            ):
                raise ValueError("Missing GHCR Docker credentials")
        except (ValueError, KeyError, TypeError, AttributeError):
            raise SystemExit(
                "The source must be a Docker config Secret containing ghcr.io credentials."
            ) from None
    else:
        username = input("GHCR username: ").strip()
        token = getpass.getpass("GHCR classic PAT with read:packages: ")
        if not username or not token:
            raise SystemExit("Both the GHCR username and token are required.")
        config_data = docker_config_data(username, token)

    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": SECRET_NAME, "namespace": args.namespace},
        "type": "kubernetes.io/dockerconfigjson",
        "data": {".dockerconfigjson": config_data},
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
