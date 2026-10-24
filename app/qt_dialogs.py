"""Native Qt task configuration, Blender preferences, and bounded log windows."""
from dataclasses import asdict
from pathlib import Path
import queue
import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QCheckBox, QFormLayout, QGroupBox, QScrollArea, QWidget,
    QDoubleSpinBox, QSpinBox, QPlainTextEdit, QFileDialog)

from tracker_core import Options, Pipeline, VIDEO_EXTENSIONS
from workload import video_info
from blender_runtime import discover_blenders, probe_blender
from qt_style import inset_numeric_fields
from app_info import SOLVER_CHOICES


def label(text, name='muted'):
    result = QLabel(text)
    result.setObjectName(name)
    result.setWordWrap(True)
    return result


def button(text, callback, primary=False):
    result = QPushButton(text)
    if primary:
        result.setObjectName('primary')
    result.clicked.connect(callback)
    return result


class VideoInput(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setPlaceholderText('拖入视频或文件夹，或手动输入完整路径（每行一个）')
        self.setMaximumHeight(82)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            values = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            self.setPlainText('\n'.join(values))
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class TaskDialog(QDialog):
    def __init__(self, parent, options, job=None, paths=None):
        super().__init__(parent)
        self.setWindowTitle('编辑任务' if job else '添加任务')
        self.resize(680, 760)
        self.setMinimumSize(590, 540)
        self.result_data = None
        self.job = job
        self.base = options
        self.metadata = {}
        self.pending = queue.Queue()
        self.generation = 0
        self.probing = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        head = QWidget()
        hl = QVBoxLayout(head)
        hl.setContentsMargins(24, 20, 24, 14)
        hl.addWidget(label(self.windowTitle(), 'sectionTitle'))
        hl.addWidget(label('设置素材与处理参数，任务将加入工作区。'))
        root.addWidget(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 0, 24, 16)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        basic = QGroupBox('基本信息')
        form = QFormLayout(basic)
        form.setSpacing(10)
        self.name = QLineEdit(job.get('name', '') if job else '')
        self.name.setPlaceholderText('留空使用视频文件名；批量添加时作为名称前缀')
        form.addRow('任务名称', self.name)
        self.source = VideoInput()
        self.source.setPlainText(job['path'] if job else '\n'.join(map(str, paths or [])))
        if job:
            self.source.setReadOnly(True)
        form.addRow('输入素材', self.source)
        selectors = QHBoxLayout()
        selectors.addWidget(button('选择视频…', self.browse))
        selectors.addWidget(button('选择文件夹…', self.browse_folder))
        selectors.addStretch()
        if not job:
            form.addRow('', selectors)
        self.video_note = label('正在读取视频信息…' if self.source.toPlainText() else '支持 MP4、MOV、MKV 等常见视频格式')
        form.addRow('', self.video_note)
        self.output = QLineEdit(options.output_root)
        outrow = QHBoxLayout()
        outrow.addWidget(self.output)
        outrow.addWidget(button('浏览…', self.browse_output))
        form.addRow('输出根目录', outrow)
        layout.addWidget(basic)
        processing = QGroupBox('处理设置')
        form = QFormLayout(processing)
        form.setSpacing(10)
        self.backend = QComboBox()
        for key, text in SOLVER_CHOICES.items(): self.backend.addItem(text, key)
        self.backend.setCurrentIndex(0 if options.backend == 'fastmap' else 1)
        form.addRow('求解方案', self.backend)
        self.quality = QComboBox()
        # Presets expose exact existing parameters; no synthetic solver accuracy switch.
        for title, values in [('默认 · 沿用当前参数', None), ('高 · SIFT 4096 / 匹配 15', (4096, 15)),
                              ('中 · SIFT 2048 / 匹配 10', (2048, 10)), ('低 · SIFT 1024 / 匹配 5', (1024, 5))]:
            self.quality.addItem(title, values)
        form.addRow('处理精度', self.quality)
        self.rate = QComboBox()
        for title, value in [('默认 · 原始帧率', 0), ('30 帧 / 秒', 30), ('60 帧 / 秒', 60), ('自定义', -1)]:
            self.rate.addItem(title, value)
        current = options.fps
        self.rate.setCurrentIndex(0 if not current else 1 if current == 30 else 2 if current == 60 else 3)
        self.custom_rate = QDoubleSpinBox()
        self.custom_rate.setRange(.01, 240)
        self.custom_rate.setDecimals(3)
        self.custom_rate.setValue(current or 24)
        self.custom_rate.setSuffix(' fps')
        raterow = QHBoxLayout()
        raterow.addWidget(self.rate, 1)
        raterow.addWidget(self.custom_rate)
        form.addRow('处理帧率', raterow)
        self.scale = QComboBox()
        for title, value in [('默认 · 原始尺寸', 1.), ('0.8 倍', .8), ('0.5 倍', .5), ('0.2 倍', .2)]:
            self.scale.addItem(title, value)
        index = self.scale.findData(options.image_scale)
        if index < 0:
            self.scale.addItem(f'{options.image_scale:g} 倍', options.image_scale)
            index = self.scale.count() - 1
        self.scale.setCurrentIndex(index)
        form.addRow('处理分辨率', self.scale)
        self.size_note = label('尺寸缩放由 FFmpeg 完成；SIFT 最大尺寸是独立参数。')
        form.addRow('', self.size_note)
        layout.addWidget(processing)
        output = QGroupBox('输出设置')
        form = QFormLayout(output)
        self.sequence = QCheckBox('计算完成后导出 JPG 序列帧')
        self.sequence.setChecked(options.export_sequence)
        self.sequence_size = QComboBox()
        self.sequence_size.addItem('跟随处理分辨率', False)
        self.sequence_size.addItem('原始视频分辨率 · 重新抽帧', True)
        self.sequence_size.setCurrentIndex(int(options.sequence_original))
        form.addRow(self.sequence)
        form.addRow('序列帧尺寸', self.sequence_size)
        self.blend = QCheckBox('计算完成后生成 Blender 文件')
        self.blend.setChecked(bool(job and job.get('blender')))
        form.addRow(self.blend)
        form.addRow(label('项目文件夹以任务名命名；点云在 point could，日志在 log，可选序列帧在 video。\nBlender 文件直接放在项目文件夹，名称含版本号；必要的求解缓存统一存入 _cache。'))
        layout.addWidget(output)
        self.advanced_toggle = QPushButton('›  高级参数')
        self.advanced_toggle.setCheckable(True)
        layout.addWidget(self.advanced_toggle)
        advanced = QGroupBox('FastMap / GLOMAP 参数')
        form = QFormLayout(advanced)
        self.max_size = QSpinBox(); self.max_size.setRange(256, 16384); self.max_size.setValue(options.max_size)
        self.overlap = QSpinBox(); self.overlap.setRange(1, 200); self.overlap.setValue(options.overlap)
        self.gpu_min = QSpinBox(); self.gpu_min.setRange(2, 1000000); self.gpu_min.setValue(options.gpu_min_images)
        self.camera = QComboBox()
        self.camera.addItem('自动估计畸变（默认）', 'radial'); self.camera.addItem('近似针孔（无畸变）', 'pinhole')
        self.camera.setCurrentIndex(max(0, self.camera.findData(options.camera_mode)))
        self.decode = QCheckBox('CUDA 视频解码（失败时沿用 CPU 解码回退）'); self.decode.setChecked(options.cuda_decode)
        self.color = QCheckBox('FastMap 点云取色'); self.color.setChecked(options.color)
        self.gpu = QCheckBox('GLOMAP 使用 CUDA / cuDSS 求解'); self.gpu.setChecked(options.glomap_gpu)
        for name, field in [('SIFT 最大尺寸', self.max_size), ('相邻匹配范围', self.overlap), ('GPU 最少图像', self.gpu_min), ('相机模型', self.camera)]:
            form.addRow(name, field)
        for field in (self.decode, self.color, self.gpu):
            form.addRow(field)
        layout.addWidget(advanced)
        advanced.setVisible(False)
        self.advanced_toggle.toggled.connect(advanced.setVisible)
        self.advanced_toggle.toggled.connect(lambda checked: self.advanced_toggle.setText(('⌄' if checked else '›') + '  高级参数'))
        self.quality.currentIndexChanged.connect(self.apply_quality)
        self.rate.currentIndexChanged.connect(self.update_controls)
        self.scale.currentIndexChanged.connect(self.update_controls)
        self.backend.currentIndexChanged.connect(self.update_controls)
        self.sequence.toggled.connect(self.update_controls)
        foot = QWidget()
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(24, 10, 24, 16)
        self.error = QLabel(); self.error.setWordWrap(True); self.error.setStyleSheet('color: #d8443c;'); self.error.hide()
        fl.addWidget(self.error)
        row = QHBoxLayout(); row.addWidget(label('每个任务独立保存参数')); row.addStretch()
        row.addWidget(button('取消', self.reject))
        self.confirm = button('保存修改' if job else '添加任务', self.accept_task, True)
        self.confirm.setDefault(True)
        row.addWidget(self.confirm)
        fl.addLayout(row); root.addWidget(foot)
        self.debounce = QTimer(self); self.debounce.setSingleShot(True); self.debounce.setInterval(350)
        self.debounce.timeout.connect(self.probe)
        self.source.textChanged.connect(lambda: self.debounce.start())
        self.poller = QTimer(self); self.poller.setInterval(100); self.poller.timeout.connect(self.receive); self.poller.start()
        self.update_controls()
        inset_numeric_fields(self)
        if self.source.toPlainText():
            self.debounce.start()

    def paths(self):
        values = []
        for line in self.source.toPlainText().splitlines():
            path = Path(line.strip().strip('"'))
            if not line.strip():
                continue
            if path.is_dir() and not (self.job and self.job.get('scene')):
                values.extend(sorted((p for p in path.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS), key=lambda p: p.name.casefold()))
            else:
                values.append(path)
        return list(dict.fromkeys(str(p.resolve()) for p in values))

    def browse(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '选择视频', str(Path.home()), '视频 (*.mp4 *.mov *.MOV *.mkv *.avi *.m4v *.webm *.mts *.m2ts *.wmv *.mpg *.mpeg);;所有文件 (*)')
        if paths:
            self.source.setPlainText('\n'.join(paths))

    def browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, '选择视频文件夹')
        if path:
            self.source.setPlainText(path)

    def browse_output(self):
        path = QFileDialog.getExistingDirectory(self, '选择输出目录', self.output.text())
        if path:
            self.output.setText(path)

    def probe(self):
        self.generation += 1
        generation = self.generation
        self.metadata = {}
        self.probing = True
        self.confirm.setEnabled(False)
        self.video_note.setText('正在读取视频信息…')
        try:
            paths = self.paths()
        except OSError as error:
            self.pending.put((generation, {}, str(error)))
            return
        def work():
            try:
                infos = {} if self.job and self.job.get('scene') else {path: video_info(path, 0) for path in paths}
                self.pending.put((generation, infos, '' if paths else '请选择至少一个视频。'))
            except Exception as error:
                self.pending.put((generation, {}, f'无法读取视频：{error}'))
        threading.Thread(target=work, daemon=True).start()

    def receive(self):
        while not self.pending.empty():
            generation, infos, error = self.pending.get_nowait()
            if generation != self.generation:
                continue
            self.probing = False
            self.metadata = infos
            self.confirm.setEnabled(True)
            if error:
                self.video_note.setText(error)
            elif len(infos) == 1:
                info = next(iter(infos.values()))
                self.video_note.setText(f"{info['width']} × {info['height']}  ·  {info['fps']:.3f} fps  ·  {info['duration']:.1f} 秒")
            else:
                self.video_note.setText(f'已读取 {len(infos)} 个视频 · 每个视频创建一个独立任务')
            self.update_controls()

    def apply_quality(self):
        values = self.quality.currentData()
        if values:
            self.max_size.setValue(values[0]); self.overlap.setValue(values[1])

    def update_controls(self, *_):
        self.custom_rate.setVisible(self.rate.currentData() == -1)
        rates = [info['fps'] for info in self.metadata.values() if info['fps'] > 0]
        rate = min(rates) if rates else None
        for index, threshold in ((1, 30), (2, 60)):
            self.rate.model().item(index).setEnabled(rate is not None and rate + .001 >= threshold)
        self.rate.model().item(3).setEnabled(rate is not None)
        if rate is not None:
            self.custom_rate.setMaximum(min(240, rate))
        self.sequence_size.setEnabled(self.sequence.isChecked())
        self.gpu.setEnabled(self.backend.currentData() == 'glomap')
        self.color.setEnabled(self.backend.currentData() == 'fastmap')
        self.camera.setEnabled(self.backend.currentData() == 'fastmap')
        if len(self.metadata) == 1:
            info = next(iter(self.metadata.values()))
            scale = self.scale.currentData()
            width, height = info.get('width', 0) or 0, info.get('height', 0) or 0
            w, h = (width, height) if scale == 1 else (max(2, int(width * scale / 2) * 2), max(2, int(height * scale / 2) * 2))
            self.size_note.setText(f'处理尺寸 {w} × {h} · FFmpeg 缩放，SIFT 最大尺寸独立控制')

    def accept_task(self):
        try:
            if self.probing or self.debounce.isActive():
                raise ValueError('请等待视频信息读取完成。')
            paths = self.paths()
            if not paths:
                raise ValueError('请选择至少一个视频。')
            scene = bool(self.job and self.job.get('scene'))
            if not scene and (set(paths) != set(self.metadata) or any(not Path(p).is_file() or Path(p).suffix.lower() not in VIDEO_EXTENSIONS for p in paths)):
                raise ValueError('视频路径无效或无法读取，请检查输入素材。')
            fps = self.rate.currentData()
            if fps == -1:
                fps = self.custom_rate.value()
            if fps and (not self.metadata or any(info['fps'] + .001 < fps for info in self.metadata.values())):
                raise ValueError('处理帧率不能超过任一输入视频的原始帧率。')
            if not self.output.text().strip():
                raise ValueError('请选择输出目录。')
            options = Options(**{**asdict(self.base), 'backend': self.backend.currentData(),
                'output_root': str(Path(self.output.text().strip()).resolve()), 'fps': fps,
                'max_size': self.max_size.value(), 'overlap': self.overlap.value(),
                'gpu_min_images': self.gpu_min.value(), 'camera_mode': self.camera.currentData(),
                'cuda_decode': self.decode.isChecked(), 'color': self.color.isChecked(),
                'glomap_gpu': self.gpu.isChecked(), 'image_scale': self.scale.currentData(),
                'export_sequence': self.sequence.isChecked(), 'sequence_original': self.sequence_size.currentData()})
            Pipeline(options).preflight()
            self.result_data = dict(paths=paths, name=self.name.text().strip(), options=options,
                                    auto_blender=self.blend.isChecked())
            self.accept()
        except (ValueError, OSError) as error:
            self.error.setText(str(error)); self.error.show()


class BlenderDialog(QDialog):
    def __init__(self, parent, settings, fps=30, opening=False):
        super().__init__(parent)
        self.setWindowTitle('打开 Blender 文件' if opening else 'Blender 导出设置')
        self.resize(650, 250 if opening else 550)
        self.result_data = None
        self.settings = dict(settings)
        self.opening = opening
        self.records = []
        self.pending = queue.Queue()
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 20, 24, 20); layout.setSpacing(12)
        layout.addWidget(label('选择 Blender 版本', 'sectionTitle'))
        row = QHBoxLayout(); self.versions = QComboBox(); self.versions.setMinimumWidth(340)
        row.addWidget(self.versions, 1); row.addWidget(button('添加版本…', self.browse)); layout.addLayout(row)
        self.note = label('正在查找本机 Blender…'); layout.addWidget(self.note)
        self.mesh = QCheckBox('同时生成可编辑网格（用于几何节点及渲染）'); self.mesh.setChecked(settings.get('blender_as_mesh', True))
        self.points = QCheckBox('显示彩色点（工程内保存颜色，无需额外安装插件）'); self.points.setChecked(settings.get('blender_draw_points', True))
        self.background = QCheckBox('预览和渲染使用抽帧背景'); self.background.setChecked(settings.get('blender_background_images', True))
        self.warnings = QCheckBox('隐藏镜头畸变警告'); self.warnings.setChecked(settings.get('blender_suppress_warnings', True))
        from blender_export import default_point_radius
        self.radius = QDoubleSpinBox(); self.radius.setDecimals(4); self.radius.setRange(.0001, 100); self.radius.setValue(default_point_radius(settings))
        self.fps = QDoubleSpinBox(); self.fps.setDecimals(3); self.fps.setRange(.01, 240); self.fps.setValue(float(fps))
        if not opening:
            group = QGroupBox('摄影测量场景'); form = QFormLayout(group)
            for field in (self.points, self.mesh, self.background, self.warnings): form.addRow(field)
            form.addRow('网格渲染点半径', self.radius); form.addRow('Blender FPS', self.fps)
            layout.addWidget(group)
            layout.addWidget(label('从本软件打开 Blender 文件会自动加载内置摄影测量插件。\n相机背景引用项目 _cache/images 图像，请保留缓存目录。'))
        layout.addStretch()
        row = QHBoxLayout(); row.addStretch(); row.addWidget(button('取消', self.reject))
        self.confirm = button('打开文件' if opening else '确认导出设置', self.accept_settings, True); self.confirm.setEnabled(False)
        row.addWidget(self.confirm); layout.addLayout(row)
        inset_numeric_fields(self)
        self.timer = QTimer(self); self.timer.setInterval(100); self.timer.timeout.connect(self.receive); self.timer.start()
        def discover():
            try: self.pending.put((discover_blenders(self.settings), None))
            except Exception as error: self.pending.put(([{'error': str(error)}], None))
        threading.Thread(target=discover, daemon=True).start()

    def receive(self):
        while not self.pending.empty():
            records, manual = self.pending.get_nowait()
            if records and 'error' in records[0]:
                self.note.setText(records[0]['error'])
                continue
            current = self.versions.currentData()
            preferred = manual or (current or {}).get('path') or self.settings.get('blender_path')
            for record in records:
                if record['path'] not in [r['path'] for r in self.records]: self.records.append(record)
            self.versions.clear()
            for record in self.records: self.versions.addItem(f"{record['version']}  |  {record['path']}", record)
            index = next((i for i, r in enumerate(self.records) if r['path'].casefold() == (preferred or '').casefold()), -1)
            if index < 0 and len(self.records) == 1 and not preferred: index = 0
            self.versions.setCurrentIndex(index)
            self.note.setText(f'找到 {len(self.records)} 个版本，请选择本次使用的版本。' if self.records else '未找到可用版本，请手动添加 blender.exe。')
            self.confirm.setEnabled(bool(self.records))

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择 Blender', '', 'Blender (blender.exe)')
        if not path: return
        self.note.setText('正在验证 Blender 版本…')
        def safe_work():
            try: self.pending.put(([probe_blender(path)], str(Path(path).resolve())))
            except Exception as error: self.pending.put(([{'error': str(error)}], None))
        threading.Thread(target=safe_work, daemon=True).start()

    def accept_settings(self):
        record = self.versions.currentData()
        if not record:
            self.note.setText('请选择本次使用的 Blender 版本。'); return
        if not self.opening and not self.mesh.isChecked() and not self.points.isChecked():
            self.note.setText('请至少选择彩色点或可编辑网格。'); return
        self.result_data = dict(record, fps=self.fps.value(), as_mesh=self.mesh.isChecked(),
            draw_points=self.points.isChecked(), suppress_warnings=self.warnings.isChecked(),
            background_images=self.background.isChecked(), point_radius=self.radius.value(),
            paths=[r['path'] for r in self.records])
        self.accept()
