#!/bin/zsh -l
# launchd(com.noel.profile-card)가 6시간마다 실행: 카드를 다시 그리고, 바뀌었으면 커밋·push한다.
set -euo pipefail
cd "${0:A:h}/.."
git pull --ff-only --quiet
/usr/bin/python3 scripts/card.py
git add assets/card.svg
git diff --cached --quiet && exit 0
# GitHub 이미지 캐시(camo)를 피하려고 README의 ?t= 값을 바꾼다
ts=$(date +%s)
sed -i '' -E "s|(assets/card\.svg)(\?t=[0-9]+)?|\1?t=${ts}|" README.md
git add README.md
git commit --quiet -m "Update profile card"
git push --quiet
