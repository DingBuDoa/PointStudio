"""Capture the actual Qt interface with explicitly synthetic demonstration tasks."""
from pathlib import Path
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from qt_app import MainWindow, configure_application
from tracker_core import Options

app = QApplication([])
configure_application(app)
with tempfile.TemporaryDirectory() as directory:
    window = MainWindow(config_dir=directory, background=False)
    for name, backend, scale in [('庭院环绕 · 示例', 'fastmap', .5),
                                  ('建筑立面 · 示例', 'glomap', .8),
                                  ('桌面物体 · 示例', 'fastmap', 1)]:
        window.tasks.add(Path('C:/PointStudio-Demo/素材') / (name.split(' · ')[0] + '.mp4'),
                         Options(backend=backend, fps=10, image_scale=scale), name)
    window.rebuild_rows()
    window.show()
    app.processEvents()
    window.table.selectRow(0)
    app.processEvents()
    destination = ROOT / 'docs/images/workspace.png'
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(destination)):
        raise RuntimeError('Screenshot failed')
    window.poller.stop()
    window.hide()
    print(destination)
