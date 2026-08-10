import unittest

from app.utils.uri import redact_uri_for_log


class UriRedactionTests(unittest.TestCase):
    def test_removes_credentials_query_and_fragment_from_http_url(self):
        value = redact_uri_for_log(
            "https://user:secret@example.test:8443/course/PPT.mp4?token=abc#part"
        )

        self.assertEqual(value, "https://example.test:8443/course/PPT.mp4")

    def test_keeps_local_path_without_modification(self):
        self.assertEqual(
            redact_uri_for_log("/data/course/course-001/PPT.mp4"),
            "/data/course/course-001/PPT.mp4",
        )


if __name__ == "__main__":
    unittest.main()
