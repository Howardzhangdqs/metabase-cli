#!/usr/bin/env bash
# 发布 mbcli 单文件脚本到 GitHub Release
# 流程: 校验 tag → 以 tag 提交时间确定性生成 mbcli.py → 附 sha256 → gh release create
# 用法: ./scripts/release.sh <tag>       例: ./scripts/release.sh v0.1.0
# 前置: gh 已登录；remote origin 已配置且当前分支已推送
set -euo pipefail

TAG="${1:?用法: $0 <tag，如 v0.1.0>}"
cd "$(git rev-parse --show-toplevel)"

# 1) 工作树必须干净（生成物 mbcli.py 除外）
if ! git diff --quiet --ignore-submodules HEAD -- . ':(exclude)mbcli.py'; then
  echo "错误: 工作树有未提交改动，先提交再发布" >&2; exit 1
fi

# 2) tag 不存在则打在当前 HEAD（带注解）
if ! git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  git tag -a "$TAG" -m "mbcli $TAG"
  echo "已创建 tag $TAG"
fi

# 3) 以 tag 指向提交的时间戳做 SOURCE_DATE_EPOCH → 确定性生成
EPOCH=$(git log -1 --format=%ct "$TAG")
SOURCE_DATE_EPOCH="$EPOCH" python3 scripts/build-single.py
sha256sum mbcli.py > mbcli.py.sha256
echo "产物: mbcli.py ($(wc -c < mbcli.py) 字节, sha256 $(cut -d' ' -f1 mbcli.py.sha256 | cut -c1-16)…)"

# 4) 前置校验：remote 与 gh
[ -n "$(git remote get-url origin 2>/dev/null)" ] || { echo "错误: 未配置 remote origin" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "错误: gh 未登录" >&2; exit 1; }
git push origin "refs/tags/$TAG"

# 5) 发布（幂等: release 已存在则替换附件）
NOTES="mbcli $TAG 单文件脚本。

\`\`\`bash
# 目标机要求: Python >= 3.10，且 pip install typer httpx（rich 由 typer 携带）
python3 mbcli.py --help
python3 mbcli.py connect          # 交互式连上后零参数使用
\`\`\`

完整文档见仓库 USAGE.md；校验: sha256sum -c mbcli.py.sha256"
if gh release view "$TAG" >/dev/null 2>&1; then
  gh release upload "$TAG" mbcli.py mbcli.py.sha256 --clobber
  echo "Release $TAG 已存在，附件已替换"
else
  gh release create "$TAG" mbcli.py mbcli.py.sha256 \
    --title "mbcli $TAG" --notes "$NOTES"
  echo "已发布 $TAG"
fi
