import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent


class RepositoryLayoutTest(unittest.TestCase):
    def test_expected_service_and_shared_packages_exist(self) -> None:
        expected_service_projects = (
            "control_service",
            "orchestrator_service",
            "vision_orchestrator_service",
            "online_gateway_service",
        )
        expected_shared_packages = (
            "packages/platform_common",
            "packages/platform_contracts",
            "packages/operator_registry_client",
        )

        for relative_path in expected_service_projects:
            package = WORKSPACE_ROOT / relative_path / "app"
            self.assertTrue(package.is_dir(), relative_path)
            self.assertTrue((package / "__init__.py").is_file(), relative_path)

        for relative_path in expected_shared_packages:
            package = PROJECT_ROOT / relative_path
            self.assertTrue(package.is_dir(), relative_path)
            self.assertTrue((package / "__init__.py").is_file(), relative_path)

    def test_repository_contains_deployment_migration_and_test_roots(self) -> None:
        for relative_path in ("deploy", "migrations", "tests"):
            self.assertTrue((PROJECT_ROOT / relative_path).is_dir(), relative_path)

    def test_rebuildable_files_inventory_documents_cleanup_boundaries(self) -> None:
        inventory = PROJECT_ROOT / "docs" / "本地可重建文件清单.md"

        self.assertTrue(inventory.is_file())
        content = inventory.read_text(encoding="utf-8")
        for rebuildable in (
            ".pytest_cache/",
            ".mypy_cache/",
            ".ruff_cache/",
            "__pycache__/",
            "*.egg-info/",
            ".DS_Store",
        ):
            self.assertIn(rebuildable, content)
        self.assertIn(".venv/", content)
        self.assertIn("本轮保留", content)
        for protected in ("migrations/", "deploy/", "harness/", "tests/"):
            self.assertIn(protected, content)


if __name__ == "__main__":
    unittest.main()
