import unittest

from app.core.v4_itn import V4ITN


class V4ITNTests(unittest.IsolatedAsyncioTestCase):
    async def test_chinese_numbers_dates_percentages_and_units(self):
        itn = V4ITN()
        cases = {
            '二零二四年十月二十日': '2024/10/20',
            '百分之三点五': '3.5%',
            '二点五平方电线': '2.5平方电线',
            '普通中文内容': '普通中文内容',
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                text, elapsed, status, error = await itn.apply(source)
                self.assertEqual(text, expected)
                self.assertGreaterEqual(elapsed, 0)
                self.assertEqual(status, 'applied')
                self.assertIsNone(error)

    async def test_terms_and_continuous_digits_are_preserved_or_normalized(self):
        itn = V4ITN()
        text, _, status, _ = await itn.apply('一氧化碳二氧化碳一阶导数二维传热三维空间一二三四五六')
        self.assertTrue(text.startswith('一氧化碳二氧化碳一阶导数二维传热三维空间'))
        self.assertEqual(status, 'applied')

    async def test_disabled_mode_and_failure_fallback(self):
        disabled = V4ITN(enabled=False)
        self.assertEqual(await disabled.apply('二点五'), ('二点五', 0.0, 'disabled', None))

        failing = V4ITN()
        # Replace the synchronous normalizer with a failing test double.
        class Broken:
            def normalize(self, _):
                raise RuntimeError('test failure')

        failing._normalizer = Broken()
        text, _, status, error = await failing.apply('二点五')
        self.assertEqual(text, '二点五')
        self.assertEqual(status, 'fallback')
        self.assertEqual(error, 'RuntimeError')
        self.assertIsInstance(failing._normalizer, Broken)

    async def test_normalizer_is_cached(self):
        itn = V4ITN()
        await itn.apply('二点五')
        normalizer = itn._normalizer
        await itn.apply('三点一四')
        self.assertIs(itn._normalizer, normalizer)

    async def test_warmup_builds_the_cached_normalizer(self):
        itn = V4ITN()
        await itn.warmup()
        self.assertIsNotNone(itn._normalizer)


if __name__ == '__main__':
    unittest.main()
