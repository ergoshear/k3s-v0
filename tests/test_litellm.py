import subprocess
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class LiteLLMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.values = yaml.safe_load((ROOT / "apps/litellm/values.yaml").read_text())

    def test_values_have_valid_litellm_structure(self):
        self.assertIsInstance(self.values, dict)

        proxy_config = self.values.get("proxy_config")
        self.assertIsInstance(proxy_config, dict)

        model_list = proxy_config.get("model_list")
        self.assertIsInstance(model_list, list)
        self.assertTrue(model_list)

        for entry in model_list:
            with self.subTest(model=entry.get("model_name") if isinstance(entry, dict) else None):
                self.assertIsInstance(entry, dict)
                if not isinstance(entry, dict):
                    continue
                self.assertIsInstance(entry.get("model_name"), str)
                self.assertTrue(entry["model_name"].strip())

                params = entry.get("litellm_params")
                self.assertIsInstance(params, dict)
                if not isinstance(params, dict):
                    continue
                for key in ("model", "api_base", "api_key"):
                    with self.subTest(parameter=key):
                        self.assertIsInstance(params.get(key), str)
                        self.assertTrue(params[key].strip())

                if "model_info" in entry:
                    self.assertIsInstance(entry["model_info"], dict)

    def test_application_manifests_render_as_valid_yaml(self):
        rendered = subprocess.check_output(
            ["kubectl", "kustomize", str(ROOT / "argocd/applications")],
            text=True,
        )
        resources = [resource for resource in yaml.safe_load_all(rendered) if resource]

        self.assertTrue(resources)
        for resource in resources:
            with self.subTest(kind=resource.get("kind") if isinstance(resource, dict) else None):
                self.assertIsInstance(resource, dict)
                if not isinstance(resource, dict):
                    continue
                for key in ("apiVersion", "kind", "metadata"):
                    self.assertIn(key, resource)
                self.assertIsInstance(resource["metadata"], dict)
