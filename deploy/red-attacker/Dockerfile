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
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        mariadb-client \
    && rm -rf /var/lib/apt/lists/*

# attach-red.sh 會以 `sleep infinity` 覆寫啟動指令再把 veth 塞進 netns；
# 這裡給個安全預設，讓 image 單獨 `docker run` 也不會立即退出。
CMD ["sleep", "infinity"]
