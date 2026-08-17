# 紅隊最小攻擊 image（票 #46）—— 內容由 #44 攻擊鏈的「實際需要」決定，不預裝「以後可能用到的」。
#
# 硬約束：Z-RED（VLAN30）**沒有對外網**，容器以 `--network none` 起再接 OVS
# （見 scripts/range/attach-red.sh）。所以執行期一個套件都裝不了 —— 容器裡有什麼
# 完全取決於這顆 image。build 在**有網的 host** 上完成，接上 VLAN30 後零安裝步驟。
#
# 攻擊鏈：web 漏洞（HTTP SQLi）→ 撈出 credentials 表裡的 dbadmin 帳密 → 直連 :3306 讀 flag。
#   curl           —— 打靶機 :80 的 /product?id= SQLi（第一跳）
#   mariadb-client —— 拿撈到的帳密直連 :3306 讀 vault.flag（第二跳）
# 就這兩個。防火牆已放行 RED→TARGET:{80,3306}，兩跳在網路層都成立。
#
# 為什麼不是 netshoot：那是網路除錯工具箱，沒有 SQL client —— 第二跳的憑證撈到了也用不了，
# 攻擊鏈斷在工具而非玩家能力（WS2 spec §6.4）。為什麼「最小」：工具越少訓練越硬；
# 要加是增量（某個 scenario 真的打不動再加），拔掉已被依賴的工具才是重工。
#
# 真 ttyd（不是 deploy/edge/ws_stub.py 那個──那支只是 T3 網路契約測試用的
# WebSocket 樁，沒有真的 shell）。比照 deploy/blue-seat/Dockerfile 同一顆
# 官方靜態執行檔、同一個下載方式：ttyd 不在 bookworm 的 apt 來源裡，且接上
# 無網的 VLAN30 後零安裝步驟，執行期一個套件都裝不了。
#
# pre-UAT 2026-08-17 發現：這顆 image 原本 CMD 是 `sleep infinity`，
# attach-red.sh 另外在 netns 裡起一支 stub_listener.py 佔住 :7681 只為了
# 網路契約自測——玩家 Portal 的 Shell 面板打進來，ttyd 代理連得到這個 port，
# 但完全沒有真的 shell 在後面，逐字對應 recv() connection reset。
FROM debian:bookworm-slim

ARG TTYD_VERSION=1.7.7
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        mariadb-client \
    && curl -fsSL -o /usr/local/bin/ttyd \
        "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.x86_64" \
    && chmod +x /usr/local/bin/ttyd \
    && rm -rf /var/lib/apt/lists/*

# 受限帳號，非 root——跟 deploy/blue-seat/Dockerfile 同一個理由（WS8 spec
# §6.1 的模式）：容器只管執行環境，這裡不需要另外的 sudoers allowlist——
# 紅隊這條攻擊鏈（curl + mariadb-client）不涉及任何本機權限提升，§6.1 那份
# 「先留空、有 scenario 真的需要再一行一條加」的原則到目前為止用不到，加一份
# 空白 sudoers 檔只是複製貼上，不是真的邊界。
RUN useradd -m -s /bin/bash red
USER red
WORKDIR /home/red

EXPOSE 7681
# -W：允許寫入（互動 shell 需要）。ttyd 本身不做認證——認證與存取邊界由
# Admission 的 ttyd 代理層負責（同 blue-seat 的分工，SA §12.3）。
#
# --base-path：nginx 這一路（ui/player/app.js → deploy/ui → deploy/edge）
# 把外部路徑 `/terminal/main` 原封不動轉給這支 ttyd（proxy_pass 沒帶額外
# path component，nginx 因此保留原始 URI）。ttyd 服的頁面本身對任何路徑
# 都會回同一份 index.html，但頁面裡的 JS 拿這個值組 WebSocket 連線網址——
# 沒設會組出 `/ws`，跟真正代理路徑 `/terminal/main/ws` 對不上，握手失敗。
# 紅隊座位固定用 "main" 這個終端機代號（見 src/admission/templates/join.html
# 對 team=red 的頁面導轉邏輯），六台紅隊容器可以一致寫死。
CMD ["ttyd", "-p", "7681", "-W", "--base-path", "/terminal/main", "bash"]
