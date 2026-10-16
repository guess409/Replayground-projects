#!/data/data/com.termux/files/usr/bin/bash
cd ~/Shoping || exit 1

# 1. cadangan dulu (file HTML lama + database)
c="cadangan_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$c" public
cp public/*.html "$c"/ 2>/dev/null
cp reymarket.db "$c"/ 2>/dev/null

# 2. pindahkan semua .html baru dari folder utama ke public/
for f in *.html; do
  [ -e "$f" ] && mv -f "$f" public/ && echo "dipindah: $f"
done

echo
echo "Cadangan ada di: $c"
echo "Isi public/:"
ls public
