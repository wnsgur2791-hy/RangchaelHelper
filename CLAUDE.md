# 작업 메모 (Claude용)

- 프로그램 원본: `랭챌분석기/` (Python + tkinter, Windows 사용자용). 사용자 설명은 한국어, 비전공자 눈높이로.
- 새 버전 배포: 코드 수정 → `랭챌분석기/app/version.py` 버전 올리기 → commit/push(main) →
  `python tools/release.py X.Y.Z "바뀐 내용" ...` (gh CLI로 릴리스 생성 + update.zip / RangchaelHelper.zip 첨부).
  사용자 PC는 켤 때 `app/updater.py`가 releases/latest 의 update.zip 을 받아 자동 적용함.
- update.zip 에 .bat 파일은 반영되지 않음(실행 중 덮어쓰기 위험). 기록데이터/설정.json 은 절대 포함하지 않음.
- `config.json` 의 discord_webhook_b64 = 매주 기록을 받는 디스코드 웹훅(base64). 바꾸면 즉시 반영.
