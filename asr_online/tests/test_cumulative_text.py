import unittest

from app.core.cumulative_text import CumulativeText, normalized


class CumulativeTextTests(unittest.TestCase):
    def test_two_prefixes_preserve_tail_and_prior_display(self):
        text = CumulativeText()
        text.append('我们研究文华历史的发展', 64000)
        snapshot = text.raw
        text.append('然后讨论社会变化', 80000)
        self.assertEqual(text.adopt('我们研究文化历史的发展。', snapshot, 64000, 1), 'applied')
        self.assertEqual(text.visible, '我们研究文化历史的发展。然后讨论社会变化')
        text.append('以及新的问题', 128000)
        second = text.raw
        text.append('我们继续学习', 144000)
        self.assertEqual(text.adopt('我们研究文化历史的发展，然后讨论社会变化以及新的问题。',
                                   second, 128000, 2), 'applied')
        self.assertTrue(text.visible.endswith('新的问题。我们继续学习'))
        self.assertNotIn('文华', text.visible)
        self.assertEqual(text.adopt('旧结果', snapshot, 64000, 1), 'stale')

    def test_delayed_online_word_waits_for_anchor(self):
        text = CumulativeText(raw='今天我们学习人工智')
        source = text.raw
        corrected = '今天我们学习人工智能技术。'
        self.assertEqual(text.adopt(corrected, source, 64000, 1), 'no_unique_suffix_anchor')
        text.append('能技术然后继续', 80000)
        self.assertEqual(text.adopt(corrected, source, 64000, 1), 'applied')
        self.assertEqual(text.visible, corrected + '然后继续')

    def test_repeated_anchor_is_not_guessed(self):
        text = CumulativeText(raw='讨论历史发展讨论历史发展后续内容')
        self.assertEqual(text.adopt('历史发展', '讨论历史发展', 64000, 1),
                         'no_unique_suffix_anchor')

    def test_numeric_spelling_and_mapping(self):
        a, _ = normalized('二十世纪三十年代')
        b, _ = normalized('20世纪30年代')
        self.assertEqual(a, b)
        text = CumulativeText(raw='这是二十世纪三十年代的故事后面继续')
        self.assertEqual(text.adopt('这是20世纪30年代的故事。',
                         '这是二十世纪三十年代的故事', 64000, 1), 'applied')
        self.assertEqual(text.visible, '这是20世纪30年代的故事。后面继续')

    def test_itn_display_text_uses_raw_candidate_for_alignment(self):
        text = CumulativeText(raw='百分之三点五增长然后继续')
        self.assertEqual(
            text.adopt(
                '3.5%增长。',
                '百分之三点五增长',
                64000,
                1,
                alignment_candidate='百分之三点五增长。',
            ),
            'applied',
        )
        self.assertEqual(text.visible, '3.5%增长。然后继续')

    def test_quality_and_final_tail_protection(self):
        text = CumulativeText(raw='我们今天研究文化历史的发展然后讨论社会变化')
        self.assertEqual(text.adopt('。', text.raw, 64000, 1, final=True), 'no_text')
        self.assertEqual(text.adopt('历史。', text.raw, 64000, 1, final=True), 'abnormal_truncation')
        self.assertEqual(text.adopt('我们今天研究文化历史的发展', text.raw,
                                   64000, 1, final=True), 'missing_tail')
        self.assertEqual(text.adopt('我们今天研究文化历史的发展，然后讨论社会变化。',
                                   text.raw, 64000, 1, final=True), 'applied')

    def test_repeated_increment_is_retained(self):
        text = CumulativeText()
        text.append('对', 1)
        text.append('对', 2)
        self.assertEqual(text.raw, '对对')

    def test_english_word_boundary_is_not_cut(self):
        text = CumulativeText(raw='we discuss functionality today')
        self.assertEqual(text.adopt('we discuss function', 'we discuss function',
                                   64000, 1), 'no_unique_suffix_anchor')

    def test_english_increments_keep_word_spacing(self):
        text = CumulativeText()
        text.append('we discuss', 1)
        text.append('history today', 2)
        self.assertEqual(text.raw, 'we discuss history today')
        self.assertEqual(text.adopt('We study history today', text.raw, 64000, 1), 'applied')
        text.append('and tomorrow', 3)
        self.assertEqual(text.visible, 'We study history today and tomorrow')


if __name__ == '__main__':
    unittest.main()
