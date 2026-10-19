import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import create_tailscale_operator_secret as secret_script


def completed(stdout=""):
    return secret_script.subprocess.CompletedProcess(
        args=["kubectl"], returncode=0, stdout=stdout, stderr=""
    )


class TailscaleOperatorSecretTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.kubeconfig = Path(self.tempdir.name) / "k3s.yaml"
        self.kubeconfig.touch()

    def test_generates_secret_without_putting_credentials_in_args_or_output(self):
        results = [
            completed(),
            completed("namespace/tailscale created\n"),
            completed(),
            completed("secret/operator-oauth created\n"),
        ]
        with (
            patch.object(secret_script.subprocess, "run", side_effect=results) as run,
            patch.object(
                secret_script.getpass,
                "getpass",
                side_effect=["client-id", "oauth-client-secret"],
            ),
        ):
            result = secret_script.main(["--kubeconfig", str(self.kubeconfig)])

        self.assertEqual(result, 0)
        self.assertNotIn("oauth-client-secret", " ".join(run.call_args.args[0]))
        self.assertNotIn("oauth-client-secret", run.call_args.kwargs["input"])
        document = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(document["kind"], "Secret")
        self.assertEqual(document["metadata"]["namespace"], "tailscale")
        self.assertEqual(
            base64.b64decode(document["data"]["client_id"]).decode(),
            "client-id",
        )
        self.assertEqual(
            base64.b64decode(document["data"]["client_secret"]).decode(),
            "oauth-client-secret",
        )

    def test_existing_secret_is_not_overwritten_without_rotate(self):
        with (
            patch.object(
                secret_script.subprocess,
                "run",
                side_effect=[
                    completed("namespace/tailscale\n"),
                    completed('{"metadata":{"name":"operator-oauth"}}'),
                ],
            ) as run,
            patch.object(secret_script.getpass, "getpass") as prompt,
            self.assertRaisesRegex(SystemExit, "already exists"),
        ):
            secret_script.main(["--kubeconfig", str(self.kubeconfig)])

        self.assertEqual(run.call_count, 2)
        prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
