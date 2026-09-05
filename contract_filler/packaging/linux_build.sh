#!/usr/bin/env bash
# ============================================================ #
# 供用电合同手写填充工具 · Linux(银河麒麟V11 / Debian11) 容器内构建脚本
# ------------------------------------------------------------
# 由 .github/workflows/build.yml 在 debian:bullseye 容器内调用：
#   docker run --rm -e CF_ROOT=/app -v $GITHUB_WORKSPACE:/app \
#       -w /app debian:bullseye bash /app/packaging/linux_build.sh
#
# 独立成脚本文件的原因：
#   若把这段逻辑内联进 docker run ... bash -c "..." 的双引号串里，
#   外层 bash 会先在【主机】上展开 $onedir/$(find)/heredoc 等，
#   导致变量为空、heredoc 被误解析，报 "syntax error near `>'"。
#   单独成文件后，变量与 heredoc 都在【容器内】正常解析，无此坑。
#
# 产物：
#   /app/dist_linux/contract_filler_linux/   规范化后的 onedir（含 config/fonts/assets/samples 旁侧资源）
#   /app/dist_linux/contract_filler_linux/contract_filler   入口可执行文件
# ============================================================ #

set -e
set -o pipefail

echo '>>> 修复 Debian 11 apt 软件源（Bullseye 已 EOL，切到官方永久归档 archive）...'
# 背景：官方 debian:bullseye 镜像的 sources 指向 deb.debian.org 的 debian-security，
# 但 bullseye 已 EOL，该源的 pool 内容被逐步清空 -> 拉包 404。deb.debian.org 主源
# 同样处于"半归档"状态（Release 在、二进制在清空）。唯一可靠源是 Debian 官方永久
# 归档 archive.debian.org：其中 python3.9-venv、libgl1 等均存在；security 补丁已并入
# bullseye 主源，无需单独的 debian-security 行（archive 下根本没有 bullseye dist）。
# 用 http + trusted=yes：archive 不支持 https，镜像 keyring 与其同源可信任。
cat > /etc/apt/sources.list << 'SOURCES'
deb [trusted=yes] http://archive.debian.org/debian/ bullseye main contrib non-free
deb [trusted=yes] http://archive.debian.org/debian/ bullseye-updates main contrib non-free
SOURCES
# 有些镜像把源写在这两个文件里，一并清掉，避免残留旧 security 条目
rm -f /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true
# 清理过期 lists，强制按新源重建
rm -rf /var/lib/apt/lists/*
# archive 的 Release 日期较旧，跳过"有效期限"检查避免被拦
echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99no-check-valid
apt-get -o Acquire::Retries=5 update -q

echo '>>> 安装系统依赖...'
apt-get install -y python3 python3-pip python3-venv
# PyQt5 需要的系统级 Qt/X11 运行库（无头容器里也要收集齐）
apt-get install -y \
  libxcb1 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
  libxcb-xinerama0 libxcb-xkb1 libxcb-xfixes0 \
  libxkbcommon0 libxkbcommon-x11-0 libgl1 libegl1 \
  libdbus-1-3 libfontconfig1 fontconfig \
  libxrender1 libxext6 libx11-6 libxi6 libsm6 libice6

echo '>>> 建立虚拟环境...'
python3 -m venv /opt/venv
# shellcheck disable=SC1091
source /opt/venv/bin/activate
pip install --upgrade pip

echo '>>> 安装 Python 依赖（含 PyQt5）...'
# 国内加速可去掉注释下一行，或追加 -i 清华镜像
# pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
pip install pyinstaller pillow numpy openpyxl python-docx pyqt5

echo '>>> PyInstaller 打包（多模块 spec）...'
pyinstaller packaging/contract_tool_linux.spec --noconfirm \
  --distpath /app/dist_linux \
  --workpath /tmp/pybuild

echo '>>> 打包完成，产物列表（确认 onedir 真实目录名）：'
ls -la /app/dist_linux/

# ------------------------------------------------------------------
# 关键①：把 onedir 规范化成下游步骤固定引用的名字。
# PyInstaller 的 onedir 目录名 = spec 里 EXE/COLLECT 的 name 参数。
# 若 spec 里写的是 contract_filler（ASCII，推荐），产物会是
# dist_linux/contract_filler/ 而不是 dist_linux/contract_filler_linux/，
# 导致后面 cd dist_linux/contract_filler_linux 直接 No such file。
# 这里不猜名字，直接把 dist_linux/ 下唯一的 onedir 重命名为
# contract_filler_linux，并保证可执行文件名是 contract_filler。
# ------------------------------------------------------------------
cd /app/dist_linux
# dist_linux/ 下应当只有一个子目录（就是 onedir）
onedir=$(find . -mindepth 1 -maxdepth 1 -type d -name "contract_filler*" | head -1)
if [ -z "$onedir" ]; then
  echo "!!! 未在 dist_linux 下找到 onedir 目录，实际内容如下："; ls -la /app/dist_linux/; exit 1
fi
echo ">>> 检测到 onedir：$onedir"
# 重命名目录为固定名（若已经是该名则跳过）
if [ "$onedir" != "./contract_filler_linux" ]; then
  mv "$onedir" contract_filler_linux
fi
# 确保入口二进制名字固定为 contract_filler（若 spec 用了别的名）
if [ ! -f contract_filler_linux/contract_filler ]; then
  bin=$(find contract_filler_linux -mindepth 1 -maxdepth 1 -type f -perm -u+x | head -1)
  if [ -z "$bin" ]; then
    echo "!!! onedir 内找不到可执行文件。实际内容："; ls -la contract_filler_linux/; exit 1
  fi
  echo ">>> 重命名可执行文件：$(basename "$bin") -> contract_filler"
  mv "$bin" contract_filler_linux/contract_filler
  chmod +x contract_filler_linux/contract_filler
fi

# ------------------------------------------------------------------
# 关键②：把 fonts/config/assets/samples 复制到 exe 旁边。
# 程序在打包后通过 app_base()=exe所在目录 去定位这些资源
# （core/paths.py，DEFAULT_CONFIG=exe旁/config/template.json）。
# Windows 的 build_package.py 有专门的 copy_resources() 做这件事；
# Linux 容器里若只跑 pyinstaller，资源会被打进 _internal/，
# exe 旁边并没有 config/fonts/assets/samples，
# 冒烟测试在 cd 成功之后仍会因找不到模板/字体而崩溃。
# 这里补齐与 Windows 一致的行为：
# ------------------------------------------------------------------
echo '>>> 复制可编辑资源到 exe 旁（config/fonts/assets/samples）...'
for d in config fonts assets samples; do
  if [ -d /app/$d ]; then
    rm -rf /app/dist_linux/contract_filler_linux/$d
    cp -r /app/$d /app/dist_linux/contract_filler_linux/$d
    echo "   复制资源: $d/"
  else
    echo "   [警告] /app/$d 不存在，跳过"
  fi
done

echo '>>> 规范化后 dist_linux 结构：'
ls -la /app/dist_linux/
ls -la /app/dist_linux/contract_filler_linux/ | head -25

# ------------------------------------------------------------------
# 关键③：在容器内做无头冒烟测试（而不是放到 GitHub 主机上跑）。
# 原因：Qt/PyQt5 需要 libGL、libxkbcommon、xcb 等系统运行库，
#       这些只在本容器里装过；主机(ubuntu-latest)环境不确定，
#       放到主机跑可能报 could not load xcb / libGL。
#       冒烟在容器内、与目标环境(麒麟V11=Debian11)一致的地方验证最可靠。
# 产物写 /tmp/smoke（容器内），只用于判断成功与否，不落盘。
# ------------------------------------------------------------------
echo '>>> 容器内无头冒烟测试（QT_QPA_PLATFORM=offscreen）...'
cd /app/dist_linux/contract_filler_linux
rm -rf /tmp/smoke && mkdir -p /tmp/smoke
QT_QPA_PLATFORM=offscreen ./contract_filler \
  --excel samples/测试数据_3户.xlsx \
  --out /tmp/smoke \
  --fmt jpg
echo '>>> 冒烟输出文件：'
ls -la /tmp/smoke/
N=$(ls /tmp/smoke/*.jpg 2>/dev/null | wc -l)
echo ">>> 生成的 jpg 数量：$N"
if [ "$N" -lt 1 ]; then
  echo "!!! 冒烟失败：未生成任何 jpg"; exit 1
fi
echo '>>> 冒烟测试通过 ✔'

echo '>>> Linux 容器内构建全部完成 ✔'
