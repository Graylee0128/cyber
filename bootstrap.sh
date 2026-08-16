#!/usr/bin/env bash
# 一鍵部署入口：curl -fsSL https://raw.githubusercontent.com/Graylee0128/cyber/master/bootstrap.sh | sudo bash
#
# 已經是 root（sudo bash 進來的），下面不再另外 sudo。
# clone/更新 repo 到 $CYBER_DIR（預設是呼叫 sudo 那個使用者的家目錄下的
# cyber/，不是 root 的——見下方 default_repo_dir），然後呼叫 deploy.sh。
# 想帶 deploy.sh 的參數（例如 --install-deps）：
#   curl -fsSL <url>/bootstrap.sh | sudo bash -s -- --install-deps
set -euo pipefail

REPO_URL="https://github.com/Graylee0128/cyber.git"
BRANCH="${CYBER_REF:-master}"

# 預設 clone 位置（#144 D4，真主機實測抓到的 bug，見 .scratch/144-bootstrap-ux/
# test-report.md）：檔頭示範的用法是 `curl ... | sudo bash`，**不帶** `-H`。
# `sudo` 不帶 `-H` 時不會改變 `$HOME`——它繼續是呼叫者（`SUDO_USER`）登入時的
# `$HOME`，不是 root 的。但這支腳本原本一律信任 `$HOME`，於是在某些
# shell／發行版組合下（環境變數繼承方式不同）曾經解析成 root 家目錄，
# repo 因此落在 `/root/cyber`，跟使用者從自己家目錄找 `~/cyber` 的直覺對不上。
# 這裡改成優先問「呼叫 sudo 的那個使用者」的家目錄；沒有 `SUDO_USER`（例如
# 直接以 root 身分登入跑，不經 sudo）才退回 `$HOME`。
default_repo_dir() {
  if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    local sudo_home
    sudo_home="$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6)"
    if [ -n "$sudo_home" ]; then
      echo "$sudo_home/cyber"
      return
    fi
  fi
  echo "$HOME/cyber"
}
REPO_DIR="${CYBER_DIR:-$(default_repo_dir)}"

# 責任邊界（#144 D4）：這支腳本只管「repo 怎麼來的、花了多久」，部署本身的
# phase timing／readiness／完成摘要全部是 deploy.sh 的事——直接執行
# `sudo bash deploy.sh` 的人也要拿到同一份完成體驗，不能只有 curl bootstrap
# 才看得到 URL 導引。
#
# 這裡把原本的 `exec bash deploy.sh` 換成一般呼叫：`exec` 會讓目前的行程被
# deploy.sh 取代，bootstrap 自己永遠拿不回控制權，也就量不出「repo 更新 +
# 部署」的總時間。換掉之後 exit code 不會再像 exec 那樣自動繼承，必須自己
# 存下 `$?` 並在最後顯式 `exit`——這條是 D4 acceptance criteria 明寫的：
# bootstrap exit code 必須等於 deploy.sh exit code，不得吞錯。
BOOTSTRAP_START="$(date +%s)"

[ "$(id -u)" = 0 ] || {
  echo "❌ 需要 root：curl -fsSL <url>/bootstrap.sh | sudo bash" >&2
  exit 1
}

command -v git >/dev/null 2>&1 || {
  echo "❌ 找不到 git，先裝 git 再跑一次" >&2
  exit 1
}

# 獨立一行、不跟 clone／更新的 banner 混在一起——真主機實測時這個路徑被
# 埋在後面一大串 git／docker 輸出裡沒被注意到，這裡先單獨講一次。
echo "repo 目錄：$REPO_DIR（想換位置：CYBER_DIR=/path/to/dir curl ... | sudo bash）"

REPO_START="$(date +%s)"
if [ -d "$REPO_DIR/.git" ]; then
  echo "== $REPO_DIR 已是 git repo，更新到 origin/$BRANCH（會 reset --hard，本地未提交的修改會不見）=="
  git -C "$REPO_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$REPO_DIR" checkout "$BRANCH"
  git -C "$REPO_DIR" reset --hard "origin/$BRANCH"
elif [ -e "$REPO_DIR" ]; then
  echo "❌ $REPO_DIR 已存在但不是 git repo。設 CYBER_DIR 指到別的路徑再跑一次" >&2
  exit 1
else
  echo "== clone $REPO_URL@$BRANCH 到 $REPO_DIR =="
  git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$REPO_DIR"
fi
REPO_ELAPSED=$(( $(date +%s) - REPO_START ))

COMMIT="$(git -C "$REPO_DIR" rev-parse HEAD)"
echo "== 部署 commit $COMMIT（可事後對照 https://github.com/Graylee0128/cyber/commit/$COMMIT）=="
echo "✓ Repository — ${REPO_ELAPSED}s"

# `set -e` 這裡刻意暫停：deploy.sh 失敗時要能往下走到「存 exit code、印
# bootstrap 總時間、原樣回傳」，不能讓失敗直接把整支 bootstrap 腰斬掉
# （那樣使用者連 deploy.sh 到底跑了多久都看不到）。
set +e
bash "$REPO_DIR/deploy.sh" "$@"
DEPLOY_EXIT=$?
set -e

BOOTSTRAP_ELAPSED=$(( $(date +%s) - BOOTSTRAP_START ))
echo
echo "Bootstrap total（repo acquisition + deploy.sh）：${BOOTSTRAP_ELAPSED}s"

exit "$DEPLOY_EXIT"
