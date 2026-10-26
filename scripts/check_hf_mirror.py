# /// script
# requires-python = ">=3.12"
# dependencies = ["huggingface-hub>=0.36,<2"]
# ///

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.request import Request, urlopen


os.environ.setdefault("HF_ENDPOINT", "https://hf.ergoshear.dev")
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"

from huggingface_hub import hf_hub_download


REPO = "hf-internal-testing/tiny-random-gpt2"
FILES = ("config.json", "model.safetensors")


def check_admission(kubeconfig):
    kubectl = ["kubectl", "--kubeconfig", str(kubeconfig)]
    expected = {
        "HF_ENDPOINT": "http://hf-mirror.hf-mirror.svc.cluster.local:8080",
        "HF_HUB_DISABLE_XET": "1",
    }
    base = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"generateName": "hf-admission-check-", "namespace": "default"},
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {"name": "client", "image": "busybox:1.36"},
                {"name": "sidecar", "image": "busybox:1.36"},
            ],
            "initContainers": [{"name": "download", "image": "busybox:1.36"}],
        },
    }

    def admit(pod):
        output = subprocess.check_output(
            kubectl + ["create", "--dry-run=server", "-f", "-", "-o", "json"],
            input=json.dumps(pod),
            text=True,
        )
        return json.loads(output)["spec"]

    for mode in ("missing", "explicit", "valueFrom", "no-init"):
        pod = copy.deepcopy(base)
        for container in pod["spec"]["containers"] + pod["spec"]["initContainers"]:
            env = container.setdefault("env", [])
            for name in expected:
                if mode == "explicit":
                    env.append({"name": name, "value": "old"})
                elif mode == "valueFrom":
                    env.append({
                        "name": name,
                        "valueFrom": {"configMapKeyRef": {"name": "unused", "key": "old"}},
                    })
            env.append({"name": "UNRELATED", "value": "preserved"})
        if mode == "no-init":
            del pod["spec"]["initContainers"]
        spec = admit(pod)
        for container in spec["containers"] + spec.get("initContainers", []):
            env = {entry["name"]: entry for entry in container["env"]}
            for name, value in expected.items():
                if env.get(name) != {"name": name, "value": value}:
                    raise RuntimeError(f"admission {mode}: incorrect {name}")
            if env["UNRELATED"]["value"] != "preserved":
                raise RuntimeError(f"admission {mode}: unrelated variable changed")
        print(f"PASS admission {mode}: containers and init containers configured")
    for namespace in ("hf-mirror", "kube-system", "kyverno"):
        pod = copy.deepcopy(base)
        pod["metadata"]["namespace"] = namespace
        spec = admit(pod)
        for container in spec["containers"] + spec.get("initContainers", []):
            if expected.keys() & {entry["name"] for entry in container.get("env", [])}:
                raise RuntimeError(f"admission: {namespace} was not excluded")
        print(f"PASS admission exclusion: {namespace}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-admission", action="store_true")
    parser.add_argument("--kubeconfig", type=Path, default=Path.home() / ".kube/k3s.yaml")
    args = parser.parse_args()
    if args.check_admission:
        check_admission(args.kubeconfig)
    endpoint = os.environ["HF_ENDPOINT"].rstrip("/")
    with tempfile.TemporaryDirectory(prefix="hf-mirror-check-") as directory:
        for filename in FILES:
            results = []
            for client in ("first", "second"):
                path = hf_hub_download(
                    REPO,
                    filename,
                    cache_dir=Path(directory) / client,
                    token=False,
                )
                results.append(Path(path).read_bytes())
            if results[0] != results[1]:
                raise RuntimeError(f"{filename}: clean clients received different files")

            url = f"{endpoint}/{REPO}/resolve/main/{filename}"
            with urlopen(url, timeout=60) as response:
                if response.headers.get("X-Cache-Status") != "HIT":
                    raise RuntimeError(f"{filename}: expected a server-side cache HIT")
                if response.read() != results[0]:
                    raise RuntimeError(f"{filename}: cached response differs")

            with urlopen(Request(url, headers={"Range": "bytes=0-31"}), timeout=60) as response:
                if response.status != 206 or response.read() != results[0][:32]:
                    raise RuntimeError(f"{filename}: ranged download failed")
                expected = f"bytes 0-31/{len(results[0])}"
                if response.headers.get("Content-Range") != expected:
                    raise RuntimeError(f"{filename}: incorrect Content-Range")

            with urlopen(
                f"https://huggingface.co/{REPO}/resolve/main/{filename}", timeout=60
            ) as response:
                upstream = response.read()
            if hashlib.sha256(upstream).digest() != hashlib.sha256(results[0]).digest():
                raise RuntimeError(f"{filename}: mirror differs from upstream")
            print(
                f"PASS {filename}: two clean clients, cache HIT, range, upstream SHA256 "
                f"{hashlib.sha256(results[0]).hexdigest()}"
            )


if __name__ == "__main__":
    main()
