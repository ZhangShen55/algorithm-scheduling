from .campaign import CampaignCaseRunner

_CASE_IDS = tuple(f"VIS-{number:03d}" for number in range(1, 29))

for _case_id in _CASE_IDS:
    globals()[_case_id.lower().replace("-", "_")] = CampaignCaseRunner(
        "vision", _case_id
    )

del _case_id
