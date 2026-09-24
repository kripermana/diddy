#!/usr/bin/env bash
# Susun repository git dari tarball rilis Diddy (termasuk rilis lama bernama LiteDDI):
# satu commit dan satu tag per versi.
#
#   tools/import-history.sh <folder-berisi-tarball> <folder-repo-baru>
#
# Tarball bernama diddy-X.Y.Z.tar.gz atau liteddi-X.Y.Z.tar.gz (root folder 'diddy/' atau 'liteddi/'),
# urutan diambil dari nomor versi, jadi riwayat LiteDDI 1.x dan Diddy 2.x tersambung dalam satu repo.
# Setiap commit berisi isi tarball versi itu, CHANGELOG.md sampai versi itu, dan .gitignore.
# Tanggal commit diambil dari waktu modifikasi tarball, kecuali COMMIT_DATE diberikan.
# Penulis: AUTHOR_NAME / AUTHOR_EMAIL, default dari `git config` atau kripermana.
set -euo pipefail
[ $# -eq 2 ] || { sed -n '2,10p' "$0"; exit 1; }
TARDIR="$(cd "$1" && pwd)"
REPO="$2"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHANGELOG="$HERE/CHANGELOG.md"
GITIGNORE="$HERE/.gitignore"
NAME="${AUTHOR_NAME:-$(git config --global user.name 2>/dev/null || echo kripermana)}"
EMAIL="${AUTHOR_EMAIL:-$(git config --global user.email 2>/dev/null || echo kripermana@users.noreply.github.com)}"

[ ! -e "$REPO/.git" ] || { echo "$REPO sudah berisi repository git, pakai folder baru."; exit 1; }
mapfile -t TARS < <(ls "$TARDIR"/liteddi-*.tar.gz "$TARDIR"/diddy-*.tar.gz 2>/dev/null \
  | sed -E 's/.*(liteddi|diddy)-([0-9.]+)\.tar\.gz/\2 &/' | sort -V | cut -d' ' -f2-)
[ ${#TARS[@]} -gt 0 ] || { echo "Tidak ada diddy-*.tar.gz atau liteddi-*.tar.gz di $TARDIR"; exit 1; }

mkdir -p "$REPO"; cd "$REPO"
git init -q -b main
git config user.name "$NAME"; git config user.email "$EMAIL"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

section() {  # changelog sampai versi $1 (mode=upto) atau hanya bagian versi $1 (mode=only)
  python3 - "$CHANGELOG" "$1" "$2" <<'PY'
import re, sys
text, ver, mode = open(sys.argv[1]).read(), sys.argv[2], sys.argv[3]
key = lambda v: tuple(int(x) for x in v.split("."))
parts = re.split(r"(?m)^(?=## \[)", text)
head, secs = parts[0], parts[1:]
def v(s): return re.match(r"## \[([\d.]+)\]", s).group(1)
if mode == "only":
    print("".join(s for s in secs if v(s) == ver).split("\n", 1)[-1].strip())
else:
    print((head + "".join(s for s in secs if key(v(s)) <= key(ver))).rstrip())
PY
}

DONE=()
for tb in "${TARS[@]}"; do
  ver="$(basename "$tb" | sed -E 's/(liteddi|diddy)-([0-9.]+)\.tar\.gz/\2/')"
  name="$(basename "$tb" | sed -E 's/-[0-9.]+\.tar\.gz//')"
  rm -rf "$WORK"/*; tar xzf "$tb" -C "$WORK"
  root="$WORK/$name"; [ -d "$root" ] || root="$(find "$WORK" -mindepth 1 -maxdepth 1 -type d | head -1)"
  [ -n "$root" ] && [ -d "$root" ] || { echo "Lewati $tb: tidak ada folder aplikasi di dalamnya"; continue; }
  find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
  cp -a "$root/." .
  find . -name __pycache__ -type d -prune -exec rm -rf {} +
  section "$ver" upto > CHANGELOG.md
  cp "$GITIGNORE" .gitignore
  body="$(section "$ver" only)"
  when="${COMMIT_DATE:-$(date -r "$tb" -R)}"
  git add -A
  label="Diddy"; [ "$name" = liteddi ] && label="LiteDDI"
  GIT_AUTHOR_DATE="$when" GIT_COMMITTER_DATE="$when" \
    git commit -q -m "$label $ver" -m "${body:-Rilis $ver}"
  git tag -a "v$ver" -m "$label $ver"
  DONE+=("$ver"); echo "  commit + tag v$ver"
done

ALL="$(grep -oE '^## \[[0-9.]+\]' "$CHANGELOG" | tr -d '#[] ' | sort -V)"
MISSING="$(comm -23 <(echo "$ALL") <(printf '%s\n' "${DONE[@]}" | sort -V))"
echo
echo "Selesai: ${#DONE[@]} versi di $REPO"
if [ -n "$MISSING" ]; then
  echo "Versi tanpa tarball (tidak dibuat commit-nya, tetap tercatat di CHANGELOG.md):"
  echo "$MISSING" | sed 's/^/  /' | paste -sd' '
fi
echo
echo "Langkah berikutnya:"
echo "  cd $REPO"
echo "  git remote add origin git@github.com:<username>/diddy.git"
echo "  git push -u origin main --tags"
