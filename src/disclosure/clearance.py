from __future__ import annotations


#: visibility 的機密階序。數字越大越機密。
#: public ⊂ blue ⊂ purple ⊂ instructor —— 是一條線性階序，不是互斥集合。
VISIBILITY_RANK = {"public": 0, "blue": 1, "purple": 2, "instructor": 3}

#: 呼叫者身分 → clearance。與 VISIBILITY_RANK 同軸：clearance N 的人看得到
#: rank ≤ N 的行。red 只看得到 public（Battleboard 等級）。
#:
#: `red` 與 `public` 同 rank 0 是刻意、非疏漏（#153 grilling 時確認）：
#: Battleboard 自己的 gateway identity 就是 `red`（`ui/README.md` 的
#: audience/gateway 對照表），兩者結構上就是同一個可見範圍。目前沒有任何
#: 需求要讓 Red 看到 Battleboard 看不到的東西——experience-contract.md 的
#: cue 表全部落在 `public`/`role:blue`，沒有 `role:red` 的實際用例。若未來
#: 真的出現這種需求，該走一次獨立的 ADR，不要把它當 bug 直接改這條線性
#: 階序（改動牽動 event_visibility/fields/event_stream 好幾個模組）。
CALLER_CLEARANCE = {"red": 0, "blue": 1, "purple": 2, "instructor": 3}

def visibility_rank(visibility: str) -> int:
    """visibility 的機密階。未知 visibility → fail closed，當成最嚴格一級。"""
    return VISIBILITY_RANK.get(visibility, max(VISIBILITY_RANK.values()))
