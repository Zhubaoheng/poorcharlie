#!/bin/bash
# One-click mihomo proxy setup for investagent.
# Run on your server: bash deploy/setup-proxy.sh
#
# After running, add to .env:
#   CLASH_SOCKET=/tmp/mihomo.sock
#   CLASH_PROXY=http://127.0.0.1:7890
#   CLASH_GROUP=proxy-rotate

set -e

MIHOMO_DIR="$HOME/.config/mihomo"
MIHOMO_BIN="/usr/local/bin/mihomo"
CONFIG_SRC="$(dirname "$0")/mihomo-config.yaml"

# 1. Install mihomo if not present
if ! command -v mihomo &>/dev/null; then
    echo "Installing mihomo..."
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)  ARCH_NAME="amd64" ;;
        aarch64) ARCH_NAME="arm64" ;;
        arm64)   ARCH_NAME="arm64" ;;
        *)       echo "Unsupported arch: $ARCH"; exit 1 ;;
    esac
    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    URL="https://github.com/MetaCubeX/mihomo/releases/download/v1.19.8/mihomo-${OS}-${ARCH_NAME}-v1.19.8.gz"
    echo "Downloading from $URL"
    curl -L "$URL" -o /tmp/mihomo.gz
    gunzip -f /tmp/mihomo.gz
    chmod +x /tmp/mihomo
    sudo mv /tmp/mihomo "$MIHOMO_BIN"
    echo "mihomo installed at $MIHOMO_BIN"
else
    echo "mihomo already installed: $(which mihomo)"
fi

# 2. Copy config
mkdir -p "$MIHOMO_DIR"
cp "$CONFIG_SRC" "$MIHOMO_DIR/config.yaml"
echo "Config copied to $MIHOMO_DIR/config.yaml"

# 3. Start mihomo
if pgrep -x mihomo > /dev/null; then
    echo "mihomo already running, restarting..."
    sudo pkill mihomo
    sleep 2
fi

echo "Starting mihomo..."
nohup mihomo -d "$MIHOMO_DIR" > "$MIHOMO_DIR/mihomo.log" 2>&1 &
sleep 3

# 4. Verify
if [ -S /tmp/mihomo.sock ]; then
    echo ""
    echo "=== mihomo is running ==="
    echo "  HTTP proxy:  http://127.0.0.1:7890"
    echo "  Socket:      /tmp/mihomo.sock"
    echo "  Group:       proxy-rotate"
    echo ""
    echo "Add to .env:"
    echo "  CLASH_SOCKET=/tmp/mihomo.sock"
    echo "  CLASH_PROXY=http://127.0.0.1:7890"
    echo "  CLASH_GROUP=proxy-rotate"
    echo ""
    # Test connectivity
    echo "Testing proxy..."
    curl -s --max-time 5 -x http://127.0.0.1:7890 'https://82.push2.eastmoney.com/api/qt/clist/get?pn=1&pz=2&fs=m:0+t:6&fields=f12,f14' | head -c 100
    echo ""
    echo "Proxy OK!"
else
    echo "ERROR: mihomo socket not found at /tmp/mihomo.sock"
    echo "Check logs: cat $MIHOMO_DIR/mihomo.log"
    exit 1
fi
