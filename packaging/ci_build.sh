#!/bin/bash
# 一键：推送到 GitHub → 触发 Actions 构建 Windows 安装程序 → 下载产物到 dist/ci
#
# 用法: bash packaging/ci_build.sh <GitHub_PAT>
#
# PAT 申请：https://github.com/settings/tokens
#   - 经典 token：勾选 repo
#   - 细粒度 token：Contents 读+写、Actions 读、Metadata 读
set -euo pipefail

cd "$(dirname "$0")/.."

TOKEN="${1:-}"
if [[ -z "$TOKEN" ]]; then
  echo "用法: bash packaging/ci_build.sh <GitHub_PAT>" >&2
  exit 1
fi

OWNER="SnowBailey"
REPO="AVCAD"
WF="build-windows.yml"
API="https://api.github.com/repos/$OWNER/$REPO"
AUTH=(-H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json")

echo "▶ 1/4 推送到 GitHub（main 分支）"
git add -A
if ! git diff --cached --quiet; then
  git commit -q -m "chore: 同步打包脚本与工作流 ($(date +%Y-%m-%d' '%H:%M))"
fi
git branch -M main
# 用 insteadOf 临时注入凭据，不写入 .git/config
git -c url."https://x-access-token:${TOKEN}@github.com/${OWNER}/${REPO}".insteadOf="https://github.com/${OWNER}/${REPO}" \
    push -u origin main

echo "▶ 2/4 等待 Actions 构建（Windows）"
RUN_ID=""
for i in $(seq 1 20); do
  RUN_ID=$(curl -s "${AUTH[@]}" "$API/actions/workflows/$WF/runs?branch=main&per_page=1" \
           | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['workflow_runs'][0]['id'] if d.get('workflow_runs') else '')" 2>/dev/null || echo "")
  if [[ -n "$RUN_ID" ]]; then break; fi
  echo "   … 尚未出现运行记录，等待 15s（$i/20）"
  sleep 15
done
if [[ -z "$RUN_ID" ]]; then echo "❌ 未找到 workflow 运行记录" >&2; exit 1; fi
echo "   run_id=$RUN_ID"

for i in $(seq 1 60); do
  STATE=$(curl -s "${AUTH[@]}" "$API/actions/runs/$RUN_ID")
  STATUS=$(echo "$STATE" | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))")
  CONC=$(echo "$STATE"   | python3 -c "import sys,json;print(json.load(sys.stdin).get('conclusion','') or '')")
  echo "   [$i/60] status=$STATUS conclusion=$CONC"
  if [[ "$STATUS" == "completed" ]]; then
    if [[ "$CONC" != "success" ]]; then
      echo "❌ 构建失败（conclusion=$CONC）"
      echo "   详情: https://github.com/$OWNER/$REPO/actions/runs/$RUN_ID"
      exit 1
    fi
    break
  fi
  sleep 20
done

echo "▶ 3/4 下载构建产物"
mkdir -p dist/ci
rm -f dist/ci/artifact.zip
python3 - "$TOKEN" "$API" "$RUN_ID" <<'PY'
import json, subprocess, sys, urllib.request
token, api, run_id = sys.argv[1], sys.argv[2], sys.argv[3]
def get(url):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "avcad-ci"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
arts = get(f"{api}/actions/runs/{run_id}/artifacts").get("artifacts", [])
if not arts:
    print("没有可下载的产物", file=sys.stderr); sys.exit(1)
art = max(arts, key=lambda a: a["id"])
print("   产物:", art["name"], art["size_in_bytes"], "bytes")
subprocess.run(["curl", "-sL", "-H", f"Authorization: Bearer {token}",
                "-o", "dist/ci/artifact.zip", art["archive_download_url"]], check=True)
PY

echo "▶ 4/4 解包"
cd dist/ci
unzip -o -q artifact.zip
rm -f artifact.zip
cd ../..
echo
echo "✅ 完成，产物在 dist/ci："
ls -lh dist/ci
