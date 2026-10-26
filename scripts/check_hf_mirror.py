# /// script
# requires-python = ">=3.12"
# dependencies = ["huggingface-hub>=0.36,<2"]
# ///

import hashlib
import os
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen


os.environ.setdefault("HF_ENDPOINT", "https://hf.ergoshear.dev")
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"

from huggingface_hub import hf_hub_download


REPO = "hf-internal-testing/tiny-random-gpt2"
FILES = ("config.json", "model.safetensors")


def main():
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
