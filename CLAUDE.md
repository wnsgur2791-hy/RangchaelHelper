# 작업 메모 (Claude용)

- 프로그램 원본: `랭챌분석기/` (Python + tkinter, Windows 사용자용). 사용자 설명은 한국어, 비전공자 눈높이로.
- 새 버전 배포: 코드 수정 → `python tools/release.py X.Y.Z "바뀐 내용" ...`
  (version.py 수정 + commit/push → 그 커밋 번호와 파일 목록(sha256)을 latest.json 에 적어 commit/push).
  이 세션 종류에서는 GitHub '릴리스' 생성이 막혀 있어서 릴리스 대신 latest.json 방식을 씀.
- 사용자 PC는 켤 때 `app/updater.py`가 main 의 latest.json 을 읽고, 다른 파일만 raw.githubusercontent.com/<repo>/<ref>/랭챌분석기/<경로> 에서 받아 적용.
  .bat 파일은 반영 안 됨(실행 중 덮어쓰기 위험). 학습 그림은 없는 것만 추가, name_fix.json 은 합침. 기록데이터/설정.json 은 건드리지 않음.
- `config.json` 의 discord_webhook_b64 = 매주 기록을 받는 디스코드 웹훅(base64). 바꾸면 즉시 반영.
