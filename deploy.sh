#!/bin/bash
MSG="${1:-자동 업데이트 $(date '+%Y-%m-%d %H:%M')}"

echo "📋 변경된 파일 확인 중..."
git status --short

if [ -z "$(git status --porcelain)" ]; then
  echo "✅ 변경된 내용이 없습니다."
  exit 0
fi

echo "📦 변경사항 스테이징..."
git add -A

echo "💾 커밋: $MSG"
git commit -m "$MSG"

echo "🚀 GitHub로 푸시 중..."
git push origin main

echo "✅ 배포 완료!"
echo "🔗 대시보드: https://daye8965.github.io/hospital-news-dashboard-dev"
