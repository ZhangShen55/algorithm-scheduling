from .campaign import CampaignCaseRunner

_CASE_IDS = (
    *(f"ONL-{number:03d}" for number in range(1, 21)),
    *(f"FACE-{number:03d}" for number in range(1, 15)),
)

for _case_id in _CASE_IDS:
    globals()[_case_id.lower().replace("-", "_")] = CampaignCaseRunner(
        "online", _case_id
    )

del _case_id
