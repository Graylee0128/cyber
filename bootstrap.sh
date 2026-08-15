#!/usr/bin/env bash
# 一鍵部署入口：curl -fsSL https://raw.githubusercontent.com/Graylee0128/cyber/master/bootstrap.sh | sudo bash
#
# 已經是 root（sudo bash 進來的），下面不再另外 sudo。
# clone/更新 repo 到 $CYBER_DIR，然後直接呼叫 deploy.sh。
# 想帶 deploy.sh 的參數（例如 --install-deps）：
#   curl -fsSL <url>/bootstrap.sh | sudo bash -s -- --install-deps
set -euo pipefail

REPO_URL="https://github.com/Graylee0128/cyber.git"
REPO_DIR="${CYBER_DIR:-$HOME/cyber}"
BRANCH="${CYBER_REF:-master}"

[ "$(id -u)" = 0 ] || {
  echo "❌ 需要 root：curl -fsSL <url>/bootstrap.sh | sudo bash" >&2
  exit 1
}

command -v git >/dev/null 2>&1 || {
  echo "❌ 找不到 git，先裝 git 再跑一次" >&2
  exit 1
}

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

COMMIT="$(git -C "$REPO_DIR" rev-parse HEAD)"
echo "== 部署 commit $COMMIT（可事後對照 https://github.com/Graylee0128/cyber/commit/$COMMIT）=="

exec bash "$REPO_DIR/deploy.sh" "$@"
