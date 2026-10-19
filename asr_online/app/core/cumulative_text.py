"""Conservative text alignment; receive watermarks are not word timestamps."""
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from app.utils.character_utils import safe_concat


def normalized(text: str) -> tuple[str, list[int]]:
    # Map numeric spellings to a common value while retaining source offsets.
    digits = '零一二三四五六七八九'
    chars, offsets = [], []
    pattern = r'[零〇一二两三四五六七八九十百千万]+|[0-9]+'
    cursor = 0
    for match in re.finditer(pattern, text):
        for i in range(cursor, match.start()):
            for char in unicodedata.normalize('NFKC', text[i]).lower():
                if char.isalnum():
                    chars.append(char)
                    offsets.append(i + 1)
        token = match.group().replace('〇', '零').replace('两', '二')
        if token.isascii():
            value = str(int(token))
        elif not any(c in token for c in '十百千万'):
            value = ''.join(str(digits.index(c)) for c in token)
        else:
            total = section = number = 0
            for char in token:
                if char in digits:
                    number = digits.index(char)
                elif char == '万':
                    total += (section + number) * 10000
                    section = number = 0
                else:
                    section += (number or 1) * {'十':10, '百':100, '千':1000}[char]
                    number = 0
            value = str(total + section + number)
        chars.extend(value)
        offsets.extend([match.end()] * len(value))
        cursor = match.end()
    for i in range(cursor, len(text)):
        for char in unicodedata.normalize('NFKC', text[i]).lower():
            if char.isalnum():
                chars.append(char)
                offsets.append(i + 1)
    return ''.join(chars), offsets


def quality_reason(source: str, candidate: str) -> str | None:
    old, _ = normalized(source)
    new, _ = normalized(candidate)
    if not new:
        return 'no_text'
    if len(old) >= 8 and len(new) < len(old) * .55:
        return 'abnormal_truncation'
    if len(old) >= 8 and len(new) > len(old) * 2 + 8:
        return 'abnormal_expansion'
    if len(old) >= 12 and SequenceMatcher(None, old, new, autojunk=False).ratio() < .35:
        return 'unrelated_text'
    return None


def align_prefix(raw: str, snapshot_raw: str, candidate: str) -> tuple[int | None, str]:
    current, offsets = normalized(raw)
    snapshot, _ = normalized(snapshot_raw)
    corrected, _ = normalized(candidate)
    reason = quality_reason(snapshot_raw, candidate)
    if reason:
        return None, reason
    # An exact suffix anchor must be unique even outside the watermark window.
    # Four characters is the minimum; prefer up to twelve. No guessed cut.
    for width in range(min(12, len(corrected)), 3, -1):
        anchor = corrected[-width:]
        hits = [m.start() for m in re.finditer('(?=' + re.escape(anchor) + ')', current)]
        if len(hits) != 1:
            continue
        end = hits[0] + width
        if abs(end - len(snapshot)) > 24:
            continue
        if quality_reason(current[:end], corrected):
            continue
        cut = offsets[end - 1]
        if end < len(offsets) and offsets[end] == cut:
            continue
        if (cut < len(raw) and raw[cut-1].isascii() and raw[cut].isascii()
                and raw[cut-1].isalnum() and raw[cut].isalnum()):
            continue
        # Keep punctuation attached to the corrected prefix out of the raw tail.
        while cut < len(raw) and not raw[cut].isalnum():
            cut += 1
        return cut, 'aligned'
    return None, 'no_unique_suffix_anchor'


@dataclass
class CumulativeText:
    raw: str = ''
    records: list[tuple[int, str]] = field(default_factory=list)
    base: str = ''
    cut: int = 0
    covered_end: int = -1
    revision: int = 0

    def append(self, addition: str, end_sample: int) -> None:
        # FunASR online returns increments. Repeated words are real data too.
        if addition:
            self.raw = safe_concat(self.raw, addition)
            self.records.append((end_sample, addition))

    @property
    def visible(self) -> str:
        return safe_concat(self.base, self.raw[self.cut:]) if self.base else self.raw

    def adopt(self, candidate: str, snapshot_raw: str, end: int, revision: int,
              *, final: bool = False) -> str:
        if end <= self.covered_end or revision <= self.revision:
            return 'stale'
        reason = quality_reason(self.raw if final else snapshot_raw, candidate)
        if reason:
            return reason
        if final:
            # A final may fix its last word, but cannot silently omit a long tail.
            old, _ = normalized(self.raw)
            new, _ = normalized(candidate)
            anchor = new[-6:]
            if len(anchor) == 6 and old.count(anchor) == 1:
                missing = len(old) - (old.index(anchor) + len(anchor))
                if missing >= 4:
                    return 'missing_tail'
            cut = len(self.raw)
        else:
            cut, reason = align_prefix(self.raw, snapshot_raw, candidate)
            if cut is None:
                return reason
            if cut < self.cut:
                return 'backward_boundary'
        self.base, self.cut = candidate, cut
        self.covered_end, self.revision = end, revision
        return 'applied'
