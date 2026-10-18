# -*- coding: utf-8 -*-
"""실행.bat 이 부르는 시작 파일: (지난번 업데이트 확인) -> 새 버전 확인/적용 -> 프로그램 켜기"""
import os, sys, subprocess, traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)


def say(msg):
    try:
        print(msg, flush=True)
    except Exception:
        pass


def main():
    import updater
    try:
        updater.rollback_if_needed(say)            # 지난번 새 버전이 켜지다 죽었으면 되돌림
    except Exception as e:
        say(f'  (되돌리기 확인 실패: {e})')
    if os.environ.get('RANGCHAL_RELAUNCHED') != '1':       # 되돌린 직후 다시 켤 때는 업데이트하지 않음
        say('랭챌 분석기: 새 버전 확인 중...')
        try:
            updater.auto_update(say)
        except Exception as e:
            say(f'  (업데이트 중 오류: {e}. 지금 버전으로 켤게요)')
    try:
        import gui
    except Exception:
        traceback.print_exc()
        # 새 버전 코드가 아예 안 읽히면 바로 되돌리고 다시 켬 (한 번만)
        if os.environ.get('RANGCHAL_RELAUNCHED') != '1' and updater.rollback_if_needed(say):
            env = dict(os.environ, RANGCHAL_RELAUNCHED='1')
            sys.exit(subprocess.call([sys.executable, os.path.abspath(__file__)], env=env))
        raise
    gui.main()


if __name__ == '__main__':
    main()
