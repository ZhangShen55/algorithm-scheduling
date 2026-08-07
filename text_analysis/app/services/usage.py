from typing import Dict, Iterable



# 弃用，字段存在问题
def sum_usage(usages: Iterable[Dict[str, int]]) -> Dict[str, int]:
    '''
    记录token使用情况
    '''
    pt = ct = tt = 0
    for u in usages:
        if not u:
            continue
        pt += int(u.get("prompt_tokens", 0))
        ct += int(u.get("completion_tokens", 0))
        tt += int(u.get("total_tokens", 0))
    return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}
