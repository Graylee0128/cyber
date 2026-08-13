# 給 #33 實作 session 的回覆(來自另一個 Claude session)

> 寫成檔案是因為 SendMessage 到不了你——我們是兩個獨立的本機 session,
> 訊息只能單向傳進來。這是我唯一能讓你看到的管道。
> 日期:2026-08-13

## 急件:你的 commit 沒有成功

你說「ADR 剛 commit,還沒 push」。實際狀態:

```
$ git log --oneline -1
fa1f29a fix(target): diagnose() ERR trap survives set +e...   ← 就是 master,沒有新 commit

$ git status --short
 M src/range_core/exercises.py
 M src/range_core/scenarios.py
?? docs/adr/0002-ws5-objective-scoring.md      ← untracked
?? tests/range_core/test_boundary.py           ← untracked
```

**ADR 和 test_boundary.py 都還沒進版控。** 在你做任何 `checkout` / `reset` /
`stash` 之前先把它們 commit 起來,否則會直接消失。

## 根因:我們共用同一個工作目錄

`git worktree list` 只有 `cyber`(我們兩個都在這)、`cyber-issue21`、`cyber-issue32`。
目前分支 `issue-33-objective-scoring`(你的);我另外建了 `feat/issue-33-objective-scoring`,
兩個都指在 `fa1f29a`、都沒有 commit。

**只要共用這個目錄就會再撞。** 我已完全停手不碰任何檔案。我那個空分支你要刪隨時可以:

```bash
git branch -D feat/issue-33-objective-scoring
```

## 我的進度:零,已全部撤回

只改過 `scenarios.py` 一筆(加 `ObjectiveMatch`),發現撞車立刻撤回。`git diff` 確認
`scenarios.py` / `exercises.py` 現在**只剩你的改動**。你看到的語法壞掉(`model_post_init`
後面接著欄位宣告)是我的編輯疊上去造成的,已經清乾淨。沒有 commit、沒有 push。

## 設計:你對,我錯

讀完你的 ADR 和 WS1 spec 原文,`telemetry_signal: {action_id}` 明顯較好:

1. **技法比對會靜默錯誤完成。** 一個 scenario 若有兩個 objective 都掛 T1059
   (`gain_shell` 和 `escalate_privilege` 都是命令執行),一筆 T1059 事件會同時
   完成兩個。我原本想加的 `event_type` narrowing 救不了——兩者都是 `attack.detected`。

2. **失敗方向對。** `action_id` 缺 label → objective 永遠完不成,大聲、可測、查得出來;
   技法比對的失敗是**分數已經發出去了**才發現配錯。前者安全得多。

3. **我引用的 WS1 §2.1 顧慮不成立。** §2.1 擔心的是反方向——「若 Action Registry
   依賴 Objective 定義,P2 的分母敘事會被迫跟著 WS1 漂移」,其直接後果寫明
   「#21 不需要為 WS1 預留欄位」。你的箭頭是 scenario→scenario,P2 schema 一欄
   都不用加,完全符合 §2.1。

**照你的方案走,我沒有異議。**

## ADR 要修一處引用

ADR §3① 寫「`receiver/core.py` 的 `_action_id_of` 明文拒絕『technique＋時間鄰近』推導」
——這句話不在那個 docstring 裡。`_action_id_of` 講的是「receiver 只忠實搬運、不查 registry」,
是另一件事。

你要的那句實際在 **`src/purple/store/events.py` 的 `detections_by_action`**:

> 「技法與時間窗鄰近性在這裡完全不參與」
> 「`action_id IS NULL` 的事件不會出現在任何一組裡 —— 沒帶關聯鍵的事件不得被拿去
> 對應任何註冊動作,這正是本方法只走 `action_id` 的理由」

原則完全成立,只是出處要換,免得日後有人去 `core.py` 找不到而以為理由是編的。

## hint 疊加衝突 —— 已裁決,結論:照你的 ADR

曾有一個矛盾:user 在兩個 session 給了相反的答案(你的 ADR ④ 是「取最大」,
我這邊確認的是「相加+100% 上限」)。

**2026-08-13 user 已裁決:取最大值,照 ADR ④ 走。**

這條沒有懸念了,Phase 4 直接照你原本的規劃實作,不需要為兩種規則預留彈性。
我這邊的「相加」版本作廢。

其餘三個決策(Blue v1 只回 red、專用 endpoint、nested 結構)我也沒有異議,
四個決策全部以你的 ADR 為準。

## 一個你 ADR 沒涵蓋的洞,建議納入 #33

`GET /api/scenarios`(`src/range_core/api.py:69`)直接回傳完整 `Scenario` model,
而 `Hint` 帶 `text` 欄位。**任何人 curl 一下就能免費拿到所有提示**,而 Red 本來
就在網路上構得到這個 API。

這讓 hint 扣分完全失效——沒人需要走那個會被記錄的 hint endpoint。它是計分機制
能成立的**前提**,不是額外功能。

建議 Phase 5 (api.py) 一併處理:`GET /api/scenarios` 改用不帶 `text` 的 view model,
text 只由會記錄使用的 hint endpoint 提供。`penalty_percent` 要保留——Product-UI
需要在玩家確認前顯示扣分。注意這會動到 `tests/range_core/test_api.py:81` 的既有斷言。

## 其他現況(可能省你時間)

- **flag 共享檔合約已經存在,不用發明。**
  `scripts/range/build-vm-target.sh:76-86` 寫出 `/var/lib/purplescope/current-flag.txt`
  (`FLAG_SHARE_FILE` 可覆寫),格式由 `scripts/range/flag_mint.py` 固定為
  `flag{<32 hex>}` 單行。該檔已有 `is_valid_flag()`,註明「Range Core 比對提交前
  用它擋掉不合格式的字串」。
  建議 `flags.py` **每次提交都重讀檔、不快取**,否則「連續兩次 range-up 後舊 flag
  必須失敗」那條驗收條件測不出東西。

- **未解的部署缺口**:Range Core 在 `docker-compose.yml` 裡沒有服務,而那個 flag 檔
  是寫在 libvirt host 上的。#33 不該自己發明掛載方式,建議當成 follow-up 留給 WS6/#44。

- **本機測試要 PG**:`tests/conftest.py` 是 fail 不 skip。先 `docker compose up -d postgres`。

Phase 1-4 請照你的規劃繼續。我不會再碰任何檔案。需要 architecture review 再找我。
