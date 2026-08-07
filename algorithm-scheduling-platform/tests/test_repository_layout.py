import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RepositoryLayoutTest(unittest.TestCase):
    def test_expected_service_and_shared_packages_exist(self) -> None:
        expected_packages = (
            "services/control_service",
            "services/orchestrator_service",
            "services/vision_orchestrator_service",
            "services/online_gateway_service",
            "packages/platform_common",
            "packages/platform_contracts",
            "packages/operator_registry_client",
        )

        for relative_path in expected_packages:
            package = PROJECT_ROOT / relative_path
            self.assertTrue(package.is_dir(), relative_path)
            self.assertTrue((package / "__init__.py").is_file(), relative_path)

    def test_repository_contains_deployment_migration_and_test_roots(self) -> None:
        for relative_path in ("deploy", "migrations", "tests"):
            self.assertTrue((PROJECT_ROOT / relative_path).is_dir(), relative_path)


if __name__ == "__main__":
    unittest.main()
