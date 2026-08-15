# Bootstrap UX Test（issue #144 D0，第一版，部分欄位待補）

> 使用者自己動手測，這份先把已知資訊記下來，未知欄位明確標 TBD——依 D0 要求「不能取得者明確寫原因」。

## Environment

| 欄位 | 值 |
|---|---|
| CPU | TBD |
| RAM | TBD |
| Disk | TBD |
| OS/kernel | TBD |
| VM/bare metal | VM（測試者自己的 VM，非 `.88`） |
| Nested virtualization | **未測**——這輪只測了測試者自己的 VM，沒有巢狀虛擬化環境（跟本機 Windows 主機、`192.168.1.105` 同款已知限制：無 `/dev/kvm`，`deploy.sh` 應會自動退成 `--stack-only` 或 degraded 模式，需另一輪在有 KVM 的主機補測才能取得 full range 的 timing） |
| Network | TBD |
| Commit | TBD（測試者未提供，需要用 `bootstrap.sh` 印出的 commit SHA 或 `git -C /root/cyber log -1` 補） |

## Command

```
curl -fsSL <bootstrap.sh URL> | sudo bash
```

**已知 bug（本輪測試意外發現，非 D0 本來要找的東西，但直接關聯 D4 的 bootstrap/deploy 責任邊界）**：

`sudo bash` 沒帶 `-H`，`bootstrap.sh` 用 `$HOME` 組 clone 目的地（`REPO_DIR="${CYBER_DIR:-$HOME/cyber}"`），
在這個執行方式下 `$HOME` 解析成 **root 的家目錄**，不是測試者原本預期的使用者家目錄——
repo 因此 clone 在 `/root/cyber`，不是 `/home/<user>/cyber`。測試者一開始因此在自己的家目錄找不到任何東西，
花了額外時間才確認 clone 落點。

這不只是「操作者猜錯路徑」的問題——`bootstrap.sh` 檔頭註解寫的用法本身就是
`curl -fsSL <url>/bootstrap.sh | sudo bash`，沒有要求帶 `-H`，等於官方示範的用法會導致這個結果。
**建議 #144 D4（bootstrap/deploy 責任邊界）順便處理**：要嘛 `bootstrap.sh` 內部不依賴 `$HOME`（改用
`getent passwd "${SUDO_USER:-root}"` 取得原始使用者家目錄，或維持算 root 家目錄但在 completion summary
明確印出 clone 路徑，不要讓使用者自己猜），要嘛檔頭用法註解明確警告 `sudo bash` 下 `$HOME` 行為與帶
`-H`／`sudo -u` 的差異。

## Timing

| Phase | Elapsed |
|---|---|
| repo acquisition | TBD（未單獨量） |
| preflight/deps | TBD（未單獨量） |
| compose/build | TBD（未單獨量） |
| AI pull | TBD（未單獨量，但確認有跑——見下方 Result，ollama 容器已 healthy） |
| range-up | TBD（未單獨量；因無巢狀虛擬化，懷疑本輪根本沒跑到這段，見 Result 的 mode 判斷） |
| **total** | **約 8–9 分鐘**（測試者肉眼估計，非腳本輸出的精確值——這正是 #144 D1 要解的問題：目前部署過程沒有 phase timing 輸出，只能憑印象估） |

## Result

| 欄位 | 值 |
|---|---|
| mode | **推測 stack-only 或 degraded**——測試環境無巢狀虛擬化（見上），且部署完成後看到的 10 個容器裡沒有出現任何 range 相關產物的佐證（本輪未特別確認 `range-red*` 容器或 target VM 存不存在，屬於本次記錄的缺口，下一輪要補查 `virsh list --all` / `docker ps` 有沒有 `range-*`） |
| 容器狀態（部署完成 ~16 分鐘後查看） | 10 個容器 `Up`，多數 `healthy`：`grafana`、`evaluation-engine`、`alloy`、`receiver`、`postgres`、`prometheus`、`ollama`、`falco`、`vulnerable-app`、`loki` |
| Ollama / AI profile | **有跑**——`ollama` 容器在清單內，代表這台磁碟空間通過 `deploy.sh` 的 4GB 門檻，AI 輔助段落正常啟用 |
| Product UI (`:8090`) reachable | TBD（未測） |
| Grafana reachable | TBD（未測，但容器本身 healthy） |
| completion summary understandable without README | **否，目前談不上**——這正是 #144 D2 要補的東西：現況完成訊息只列 Grafana/Loki/Evidence API，沒有 Product UI (`:8090`) 入口，測試者是自己另外查才知道容器起了哪些 |

## Tester notes

- **哪一步最像卡住？** 沒有單獨記錄哪個 phase 卡住最久——但這本身就是 D0/D1 要解的問題：目前部署過程沒有 phase 進度輸出，「感覺卡住」與「正常在跑」肉眼分不出來，只能等結束後回頭看容器清單推測。
- **哪個提示看不懂？** 兩點：
  1. 部署完先想跑測試，但**測試指令本身沒有提示要加 `sudo`**——照著 completion 訊息裡列的指令直接跑會失敗，使用者要自己意識到需要 `sudo`（跟 `deploy.sh` 本身要求 root 是同一件事，但測試指令的提示沒有延續這個前提，容易讓人以為部署完就能用一般權限操作）。
  2. clone 路徑跟著 `$HOME` 悄悄跑到 `/root/cyber`（見上方 bug 說明），完成訊息沒有印出實際 clone 到哪裡，測試者得自己去猜/查。
- **跑完後是否知道下一步去哪？** 否——這正是 #144 整張票要解的核心問題（D2 Completion Summary、D3 Readiness verification），本輪測試直接印證了票裡列的三個 operator 問題都是真的。

## 待補（下一輪或另一位測試者接手時）

- [ ] Host/VM 硬體規格（CPU/RAM/Disk/OS/kernel）
- [ ] 精確 commit SHA
- [ ] 各 phase 的 elapsed time（目前只有肉眼估的 8–9 分鐘 total，D0 要求的逐 phase 拆解完全沒有）
- [ ] 確認這輪部署模式究竟是 full range／stack-only／degraded（`virsh list --all` 有沒有 `range-target`）
- [ ] Product UI (`:8090`) 與 Grafana 的 reachability 實測
- [ ] Cold run（repo 不存在、cache 全無）與 Warm run（repo 已存在、cache 大致在）至少各一筆——本輪屬於哪一種未明確記錄，需要補問測試者
- [ ] 在**有巢狀虛擬化**的主機（例如 `.88`）補跑一輪，取得 full range 模式下含 L2 range-up 的完整 timing——目前這筆數據完全沒有

## 額外發現（超出 D0 範圍，但直接可行動）

- `sudo bash` 不帶 `-H` 導致 `bootstrap.sh` clone 到 `/root/cyber`（見上方「Command」一節）——建議併入 #144 D4 的修復範圍，或視情況拆一張小票單獨處理，不要讓下一位測試者/使用者重踩。
