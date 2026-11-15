#!/usr/bin/env bash
# Installs ffmpeg WITHOUT depending on the Ubuntu apt mirror (it can hang on fresh runners) and makes sure
# libfribidi is present: Pillow needs it for libraqm text shaping, without which Hindi (Devanagari) captions
# render with broken matras. The Noto fonts are committed in assets/fonts. Downloads are cached in ~/.cache/media.
set -u
CACHE="$HOME/.cache/media"; mkdir -p "$CACHE"
get() {  # get <out> <url>...   (first URL that works wins)
  local out="$1"; shift
  [ -s "$out" ] && return 0
  for u in "$@"; do
    curl -fsSL --retry 3 --retry-delay 3 --connect-timeout 15 --max-time 240 -o "$out.part" "$u" && mv "$out.part" "$out" && return 0
    echo "  download failed: $u"
  done
  rm -f "$out.part"; return 1
}

# ---- ffmpeg + ffprobe
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  if get "$CACHE/ffmpeg.tar.xz" \
       https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz \
       https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz; then
    rm -rf /tmp/ffx && mkdir -p /tmp/ffx && tar -xf "$CACHE/ffmpeg.tar.xz" -C /tmp/ffx
    sudo install -m 755 $(find /tmp/ffx -type f \( -name ffmpeg -o -name ffprobe \) ) /usr/local/bin/
  else
    echo "static ffmpeg unavailable - trying apt"
    sudo timeout 240 apt-get update -qq && sudo timeout 300 apt-get install -y -qq ffmpeg
  fi
fi
ffmpeg -hide_banner -version | head -1 || { echo "ffmpeg missing"; exit 1; }

# ---- libfribidi (Hindi text shaping in Pillow)
if ! ldconfig -p 2>/dev/null | grep -q libfribidi; then
  echo "installing libfribidi0"
  sudo timeout 240 apt-get update -qq; sudo timeout 240 apt-get install -y -qq libfribidi0 || echo "libfribidi install failed"
fi
exit 0
