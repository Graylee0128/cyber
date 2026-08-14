# Z-BLUE 座位 image（票 #62，WS8 spec §6.1）—— 藍隊拿受限 shell，不是 root。
#
# 一顆 image 同時當一段裡的 a（DMZ）跟 b（內部）用——兩台的差別是網路位置
# 與（未來 WS2 declare 的）服務內容，不是帳號權限模型，所以不需要兩個 image。
#
# 真 ttyd（不是 deploy/edge/ws_stub.py 那個──那支只是 T3 網路契約測試用的
# WebSocket 樁，沒有真的 shell，見 attach_red 那批 T3 stub 的用途說明）。
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ttyd sudo \
    && rm -rf /var/lib/apt/lists/*

# 受限帳號，非 root（§6.1）。/bin/bash 給互動 shell；no login password——
# 進場憑證由 Admission 的 session/ttyd 代理把關，不是這裡的帳密。
RUN useradd -m -s /bin/bash blue

# sudo allowlist：内容與「為什麼」見同目錄的 sudoers.d/blue。
COPY deploy/blue-seat/sudoers.d/blue /etc/sudoers.d/blue
RUN chmod 0440 /etc/sudoers.d/blue && visudo -c

USER blue
WORKDIR /home/blue

EXPOSE 7681
# -W：允許寫入(互動 shell 需要)；不開 --readonly。ttyd 本身不做認證——
# 認證與存取邊界由 Admission 的 ttyd 代理層負責（見 SA §12.3、#26 Console
# 邊界的同一種分工：容器只管執行環境，身分驗證住別層）。
CMD ["ttyd", "-p", "7681", "-W", "bash"]
