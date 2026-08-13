from __future__ import annotations


#: visibility 的機密階序。數字越大越機密。
#: public ⊂ blue ⊂ purple ⊂ instructor —— 是一條線性階序，不是互斥集合。
VISIBILITY_RANK = {"public": 0, "blue": 1, "purple": 2, "instructor": 3}

#: 呼叫者身分 → clearance。與 VISIBILITY_RANK 同軸：clearance N 的人看得到
#: rank ≤ N 的行。red 只看得到 public（Battleboard 等級）。
CALLER_CLEARANCE = {"red": 0, "blue": 1, "purple": 2, "instructor": 3}

def visibility_rank(visibility: str) -> int:
    """visibility 的機密階。未知 visibility → fail closed，當成最嚴格一級。"""
    return VISIBILITY_RANK.get(visibility, max(VISIBILITY_RANK.values()))
