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

from scripts import create_frontend_website_ghcr_secret as secret_script


def completed(stdout=""):
    return secret_script.subprocess.CompletedProcess(
        args=["kubectl"], returncode=0, stdout=stdout, stderr=""
    )


class FrontendWebsiteGhcrSecretTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.kubeconfig = Path(self.tempdir.name) / "k3s.yaml"
        self.kubeconfig.touch()

    def test_prompts_and_applies_secret_without_printing_token(self):
        results = [
            completed("namespace/frontend-website\n"),
            completed(),
            completed("secret/ghcr-registry-credentials configured\n"),
        ]
        with (
            patch.object(
                secret_script.subprocess, "run", side_effect=results
            ) as run,
            patch("builtins.input", return_value="gh-user"),
            patch.object(
                secret_script.getpass, "getpass", return_value="gh-token"
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = secret_script.main(["--kubeconfig", str(self.kubeconfig)])

        self.assertEqual(result, 0)
        self.assertNotIn("gh-token", output.getvalue())
        apply_call = run.call_args_list[-1]
        self.assertNotIn("gh-token", " ".join(apply_call.args[0]))
        self.assertIn("--force-conflicts", apply_call.args[0])
        document = json.loads(apply_call.kwargs["input"])
        self.assertEqual(document["metadata"]["namespace"], "frontend-website")
        self.assertEqual(document["type"], "kubernetes.io/dockerconfigjson")
        docker_config = json.loads(
            base64.b64decode(document["data"][".dockerconfigjson"])
        )
        self.assertEqual(
            docker_config["auths"]["ghcr.io"]["username"],
            "gh-user",
        )
        self.assertEqual(
            docker_config["auths"]["ghcr.io"]["password"],
            "gh-token",
        )

    def test_existing_secret_is_not_overwritten_without_rotate(self):
        with (
            patch.object(
                secret_script.subprocess,
                "run",
                side_effect=[
                    completed("namespace/frontend-website\n"),
                    completed('{"metadata":{"name":"ghcr-registry-credentials"}}'),
                ],
            ) as run,
            patch("builtins.input") as prompt,
            self.assertRaisesRegex(SystemExit, "already exists"),
        ):
            secret_script.main(["--kubeconfig", str(self.kubeconfig)])

        self.assertEqual(run.call_count, 2)
        prompt.assert_not_called()

    def test_missing_namespace_stops_before_prompting_for_credentials(self):
        with (
            patch.object(
                secret_script.subprocess, "run", return_value=completed()
            ) as run,
            patch("builtins.input") as prompt,
            self.assertRaisesRegex(SystemExit, "does not exist"),
        ):
            secret_script.main(["--kubeconfig", str(self.kubeconfig)])

        run.assert_called_once()
        prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
