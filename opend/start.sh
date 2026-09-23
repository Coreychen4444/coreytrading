#!/usr/bin/env bash
# OpenD 常驻启动：tmux 会话 `opend`，关终端不死；控制台（update / 重新登录）= tmux attach -t opend。
# 新版 OpenD 配置里的账号密码不再用于登录：首次在控制台交互登录选 Y 记住密码，
# 之后只认命令行 -login_account + -login_by_remember=1（见 opend/README.md）。
# 账号取自 OpenD.xml 的 login_account，不在这里另存一份。
#
# 安装目录：OPEND_INSTALL（OpenD.xml 所在目录；自更新原地覆盖，目录名不一定等于版本号）。
# 可执行文件：OPEND_BIN 覆盖；缺省依次找 macOS app bundle 与安装目录下的 OpenD。
set -euo pipefail

INSTALL="${OPEND_INSTALL:-$HOME/moomoo_OpenD}"
CFG="$INSTALL/OpenD.xml"
SESSION=opend

if [ -n "${OPEND_BIN:-}" ]; then
  BIN="$OPEND_BIN"
elif [ -x "$INSTALL/OpenD.app/Contents/MacOS/OpenD" ]; then
  BIN="$INSTALL/OpenD.app/Contents/MacOS/OpenD"
else
  BIN="$INSTALL/OpenD"
fi

[ -f "$CFG" ] || { echo "OpenD.xml not found: $CFG — set OPEND_INSTALL to the OpenD install directory" >&2; exit 1; }
[ -x "$BIN" ] || { echo "OpenD binary not found: $BIN — set OPEND_INSTALL (or OPEND_BIN)" >&2; exit 1; }
ACCOUNT=$(sed -n 's|^[[:space:]]*<login_account>\([^<]*\)</login_account>.*|\1|p' "$CFG" | head -1)
case "$ACCOUNT" in
  ""|FILL_ME*) echo "login_account missing or placeholder in $CFG — 配置被自更新覆盖？流程见 opend/README.md" >&2; exit 1 ;;
esac
grep -qE '^[[:space:]]*<rsa_private_key>' "$CFG" || { echo "rsa_private_key commented out in $CFG — API 加密连不上，先恢复配置" >&2; exit 1; }

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "already running in tmux session '$SESSION' — console: tmux attach -t $SESSION"
  exit 0
fi
if pgrep -f "$BIN" >/dev/null; then
  echo "OpenD already running outside tmux (pids: $(pgrep -f "$BIN" | tr '\n' ' ')) — stop it first" >&2
  exit 1
fi

tmux new-session -d -s "$SESSION" "'$BIN' -cfg_file='$CFG' -login_account=$ACCOUNT -login_by_remember=1"
echo "started in tmux session '$SESSION' — console: tmux attach -t $SESSION (detach: Ctrl-b d)"
