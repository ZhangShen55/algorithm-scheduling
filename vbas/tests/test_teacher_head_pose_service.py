import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.teacher_head_pose_service import (
    DEFAULT_DIRECTMHP_DATA,
    DEFAULT_DIRECTMHP_ROOT,
    DEFAULT_DIRECTMHP_WEIGHTS,
    DirectMHPBackend,
    HeadPosePrediction,
    TeacherHeadPoseConfig,
    TeacherHeadPoseThresholds,
    build_success_head_pose_result,
    get_teacher_head_pose_config,
)


def fake_torch(*, cuda_available: bool, device_count: int):
    return SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: cuda_available,
            device_count=lambda: device_count,
        ),
        device=lambda value: value,
    )


class TeacherHeadPoseServiceTest(unittest.TestCase):
    def test_default_directmhp_assets_use_project_root_layout(self):
        self.assertTrue(str(DEFAULT_DIRECTMHP_ROOT).endswith("/app/vendor/DirectMHP"))
        self.assertTrue(
            str(DEFAULT_DIRECTMHP_WEIGHTS).endswith(
                "/models/cmu_m_1280_e200_t40_lw010_best.pt"
            )
        )
        self.assertTrue(
            str(DEFAULT_DIRECTMHP_DATA).endswith("/models/cmu_panoptic_coco.yaml")
        )

    def test_head_pose_direction_uses_student_view_yaw_and_angle_over_threshold(self):
        prediction = HeadPosePrediction(
            box=(10, 20, 50, 80),
            confidence=0.86,
            pitch=31.0,
            yaw=-35.0,
            roll=2.0,
        )

        result = build_success_head_pose_result(
            prediction,
            teacher_confidence=0.95,
            teacher_box=(100, 200, 300, 500),
            crop_offset=(90, 180),
            thresholds=TeacherHeadPoseThresholds(side_yaw=25.0, down_pitch=25.0),
        )

        self.assertEqual(result.FaceDirection, "right")
        self.assertEqual(result.Yaw, 35.0)
        self.assertEqual(result.Angle, 10.0)
        self.assertEqual(result.Pitch, 31.0)
        self.assertEqual(result.Roll, 2.0)
        self.assertTrue(result.IsLookingDown)
        self.assertEqual(result.HeadBox.LeftTopX, 100)
        self.assertEqual(result.HeadBox.LeftTopY, 200)

    def test_head_pose_direction_reports_front_inside_side_threshold(self):
        prediction = HeadPosePrediction(
            box=(0, 0, 20, 20),
            confidence=0.70,
            pitch=0.0,
            yaw=12.0,
            roll=0.0,
        )

        result = build_success_head_pose_result(
            prediction,
            teacher_confidence=0.90,
            teacher_box=(100, 200, 300, 500),
            crop_offset=(0, 0),
            thresholds=TeacherHeadPoseThresholds(side_yaw=25.0, down_pitch=25.0),
        )

        self.assertEqual(result.FaceDirection, "front")
        self.assertEqual(result.Yaw, -12.0)
        self.assertEqual(result.Angle, 0.0)
        self.assertFalse(result.IsLookingDown)


class DirectMHPDeviceGuardTest(unittest.TestCase):
    def _settings_module(
        self,
        *,
        operator_device: str,
        head_device: str,
        torch_module,
        prepare_model_path=Mock(),
    ):
        return SimpleNamespace(
            device=operator_device,
            torch=torch_module,
            settings=SimpleNamespace(
                GPU_ID=operator_device,
                Teacher_Head_Pose={
                    "Enabled": True,
                    "Device": head_device,
                },
            ),
            model_path_resolver=SimpleNamespace(
                prepare_model_path=prepare_model_path,
            ),
        )

    def test_cpu_directmhp_device_fails_before_weight_materialization(self):
        prepare_model_path = Mock()
        settings_module = self._settings_module(
            operator_device="cuda:0",
            head_device="cpu",
            torch_module=fake_torch(cuda_available=True, device_count=1),
            prepare_model_path=prepare_model_path,
        )

        with (
            patch.dict("os.environ", {"REQUIRE_GPU": "true"}, clear=False),
            patch.dict("sys.modules", {"app.core.settings": settings_module}),
            self.assertRaisesRegex(RuntimeError, "DirectMHP.*cuda:0"),
        ):
            get_teacher_head_pose_config()

        prepare_model_path.assert_not_called()

    def test_mismatched_directmhp_device_fails_before_weight_materialization(self):
        prepare_model_path = Mock()
        settings_module = self._settings_module(
            operator_device="cuda:0",
            head_device="cuda:1",
            torch_module=fake_torch(cuda_available=True, device_count=2),
            prepare_model_path=prepare_model_path,
        )

        with (
            patch.dict("os.environ", {"REQUIRE_GPU": "true"}, clear=False),
            patch.dict("sys.modules", {"app.core.settings": settings_module}),
            self.assertRaisesRegex(RuntimeError, "DirectMHP.*cuda:1.*cuda:0"),
        ):
            get_teacher_head_pose_config()

        prepare_model_path.assert_not_called()

    def test_out_of_range_directmhp_device_fails_before_weight_materialization(self):
        prepare_model_path = Mock()
        settings_module = self._settings_module(
            operator_device="cuda:0",
            head_device="cuda:1",
            torch_module=fake_torch(cuda_available=True, device_count=1),
            prepare_model_path=prepare_model_path,
        )

        with (
            patch.dict("os.environ", {"REQUIRE_GPU": "true"}, clear=False),
            patch.dict("sys.modules", {"app.core.settings": settings_module}),
            self.assertRaisesRegex(RuntimeError, "cuda:1.*索引越界"),
        ):
            get_teacher_head_pose_config()

        prepare_model_path.assert_not_called()

    def test_valid_cuda_zero_does_not_change_visible_devices(self):
        prepared = Path("/tmp/directmhp.pt")
        prepare_model_path = Mock(return_value=prepared)
        settings_module = self._settings_module(
            operator_device="cuda:0",
            head_device="cuda:0",
            torch_module=fake_torch(cuda_available=True, device_count=1),
            prepare_model_path=prepare_model_path,
        )

        with (
            patch.dict(
                "os.environ",
                {"REQUIRE_GPU": "true", "CUDA_VISIBLE_DEVICES": "sentinel"},
                clear=False,
            ),
            patch.dict("sys.modules", {"app.core.settings": settings_module}),
        ):
            config = get_teacher_head_pose_config()
            self.assertEqual(
                "sentinel", __import__("os").environ["CUDA_VISIBLE_DEVICES"]
            )

        self.assertEqual("cuda:0", config.device)
        prepare_model_path.assert_called_once()

    def test_backend_rejects_invalid_device_before_attempt_load(self):
        config = TeacherHeadPoseConfig(
            directmhp_root=Path("/tmp/directmhp"),
            directmhp_weights=Path("/tmp/directmhp.pt"),
            directmhp_data=Path("/tmp/directmhp.yaml"),
            device="cpu",
        )
        backend = DirectMHPBackend(config)
        settings_module = self._settings_module(
            operator_device="cuda:0",
            head_device="cpu",
            torch_module=fake_torch(cuda_available=True, device_count=1),
        )

        with (
            patch.dict("os.environ", {"REQUIRE_GPU": "true"}, clear=False),
            patch.dict("sys.modules", {"app.core.settings": settings_module}),
            patch.object(backend, "validate_files") as validate_files,
            self.assertRaisesRegex(RuntimeError, "DirectMHP.*cuda:0"),
        ):
            backend.load()

        validate_files.assert_not_called()


if __name__ == "__main__":
    unittest.main()
