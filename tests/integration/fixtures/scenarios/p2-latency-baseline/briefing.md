# P2 Latency Baseline

**這不是給玩家的敘事簡報 —— 它是 P2 detection latency 的量測載具（#90 Phase 4）。**

## 目的

對靶機 VM 的可計分 SQLi 攻擊面（`/product`，票 #44）連打 20 次，每次帶一個獨立的
註冊 `action_id`（`sqli-lat-01` … `sqli-lat-20`），量到 20 個 detection latency 樣本，
算出站得住的 p50/p95。

## 為什麼是 20 個同技法動作

`summarize_latency` 要求每個 mode 恰好 20 筆才算最終量測（#21 §四）。20 個動作全是
T1190，靠各自的 `action_id` 區分 —— 這正是 #90「同 technique 的多個動作必須各自可計分，
不得靠時間窗鄰近性二選一」的實測落點：偵測層 `SQLInjectionBurstTarget` 以
`by (source_ip, action_id)` 分組，20 個 action 各自成 series、各自一個 firing。

## 跑法

見 `tests/integration/test_p2_latency_measurement.py`（`PURPLE_RANGE_CHAIN=1` 開啟），
或 `scripts/range/measure-p2-latency.sh`。前提：range 已起（golden 靶機 + 紅隊）、
compose 全棧含 Grafana 在跑。
