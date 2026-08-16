# Cyber Range Operator Guide

> 給 Instructor、Purple Analyst 與現場工作人員。這份文件的目標是：一個沒辦過活動的人，
> 照著這份文件從零把平台跑起來、辦完一場演練、收乾淨。
>
> 玩家請看 [Participant Guide](../participant-guide/README.md)；
> 系統怎麼運作請看 [Technical Handbook](../technical-handbook/README.md)；
> 誰能看什麼請看 [Role × UI × Permission Matrix](../architecture/role-ui-permission-matrix.md)；
> 常見問題見 [FAQ](../FAQ.md)。

## 1. Pre-flight Checklist

**活動當天開始前逐項確認。** 標 🔴 的項目沒做等於把平台的權限邊界拆掉。

### 主機與依賴

- [ ] 主機規格符合 §2 的 Minimum（含 KVM / 巢狀虛擬化）
- [ ] `docker` 已安裝**且在跑**（裝了不等於起了，`deploy.sh` 的 preflight 會查）
- [ ] 要建完整 range：`openvswitch` / `libvirt` / `qemu` / `nftables` 已就緒，`/dev/kvm` 存在
- [ ] 確認 kernel 版本，決定 Falco 模式（見 §3）

### 安全邊界

- [ ] 🔴 **`UI_PRIVILEGED_CIDR` 已收斂到 Z-MGMT 網段。** compose 預設值是 `0.0.0.0/0`，
      那是本機 demo 用的值。**保持預設 = 任何人拿到網址就能開教官控台。**
- [ ] 🔴 所有服務 token（`RANGE_CORE_TOKEN_*`、`PURPLE_EVIDENCE_TOKEN_*`、
      `ADMISSION_INSTRUCTOR_TOKEN`）已從預設值改掉
- [ ] 🔴 教官登入 token 已設定且只有工作人員知道
- [ ] 知悉 **Blue SOC 與 Blue Portal 的網址沒有身分保護**（見
      [matrix §6 已知缺口](../architecture/role-ui-permission-matrix.md#6-已知缺口) 缺口 1）——
      不要把這些網址寫在紅隊看得到的地方
- [ ] 知悉 **Purple Console 今天必須用教官 session 登入**（matrix 缺口 2）

### 平台健康

- [ ] `sudo bash test.sh` 跑過，被略過的層都知道為什麼被略過
- [ ] Grafana 存活（Instructor Console 上的狀態燈是綠的）——**Grafana 是唯一的 alert engine，
      它掛了偵測全停，而且不會有任何告警通知你**
- [ ] 靶機 VM 起得來，攻擊鏈跑得通（`test.sh` 的 T4）
- [ ] Battleboard 投影畫面能開、字夠大

### 場次

- [ ] 已知道紅藍各幾人，座位上限設好
- [ ] 遠端玩家的邀請連結已簽發（**一次性，用掉就要重簽**）
- [ ] 現場玩家的進場方式已確認

## 2. Hardware Requirements

由 [#137](https://github.com/Graylee0128/cyber/issues/137) 的三個硬體 profile × S0/S1/S2
實測拍板（完整方法論、逐 profile 數據見 [Hardware Baseline](../deployment/hardware-baseline.md)）：

| | Minimum | Recommended |
|---|---|---|
| CPU | 4 vCPU | 8 vCPU |
| RAM | 8 GiB | 16 GiB |
| Disk | ≥100 GB SSD/NVMe | ≥100 GB SSD/NVMe |

Minimum(4C/8G) 在典型演練負載（30 Red + 20 Blue，70 個動態 seat 容器）下 100% 建置成功，
RAM headroom ≥20%（實測 22.4%），但**唯一在三個 profile 中出現過短暫 CPU throttling**
（139ms，一個 cgroup period）——已驗證可用但餘裕較薄，資源緊繃的場地優先選 Recommended。

已驗證範圍：1 host + 1 target VM + 完整 stack + 上述典型負載。**未驗證**：multi-host、
超過 30+20 人的演練規模、volumetric DDoS / heavy PCAP / malware detonation（見
[Hardware Baseline §7](../deployment/hardware-baseline.md#7-validated-envelope)）。

補充參考（承載上限，非 production minimum）：[#78](https://github.com/Graylee0128/cyber/issues/78)
的 capacity spike——6 核 / 10 GiB / 97 GB 的 VirtualBox VM 上，完整 stack ＋ 靶機 VM 共存時
70 個容器約用 3.7 GiB RAM，推到 230 個容器才碰到 RAM 瓶頸。

硬性 prerequisite（與規格無關，缺了就不能跑）：

- 虛擬化：VT-x / AMD-V，`/dev/kvm` 可用
- 部署形態：單機（multi-host 未驗證）
- Disk：SSD / NVMe

**已驗證的 envelope**：單機 ＋ 1 台靶機 VM ＋ N 個紅隊座位 ＋ M 個藍隊座位 ＋ 完整平台 stack，
在標準演練負載下。**未驗證**：volumetric DDoS、高速封包擷取、malware detonation、multi-host。

## 3. Deploy Cyber

### 全新主機

```bash
curl -fsSL https://raw.githubusercontent.com/Graylee0128/cyber/master/bootstrap.sh | sudo bash -s -- --install-deps
```

`bootstrap.sh` 只做兩件事：clone / 更新 repo 到 `~/cyber`（`CYBER_DIR` 可覆寫），然後呼叫
`deploy.sh`。

### 已經 clone 好

```bash
sudo bash deploy.sh
```

常用變化：

| 指令 | 用途 |
|---|---|
| `sudo bash deploy.sh --install-deps` | 乾淨主機：連依賴一起裝再部署 |
| `sudo bash deploy.sh --stack-only` | 只起觀測棧，不建 range（沒有 KVM / OVS 的機器） |
| `sudo bash deploy.sh --reset` | 先拆乾淨再部署——**演練之間回到已知狀態就用這個** |

`deploy.sh` 分兩層：**L1 觀測／評估平面**（compose：Postgres / Loki / Prometheus / Grafana /
Alloy / receiver / evaluation-engine ＋ Product UI／Admission／Range Core，見下）永遠會起；
**L2 Range**（OVS VLAN 四區 ＋ nftables 方向性防火牆 ＋ 靶機 VM ＋ 紅隊容器）需要 KVM /
libvirt / OVS，缺了會自動跳過並說明原因。

### Product UI 與 Admission

**`deploy.sh` 預設就會一併帶起 Product UI 與 Admission**（`--profile admission-e2e`，
[#144](https://github.com/Graylee0128/cyber/issues/144)）——完成摘要會直接印出畫面網址，
不需要另外手動起。部署完看到 `✅ Cyber deployment ready` 就代表 `http://<host>:8090/` 可以開了。

只想單獨重啟／迭代 UI 這一塊（不重跑整個 `deploy.sh`）才需要手動下這行：

```bash
docker compose --profile admission-e2e up -d --build \
  ui evaluation-api admission-range-core admission admission-receiver
```

Blue SOC 的 Grafana 分頁與 Evidence 查詢還需要**預設 profile 也起著**：

```bash
docker compose up -d grafana evaluation-engine loki
```

> ⚠️ 兩個 profile 各有自己的 Postgres。access plane 的 event id 對 `evaluation-engine`
> （預設 profile 的 DB）是看不到的，除非兩邊指向同一個 DB。

### Falco 模式

腳本會自動判斷，也可以覆寫：

| 模式 | 何時採用 | Falco 跑在哪 |
|---|---|---|
| `container` | host kernel 為 Falco 驅動支援的版本 | compose 的 falco 容器 |
| `vm` | host kernel ≥ 7（實測驅動不相容） | golden 靶機 VM（kernel 6.8） |

```bash
FALCO_MODE=vm sudo bash deploy.sh
```

## 4. 建立 Exercise

在 **Instructor Console**（`/instructor/`，需先在 `/instructor-login/` 用教官 token 登入）：

1. 從 scenario 下拉選單選擇本場次的 scenario
2. 按「**開始演練**」——會跳出提示要你填入紅隊來源 IP 清單（逗號分隔）
3. 確認倒數計時開始

對應端點：`POST /api/exercises/start`，body 含 `scenario_id` 與 `players[]`
（每個玩家的 `player_id` 與 `source_ip`）。

> **「預備（供 Admission 領位）」按鈕會回 403。** 這是設計，不是故障——
> `POST /api/exercises/prepare` 只接受 Admission 的服務身分，教官控台不是那條路徑的
> 合法呼叫者。按鈕刻意保留並顯示說明。

## 5. 建立 / 分配 Seats

在 **Event Control**（`/event-control/`）：

1. 填入 `exercise_id`，按「**載入**」
2. 在座位池面板填紅隊與藍隊上限，按「**設定上限**」（`PUT /admission/{id}/pool-config`）
3. 按「**鎖定並建置藍隊座位**」（`POST /admission/{id}/pool-config/lock`）

> 🔴 **鎖定不可逆。** 鎖定會實際建置藍隊座位容器（每個藍隊座位兩台：`a` = DMZ、
> `b` = 內網）。**人數確定之後才按。**

紅隊座位是 1 容器 / 人，藍隊是 2 容器 / 人——**估算資源時不能把「50 人」直接當成
「50 個容器」**。

## 6. 發 Invite Links

### 遠端玩家

在 Event Control 的「遠端邀請連結」面板按「**簽發一次性連結**」
（`POST /admission/{id}/remote-links`）。

> 🔴 **token 只顯示一次，不會保存在畫面上。** 當下就複製發出去，關掉就要重簽。

每條連結旁有「撤銷」按鈕（`DELETE`）。

### 現場玩家

現場進場碼由 Admission 的 HMAC 機制產生（60 秒時間桶、12 位十六進位字元）。

> ⚠️ **Event Control 畫面上那個會滾動的「現場進場碼」目前是純前端的假資料**，
> 每 60 秒隨機換一次，**沒有接上真正的 HMAC 驗證**。現階段的現場入場請改用遠端連結，
> 或直接由工作人員代為領位。

## 7. 啟動 Target

靶機 VM 由 `deploy.sh` 的 L2 建置。確認方式：

```bash
sudo bash test.sh          # T4 會跑真環境全鏈：Red action → Falco → Alloy → Loki → Detection → Score
```

`test.sh` 分四層由上往下跑，**被略過的層會標明理由，不會把略過講成通過**。T4 通過代表
整條攻防鏈是活的。

## 8. Instructor Console

`/instructor/` — 場次生命週期與即時真相。

| 面板 | 能做什麼 |
|---|---|
| 演練生命週期 | 選 scenario、開始演練、結束並重置 |
| 維運動作 | 重掃遙測 Objective（`POST /api/objectives/sync`）、計算並保存延遲摘要 |
| Grafana 狀態燈 | 唯一 alert engine 的存活指示 |
| 攻防進度 | **未遮蔽**的攻擊鏈（教官看得到真名） |
| 比分 | 逐玩家分數 |
| Raw Event | **未遮蔽**的原始事件 JSON（SSE），全平台唯一 clearance 3 的畫面 |
| Admission 告警 | 座位相關的異常 |
| SOC Copilot | 把 Admission 告警唸成一段 AI 摘要。**純呈現層**，不寫回計分／證據；AI 服務沒起或逾時時這裡空著，其餘功能不受影響 |

**做不到的事**：Override Score、Inject Event。畫面上沒有按鈕，後端也沒有端點——
2026-08-15 決議在有真實教官需求出現前不做。

## 9. Event Control

`/event-control/` — 只管座位與憑證，**不碰場次狀態與分數**。

| 面板 | 能做什麼 |
|---|---|
| 場次 | 載入 `exercise_id` |
| 座位池 | 設定紅藍上限、鎖定並建置（不可逆）、看剩餘可用數 |
| 遠端邀請連結 | 簽發一次性連結、撤銷 |
| 維運 | 清理逾時的領位請求（`POST /maintenance/expire`） |
| 座位告警 | 異常清單 |
| 單一座位操作 | 重新綁定會話、釋出座位 |

**「重新綁定會話」是現場最常用的救援動作**：它把座位的 session 綁到**你這個瀏覽器**。
玩家換裝置、關了無痕視窗、cookie 掉了，都用這個。

**「釋出座位」會把座位還回池子**，玩家要重新領位。

## 10. Purple Console

`/purple/` — 演練效果評估，**唯讀**。

| 分頁 | 內容 |
|---|---|
| 涵蓋率表 | 逐 technique 的紅隊執行 / 藍隊偵測狀態 |
| 動作下鑽 | 單一動作的 judgement、證據等級 C1–C3、gap 分類、**逐 telemetry source** 的 ✅/❌/— |
| Exercise Report | 即時預覽 |

三個符號不能混為一談：

- **✅** 該來源有事件
- **❌** 該來源已部署但沒有事件 → **visibility gap**（要補收集來源）
- **—** 該來源未部署 → 不在範圍內，**不是缺口**

同理，「有 log 但沒有規則」是 **detection gap**（要補規則），跟「根本沒有 log」是兩件事。
講評時分清楚。

> ⚠️ **沒有登記在 `config/scenario-sources.yaml` 的 scenario 會讓 Evaluation API 回 503**，
> 涵蓋率表與下鑽是空的、不是零資料。目前登記的是 `shopdb-credential-pivot`；
> `admission-e2e`、`p2-latency-baseline` 這類量測載具沒有登記。**講評前先確認本場 scenario 已註冊。**

## 11. Battleboard 投影

`/battleboard/` — 公開層，任何人都能開，**包括紅隊**。

投影前確認：

- [ ] 用**公開層網址**投影（`/battleboard/`），不是教官的揭露版
- [ ] 畫面上只有匿名化的 `Attack #N`，沒有真實 technique 名稱
- [ ] 字級在教室最後一排看得清楚

揭露開關由 gateway 前綴強制，投影機那台瀏覽器沒有辦法要求提前揭露——**這是設計上的
保證，不是靠操作紀律**。

## 12. Exercise Monitoring

演練進行中要盯的四件事：

| 盯什麼 | 在哪看 | 出問題的徵兆 |
|---|---|---|
| Grafana 存活 | Instructor Console 狀態燈 | 燈滅 = **偵測全停且不會有告警**，立刻處理 |
| 座位異常 | Event Control 座位告警 | 有玩家進不來或掉線 |
| 攻防進度 | Instructor Console 攻擊鏈（未遮蔽） | 紅隊卡住太久 / 藍隊完全沒動作 |
| 事件流 | Instructor Console Raw Event | 事件停止流入 = 遙測管線斷了 |

## 13. Incident / Failure Handling

| 症狀 | 處理 |
|---|---|
| 玩家終端機打不開 | 確認網址帶了終端機代號 → 確認是不是換了瀏覽器 → Event Control「重新綁定會話」 |
| 玩家 flag 提交 403「來源 IP 不在名冊上」 | 該玩家的座位沒登記進本場名冊；確認開始演練時的紅隊 IP 清單是否包含他 |
| 藍隊「封鎖來源」顯示派送失敗 | Z-MGMT 的 response 佇列有問題。**該動作不會計分**，這是刻意的——別讓分數顯示一個沒發生的封鎖 |
| SOC 沒有任何 alert | 依序確認：靶機活著 → Falco 有事件 → Alloy → Loki → Grafana Alerting。Grafana 是唯一 alert engine |
| 遙測儀表板空白 | 預設 profile 的 `grafana` / `loki` 沒起 |
| Purple Console 回 503／涵蓋率表空白 | 本場 scenario 未在 `config/scenario-sources.yaml` 註冊 |
| 教官控台開不了 | 確認瀏覽器 IP 在 `UI_PRIVILEGED_CIDR` 內，且已在 `/instructor-login/` 登入 |
| 座位卡在 requested 狀態 | Event Control「清理逾時的領位請求」 |

**Grafana 掛掉是最需要警覺的失效**：它是唯一 alert engine，掛了之後偵測完全停止，
而且系統不會主動告訴你。狀態燈是目前唯一的信號。

## 14. Reset / Recovery

### 場次重置（保留平台）

Instructor Console →「結束並重置」（`POST /api/exercises/reset`）。清空場次狀態與分數，
**保留稽核紀錄**。重置後座位需要重新分配。

### 平台重建

```bash
sudo bash deploy.sh --reset
```

先拆乾淨再部署，回到已知狀態。**演練與演練之間建議這樣做。**

## 15. Exercise 結束

```
教官宣布結束
      ↓
Instructor Console →「結束並重置」
      ↓
Battleboard 切到揭露版（教官前綴）→ 顯示真實攻擊鏈
      ↓
Purple Console 講評：哪些被看到、哪些沒有、是 visibility gap 還是 detection gap
      ↓
Instructor Console →「計算並保存延遲摘要」
```

> 「計算並保存延遲摘要」樣本數不足時會回 409，畫面顯示為提示而非錯誤。

## 16. Export Report

> ⚠️ **目前沒有匯出功能。**

Purple Console 的「Exercise Report」分頁是**即時計算的預覽**，每次輪詢都重新算，
**沒有持久化、沒有下載連結、沒有 PDF / CSV**。要留存請自行截圖或抄錄。

持久化版本追蹤在 [#28](https://github.com/Graylee0128/cyber/issues/28)。

## 17. Cleanup

```bash
sudo bash deploy.sh --reset      # 拆掉 range 與 compose
```

活動後另外確認：

- [ ] 遠端邀請連結全部撤銷（沒用掉的也撤）
- [ ] 若有調整過 `UI_PRIVILEGED_CIDR` 或 token，確認沒有把測試值留在正式設定裡
- [ ] 需要留存的報告已截圖（見 §16，沒有匯出功能）
- [ ] 靶機 VM 已停止，磁碟空間已回收
