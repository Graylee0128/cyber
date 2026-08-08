# 10 — Prometheus / OTLP 路徑：brute force scenario

**What to build:** 一條走 metrics 而非 log 的偵測路徑。**02b、03、08 全部走 Loki —— `:9090` 與 `:4317` 這兩條契約路徑此前完全沒被測過。**

**Blocked by:** 03, 04

**Status:** ready-for-agent

先寫紅燈：

- [ ] `metric_alert_produces_core_event` —— 走 PromQL，不是 LogQL

再變綠：

- [ ] OTLP `:4317` 實際被使用，不只是寫在契約裡
- [ ] Prometheus `:9090` 路徑端到端通
- [ ] 該來源的四項欄位可查詢可聚合：**來源 IP、目的、時間、動作結果**
- [ ] T1110 進入 technique 白名單，且其 `note`（須與成功登入證據連結後才可敘述成入侵路徑）被帶到下游
- [ ] 此來源納入 source registry 的 expected 清單（07）
