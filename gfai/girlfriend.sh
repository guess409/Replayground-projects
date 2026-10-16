#!/data/data/com.termux/files/usr/bin/bash
# Pacar AI - Termux, cuma butuh curl + jq
# Install: pkg install curl jq -y
# Pakai:   export GEMINI_API_KEY="isi_key_kamu"  lalu  bash pacar.sh

API_KEY="${GEMINI_API_KEY:?Set dulu: export GEMINI_API_KEY=\"key_kamu\"}"
MODEL="gemini-3.8-flash"
URL="https://generativelanguage.googleapis.com/v1beta/models/$MODEL:generateContent"
NAMA="Rey"

PERSONA="Kamu adalah pacar virtual yang manis, perhatian, dan sedikit manja. Panggil user dengan 'sayang'. Balas singkat, hangat, pakai bahasa Indonesia santai, boleh pakai emoji."

HIST="$HOME/.pacar_history.json"
[ -f "$HIST" ] || echo '[]' > "$HIST"

echo "💕 Pacar AI siap. Ketik 'exit' untuk keluar, 'reset' untuk hapus ingatan."

while true; do
  printf "\n%s: " "$NAMA"
  read -r input
  [ -z "$input" ] && continue
  [ "$input" = "exit" ] && break
  if [ "$input" = "reset" ]; then echo '[]' > "$HIST"; echo "(ingatan dihapus)"; continue; fi

  # tambah pesan user ke riwayat
  jq --arg t "$input" '. + [{"role":"user","parts":[{"text":$t}]}]' "$HIST" > "$HIST.tmp" && mv "$HIST.tmp" "$HIST"

  # susun body request (persona + riwayat, simpan 20 pesan terakhir)
  BODY=$(jq -n --arg p "$PERSONA" --slurpfile h "$HIST" \
    '{system_instruction:{parts:[{text:$p}]}, contents:($h[0][-20:])}')

  # coba sampai 5x kalau server sibuk (high demand / 503 / 429)
  for i in 1 2 3 4 5; do
    RES=$(curl -s -X POST "$URL" \
      -H "Content-Type: application/json" \
      -H "x-goog-api-key: $API_KEY" \
      -d "$BODY")
    REPLY=$(echo "$RES" | jq -r '.candidates[0].content.parts[0].text // empty')
    [ -n "$REPLY" ] && break
    CODE=$(echo "$RES" | jq -r '.error.code // 0')
    case "$CODE" in 429|500|503) echo "⏳ Server sibuk, coba lagi ($i/5)..."; sleep $((i*2));; *) break;; esac
  done

  if [ -z "$REPLY" ]; then
    echo "⚠️ Error: $(echo "$RES" | jq -r '.error.message // "tidak ada balasan"')"
    # buang pesan user terakhir supaya riwayat tidak rusak
    jq 'del(.[-1])' "$HIST" > "$HIST.tmp" && mv "$HIST.tmp" "$HIST"
    continue
  fi

  echo -e "\n💗 Pacar: $REPLY"

  # simpan balasan ke riwayat
  jq --arg t "$REPLY" '. + [{"role":"model","parts":[{"text":$t}]}]' "$HIST" > "$HIST.tmp" && mv "$HIST.tmp" "$HIST"
done

