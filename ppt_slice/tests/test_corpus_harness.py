import json
import inspect
import tempfile
import unittest
from pathlib import Path

from harness.tools.corpus import (
    build_inventory,
    discover_ppt_videos,
    split_for_url,
    write_inventory,
)


ROOT = "http://example.test/course/"


class CorpusDiscoveryTests(unittest.TestCase):
    def test_recurses_same_origin_and_filters_to_ppt_mp4(self):
        pages = {
            ROOT: """
                <a href="class-a/">A</a>
                <a href="/course/class-b/">B</a>
                <a href="http://evil.test/course/">evil</a>
                <a href="/outside/">outside</a>
                <a href="class-a/PPT.mp4">duplicate</a>
                <a href="notes.txt">notes</a>
            """,
            f"{ROOT}class-a/": """
                <a href="../">up</a>
                <a href="PPT.mp4">ppt</a>
                <a href="teacher.mp4">teacher</a>
            """,
            f"{ROOT}class-b/": """
                <a href="PPT%E8%AF%BE%E4%BB%B6.MP4">ppt</a>
            """,
        }

        result = discover_ppt_videos(ROOT, fetch_html=pages.__getitem__)

        self.assertEqual(result.directory_count, 3)
        self.assertEqual(
            result.video_urls,
            [
                f"{ROOT}class-a/PPT.mp4",
                f"{ROOT}class-b/PPT%E8%AF%BE%E4%BB%B6.MP4",
            ],
        )
        self.assertGreaterEqual(result.filtered_count, 3)
        self.assertEqual(result.errors, [])

    def test_new_course_is_discovered_without_fixed_expected_count(self):
        pages = {ROOT: '<a href="new-course/PPT.mp4">new</a>'}

        result = discover_ppt_videos(ROOT, fetch_html=pages.__getitem__)

        self.assertEqual(result.video_urls, [f"{ROOT}new-course/PPT.mp4"])

    def test_directory_failure_is_retained(self):
        pages = {ROOT: '<a href="broken/">broken</a>'}

        result = discover_ppt_videos(ROOT, fetch_html=pages.__getitem__)

        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0]["url"], f"{ROOT}broken/")
        self.assertIn("broken", result.errors[0]["reason"])


class InventoryTests(unittest.TestCase):
    def test_known_truth_video_is_pinned_to_calibration_and_changes_fingerprint(self):
        self.assertIn(
            "known_calibration_urls",
            inspect.signature(build_inventory).parameters,
            "已知真值样本不能进入未见保留集",
        )
        holdout_url = next(
            f"{ROOT}known-{index}/PPT.mp4"
            for index in range(100)
            if split_for_url(f"{ROOT}known-{index}/PPT.mp4") == "HOLDOUT"
        )
        course_name = holdout_url.removeprefix(ROOT).split("/", 1)[0]
        pages = {ROOT: f'<a href="{course_name}/PPT.mp4">known</a>'}
        metadata = lambda url: {"content_length": 1, "last_modified": "now"}
        probe = lambda url: {
            "duration": 60.0,
            "codec": "h264",
            "fps": 25.0,
            "width": 1920,
            "height": 1080,
        }
        default_inventory = build_inventory(
            ROOT,
            fetch_html=pages.__getitem__,
            head_resource=metadata,
            probe_video=probe,
            run_id="default-split",
        )
        pinned_inventory = build_inventory(
            ROOT,
            fetch_html=pages.__getitem__,
            head_resource=metadata,
            probe_video=probe,
            run_id="pinned-split",
            known_calibration_urls={holdout_url},
        )

        self.assertEqual(default_inventory["items"][0]["split"], "HOLDOUT")
        self.assertEqual(pinned_inventory["items"][0]["split"], "CALIBRATION")
        self.assertEqual(pinned_inventory["items"][0]["split_reason"], "KNOWN_TRUTH")
        self.assertNotEqual(
            default_inventory["inventory_fingerprint"],
            pinned_inventory["inventory_fingerprint"],
        )

    def test_builds_inventory_with_metadata_probe_and_failure_status(self):
        pages = {
            ROOT: '<a href="course-a/PPT.mp4">A</a><a href="course-b/PPT.mp4">B</a>'
        }

        def head(url):
            if "course-b" in url:
                raise RuntimeError("资源不可达")
            return {"content_length": 1234, "last_modified": "Fri, 07 Aug 2026 00:00:00 GMT"}

        def probe(url):
            return {
                "duration": 61.25,
                "codec": "h264",
                "fps": 25.0,
                "width": 1920,
                "height": 1080,
            }

        inventory = build_inventory(
            ROOT,
            fetch_html=pages.__getitem__,
            head_resource=head,
            probe_video=probe,
            run_id="run-test",
            discovered_at="2026-08-07T00:00:00+08:00",
        )

        self.assertEqual(inventory["run_id"], "run-test")
        self.assertEqual(inventory["directory_count"], 1)
        self.assertEqual(inventory["video_count"], 2)
        self.assertEqual(inventory["items"][0]["course_name"], "course-a")
        self.assertEqual(inventory["items"][0]["probe_status"], "COMPLETED")
        self.assertEqual(inventory["items"][0]["duration"], 61.25)
        self.assertEqual(inventory["items"][1]["probe_status"], "FAILED")
        self.assertEqual(inventory["items"][1]["error_reason"], "资源不可达")
        self.assertTrue(inventory["inventory_fingerprint"])

    def test_split_is_deterministic_for_all_files_in_same_course(self):
        first = split_for_url(f"{ROOT}course-a/PPT.mp4")
        second = split_for_url(f"{ROOT}course-a/sub/PPT-2.mp4", course_url=f"{ROOT}course-a/")
        explicit_first = split_for_url(f"{ROOT}course-a/PPT.mp4", course_url=f"{ROOT}course-a/")

        self.assertIn(first, {"CALIBRATION", "HOLDOUT"})
        self.assertEqual(explicit_first, second)

    def test_inventory_is_written_atomically_without_video_files(self):
        inventory = {"run_id": "run-test", "items": []}
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "inventory.json"

            write_inventory(destination, inventory)

            self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), inventory)
            self.assertFalse((Path(temp_dir) / "inventory.json.part").exists())
            self.assertEqual(list(Path(temp_dir).glob("*.mp4")), [])


if __name__ == "__main__":
    unittest.main()
