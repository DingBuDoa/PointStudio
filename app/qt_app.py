"""PySide6 desktop workspace. Existing solvers, reports and importers are reused."""
from collections import deque
from dataclasses import asdict, fields
from pathlib import Path
import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback

from PySide6.QtCore import (Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel,
                            QTimer, QSize, QUrl, QRect, QEvent)
from PySide6.QtGui import QColor, QFont, QFontDatabase, QDesktopServices, QAction, QKeySequence, QTextCursor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QTableView, QHeaderView, QAbstractItemView,
    QStackedWidget, QButtonGroup, QSplitter, QFormLayout, QGroupBox, QScrollArea,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QFileDialog, QMessageBox,
    QMenu, QDialog, QPlainTextEdit, QProgressBar, QInputDialog, QStyledItemDelegate,
    QStyleOptionViewItem, QStyle, QSizePolicy, QToolTip)

from tracker_core import Options, Pipeline, Cancelled
from paths import ROOT, APP, CONFIG, LOGS, OUTPUTS, BINARY_DIRS, saved_path, portable_path
from project_library import ProjectLibrary, model_ready
from project_layout import result_directory, result_report, blend_filename
from estimate import estimate_jobs
from hardware import get_hardware, describe_hardware
from log_management import rotate_log, RETENTION_CHOICES, expired_logs, delete_expired
from task_queue import TaskQueue, MOVABLE_STATES
from qt_dialogs import TaskDialog, BlenderDialog, button, label
from qt_style import STYLE, icon, inset_numeric_fields
from app_info import APP_NAME, APP_VERSION, SOLVER_CHOICES
from json_store import atomic_write_json
from task_storage import restore_jobs

SORT_ROLE = Qt.ItemDataRole.UserRole + 1
SOLVERS = {'fastmap': 'FastMap', 'glomap-gpu': 'GLOMap · GPU', 'glomap-cpu': 'GLOMap · CPU', 'glomap': 'GLOMap'}


def output_size(size):
    if size is None: return '—'
    for suffix, divisor in (('GiB', 2**30), ('MiB', 2**20), ('KiB', 2**10)):
        if size >= divisor: return f'{size / divisor:.1f} {suffix}'
    return f'{size} B'


def row_actions(row):
    pending = row['state'] in ('等待', '排队', '失败', '已停止') and row.get('queued')
    return [('edit', '编辑任务参数' if pending else '重命名', bool(pending or row.get('record') and not row['busy'])),
            ('play', '开始此任务', bool(pending and not row['busy'])),
            ('folder', '打开结果 / 输出目录', bool(row.get('directory'))),
            ('log', '查看任务日志', bool(row['result'] or row.get('scene_path'))),
            ('trash', '移入项目回收目录' if row.get('record') else '从队列移除',
             bool(row.get('record') and not row['busy'] or row.get('queued') and row['state'] not in ('运行中', '排队')))]


class TaskModel(QAbstractTableModel):
    headings = ['任务 / 素材', '求解方案', '处理设置', '输出大小', '创建时间', '状态 / 阶段', '操作']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headings)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headings[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid(): return None
        row, col = self.rows[index.row()], index.column()
        if role == Qt.ItemDataRole.UserRole: return row
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == 0 and row.get('queue_position'):
                return f"执行顺序：第 {row['queue_position']} 项\n{row['name']}\n{row['path']}"
            if col == 4: return '创建：' + (row['date'] or '旧记录未提供') + '\n完成：' + (row['result'].get('completed_at') or '尚未完成')
            if col == 3:
                size = row.get('bytes')
                return (f'输出大小：{output_size(size)}（{size:,} 字节）\n' if size is not None else '') + row['result'].get('model', '尚未生成点云')
            if col == 6: return '编辑 · 开始 · 输出目录 · 日志 · 移除（按任务状态启用）'
            return f"{row['name']}\n{row['path']}\n{row.get('error', '')}".strip()
        if role == SORT_ROLE:
            if col == 3: return row.get('bytes') if row.get('bytes') is not None else -1
            return row['date'] if col == 4 else row['state'] if col == 5 else row['cells'][col].casefold()
        if role == Qt.ItemDataRole.DisplayRole: return row['cells'][col]
        if role == Qt.ItemDataRole.ForegroundRole:
            return QColor('#1c1f26' if col == 0 else '#697382')

    def replace(self, rows):
        self.beginResetModel(); self.rows = rows; self.endResetModel()


class TaskFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.query, self.state = '', '全部'
        self.setSortRole(SORT_ROLE)

    def filterAcceptsRow(self, index, parent):
        row = self.sourceModel().rows[index]
        if self.query and self.query not in (row['name'] + ' ' + row['path'] + ' ' + row['cells'][1]).casefold():
            return False
        state = row['state']
        return (self.state == '全部' or self.state == '待处理' and state in ('等待', '排队')
                or self.state == '进行中' and state == '运行中'
                or self.state == '已完成' and state == '完成'
                or self.state == '异常' and state in ('失败', '已停止', '文件不完整'))


class TaskTableView(QTableView):
    """Fill unused space on window resize without competing with header dragging."""
    def __init__(self):
        super().__init__()
        self.name_fit_ready = False
        self.fit_viewport_width = None

    def fit_name_column(self):
        if not self.name_fit_ready: return
        width = self.viewport().width()
        used = sum(self.columnWidth(column) for column in range(1, self.model().columnCount())
                   if not self.isColumnHidden(column))
        # Refit only when the viewport changes, never during a header drag.
        # A manually adjusted name column must still follow later window sizes.
        self.setColumnWidth(0, max(220, width - used))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'fit_viewport_width') and self.viewport().width() != self.fit_viewport_width:
            self.fit_viewport_width = self.viewport().width()
            self.fit_name_column()


class TaskDelegate(QStyledItemDelegate):
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback
        self.icons = {(name, enabled): icon(name, '#697382' if enabled else '#c7ccd5')
                      for name in ('edit', 'play', 'folder', 'log', 'trash') for enabled in (True, False)}

    def action_rects(self, option):
        return [QRect(option.rect.left() + 6 + index * 26, option.rect.center().y() - 13, 26, 26) for index in range(5)]

    def paint(self, painter, option, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        if index.column() not in (0, 5, 6):
            return super().paint(painter, option, index)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ''
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        rect = option.rect.adjusted(12, 0, -12, 0)
        if index.column() == 6:
            for (name, _, enabled), target in zip(row_actions(row), self.action_rects(option)):
                self.icons[name, enabled].paint(painter, target.adjusted(5, 5, -5, -5))
        elif index.column() == 0:
            font = QFont(option.font); font.setWeight(QFont.Weight.Medium)
            painter.setFont(font); painter.setPen(QColor('#1c1f26'))
            painter.drawText(rect.adjusted(0, 7, 0, -25), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(row['name'], Qt.TextElideMode.ElideRight, rect.width()))
            font.setPixelSize(11); font.setWeight(QFont.Weight.Normal)
            painter.setFont(font); painter.setPen(QColor('#98a0ac'))
            painter.drawText(rect.adjusted(0, 27, 0, -5), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(row['path'], Qt.TextElideMode.ElideMiddle, rect.width()))
        else:
            state = row['state']
            color, fill = {'完成': ('#16865f', '#eaf7f1'), '运行中': ('#2f6bff', '#eaf0ff'),
                '失败': ('#d8443c', '#fdecea'), '已停止': ('#a77516', '#fff6e3'),
                '文件不完整': ('#d8443c', '#fdecea')}.get(state, ('#737c89', '#f0f2f5'))
            text = row.get('stage', '') if state == '运行中' else ('Blender 已生成' if state == '完成' and row.get('exports') else state)
            if state in MOVABLE_STATES and row.get('queue_position'):
                text += f" · {row['queue_position']}"
            if state == '完成' and row.get('export_error'): text = '完成 · 导出异常'
            text = text or state
            font = QFont(option.font); font.setPixelSize(12); painter.setFont(font)
            width = min(rect.width(), painter.fontMetrics().horizontalAdvance(text) + 16)
            badge = rect.adjusted(0, 15, 0, -15); badge.setWidth(width)
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(fill)); painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(QColor(color)); painter.drawText(badge.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter,
                painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width - 16))
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if index.column() == 6 and event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            row = index.data(Qt.ItemDataRole.UserRole)
            for (name, _, enabled), target in zip(row_actions(row), self.action_rects(option)):
                if target.contains(event.position().toPoint()) and enabled:
                    QTimer.singleShot(0, lambda r=row, key=name: self.callback(r, key))
                    return True
        return super().editorEvent(event, model, option, index)

    def helpEvent(self, event, view, option, index):
        if index.column() == 6:
            row = index.data(Qt.ItemDataRole.UserRole)
            for (_, title, enabled), target in zip(row_actions(row), self.action_rects(option)):
                if target.contains(event.pos()):
                    QToolTip.showText(event.globalPos(), title + ('' if enabled else '（当前不可用）'), view)
                    return True
        return super().helpEvent(event, view, option, index)


class MainWindow(QMainWindow):
    def __init__(self, config_dir=None, background=True):
        super().__init__()
        self.setWindowTitle(f'{APP_NAME} {APP_VERSION} · 相机求解与点云')
        self.setWindowIcon(icon('tasks', '#2f6bff'))
        self.resize(1360, 870); self.setMinimumSize(1020, 680)
        self.config_dir = Path(config_dir) if config_dir else CONFIG
        self.config_dir.mkdir(parents=True, exist_ok=True)
        LOGS.mkdir(exist_ok=True); OUTPUTS.mkdir(exist_ok=True)
        self.settings_path = self.config_dir / 'settings.json'
        try:
            self.config = json.loads(self.settings_path.read_text(encoding='utf-8'))
            if not isinstance(self.config, dict): self.config = {}
        except (OSError, ValueError): self.config = {}
        known = {f.name for f in fields(Options)}
        values = {key: value for key, value in self.config.items() if key in known}
        settings_error = None
        try:
            self.defaults = Options(**values).validate()
            for key in ('output_root', 'python'):
                setattr(self.defaults, key, str(saved_path(getattr(self.defaults, key), self.config.get('distribution_root'))))
        except (ValueError, TypeError) as error:
            self.defaults = Options(); settings_error = str(error)
        if Path(self.defaults.output_root).resolve() == ROOT / 'outputs':
            self.defaults.output_root = str(OUTPUTS)
        self.library = ProjectLibrary(self.config_dir / 'project-library.json')
        self.events = queue.Queue()
        if settings_error:
            self.events.put(dict(type='log', text=f'[设置恢复] 处理参数无效，已使用默认值：{settings_error}\n'))
        self.tasks = TaskQueue(self.events.put)
        self.records = {}
        self.project_loading = False
        self.project_refresh_pending = False
        self.export_pipeline = None
        self.exporting = False
        self.started = 0
        self.estimate_generation = 0
        self.log_lines = deque(); self.log_chars = 0
        self.log_dialog = self.log_widget = None
        self.last_scene = None
        self.background = background
        self.restore_tasks()
        self.build()
        self.rebuild_rows()
        self.poller = QTimer(self); self.poller.setInterval(100); self.poller.timeout.connect(self.poll); self.poller.start()
        self.estimate_timer = QTimer(self); self.estimate_timer.setSingleShot(True); self.estimate_timer.setInterval(300)
        self.estimate_timer.timeout.connect(self.estimate)
        if background:
            QTimer.singleShot(100, self.refresh_projects)
            QTimer.singleShot(300, self.check_environment)
            QTimer.singleShot(1200, self.automatic_cleanup)

    @property
    def busy(self):
        return self.tasks.running or self.exporting

    def build(self):
        central = QWidget(); shell = QHBoxLayout(central); shell.setContentsMargins(0, 0, 0, 0); shell.setSpacing(0)
        self.setCentralWidget(central)
        side = QWidget(); side.setObjectName('sidebar'); side.setFixedWidth(196)
        sl = QVBoxLayout(side); sl.setContentsMargins(12, 22, 12, 16); sl.setSpacing(4)
        brand = QHBoxLayout(); brand.setContentsMargins(6, 0, 0, 0); brand.setSpacing(10)
        mark = QLabel('PS'); mark.setObjectName('mark'); mark.setFixedSize(33, 33); mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.addWidget(mark)
        names = QVBoxLayout(); names.setSpacing(1); names.addWidget(label(APP_NAME, 'brand')); names.addWidget(label('相机求解与点云'))
        brand.addLayout(names); brand.addStretch(); sl.addLayout(brand); sl.addSpacing(25)
        self.pages = QStackedWidget(); self.nav_group = QButtonGroup(self)
        for i, (text, name) in enumerate((('任务', 'tasks'), ('设置', 'settings'), ('关于', 'about'))):
            nav = QPushButton('  ' + text); nav.setIcon(icon(name)); nav.setIconSize(QSize(17, 17)); nav.setObjectName('nav'); nav.setCheckable(True)
            self.nav_group.addButton(nav, i); sl.addWidget(nav)
        self.nav_group.idClicked.connect(self.pages.setCurrentIndex)
        self.nav_group.button(0).setChecked(True)
        sl.addStretch(); sl.addWidget(label('本地工作空间\nGLOMAP / FastMap\n内置摄影测量导入插件'))
        shell.addWidget(side)
        main = QWidget(); right = QVBoxLayout(main); right.setContentsMargins(0, 0, 0, 0); right.setSpacing(0)
        self.solver_notice = QWidget(); self.solver_notice.setObjectName('solverNotice')
        notice = QHBoxLayout(self.solver_notice); notice.setContentsMargins(20, 12, 20, 12)
        self.solver_notice_text = label('', 'solverNoticeText'); self.solver_notice_text.setTextFormat(Qt.TextFormat.PlainText)
        notice.addWidget(self.solver_notice_text, 1)
        self.solver_notice_log = ''
        notice.addWidget(button('查看日志', lambda: self.open_path(self.solver_notice_log)))
        notice.addWidget(button('关闭提醒', self.solver_notice.hide))
        self.solver_notice.hide(); right.addWidget(self.solver_notice); right.addWidget(self.pages, 1)
        shell.addWidget(main, 1)
        self.build_workspace(); self.build_settings(); self.build_about(); inset_numeric_fields(self)
        self.hardware_label = QLabel('正在检测运行环境…' if self.background else '本地运行环境')
        self.hardware_label.setObjectName('muted')
        self.hardware_label.setMinimumWidth(0)
        self.hardware_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.elapsed_label = label('00:00')
        self.statusBar().addWidget(self.hardware_label, 1); self.statusBar().addPermanentWidget(self.elapsed_label)
        self.statusBar().setSizeGripEnabled(True)
        add_action = QAction(self); add_action.setShortcut(QKeySequence('Ctrl+N')); add_action.triggered.connect(self.add_task); self.addAction(add_action)

    def build_workspace(self):
        page = QWidget(); page.setObjectName('workspace'); layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0); self.pages.addWidget(page)
        header = QWidget(); header.setObjectName('header'); hl = QVBoxLayout(header); hl.setContentsMargins(24, 22, 24, 16); hl.setSpacing(17)
        top = QHBoxLayout(); titles = QVBoxLayout(); titles.setSpacing(4)
        titles.addWidget(label('任务工作区', 'title')); self.summary_label = label('统一管理重建任务、点云与 Blender 场景'); titles.addWidget(self.summary_label)
        top.addLayout(titles, 1)
        self.add_button = button('添加任务', self.add_task, True); self.add_button.setIcon(icon('plus', 'white')); top.addWidget(self.add_button)
        hl.addLayout(top)
        tools = QHBoxLayout(); tools.setSpacing(8)
        self.start_button = button('开始队列', self.start_queue); self.start_button.setIcon(icon('play', '#2f6bff')); tools.addWidget(self.start_button)
        self.start_button.setToolTip('按执行顺序依次处理全部待执行任务')
        self.pause_button = button('暂停队列', self.toggle_pause); self.pause_button.setIcon(icon('pause')); tools.addWidget(self.pause_button)
        self.stop_button = button('停止', self.stop); self.stop_button.setIcon(icon('stop')); tools.addWidget(self.stop_button)
        tools.addStretch(); tools.addWidget(label('Ctrl + N  添加任务')); hl.addLayout(tools)
        filters = QHBoxLayout(); filters.setSpacing(12)
        self.search = QLineEdit(); self.search.setPlaceholderText('搜索任务名称、素材或求解方案'); self.search.setClearButtonEnabled(True); self.search.setMaximumWidth(300)
        self.search.setObjectName('taskSearch'); self.search.setFixedHeight(34)
        self.search.addAction(icon('search'), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self.apply_filter); filters.addWidget(self.search, 1)
        segments = QWidget(); segments.setObjectName('segments'); seg = QHBoxLayout(segments); seg.setContentsMargins(3, 3, 3, 3); seg.setSpacing(1)
        segments.setFixedHeight(34)
        self.filters = QButtonGroup(self)
        for i, text in enumerate(('全部', '待处理', '进行中', '已完成', '异常')):
            control = QPushButton(text); control.setObjectName('segment'); control.setCheckable(True); self.filters.addButton(control, i); seg.addWidget(control)
            control.setFixedHeight(28)
        self.filters.button(0).setChecked(True); self.filters.idClicked.connect(self.apply_filter)
        filters.addWidget(segments); filters.addStretch()
        right_tools = QHBoxLayout(); right_tools.setContentsMargins(0, 0, 0, 0); right_tools.setSpacing(10)
        self.sort_box = QComboBox(); self.sort_box.setObjectName('sortBox'); self.sort_box.setFixedSize(148, 34); self.sort_box.setToolTip('任务排序')
        self.sort_box.setPlaceholderText('按表头排序')
        for title, column, order in [('执行顺序', -1, Qt.SortOrder.AscendingOrder), ('最新创建', 4, Qt.SortOrder.DescendingOrder), ('最早创建', 4, Qt.SortOrder.AscendingOrder),
                ('名称 A → Z', 0, Qt.SortOrder.AscendingOrder), ('输出大小', 3, Qt.SortOrder.DescendingOrder)]:
            self.sort_box.addItem(title, (column, order))
        self.sort_box.setCurrentIndex(0)
        self.sort_box.currentIndexChanged.connect(self.apply_sort); right_tools.addWidget(self.sort_box); right_tools.addSpacing(6)
        self.priority_up = button('', lambda: self.move_selected(-1)); self.priority_down = button('', lambda: self.move_selected(1))
        for control, direction, title in ((self.priority_up, 'up', '上移选中任务，提高执行优先级'),
                                          (self.priority_down, 'down', '下移选中任务，降低执行优先级')):
            control.setObjectName('iconButton'); control.setIcon(icon(direction)); control.setFixedSize(34, 34)
            control.setToolTip(title + '；自动切换到执行顺序'); control.setEnabled(False); right_tools.addWidget(control)
        self.refresh_button = button('', self.refresh_projects); self.refresh_button.setObjectName('iconButton'); self.refresh_button.setIcon(icon('refresh')); self.refresh_button.setToolTip('刷新历史结果'); self.refresh_button.setFixedSize(36, 34); right_tools.addWidget(self.refresh_button)
        more = button('', lambda: None); more.setObjectName('iconButton'); more.setIcon(icon('more')); more.setFixedSize(36, 34); more.setToolTip('工作区操作')
        menu = QMenu(more); menu.addAction('添加已有项目目录…', self.import_projects); menu.addAction('打开默认输出目录', lambda: self.open_path(self.defaults.output_root)); menu.addAction('运行日志…', self.open_logs)
        more.setMenu(menu); right_tools.addWidget(more); filters.addLayout(right_tools); hl.addLayout(filters); layout.addWidget(header)
        self.banner = label('', 'banner'); self.banner.hide(); layout.addWidget(self.banner)
        split = QSplitter(Qt.Orientation.Vertical); split.setChildrenCollapsible(False)
        area = QWidget(); al = QVBoxLayout(area); al.setContentsMargins(24, 16, 24, 12); al.setSpacing(8)
        self.table = TaskTableView(); self.model = TaskModel(self); self.proxy = TaskFilter(self); self.proxy.setSourceModel(self.model)
        self.table.setModel(self.proxy); self.table.setItemDelegate(TaskDelegate(self.table, self.row_action))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False); self.table.setWordWrap(False); self.table.setSortingEnabled(True)
        self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        self.table.verticalHeader().hide(); self.table.verticalHeader().setDefaultSectionSize(58)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.horizontalHeader().setMinimumSectionSize(75)
        for column, width in enumerate((300, 110, 125, 110, 125, 125, 142)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.table.name_fit_ready = True
        self.table.horizontalHeader().sortIndicatorChanged.connect(self.sort_indicator_changed)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.selectionModel().selectionChanged.connect(self.update_details)
        self.table.doubleClicked.connect(self.row_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        al.addWidget(self.table, 1)
        self.empty = QLabel(self.table.viewport()); self.empty.setObjectName('muted'); self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.empty.setText('还没有任务\n\n点击「添加任务」选择视频，或从右上角菜单加入已有项目目录')
        self.table.viewport().installEventFilter(self)
        self.estimate_label = label('预估任务时长：添加视频后自动估算'); al.addWidget(self.estimate_label)
        self.state_label = label('就绪'); al.addWidget(self.state_label)
        self.progress = QProgressBar(); self.progress.setRange(0, 5); self.progress.setValue(0); self.progress.setTextVisible(False); al.addWidget(self.progress)
        split.addWidget(area)
        detail = QWidget(); detail.setObjectName('detail'); dl = QVBoxLayout(detail); dl.setContentsMargins(24, 14, 24, 16); dl.setSpacing(10)
        row = QHBoxLayout(); self.detail_title = label('任务详情', 'sectionTitle'); row.addWidget(self.detail_title); row.addStretch()
        self.edit_button = button('编辑参数', self.edit_task); self.edit_button.setIcon(icon('edit')); row.addWidget(self.edit_button)
        self.result_button = button('打开结果', self.open_result); self.result_button.setIcon(icon('folder')); row.addWidget(self.result_button)
        self.blend_button = button('生成 Blender 文件…', self.export_selected); row.addWidget(self.blend_button)
        self.more_button = button('更多', lambda: None); self.more_button.setMenu(self.selection_menu()); row.addWidget(self.more_button)
        dl.addLayout(row)
        self.detail_info = label('选择任务以查看参数、处理状态及输出文件。'); self.detail_info.setMinimumHeight(35); dl.addWidget(self.detail_info)
        form = QFormLayout(); form.setContentsMargins(0, 0, 0, 0); form.setSpacing(8)
        self.source_path = QLineEdit(); self.source_path.setReadOnly(True); self.source_path.setPlaceholderText('未选择任务')
        self.result_path = QLineEdit(); self.result_path.setReadOnly(True); self.result_path.setPlaceholderText('完成重建后显示结果位置')
        form.addRow('输入素材', self.source_path); form.addRow('输出结果', self.result_path); dl.addLayout(form)
        detail.setMinimumHeight(190); split.addWidget(detail); split.setStretchFactor(0, 1); split.setStretchFactor(1, 0); split.setSizes([470, 200])
        layout.addWidget(split, 1)

    def eventFilter(self, watched, event):
        if hasattr(self, 'empty') and watched is self.table.viewport():
            self.empty.setGeometry(self.table.viewport().rect())
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'table'):
            compact = self.width() < 1200
            if compact != getattr(self, 'compact_table', None):
                self.compact_table = compact
                self.table.setColumnHidden(4, compact)
                self.table.setColumnHidden(1, compact)
                self.table.fit_name_column()

    def build_settings(self):
        scroll = QScrollArea(); scroll.setWidgetResizable(True); body = QWidget(); body.setObjectName('workspace'); scroll.setWidget(body)
        self.pages.addWidget(scroll); layout = QVBoxLayout(body); layout.setContentsMargins(28, 24, 28, 24); layout.setSpacing(16)
        layout.addWidget(label('设置', 'title')); layout.addWidget(label('管理新任务的默认参数与本地环境。已添加的任务保留各自设置。'))
        self.default_controls = {}
        def settings_form(title):
            group = QGroupBox(title); form = QFormLayout(group)
            form.setVerticalSpacing(14); form.setHorizontalSpacing(32)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return group, form
        group, form = settings_form('任务默认设置')
        for key, title, low, high in [('fps', '抽帧 FPS（0 = 原始）', 0, 240), ('max_size', 'SIFT 最大尺寸', 256, 16384),
                ('overlap', '相邻匹配范围', 1, 200), ('gpu_min_images', 'GLOMAP GPU 最少图像', 2, 1000000)]:
            control = QDoubleSpinBox() if key == 'fps' else QSpinBox(); control.setRange(low, high); control.setValue(getattr(self.defaults, key)); self.default_controls[key] = control; form.addRow(title, control)
            control.setMaximumWidth(300)
        backend = QComboBox()
        for key, text in SOLVER_CHOICES.items(): backend.addItem(text, key)
        backend.setCurrentIndex(0 if self.defaults.backend == 'fastmap' else 1)
        self.default_controls['backend'] = backend; form.addRow('求解方案', backend)
        camera = QComboBox(); camera.addItem('自动估计畸变（FastMap）', 'radial'); camera.addItem('近似针孔（FastMap）', 'pinhole'); camera.setCurrentIndex(max(0, camera.findData(self.defaults.camera_mode))); self.default_controls['camera_mode'] = camera; form.addRow('相机模型', camera)
        for key, title in [('cuda_decode', 'CUDA 视频解码'), ('color', 'FastMap 点云取色'), ('glomap_gpu', 'GLOMAP 使用 CUDA / cuDSS 求解')]:
            control = QCheckBox(title); control.setChecked(getattr(self.defaults, key)); self.default_controls[key] = control; form.addRow(control)
        layout.addWidget(group)
        group, form = settings_form('路径与环境')
        for key, title in [('output_root', '默认输出目录'), ('python', 'FastMap Python')]:
            control = QLineEdit(getattr(self.defaults, key)); self.default_controls[key] = control
            row = QHBoxLayout(); row.addWidget(control, 1); row.addWidget(button('浏览…', lambda checked=False, k=key: self.browse_default(k))); form.addRow(title, row)
        form.addRow(label('每个项目自动建立同名文件夹：point could（点云）、log（日志）、video（可选序列帧）、_cache（必要缓存）。'))
        self.environment_info = label('使用随软件提供的独立运行环境。'); form.addRow(self.environment_info)
        form.addRow(button('重新检测运行环境', self.check_environment)); layout.addWidget(group)
        group, form = settings_form('Blender 与日志')
        self.blender_fps = QDoubleSpinBox(); self.blender_fps.setRange(.01, 240); self.blender_fps.setDecimals(3); self.blender_fps.setValue(float(self.config.get('blender_fps', 30))); form.addRow('默认 Blender FPS', self.blender_fps)
        self.retention = QComboBox()
        for title, days in RETENTION_CHOICES.items(): self.retention.addItem(title, days)
        self.retention.setCurrentIndex(max(0, self.retention.findData(self.config.get('log_retention_days', 0)))); form.addRow('日志保留策略', self.retention)
        form.addRow(label('启动时按修改时间清理过期诊断日志；不删除视频、点云、Blender 文件或结果记录。'))
        row = QHBoxLayout(); row.addWidget(button('预览并清理过期日志…', self.clean_logs)); row.addWidget(button('打开运行日志', self.open_logs)); row.addStretch(); form.addRow(row); layout.addWidget(group)
        row = QHBoxLayout(); row.addWidget(button('重置默认处理参数', self.reset_defaults)); row.addStretch(); row.addWidget(button('保存设置', self.save_settings_clicked, True)); layout.addLayout(row)
        self.settings_note = label(''); layout.addWidget(self.settings_note); layout.addStretch()
        for group in body.findChildren(QGroupBox):
            form = group.layout()
            for index in range(form.rowCount()):
                item = form.itemAt(index, QFormLayout.ItemRole.LabelRole)
                if item and item.widget(): item.widget().setMinimumWidth(190)

    def build_about(self):
        from qt_about import about_page
        self.pages.addWidget(about_page(self.open_path))

    def selection_menu(self):
        menu = QMenu(self)
        self.selection_actions = {}
        for key, title, callback in [('open_blend', '打开 Blender 文件…', self.open_blend), ('logs', '打开任务日志目录', self.open_task_logs),
            ('log_window', '查看运行日志…', self.open_logs), ('source', '打开素材位置', self.open_source), ('sequence', '打开导出序列帧', self.open_sequence),
            ('rename', '重命名', self.rename_selected), ('copy', '复制结果路径', self.copy_result), ('remove', '从队列移除所选任务', self.remove_selected),
            ('archive', '移入项目回收目录…', self.archive_selected)]:
            action = menu.addAction(title, callback); self.selection_actions[key] = action
        return menu

    def selected_rows(self):
        return [self.proxy.index(index.row(), 0).data(Qt.ItemDataRole.UserRole) for index in self.table.selectionModel().selectedRows()]

    def selected(self):
        rows = self.selected_rows(); return rows[0] if len(rows) == 1 else None

    def rebuild_rows(self):
        selected = [row['id'] for row in self.selected_rows()]
        records_by_model = {str(Path(r['result']['model']).resolve()): r for r in self.records.values()}
        rows, seen = [], set()
        jobs = self.tasks.snapshot()
        ordered_ids = [iid for iid, job in jobs.items() if job['state'] == '运行中']
        ordered_ids += [iid for iid, job in jobs.items() if job['state'] in (('排队',) if self.tasks.running else MOVABLE_STATES)]
        positions = {iid: index + 1 for index, iid in enumerate(ordered_ids)}
        for iid, job in jobs.items():
            result = job.get('result', {}); record = records_by_model.get(str(Path(result['model']).resolve())) if result.get('model') else None
            if record: seen.add(record['id'])
            options = job['options']; backend = options['backend']
            if backend == 'glomap': backend = 'glomap-gpu' if options['glomap_gpu'] else 'glomap-cpu'
            row = {**job, 'result': result, 'record': record, 'exports': record['exports'] if record else [],
                   'date': job.get('created_at', '') or (record.get('created', '') if record else result.get('completed_at', '')), 'solver': backend,
                   'bytes': record.get('bytes') if record else None, 'queued': True, 'queue_position': positions.get(iid)}
            if record: row['name'] = record['name']
            if row['state'] == '完成' and not (record['ready'] if record else model_ready(result)):
                row['state'] = '文件不完整'
            row['settings'] = f"{options['fps']:g} fps" if options['fps'] else '原始帧率'
            row['settings'] += f" / {options.get('image_scale', 1):g}×"
            rows.append(row)
        for record in self.records.values():
            if record['id'] in seen: continue
            result = record['result']
            work = result.get('workload') or {}
            settings = result.get('estimate_settings') or {}
            fps = settings.get('fps', 0)
            rows.append(dict(id=record['id'], name=record['name'], path=record['source'], result=result,
                record=record, state='完成' if record['ready'] else '文件不完整', exports=record['exports'],
                date=record.get('created', record['completed']), solver=result.get('backend', '未知'),
                bytes=record.get('bytes'), queued=False,
                settings=(f'{fps:g} fps' if fps else '原始帧率') + f" / {settings.get('image_scale', 1):g}×"))
        rows.sort(key=lambda row: 0 if row['state'] == '运行中' else
                  1 if row.get('queued') and row['state'] in MOVABLE_STATES else 2)
        for row in rows:
            metrics = row['result'].get('metrics', {})
            row['busy'] = self.busy
            directory = (row['record']['directory'] if row.get('record') else
                         str(result_directory(row['result'])) if row['result'].get('model') else
                         row.get('scene_path') or row.get('options', {}).get('output_root'))
            row['directory'] = directory if directory and Path(directory).is_dir() else None
            display_date = row['date'][5:16].replace('T', ' ') if row['date'] else '—'
            row['cells'] = [row['name'], SOLVERS.get(row['solver'], row['solver']), row['settings'], output_size(row.get('bytes')),
                            display_date, row['state'], '']
        self.model.replace(rows)
        for index in range(self.proxy.rowCount()):
            if self.proxy.index(index, 0).data(Qt.ItemDataRole.UserRole)['id'] in selected:
                self.table.selectionModel().select(self.proxy.index(index, 0), self.table.selectionModel().SelectionFlag.Select | self.table.selectionModel().SelectionFlag.Rows)
        self.summary_label.setText(f'{len(rows)} 项任务与历史结果 · 每次重建独立保留')
        self.update_empty(); self.update_details(); self.update_busy()

    def apply_filter(self, *_):
        self.proxy.beginFilterChange()
        self.proxy.query = self.search.text().strip().casefold(); self.proxy.state = self.filters.checkedButton().text()
        self.proxy.endFilterChange(QSortFilterProxyModel.Direction.Rows); self.update_empty(); self.update_details()

    def apply_sort(self, *_):
        if hasattr(self, 'table') and self.sort_box.currentData() is not None:
            column, order = self.sort_box.currentData()
            self.table.sortByColumn(column, order)

    def sort_indicator_changed(self, column, order):
        index = next((i for i in range(self.sort_box.count()) if self.sort_box.itemData(i)[0] == column and self.sort_box.itemData(i)[1] == order), -1)
        self.sort_box.blockSignals(True); self.sort_box.setCurrentIndex(index); self.sort_box.blockSignals(False)

    def update_priority_buttons(self):
        ids = [row['id'] for row in self.selected_rows()]
        self.priority_up.setEnabled(not self.exporting and self.tasks.can_move(ids, -1))
        self.priority_down.setEnabled(not self.exporting and self.tasks.can_move(ids, 1))

    def move_selected(self, direction):
        ids = [row['id'] for row in self.selected_rows()]
        if self.exporting or not self.tasks.move(ids, direction): return
        self.sort_box.setCurrentIndex(0)
        self.persist_tasks(); self.rebuild_rows()
        selected = self.table.selectionModel().selectedRows()
        if selected: self.table.scrollTo(selected[0])
        self.state_label.setText('执行顺序已保存；当前任务完成后按新顺序继续。' if self.tasks.running else
                                '执行顺序已保存；点击「开始队列」依次处理全部待执行任务。')

    def row_action(self, row, action):
        for index in range(self.proxy.rowCount()):
            if self.proxy.index(index, 0).data(Qt.ItemDataRole.UserRole)['id'] == row['id']:
                self.table.selectRow(index)
                break
        current = self.selected()
        if not current or current['id'] != row['id']: return
        available = {key: enabled for key, _, enabled in row_actions(current)}
        if not available.get(action): return
        if action == 'edit':
            if current.get('queued') and current['state'] in ('等待', '排队', '失败', '已停止'): self.edit_task()
            else: self.rename_selected()
        elif action == 'play': self.start_queue([current['id']])
        elif action == 'folder': self.open_path(current['directory'])
        elif action == 'log': self.open_selected_log_view()
        elif action == 'trash':
            if current.get('record'): self.archive_selected()
            else: self.remove_selected()

    def open_selected_log_view(self):
        row = self.selected()
        if not row: return
        if row['state'] == '运行中': self.open_logs(); return
        path = Path(self.task_log_path())
        dialog = QDialog(self); dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle('任务日志 · ' + row['name']); dialog.resize(940, 580)
        layout = QVBoxLayout(dialog); layout.setContentsMargins(20, 16, 20, 20)
        head = QHBoxLayout(); head.addWidget(label('任务日志', 'sectionTitle')); head.addStretch()
        head.addWidget(button('打开完整日志目录', lambda: self.open_path(path))); layout.addLayout(head)
        layout.addWidget(label(str(path)))
        preview = QPlainTextEdit(); preview.setReadOnly(True); preview.setFont(QFont('Consolas', 10)); preview.document().setMaximumBlockCount(1500)
        preview.setPlainText('正在读取最近的日志…'); layout.addWidget(preview, 1)
        pending = queue.Queue()
        def work():
            try:
                files = sorted(path.glob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True)[:8]
                pieces = []
                for file in reversed(files):
                    with file.open('rb') as stream:
                        stream.seek(max(0, file.stat().st_size - 16384))
                        text = stream.read(16384).decode('utf-8', errors='replace')
                        pieces.append(f'\n── {file.name} ──\n' + text)
                pending.put(''.join(pieces) or '尚无日志文件。')
            except OSError as error: pending.put(str(error))
        timer = QTimer(dialog); timer.setInterval(100)
        def receive():
            if not pending.empty():
                text = pending.get_nowait()
                # Bound line length as well as total memory to keep wrapping responsive.
                text = '\n'.join(line[:2000] + ('…' if len(line) > 2000 else '') for line in text.splitlines())
                preview.setPlainText(text); preview.verticalScrollBar().setValue(preview.verticalScrollBar().maximum()); timer.stop()
        timer.timeout.connect(receive); timer.start(); threading.Thread(target=work, daemon=True).start(); dialog.show()

    def update_empty(self):
        self.empty.setVisible(self.proxy.rowCount() == 0)
        self.empty.setGeometry(self.table.viewport().rect())
        self.empty.setText('没有符合筛选条件的任务\n\n试试其他关键词或状态' if self.model.rows else '还没有任务\n\n点击「添加任务」选择视频，或从右上角菜单加入已有项目目录')

    def update_details(self, *_):
        row = self.selected(); rows = self.selected_rows()
        self.update_priority_buttons()
        self.detail_title.setText(row['name'] if row else f'已选择 {len(rows)} 项任务' if rows else '任务详情')
        self.detail_title.setToolTip(row['name'] if row else '')
        self.edit_button.setEnabled(bool(row and row['id'] in self.tasks.jobs and row['state'] in ('等待', '排队', '失败', '已停止')))
        result = row['result'] if row else {}
        self.blend_button.setEnabled(bool(row and model_ready(result) and not self.busy))
        self.result_button.setEnabled(bool(row and result.get('model')))
        self.more_button.setEnabled(bool(rows))
        self.source_path.setText(row['path'] if row else ''); self.source_path.setToolTip(self.source_path.text())
        self.result_path.setText(result.get('model', '')); self.result_path.setToolTip(self.result_path.text())
        if row:
            metrics = result.get('metrics', {}); error = metrics.get('mean_reprojection_error_px')
            info = f"{row['cells'][1]}  ·  {row['settings']}  ·  {row['state']}"
            if row.get('queue_position'): info += f"  ·  执行顺序：第 {row['queue_position']} 项"
            if row.get('options'): info += f"  ·  SIFT {row['options']['max_size']} / 匹配 {row['options']['overlap']}"
            if metrics:
                info += f"\n注册帧 {metrics.get('registered_images', '—')}  ·  点云 {metrics.get('points', 0):,}"
                if isinstance(error, (int, float)): info += f'  ·  重投影误差 {error:.3f} px'
                info += f"  ·  耗时 {result.get('task_wall_seconds', result.get('mapping_wall_seconds', 0)) / 60:.1f} 分钟"
            if row.get('error'): info += '\n' + row['error']
            if row.get('export_error'): info += '\nBlender 导出失败：' + row['export_error']
            for warning in row.get('solver_warnings', result.get('solver_warnings', [])):
                info += '\n求解提醒：' + warning['text']
            self.detail_info.setToolTip('\n'.join(w['log_path'] for w in row.get('solver_warnings', result.get('solver_warnings', []))))
            self.detail_info.setText(info)
        else: self.detail_info.setText('可批量移除未排队的任务。' if rows else '选择任务以查看参数、处理状态及输出文件。路径可选择复制，双击任务可编辑或打开结果。')
        for key, action in self.selection_actions.items():
            enabled = bool(row)
            if key == 'open_blend': enabled = bool(row and row.get('exports') and not self.busy)
            elif key == 'sequence': enabled = bool(result.get('sequence'))
            elif key == 'archive': enabled = bool(row and row.get('record') and not self.busy)
            elif key == 'rename': enabled = bool(row and row['state'] not in ('运行中', '排队'))
            elif key == 'remove': enabled = bool(rows and all(r['id'] in self.tasks.jobs and r['state'] not in ('运行中', '排队') for r in rows))
            elif key == 'copy': enabled = bool(result.get('model'))
            action.setEnabled(enabled)

    def context_menu(self, position):
        index = self.table.indexAt(position)
        if index.isValid() and not self.table.selectionModel().isRowSelected(index.row(), QModelIndex()):
            self.table.selectRow(index.row())
        if self.selected_rows(): self.more_button.menu().exec(self.table.viewport().mapToGlobal(position))

    def row_double_click(self, _):
        row = self.selected()
        if row and row['id'] in self.tasks.jobs and row['state'] in ('等待', '排队', '失败', '已停止'): self.edit_task()
        elif row: self.open_result()

    def add_task(self, paths=None):
        if isinstance(paths, bool): paths = None
        dialog = TaskDialog(self, self.defaults, paths=paths)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        data = dialog.result_data; blender = self.choose_blender() if data['auto_blender'] else None
        if data['auto_blender'] and not blender: return
        for path in data['paths']:
            name = data['name']
            if name and len(data['paths']) > 1: name += ' · ' + Path(path).stem
            iid = self.tasks.add(path, data['options'], name)
            if blender: self.tasks.jobs[iid]['blender'] = blender
        self.config['last_video_directory'] = str(Path(data['paths'][0]).parent)
        self.persist_tasks(); self.rebuild_rows(); self.schedule_estimate()

    def edit_task(self):
        row = self.selected()
        if not row or row['id'] not in self.tasks.jobs: return
        job = self.tasks.begin_edit(row['id'])
        if not job: return
        self.update_busy()
        try:
            dialog = TaskDialog(self, Options(**job['options']), job=job)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                data = dialog.result_data
                blender = self.choose_blender(preferred=(job.get('blender') or {}).get('path')) if data['auto_blender'] else None
                if data['auto_blender'] and not blender: return
                self.tasks.end_edit(dict(name=data['name'] or Path(job['path']).stem,
                    options=asdict(data['options']), blender=blender, state=job['state'], error=''))
            else: self.tasks.end_edit()
        finally:
            if self.tasks.editing: self.tasks.end_edit()
            self.persist_tasks(); self.rebuild_rows(); self.schedule_estimate()

    def start_queue(self, ids=None):
        if self.busy: return
        if isinstance(ids, bool): ids = None
        if self.tasks.start(ids):
            self.started = time.perf_counter(); self.progress.setRange(0, 5); self.progress.setValue(0); self.state_label.setText('正在启动任务队列…'); self.rebuild_rows(); self.persist_tasks()
        else: QMessageBox.information(self, '没有待处理任务', '请添加新任务，或选择等待、失败、已停止的任务。')

    def toggle_pause(self):
        self.tasks.pause(not self.tasks.paused); self.update_busy()

    def stop(self):
        if self.exporting and self.export_pipeline:
            threading.Thread(target=self.export_pipeline.cancel, daemon=True).start()
        else: self.tasks.cancel()
        self.stop_button.setEnabled(False); self.state_label.setText('正在停止子进程，已完成的缓存会保留…')

    def update_busy(self):
        self.start_button.setEnabled(not self.busy)
        self.pause_button.setEnabled(self.tasks.running)
        self.pause_button.setText('继续队列' if self.tasks.paused else '暂停队列')
        self.stop_button.setEnabled(self.busy)
        self.update_priority_buttons()
        editing = bool(self.tasks.editing)
        self.banner.setVisible(self.tasks.running and (self.tasks.paused or editing))
        self.banner.setText('正在编辑任务：当前计算继续，完成后将等待编辑结束再启动下一项。' if editing else '队列已暂停：当前计算继续完成，点击「继续队列」启动下一项。')

    def refresh_projects(self):
        if self.project_loading:
            self.project_refresh_pending = True; return
        self.project_loading = True; self.refresh_button.setEnabled(False)
        output = self.defaults.output_root
        def work():
            try:
                records, warnings = self.library.scan(output); self.events.put(dict(type='projects', records=records, warnings=warnings))
            except Exception as error: self.events.put(dict(type='projects', records=[], warnings=[str(error)]))
        threading.Thread(target=work, daemon=True).start()

    def import_projects(self):
        path = QFileDialog.getExistingDirectory(self, '添加已有项目或输出目录', self.defaults.output_root)
        if not path: return
        try: self.library.add_root(path); self.refresh_projects()
        except (ValueError, OSError) as error: self.show_error(error)

    def remove_selected(self):
        rows = self.selected_rows()
        if not rows or any(r['id'] not in self.tasks.jobs or r['state'] in ('运行中', '排队') for r in rows): return
        for row in rows: self.tasks.remove(row['id'])
        self.persist_tasks(); self.rebuild_rows(); self.schedule_estimate()

    def rename_selected(self):
        row = self.selected()
        if not row or row['state'] in ('运行中', '排队'): return
        name, ok = QInputDialog.getText(self, '重命名', '显示名称（不更改文件目录）', text=row['name'])
        if not ok or not name.strip(): return
        try:
            if row.get('record'): self.library.rename(row['record'], name); row['record']['name'] = name.strip()
            if row['id'] in self.tasks.jobs: self.tasks.jobs[row['id']]['name'] = name.strip(); self.persist_tasks()
            self.rebuild_rows()
        except (OSError, ValueError) as error: self.show_error(error)

    def archive_selected(self):
        row = self.selected()
        if not row or not row.get('record') or self.busy: return
        if QMessageBox.question(self, '移入项目回收目录', f"将「{row['name']}」移入输出目录的 .trash 回收文件夹？\n项目文件与缓存一并保留，原视频保留。可依据 recovery.json 恢复。") != QMessageBox.StandardButton.Yes: return
        try:
            destination = self.library.archive(row['record'])
            if row['id'] in self.tasks.jobs: self.tasks.remove(row['id']); self.persist_tasks()
            self.records.pop(row['record']['id'], None); self.rebuild_rows(); self.refresh_projects(); self.append_log(f'[项目回收] {destination}\n')
        except (ValueError, OSError) as error: self.show_error(error)

    def open_path(self, path):
        path = Path(path)
        if path.exists():
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))): self.show_error(f'无法打开：{path}')
        else: self.show_error(f'文件或目录不存在，可能已被移动：\n{path}')

    def open_result(self):
        row = self.selected()
        if row and row['result'].get('model'): self.open_path(row['record']['directory'] if row.get('record') else result_directory(row['result']))

    def open_source(self):
        row = self.selected()
        if row: self.open_path(Path(row['path']) if Path(row['path']).is_dir() else Path(row['path']).parent)

    def open_sequence(self):
        row = self.selected()
        if row and row['result'].get('sequence'): self.open_path(row['result']['sequence'])

    def copy_result(self):
        row = self.selected()
        if row: QApplication.clipboard().setText(row['result'].get('model', ''))

    def task_log_path(self):
        row = self.selected()
        if row:
            result = row['result']
            if result.get('log_dir') and Path(result['log_dir']).is_dir(): return Path(result['log_dir'])
            if result.get('model'):
                run = Path(result['model']).parent.parent
                path = Path(result['scene']) / 'logs' / f"{result['backend']}_{run.name}"
                if path.exists(): return path
            scene = row.get('scene_path')
            if scene and (Path(scene) / 'log').exists(): return Path(scene) / 'log'
            if scene and (Path(scene) / 'logs').exists(): return Path(scene) / 'logs'
        pipeline = self.export_pipeline or self.tasks.pipeline
        return pipeline.log_dir if pipeline and pipeline.log_dir else LOGS

    def open_task_logs(self): self.open_path(self.task_log_path())

    def choose_blender(self, opening=False, preferred=None, fps=None):
        settings = dict(self.config)
        if preferred: settings['blender_path'] = preferred
        dialog = BlenderDialog(self, settings, fps or self.config.get('blender_fps', 30), opening)
        if dialog.exec() != QDialog.DialogCode.Accepted: return None
        chosen = dialog.result_data
        self.config.update(blender_path=chosen['path'], blender_paths=chosen['paths'])
        if not opening:
            self.config.update(blender_as_mesh=chosen['as_mesh'], blender_draw_points=chosen['draw_points'],
                blender_suppress_warnings=chosen['suppress_warnings'], blender_background_images=chosen['background_images'],
                blender_point_radius=chosen['point_radius'], blender_point_size_revision=2, blender_fps=chosen['fps'])
            self.blender_fps.setValue(chosen['fps'])
        self.save_preferences()
        return chosen

    def export_selected(self):
        row = self.selected()
        if not row or not model_ready(row['result']) or self.busy: return
        try: chosen = self.choose_blender()
        except OSError as error: self.show_error(error); return
        if not chosen: return
        result = row['result']; model = Path(result['model'])
        suggested = result_directory(result) / blend_filename(row['name'], chosen['version'])
        if suggested.exists(): suggested = suggested.with_stem(suggested.stem + '_' + time.strftime('%H%M%S'))
        output, _ = QFileDialog.getSaveFileName(self, '保存新的 Blender 场景', str(suggested), 'Blender (*.blend)')
        if not output: return
        if not output.lower().endswith('.blend'): output += '.blend'
        if Path(output).exists(): self.show_error('请使用新的文件名，已有 Blender 文件不会覆盖。'); return
        from blender_export import export_blender
        pipeline = Pipeline(Options(), self.events.put); self.export_pipeline = pipeline; self.exporting = True
        self.started = time.perf_counter(); self.progress.setRange(0, 0)
        self.state_label.setText('正在启动 Blender…'); self.update_busy(); self.update_details()
        def work():
            try:
                saved = export_blender(pipeline, result, output, chosen)
                self.events.put(dict(type='blender_saved', model=str(model), output=saved, blender=chosen))
                self.events.put(dict(type='export_finished', text='Blender 文件已生成', success=True))
            except Cancelled:
                self.events.put(dict(type='export_finished', text='Blender 导出已停止，原有结果保留', success=False))
            except Exception as error:
                self.events.put(dict(type='log', text=traceback.format_exc())); self.events.put(dict(type='export_finished', text=f'Blender 导出失败：{error}'))
        threading.Thread(target=work, daemon=True).start()

    def open_blend(self):
        row = self.selected()
        if not row or not row.get('exports') or self.busy: return
        blend = row['exports'][0]; settings = self.library.data['export_settings'].get(blend, {})
        if not Path(blend).is_file(): self.show_error('Blender 文件不存在，请刷新结果列表。'); return
        try:
            chosen = self.choose_blender(opening=True, preferred=settings.get('path'))
            if chosen: subprocess.Popen([chosen['path'], '--python', str(APP / 'blender_open.py'), '--', '--blend', blend], cwd=str(ROOT))
        except (OSError, ValueError) as error: self.show_error(error)

    def append_log(self, text):
        rotate_log(LOGS / 'session.log', len(text.encode('utf-8')))
        try:
            with (LOGS / 'session.log').open('a', encoding='utf-8') as stream: stream.write(text)
        except OSError: pass
        preview = ''.join(line if len(line) < 2000 else line[:2000] + '…[完整内容见日志文件]\n' for line in text[-131072:].splitlines(keepends=True))
        self.log_lines.append(preview); self.log_chars += len(preview)
        while self.log_chars > 131072 or len(self.log_lines) > 1024:
            self.log_chars -= len(self.log_lines.popleft())
        if self.log_widget:
            cursor = self.log_widget.textCursor(); cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(preview)
            excess = self.log_widget.document().characterCount() - 1 - 131072
            if excess > 0:
                cursor.movePosition(QTextCursor.MoveOperation.Start)
                cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor, excess)
                cursor.removeSelectedText()
            self.log_widget.verticalScrollBar().setValue(self.log_widget.verticalScrollBar().maximum())

    def open_logs(self):
        if self.log_dialog:
            self.log_dialog.show(); self.log_dialog.raise_(); return
        dialog = QDialog(self); dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose); dialog.setWindowTitle('运行日志 · ' + APP_NAME); dialog.resize(960, 580)
        layout = QVBoxLayout(dialog); layout.setContentsMargins(20, 16, 20, 20)
        row = QHBoxLayout(); row.addWidget(label('运行日志', 'sectionTitle')); row.addStretch(); row.addWidget(button('打开日志目录', self.open_task_logs)); layout.addLayout(row)
        layout.addWidget(label('仅预览最近 128 KiB；完整日志持续保存到磁盘。历史任务请打开对应日志目录。'))
        text = QPlainTextEdit(); text.setReadOnly(True); text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth); text.setFont(QFont('Consolas', 10)); text.document().setMaximumBlockCount(1500)
        text.setPlainText(''.join(self.log_lines) or '尚无运行日志。'); layout.addWidget(text, 1)
        self.log_dialog, self.log_widget = dialog, text
        dialog.destroyed.connect(self.log_destroyed); dialog.show()

    def log_destroyed(self, *_): self.log_dialog = self.log_widget = None

    def schedule_estimate(self):
        self.estimate_generation += 1
        if self.background: self.estimate_timer.start()

    def estimate(self):
        jobs = [job for job in self.tasks.snapshot().values() if job['state'] in ('等待', '排队', '失败', '已停止')]
        if not jobs: self.estimate_label.setText('预估任务时长：没有待处理任务'); return
        generation = self.estimate_generation
        self.estimate_label.setText('预估任务时长：正在读取本机历史与视频信息…')
        def work():
            try:
                # Different tasks have independent settings; estimate matching groups separately.
                groups = {}
                for job in jobs: groups.setdefault(json.dumps(job['options'], sort_keys=True), []).append(job)
                texts = [estimate_jobs(group, Options(**json.loads(key))) for key, group in groups.items()]
                text = texts[0] if len(texts) == 1 else f'{len(groups)} 组独立参数 · ' + '；'.join(texts)
            except Exception as error: text = f'预估任务时长：暂不可用（{error}）'
            self.events.put(dict(type='estimate', generation=generation, text=text))
        threading.Thread(target=work, daemon=True).start()

    def check_environment(self):
        python = self.default_controls['python'].text()
        self.hardware_label.setText('正在检测实际 GPU、显存与 CUDA…')
        def work():
            try:
                text = describe_hardware(get_hardware(python, force=True))
                text += ' · GLOMAP GPU ' + ('已构建' if (BINARY_DIRS['01 GLOMAP GPU'] / 'glomap.exe').is_file() else '未构建')
            except Exception as error: text = '环境检测失败：' + str(error)
            self.events.put(dict(type='environment', text=text))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        dirty = False; chunks = []
        deadline = time.perf_counter() + .012
        for _ in range(500):
            if time.perf_counter() >= deadline: break
            try: event = self.events.get_nowait()
            except queue.Empty: break
            kind, iid = event['type'], event.get('iid')
            if kind == 'log': chunks.append(event['text'])
            elif kind == 'solver_warning':
                job = self.tasks.snapshot().get(iid, {})
                self.solver_notice_text.setText(f"求解提醒 · {job.get('name', 'GLOMap')}\n{event['text']}")
                self.solver_notice_log = event['log_path']
                self.solver_notice_text.setToolTip(event['log_path'])
                self.solver_notice.show()
                self.persist_tasks(); dirty = True
            elif kind == 'stage':
                self.state_label.setText(event['text'] + f" · 阶段 {event['index']}/{event['total']}")
                self.progress.setRange(0, 5)
                self.progress.setValue(event['index'] - 1)
                with self.tasks.condition:
                    if iid in self.tasks.jobs: self.tasks.jobs[iid].update(stage=event['text'], progress=event['index'] - 1)
                dirty = True
            elif kind == 'blender_progress':
                self.state_label.setText(event['text'])
                percent = event['percent']
                self.progress.setRange(0, 100 if percent else 0)
                if percent: self.progress.setValue(percent)
                with self.tasks.condition:
                    if iid in self.tasks.jobs:
                        self.tasks.jobs[iid]['stage'] = event['text']; dirty = True
            elif kind == 'scene':
                self.last_scene = event['path']
                with self.tasks.condition:
                    if iid in self.tasks.jobs: self.tasks.jobs[iid]['scene_path'] = event['path']
                self.persist_tasks()
            elif kind == 'job':
                dirty = True
                if 'result' in event:
                    result = event['result']; job = self.tasks.snapshot().get(iid, {})
                    report = result_report(result)
                    try:
                        self.library.data['names'][str(report.resolve())] = job.get('name', Path(result['source']).stem)
                        if job.get('created_at'): self.library.data['created'][str(report.resolve())] = job['created_at']
                        self.library.add_root(job.get('options', {}).get('output_root', self.defaults.output_root)); self.library.save()
                    except (ValueError, OSError) as error: chunks.append(f'[项目名称保存失败] {error}\n')
                self.persist_tasks()
            elif kind == 'complete':
                self.progress.setRange(0, 5)
                self.progress.setValue(5)
                metrics = event.get('metrics', {})
                chunks.append(f"[完成] {metrics.get('registered_images', '?')} 注册帧 / {metrics.get('points', '?')} 点\n{event['model']}\n")
            elif kind in ('finished', 'export_finished'):
                self.progress.setRange(0, 5)
                if kind == 'export_finished':
                    self.exporting = False; self.export_pipeline = None
                    self.progress.setValue(5 if event.get('success') else 0)
                self.state_label.setText(event['text']); self.persist_tasks(); self.refresh_projects(); self.schedule_estimate(); dirty = True
            elif kind == 'projects':
                self.project_loading = False; self.refresh_button.setEnabled(True)
                self.records = {r['id']: r for r in event['records']}
                if event['warnings']: chunks.append('[项目读取] ' + '\n'.join(event['warnings']) + '\n')
                dirty = True
                if self.project_refresh_pending: self.project_refresh_pending = False; self.refresh_projects()
            elif kind == 'blender_saved':
                try: self.library.record_export(event['model'], event['output'], event['blender'])
                except OSError as error: chunks.append(f'[Blender 已保存，记录失败] {error}\n')
                chunks.append(f"[Blender] 已保存 {event['output']}\n")
            elif kind == 'environment':
                self.hardware_label.setText(event['text'][:140]); self.hardware_label.setToolTip(event['text']); self.environment_info.setText(event['text']); self.schedule_estimate()
            elif kind == 'estimate' and event['generation'] == self.estimate_generation:
                self.estimate_label.setText(event['text']); self.estimate_label.setToolTip(event['text'])
            elif kind == 'queue_wait': self.update_busy()
            elif kind == 'cleanup_preview':
                self.finish_cleanup(event)
        if chunks: self.append_log(''.join(chunks))
        if dirty: self.rebuild_rows()
        if self.busy:
            elapsed = int(time.perf_counter() - self.started); self.elapsed_label.setText(f'{elapsed // 60:02}:{elapsed % 60:02}')

    def browse_default(self, key):
        control = self.default_controls[key]
        path = QFileDialog.getExistingDirectory(self, '默认输出目录', control.text()) if key == 'output_root' else QFileDialog.getOpenFileName(self, '选择 Python', control.text(), 'Python (python.exe)')[0]
        if path: control.setText(path)

    def read_defaults(self):
        values = asdict(self.defaults)
        for key, control in self.default_controls.items():
            if isinstance(control, QCheckBox): values[key] = control.isChecked()
            elif isinstance(control, QComboBox): values[key] = control.currentData()
            elif isinstance(control, QLineEdit): values[key] = control.text().strip()
            else: values[key] = control.value()
        options = Options(**values); Pipeline(options).preflight()
        if not options.output_root: raise ValueError('默认输出目录不能为空。')
        return options

    def save_preferences(self):
        data = {**self.config, **asdict(self.defaults), 'distribution_root': str(ROOT)}
        for key in ('python', 'output_root'): data[key] = portable_path(data[key])
        data['backend_label'] = SOLVER_CHOICES[self.defaults.backend]
        data['camera_label'] = '近似针孔（无畸变）' if self.defaults.camera_mode == 'pinhole' else '自动估计畸变（默认）'
        atomic_write_json(self.settings_path, data)
        self.config = data

    def save_settings_clicked(self):
        try:
            self.defaults = self.read_defaults()
            self.config.update(blender_fps=self.blender_fps.value(), log_retention_days=self.retention.currentData())
            self.save_preferences(); self.settings_note.setText('设置已保存，将用于之后添加的任务。'); self.refresh_projects()
        except (OSError, ValueError) as error: self.show_error(error)

    def reset_defaults(self):
        defaults = Options()
        for key, control in self.default_controls.items():
            if key in ('output_root', 'backend'): continue
            value = getattr(defaults, key)
            if isinstance(control, QCheckBox): control.setChecked(value)
            elif isinstance(control, QComboBox): control.setCurrentIndex(control.findData(value))
            elif isinstance(control, QLineEdit): control.setText(str(value))
            else: control.setValue(value)
        self.blender_fps.setValue(30); self.settings_note.setText('已还原处理参数，点击「保存设置」应用。输出目录、项目和任务记录保留。')

    def persist_tasks(self):
        try:
            data = list(self.tasks.snapshot().values())
            for job in data:
                for key in ('python', 'output_root'): job['options'][key] = portable_path(job['options'][key])
                for key in ('project_dir', 'scene_path'):
                    if job.get(key): job[key] = portable_path(job[key])
            atomic_write_json(self.config_dir / 'task-queue.json', data)
        except OSError as error: self.state_label.setText(f'任务记录保存失败：{error}')

    def restore_tasks(self):
        jobs, warnings = restore_jobs(self.config_dir / 'task-queue.json', self.config.get('distribution_root'))
        self.tasks.jobs.update(jobs)
        for warning in warnings:
            self.events.put(dict(type='log', text=f'[任务恢复] {warning}\n'))

    def log_roots(self):
        return [str(OUTPUTS), str(ROOT / 'outputs'), self.defaults.output_root, *self.library.data['roots']]

    def automatic_cleanup(self):
        days = self.config.get('log_retention_days', 0)
        if days in (7, 30, 90) and not self.busy: self.prepare_cleanup(days, True)

    def clean_logs(self):
        if self.busy: QMessageBox.information(self, '任务运行中', '请等待任务结束后清理日志。'); return
        days = self.retention.currentData()
        if not days: QMessageBox.information(self, '保留全部', '当前策略保留全部日志，没有待清理项目。'); return
        self.prepare_cleanup(days, False)

    def prepare_cleanup(self, days, automatic):
        roots = self.log_roots()
        def work():
            try: self.events.put(dict(type='cleanup_preview', days=days, automatic=automatic, candidates=expired_logs(LOGS, roots, days)))
            except OSError as error: self.events.put(dict(type='log', text=f'[日志清理] {error}\n'))
        threading.Thread(target=work, daemon=True).start()

    def finish_cleanup(self, event):
        if self.busy: return
        candidates = event['candidates']; total = sum(c['bytes'] for c in candidates)
        if not event['automatic']:
            if not candidates: QMessageBox.information(self, '日志清理', '没有符合条件的过期诊断日志。'); return
            if QMessageBox.question(self, '确认清理', f"删除 {len(candidates)} 个超过 {event['days']} 天的日志，共 {total / 2**20:.2f} MiB？\n仅处理诊断日志；删除后不可恢复。") != QMessageBox.StandardButton.Yes: return
        def work():
            result = delete_expired(candidates, event['days'])
            self.events.put(dict(type='log', text=f"[日志清理] 已清理 {result['removed']} 个日志，跳过 {result['skipped']} 个。\n"))
        threading.Thread(target=work, daemon=True).start()

    def show_error(self, error): QMessageBox.warning(self, '无法完成操作', str(error))

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, '任务仍在运行', '请先停止任务，并等待后台进程退出后关闭。'); event.ignore(); return
        try:
            self.persist_tasks()
            # Save current defaults using the explicit settings action; preserve unfinished form edits.
            self.save_preferences()
        except OSError as error:
            self.show_error(error); event.ignore(); return
        self.poller.stop(); event.accept()


def main():
    parser = argparse.ArgumentParser(description=APP_NAME + ' · Qt workspace')
    parser.add_argument('--check-ui', action='store_true'); parser.add_argument('--smoke-test', type=Path)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    configure_application(app)
    window = MainWindow(); window.show()
    if args.check_ui or args.smoke_test:
        app.processEvents()
        assert window.pages.count() == 3
        if args.smoke_test:
            window.refresh_projects()
            deadline = time.monotonic() + 10
            while window.project_loading and time.monotonic() < deadline:
                app.processEvents(); time.sleep(.01)
            args.smoke_test.parent.mkdir(parents=True, exist_ok=True)
            for index, suffix in ((0, ''), (1, '-settings'), (2, '-about')):
                window.pages.setCurrentIndex(index); window.nav_group.button(index).setChecked(True); app.processEvents()
                window.grab().save(str(args.smoke_test.with_name(args.smoke_test.stem + suffix + '.png')))
        print('QT_UI_PASS', window.width(), window.height(), flush=True)
        window.poller.stop(); window.hide(); return
    sys.exit(app.exec())


def configure_application(app):
    app.setApplicationName(APP_NAME); app.setApplicationVersion(APP_VERSION)
    # The offscreen test platform has no OS font discovery on Windows.
    if not QFontDatabase.families():
        for name in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf', 'consola.ttf'):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name))
    app.setStyle('Fusion'); app.setFont(QFont('Microsoft YaHei UI', 9)); app.setStyleSheet(STYLE)


if __name__ == '__main__': main()
