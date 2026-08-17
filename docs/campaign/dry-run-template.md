# Campaign Pack v1 — Dry-run 報告模板與 Pacing 量尺

> 設計稿。定義「約 60 分鐘」這個宣稱要拿什麼尺去量。
> 實際跑 dry-run、填這份表，是 dry-run 執行子票（見 [#153](https://github.com/Graylee0128/cyber/issues/153)）；本檔只提供模板與判準。

## 為什麼需要量尺

「60 分鐘」是整體 experience / pacing 目標，**不是** YAML `duration` 相加。每位玩家不必精確 60 分鐘完成；最終必須人工 walkthrough / dry run 校正。因此 metadata 的 `duration` 只是估值，dry-run 的實測數字才是真相；時間不足時**以調整 content / pacing 解決，不是只改 `duration`**。

---

## Pacing 判準（pass/fail gate）

一次 dry-run 要能支撐「約 60 分鐘標準活動」，需同時滿足：

- [ ] 整場主要活動落在 **50–70 分鐘**（含 briefing，不含 debrief）
- [ ] 沒有任何單一空窗 > **5 分鐘**「沒有事件發生」
- [ ] 每個 Chapter transition 玩家能理解「現在為什麼要做下一步」（非硬轉）
- [ ] Alert / SFX 不造成 fatigue（見下方密度上限）
- [ ] 最後 **10 分鐘有明確高潮**（FINAL objective + countdown）
- [ ] 任一 Chapter 卡關不會讓整場停擺（Instructor 可 skip / advance）

---

## 逐條 Chain 記錄表

每條 chain 一列，dry-run 當場填：

| Chapter | 預估 (`duration`) | 實測 solve time | 主要卡點 | Blue 有無對應 telemetry/alert | 備註 |
|---|---:|---:|---|---|---|
| CH1 The Breach | 45m | | | | |
| CH2 Foothold | | | | | |
| CH3 The Stolen Key | | | | | |
| CH4 Ghost in the System | | | | | |
| FINAL The Leak | | | | | |

> 註：現有 CH1 `duration` 標 45m，但那是單條獨立演練估值；在 Campaign 節奏裡 CH1 應被壓進「Initial Incident 5–15m」區間，這正是需要 dry-run 校正、不能直接相加的原因。

---

## Phase 節奏記錄

對照 baseline（見 `README.md` 的 60 分鐘 pacing 表）：

| Phase | Baseline 區間 | 實測進入時間 | 實測停留 | 空窗 > 5m？ | 轉場是否自然 |
|---|---|---:|---:|---|---|
| Briefing | 0–5m | | | | |
| Initial Incident | 5–15m | | | | |
| Escalation | 15–35m | | | | |
| Critical Phase | 35–50m | | | | |
| Final Push | 50–60m | | | | |
| Debrief | 結束後 | | | | |

---

## Engagement / Experience 記錄

不只量「多久解完」，也觀察現場感受：

- **空窗**：有沒有過長「沒有事件發生」的時段？在哪一段？（記時間點）
- **Alert 密度**：Blue SOC 每分鐘平均幾則 alert cue？是否出現 fatigue？
  - 密度上限（設計 baseline）：critical SFX ≤ **每 90s 一次**；一般 alert 不逐筆發聲，只對「玩家需注意的事件」觸發。
- **轉場**：每個 Chapter → 下一 Chapter，玩家是否理解動機？哪一個轉得最生硬？
- **高潮**：最後 10 分鐘玩家的投入度是否明顯上升？FINAL countdown 有沒有效果？
- **meme / copy**：哪幾則文案有效？有沒有哪一則降低了 SOC 資訊判讀？（違規要記下）

---

## Detection / Purple 校驗（每條）

dry-run 同時驗證 Blue/Purple 半邊沒有紙上談兵：

- [ ] 每條的 Red objective 實際可判定（telemetry 或 submission）
- [ ] 每條宣稱有覆蓋的 detection 真的 firing、進得了 Core Event
- [ ] 每條的 intentional gap 真的**不** firing（尤其 FINAL 的 detection gap——若被誤加規則，護欄測試應紅）
- [ ] Purple Console coverage 表對每條都算得出有意義結果（非空白、非 503）
- [ ] reset 後每條可重新演練

> **這五項裡有機械可查的部分，不用每次都肉眼核對。** `scripts/range/dry-run-check.py`
> 把「intentional gap 真的不 firing」「宣稱覆蓋的 telemetry objective 真的註冊進
> Action Registry」「Battleboard 兩種 revealed 都答得出非空結果」自動化——每章
> exercise 實際玩過一輪後跑一次，先抓機械性回歸，人力再專心看真正需要人判斷的
> 部分（見下方「產出」前的提醒）。腳本**不執行 dry-run 本身**：pacing／engagement／
> meme 落地與否還是要人在現場走一輪，這份表照填。用法見腳本檔頭 docstring。

---

## 產出

dry-run 執行子票完成後，這份表填好 → 存 `archive/`（做完的東西，依 cyber/CLAUDE.md），並在 README 的 pacing 段回填「實測支撐 60 分鐘」的結論或需調整項。若判準沒過，開 follow-up 調 content/pacing，**不**用改 `duration` 蒙混。
