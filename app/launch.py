"""Launch without a console and keep actionable startup diagnostics."""
import os
import sys
import traceback
from paths import ROOT, LOGS, runtime_environment
from app_info import APP_NAME

LOGS.mkdir(exist_ok=True)
os.environ.update(runtime_environment())
os.chdir(ROOT)
from log_management import rotate_log
rotate_log(LOGS / 'launcher.log')
with (LOGS / 'launcher.log').open('a',encoding='utf-8',buffering=1) as stream:
    sys.stdout = sys.stderr = stream
    try:
        if any(arg in ('--scene', '--video') or arg.startswith(('--scene=', '--video=')) for arg in sys.argv[1:]):
            from AutoTracker_GUI import main
        else:
            from qt_app import main
        main()
    except Exception:
        traceback.print_exc()
        if '--smoke-test' not in sys.argv and '--check-ui' not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f'启动失败，请查看日志：{LOGS / "launcher.log"}', APP_NAME, 16)
