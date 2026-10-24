"""Native Qt version card and linked upstream credits, based on the HTML design."""
from html import escape
from importlib.metadata import PackageNotFoundError, version
import sys

import PySide6
from PySide6.QtCore import Qt, QUrl, qVersion
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QScrollArea, QSizePolicy

from app_info import APP_NAME, APP_VERSION, BUILD_DATE, OPEN_SOURCE_PROJECTS
from paths import ROOT
from qt_dialogs import label, button


def package_version(name):
    try: return version(name)
    except PackageNotFoundError: return '未安装'


def source_link(text, url, object_name='aboutLink'):
    link = label(f'<a href="{escape(url, quote=True)}" style="color:#2f6bff;text-decoration:none">{escape(text)}</a>', object_name)
    link.setTextFormat(Qt.TextFormat.RichText)
    link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    link.linkActivated.connect(lambda target: QDesktopServices.openUrl(QUrl(target)))
    link.setToolTip(url)
    link.setMinimumWidth(0)
    link.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return link


def about_page(open_path):
    page = QWidget(); page.setObjectName('workspace')
    layout = QVBoxLayout(page); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
    header = QWidget(); header.setObjectName('header')
    heading = QVBoxLayout(header); heading.setContentsMargins(30,20,30,18); heading.setSpacing(4)
    heading.addWidget(label('关于', 'title')); heading.addWidget(label('版本信息与开源致谢', 'muted'))
    layout.addWidget(header)
    scroll = QScrollArea(); scroll.setObjectName('aboutScroll'); scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    body = QWidget(); body.setObjectName('aboutCanvas')
    outer = QVBoxLayout(body); outer.setContentsMargins(28,30,28,30); outer.setSpacing(0)
    card = QWidget(); card.setObjectName('aboutCard'); card.setFixedWidth(700)
    card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    content = QVBoxLayout(card); content.setContentsMargins(30,26,30,24); content.setSpacing(0)
    logo = label('PS', 'aboutLogo'); logo.setFixedSize(52,52); logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
    content.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter); content.addSpacing(12)
    title = label(APP_NAME, 'aboutName'); title.setAlignment(Qt.AlignmentFlag.AlignCenter); content.addWidget(title)
    subtitle = label('Blender 实景合成图形化工具', 'aboutSubtitle'); subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
    content.addWidget(subtitle); content.addSpacing(17)
    metadata = QWidget(); metadata.setObjectName('aboutMeta')
    meta = QHBoxLayout(metadata); meta.setContentsMargins(16,12,16,12); meta.setSpacing(12)
    release = label('版本 ' + APP_VERSION, 'aboutRelease'); release.setWordWrap(False)
    date = label('构建日期  ' + BUILD_DATE, 'aboutDate'); date.setWordWrap(False)
    meta.addStretch(); meta.addWidget(release); meta.addSpacing(14); meta.addWidget(date); meta.addStretch()
    content.addWidget(metadata); content.addSpacing(14)
    description = label('从视频提取相机轨迹与彩色点云，生成可继续创作的 Blender 工程。\n素材处理、任务队列与多版本导出，在同一工作区完成。', 'aboutDescription')
    description.setMinimumHeight(40)
    description.setAlignment(Qt.AlignmentFlag.AlignCenter); content.addWidget(description); content.addSpacing(17)
    section = QHBoxLayout(); section.addWidget(label('核心开源项目', 'aboutSection')); section.addStretch()
    section.addWidget(label('感谢开源社区', 'muted')); content.addLayout(section); content.addSpacing(6)
    projects = (*OPEN_SOURCE_PROJECTS,
                ('PyTorch', package_version('torch'), '张量运算与 CUDA 加速', 'https://github.com/pytorch/pytorch'),
                ('NumPy', package_version('numpy'), '数值计算与数据转换', 'https://github.com/numpy/numpy'))
    for name, release, purpose, url in projects:
        row = QWidget(); row.setObjectName('aboutProject')
        row_layout = QVBoxLayout(row); row_layout.setContentsMargins(0,9,0,9); row_layout.setSpacing(3)
        top = QHBoxLayout(); top.setSpacing(12)
        project_title = label(name, 'aboutProjectName'); project_title.setWordWrap(False)
        top.addWidget(project_title); top.addWidget(label(purpose, 'aboutPurpose'), 1)
        component_version = label(release, 'aboutVersion'); component_version.setWordWrap(False)
        top.addWidget(component_version); row_layout.addLayout(top)
        row_layout.addWidget(source_link(url.removeprefix('https://'), url))
        content.addWidget(row)
    content.addSpacing(13)
    runtime = QHBoxLayout(); runtime.setSpacing(6)
    runtime.addWidget(source_link(f'PySide6 {PySide6.__version__} · Qt {qVersion()}', 'https://code.qt.io/pyside/pyside-setup.git'))
    python = source_link('Python ' + sys.version.split()[0], 'https://github.com/python/cpython')
    python.setAlignment(Qt.AlignmentFlag.AlignRight); runtime.addWidget(python)
    content.addLayout(runtime); content.addSpacing(7)
    content.addWidget(label('Blender 由本机安装版本提供。各开源组件的原始许可随组件保留。', 'aboutFootnote'))
    content.addSpacing(17)
    actions = QHBoxLayout(); actions.setSpacing(10); actions.addStretch()
    actions.addWidget(button('查看使用说明', lambda: open_path(ROOT / 'README.md')))
    actions.addWidget(button('打开软件目录', lambda: open_path(ROOT)))
    actions.addStretch(); content.addLayout(actions)
    outer.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter); outer.addStretch()
    scroll.setWidget(body); layout.addWidget(scroll, 1)
    return page
