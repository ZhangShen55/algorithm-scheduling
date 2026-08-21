from .campaign import CampaignCaseRunner

_CASE_IDS = (
    *(f"JOB-{number:03d}" for number in range(1, 21)),
    *(f"FILE-{number:03d}" for number in range(1, 17)),
    *(f"PPT-{number:03d}" for number in range(1, 16)),
    *(f"OCR-{number:03d}" for number in range(1, 6)),
    *(f"ASR-{number:03d}" for number in (*range(1, 14), 18)),
)

for _case_id in _CASE_IDS:
    globals()[_case_id.lower().replace("-", "_")] = CampaignCaseRunner(
        "offline", _case_id
    )

del _case_id
