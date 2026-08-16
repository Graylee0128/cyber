#!/usr/bin/env bash
# 一鍵部署入口：curl -fsSL https://raw.githubusercontent.com/Graylee0128/cyber/master/bootstrap.sh | sudo bash
#
# 已經是 root（sudo bash 進來的），下面不再另外 sudo。
# clone/更新 repo 到 $CYBER_DIR，然後呼叫 deploy.sh。
# 想帶 deploy.sh 的參數（例如 --install-deps）：
#   curl -fsSL <url>/bootstrap.sh | sudo bash -s -- --install-deps
set -euo pipefail

REPO_URL="https://github.com/Graylee0128/cyber.git"
REPO_DIR="${CYBER_DIR:-$HOME/cyber}"
BRANCH="${CYBER_REF:-master}"

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
