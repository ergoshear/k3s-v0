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

from scripts import seal_frontend_website_ghcr_secret as secret_script


SEALED_MANIFEST = """\
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: ghcr-registry-credentials
  namespace: frontend-website
spec:
  encryptedData:
    .dockerconfigjson: encrypted-value
  template:
    type: kubernetes.io/dockerconfigjson
"""


def completed(stdout="", returncode=0, stderr=""):
    return secret_script.subprocess.CompletedProcess(
        args=["kubeseal"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class FrontendWebsiteGhcrSecretTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.scripts_dir = self.root / "scripts"
        self.app_dir = self.root / "apps" / "frontend-website"
        self.scripts_dir.mkdir()
        self.app_dir.mkdir(parents=True)
        self.fake_script = self.scripts_dir / "seal_secret.py"
        self.fake_script.touch()
        self.kubeconfig = self.root / "k3s.yaml"
        self.kubeconfig.touch()
        self.kustomization = self.app_dir / "kustomization.yaml"
        self.kustomization.write_text(
            "apiVersion: kustomize.config.k8s.io/v1beta1\n"
            "kind: Kustomization\n\n"
            "resources:\n"
            "  - deployment.yaml\n"
        )

    def run_script(self, extra_args=()):
        with patch.object(secret_script, "__file__", str(self.fake_script)):
            return secret_script.main(
                ["--kubeconfig", str(self.kubeconfig), *extra_args]
            )

    def test_encrypts_credentials_without_writing_plaintext(self):
        with (
            patch.object(secret_script.shutil, "which", return_value="/bin/kubeseal"),
            patch.object(
                secret_script.subprocess,
                "run",
                return_value=completed(SEALED_MANIFEST),
            ) as run,
            patch("builtins.input", return_value="gh-user"),
            patch.object(
                secret_script.getpass, "getpass", return_value="gh-token"
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = self.run_script()

        self.assertEqual(result, 0)
        self.assertNotIn("gh-token", output.getvalue())
        command = run.call_args.args[0]
        self.assertEqual(command[0], "kubeseal")
        self.assertIn("--scope", command)
        self.assertEqual(command[command.index("--scope") + 1], "strict")
        self.assertNotIn("gh-token", " ".join(command))
        secret = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(secret["metadata"]["name"], "ghcr-registry-credentials")
        self.assertEqual(secret["metadata"]["namespace"], "frontend-website")
        self.assertEqual(secret["type"], "kubernetes.io/dockerconfigjson")
        docker_config = json.loads(
            base64.b64decode(secret["data"][".dockerconfigjson"])
        )
        self.assertEqual(
            docker_config["auths"]["ghcr.io"]["password"],
            "gh-token",
        )

        sealed_file = self.app_dir / secret_script.SEALED_SECRET_FILE
        self.assertEqual(sealed_file.read_text(), SEALED_MANIFEST)
        self.assertNotIn("gh-token", sealed_file.read_text())
        self.assertEqual(sealed_file.stat().st_mode & 0o777, 0o600)
        self.assertIn(
            f"  - {secret_script.SEALED_SECRET_FILE}",
            self.kustomization.read_text().splitlines(),
        )

    def test_existing_manifest_is_not_overwritten_without_rotate(self):
        sealed_file = self.app_dir / secret_script.SEALED_SECRET_FILE
        sealed_file.write_text(SEALED_MANIFEST)
        with (
            patch.object(secret_script.shutil, "which", return_value="/bin/kubeseal"),
            patch.object(secret_script.subprocess, "run") as run,
            patch("builtins.input") as prompt,
            self.assertRaisesRegex(SystemExit, "already exists"),
        ):
            self.run_script()

        run.assert_not_called()
        prompt.assert_not_called()

    def test_missing_kubeseal_stops_before_prompting(self):
        with (
            patch.object(secret_script.shutil, "which", return_value=None),
            patch.object(secret_script.subprocess, "run") as run,
            patch("builtins.input") as prompt,
            self.assertRaisesRegex(SystemExit, "kubeseal was not found"),
        ):
            self.run_script()

        run.assert_not_called()
        prompt.assert_not_called()

    def test_seals_existing_secret_for_cluster_wide_source_without_prompting(self):
        app_dir = self.root / "apps" / "ghcr-registry"
        app_dir.mkdir()
        (app_dir / "kustomization.yaml").write_text("resources:\n  - policies.yaml\n")
        data = secret_script.docker_config_data("gh-user", "gh-token")
        source = {
            "type": "kubernetes.io/dockerconfigjson",
            "data": {".dockerconfigjson": data},
        }
        with (
            patch.object(secret_script.shutil, "which", return_value="/bin/tool"),
            patch.object(
                secret_script.subprocess, "run",
                side_effect=[completed(json.dumps(source)), completed(SEALED_MANIFEST)],
            ) as run,
            patch("builtins.input") as prompt,
            patch.object(secret_script.getpass, "getpass") as password,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.run_script([
                "--namespace", "ghcr-registry",
                "--from-secret", "frontend-website/ghcr-registry-credentials",
            ])
        prompt.assert_not_called()
        password.assert_not_called()
        sealed_input = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(sealed_input["metadata"]["namespace"], "ghcr-registry")
        self.assertEqual(sealed_input["data"][".dockerconfigjson"], data)
        self.assertNotIn("gh-token", output.getvalue())
        self.assertTrue((app_dir / secret_script.SEALED_SECRET_FILE).is_file())

    def test_invalid_source_secret_fails_without_disclosing_contents(self):
        for source in [
            "not-json-gh-token",
            json.dumps({"data": {}, "type": "Opaque"}),
            json.dumps({
                "type": "kubernetes.io/dockerconfigjson",
                "data": {".dockerconfigjson": "invalid-base64-gh-token"},
            }),
        ]:
            with (
                self.subTest(source=source),
                patch.object(secret_script.shutil, "which", return_value="/bin/tool"),
                patch.object(
                    secret_script.subprocess, "run", return_value=completed(source)
                ) as run,
                self.assertRaisesRegex(SystemExit, "source must be a Docker config"),
            ):
                self.run_script(["--from-secret", "frontend-website/existing"])
            self.assertEqual(run.call_count, 1)

    def test_unreadable_source_secret_stops_before_sealing(self):
        with (
            patch.object(secret_script.shutil, "which", return_value="/bin/tool"),
            patch.object(
                secret_script.subprocess, "run",
                return_value=completed(returncode=1, stderr="source read failed"),
            ) as run,
            self.assertRaisesRegex(SystemExit, "Could not read the source Secret"),
        ):
            self.run_script(["--from-secret", "frontend-website/existing"])
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
