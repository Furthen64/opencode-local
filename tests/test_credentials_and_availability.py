import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout


MODULE_PATH = Path(__file__).parents[1] / "setup.py"
SPEC = importlib.util.spec_from_file_location("opencode_local_setup", MODULE_PATH)
setup = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(setup)


class CredentialAndAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self.tempdir.name) / "config"
        setup.CONFIG_DIR = self.config_dir
        setup.CONFIG_PATH = self.config_dir / "opencode.json"
        setup.METADATA_PATH = self.config_dir / "opencode-local.json"
        setup.write(setup.CONFIG_PATH, {
            "$schema": setup.SCHEMA_URL,
            "provider": {
                "cloud": {"name": "Cloud", "options": {"baseURL": "https://example.invalid/v1"}, "models": {"on": {}, "off": {}}},
                "local": {"name": "Local", "models": {"free": {}}},
            },
        })
        self.original_env = os.environ.pop("OPENCODE_LOCAL_FAKE_KEY", None)

    def tearDown(self):
        if self.original_env is not None:
            os.environ["OPENCODE_LOCAL_FAKE_KEY"] = self.original_env
        self.tempdir.cleanup()

    def cloud_metadata(self):
        data = setup.metadata()
        item = setup.provider_meta(data, "cloud")
        item["classification"] = setup.REMOTE_PAID
        config = setup.read(setup.CONFIG_PATH)
        setup.set_credential_reference(config["provider"]["cloud"], item, "OPENCODE_LOCAL_FAKE_KEY")
        setup.write(setup.CONFIG_PATH, config)
        setup.write(setup.METADATA_PATH, data)
        return data

    def test_credential_reference_never_stores_fake_value(self):
        fake_value = "NOT_A_REAL_SECRET_TEST_VALUE"
        os.environ["OPENCODE_LOCAL_FAKE_KEY"] = fake_value
        self.cloud_metadata()
        setup.sync()
        sidecar = setup.METADATA_PATH.read_text()
        config = setup.CONFIG_PATH.read_text()
        self.assertIn("OPENCODE_LOCAL_FAKE_KEY", sidecar)
        self.assertIn("{env:OPENCODE_LOCAL_FAKE_KEY}", config)
        self.assertNotIn(fake_value, sidecar)
        self.assertNotIn(fake_value, config)
        self.assertEqual([], list(self.config_dir.glob("*.bak")))
        output = io.StringIO()
        with redirect_stdout(output):
            setup.status()
        self.assertIn("Credential status: PRESENT", output.getvalue())
        self.assertNotIn(fake_value, output.getvalue())

    def test_credential_present_missing_and_not_required(self):
        data = self.cloud_metadata()
        cloud = setup.provider_meta(data, "cloud")
        self.assertEqual(("MISSING", "OPENCODE_LOCAL_FAKE_KEY"), setup.credential_status(cloud))
        os.environ["OPENCODE_LOCAL_FAKE_KEY"] = "NOT_A_REAL_SECRET_TEST_VALUE"
        self.assertEqual(("PRESENT", "OPENCODE_LOCAL_FAKE_KEY"), setup.credential_status(cloud))
        local = setup.provider_meta(data, "local")
        self.assertEqual(("NOT REQUIRED", None), setup.credential_status(local))

    def test_provider_disable_preserves_independent_model_states(self):
        data = setup.metadata()
        setup.model_meta(data, "cloud", "off")["availability"] = "disabled"
        setup.write(setup.METADATA_PATH, data)
        setup.set_availability("cloud", "disabled")
        disabled = setup.read(setup.CONFIG_PATH)
        self.assertIn("cloud", disabled["disabled_providers"])
        self.assertEqual(["off"], disabled["provider"]["cloud"]["blacklist"])
        setup.set_availability("cloud", "enabled")
        enabled = setup.read(setup.CONFIG_PATH)
        self.assertNotIn("disabled_providers", enabled)
        self.assertEqual(["off"], enabled["provider"]["cloud"]["blacklist"])
        data = setup.metadata()
        self.assertEqual("enabled", setup.provider_meta(data, "cloud")["availability"])
        self.assertEqual("disabled", setup.model_meta(data, "cloud", "off")["availability"])
        self.assertEqual("enabled", setup.model_meta(data, "cloud", "on")["availability"])


if __name__ == "__main__":
    unittest.main()
