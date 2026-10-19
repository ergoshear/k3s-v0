import base64
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import create_hermes_workspace_secret as secret_script


def completed(stdout=""):
    return secret_script.subprocess.CompletedProcess(
        args=["kubectl"], returncode=0, stdout=stdout, stderr=""
    )


class HermesWorkspaceSecretTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.kubeconfig = Path(self.tempdir.name) / "k3s.yaml"
        self.kubeconfig.touch()

    def test_generates_and_applies_secrets_without_putting_values_in_args(self):
        results = [
            completed(),
            completed("namespace/hermes-workspace created\n"),
            completed(),
            completed("secret/hermes-workspace-secrets created\n"),
        ]
        with (
            patch.object(secret_script.subprocess, "run", side_effect=results) as run,
            patch.object(
                secret_script.secrets,
                "token_urlsafe",
                side_effect=["P" * 43, "A" * 43],
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = secret_script.main(["--kubeconfig", str(self.kubeconfig)])

        self.assertEqual(result, 0)
        self.assertIn("P" * 43, output.getvalue())
        self.assertNotIn("A" * 43, output.getvalue())
        secret_apply = run.call_args_list[-1]
        self.assertNotIn("P" * 43, " ".join(secret_apply.args[0]))
        self.assertNotIn("A" * 43, " ".join(secret_apply.args[0]))
        document = json.loads(secret_apply.kwargs["input"])
        self.assertEqual(document["kind"], "Secret")
        self.assertEqual(document["metadata"]["namespace"], "hermes-workspace")
        self.assertEqual(
            base64.b64decode(document["data"]["HERMES_PASSWORD"]).decode(),
            "P" * 43,
        )
        self.assertEqual(
            base64.b64decode(document["data"]["API_SERVER_KEY"]).decode(),
            "A" * 43,
        )

    def test_existing_secret_is_not_overwritten_without_rotate(self):
        existing = {
            "kind": "Secret",
            "metadata": {"name": "hermes-workspace-secrets"},
            "data": {
                "HERMES_PASSWORD": base64.b64encode(b"old-password").decode(),
                "API_SERVER_KEY": base64.b64encode(b"old-token").decode(),
            },
        }
        with (
            patch.object(
                secret_script.subprocess,
                "run",
                side_effect=[
                    completed("namespace/hermes-workspace\n"),
                    completed(json.dumps(existing)),
                ],
            ) as run,
            patch.object(secret_script.secrets, "token_urlsafe") as generate,
            self.assertRaisesRegex(SystemExit, "already exists"),
        ):
            secret_script.main(["--kubeconfig", str(self.kubeconfig)])

        self.assertEqual(run.call_count, 2)
        generate.assert_not_called()

    def test_rotation_preserves_existing_provider_keys(self):
        existing = {
            "kind": "Secret",
            "metadata": {
                "name": "hermes-workspace-secrets",
                "namespace": "hermes-workspace",
                "annotations": {
                    "kubectl.kubernetes.io/last-applied-configuration": (
                        '{"data":{"HERMES_PASSWORD":"old-password"}}'
                    )
                },
            },
            "type": "Opaque",
            "data": {
                "HERMES_PASSWORD": base64.b64encode(b"old-password").decode(),
                "API_SERVER_KEY": base64.b64encode(b"old-token").decode(),
                "OPENAI_API_KEY": base64.b64encode(b"provider-key").decode(),
            },
        }
        results = [
            completed("namespace/hermes-workspace\n"),
            completed(json.dumps(existing)),
            completed("secret/hermes-workspace-secrets configured\n"),
        ]
        with (
            patch.object(secret_script.subprocess, "run", side_effect=results) as run,
            patch.object(
                secret_script.secrets,
                "token_urlsafe",
                side_effect=["N" * 43, "T" * 43],
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            secret_script.main(["--kubeconfig", str(self.kubeconfig), "--rotate"])

        document = json.loads(run.call_args.kwargs["input"])
        self.assertNotIn("annotations", document["metadata"])
        self.assertEqual(
            base64.b64decode(document["data"]["OPENAI_API_KEY"]).decode(),
            "provider-key",
        )
        self.assertEqual(
            base64.b64decode(document["data"]["HERMES_PASSWORD"]).decode(),
            "N" * 43,
        )


if __name__ == "__main__":
    unittest.main()
