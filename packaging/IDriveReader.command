#!/bin/bash
# IDriveReader - macOS baslatici. Cift tiklayin.
# Ilk calistirmada bu klasordeki gomulu Python'u kullanici klasorune acar (internet gerekmez,
# yonetici sifresi gerekmez). Sonraki calistirmalar dogrudan acilir.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
case "$(uname -m)" in
    arm64)  ARCH="aarch64" ;;
    x86_64) ARCH="x86_64" ;;
    *) echo "Desteklenmeyen islemci: $(uname -m)"; read -r -p "Kapatmak icin Enter..."; exit 1 ;;
esac

ARCHIVE="$(ls "$HERE"/runtime/cpython-*-"$ARCH"-apple-darwin-install_only.tar.gz 2>/dev/null | head -n 1 || true)"
if [ -z "$ARCHIVE" ]; then
    echo "runtime klasorunde $ARCH icin Python paketi bulunamadi. Klasoru eksiksiz kopyaladiniz mi?"
    read -r -p "Kapatmak icin Enter..."
    exit 1
fi

# USB bellekler (exFAT/FAT) sembolik baglanti tutamadigi icin Python kullanici klasorune acilir.
RUNTIME_ROOT="$HOME/Library/Application Support/IDriveReader"
RUNTIME="$RUNTIME_ROOT/$(basename "$ARCHIVE" .tar.gz)"
if [ ! -x "$RUNTIME/python/bin/python3" ]; then
    echo "Ilk calistirma: Python hazirlaniyor (bir kerelik, yaklasik 30 saniye)..."
    rm -rf "$RUNTIME.tmp"
    mkdir -p "$RUNTIME.tmp"
    tar -xzf "$ARCHIVE" -C "$RUNTIME.tmp"
    rm -rf "$RUNTIME"
    mv "$RUNTIME.tmp" "$RUNTIME"
fi

export IDRIVEREADER_HOME="$HERE"
echo "IDriveReader aciliyor... (bu Terminal penceresini program calisirken kapatmayin)"
exec "$RUNTIME/python/bin/python3" -I "$HERE/app/launcher.py" "$@"
