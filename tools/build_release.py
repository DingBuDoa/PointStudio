"""Assemble a clean portable distribution without modifying the working copy."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from app_info import APP_NAME, APP_VERSION, BUILD_DATE, OPEN_SOURCE_PROJECTS, SOLVER_CHOICES
from tracker_core import Options

SKIP_DIRECTORIES = {'__pycache__', '.git', '.pytest_cache', '.mypy_cache', '.ruff_cache'}


def sources():
    for name in ('app', 'photogrammetry_importer', 'runtime'):
        for parent, directories, files in os.walk(ROOT / name):
            directories[:] = sorted(d for d in directories if d not in SKIP_DIRECTORIES)
            for filename in sorted(files):
                path = Path(parent) / filename
                if path.suffix.lower() in ('.pyc', '.pyo') or filename in ('Thumbs.db', '.DS_Store'):
                    continue
                if path.is_symlink(): raise ValueError(f'Release input must be a real file: {path}')
                yield path
    for name in ('Start.bat', 'config/runtime-manifest.json', 'config/runtime-requirements.txt'):
        yield ROOT / name


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / f'{APP_NAME}-{APP_VERSION}'
    destination = destination.resolve()
    if destination.parent != ROOT or destination.name != f'{APP_NAME}-{APP_VERSION}':
        raise ValueError('Use the versioned release directory directly inside this workspace.')
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError('Destination must be empty; existing release contents are preserved.')
    destination.mkdir(exist_ok=True)
    files = list(sources())
    for index, source in enumerate(files, 1):
        target = destination / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if index % 2500 == 0: print(f'COPY {index}/{len(files)}', flush=True)
    for name in ('config', 'logs', 'output'):
        (destination / name).mkdir(exist_ok=True)
    settings = asdict(Options())
    settings.update(python='runtime/python/python.exe', output_root='output',
                    backend_label=SOLVER_CHOICES['fastmap'], blender_fps=30,
                    blender_point_radius=.005, blender_point_size_revision=2, log_retention_days=0)
    write_json(destination / 'config/settings.json', settings)
    write_json(destination / 'config/task-queue.json', [])
    write_json(destination / 'config/task-history.json', [])
    write_json(destination / 'config/project-library.json',
               dict(roots=[], names={}, exports={}, export_settings={}, created={}))
    readme = f'''# {APP_NAME} {APP_VERSION}

构建日期：{BUILD_DATE}。Windows x64 便携版。

双击 **Start.bat** 启动。请将整个文件夹放到可写位置；软件、Python、求解器与运行依赖均已随包提供，无需安装 Python 或 CUDA Toolkit。

## 运行环境

- Windows 10 / 11 x64，兼容的 NVIDIA 显卡和显卡驱动。FastMap 使用 PyTorch CUDA；特征提取与匹配也使用 GPU。
- 生成 `.blend` 文件需要另行安装 Blender **4.0 或更新版本**。导出时选择具体版本，也可手动添加 `blender.exe`。已验证过本机 Blender 4.2 与 5.2；其他版本可能存在差异。
- 首次检测环境或 Blender 版本可能需要等待几秒。运行时保留整个软件文件夹，移动前关闭正在执行的任务。

## 使用

1. 添加视频任务，选择 **FastMap-更高速度** 或 **GLOMap-更高精度**。
2. 设置帧率、处理分辨率、可选 JPG 序列帧，以及是否自动生成 Blender 文件。
3. 点击“开始队列”，按列表执行顺序运行；用上下箭头调整尚未执行的任务。
4. 完成后打开结果，或选择点云结果生成 Blender 文件。

GLOMap 从 GPU 回退到 CPU 时显示提醒，继续求解和导出；处理速度可能降低。提醒与完整日志会保存。求解器真正报错或输入无效时仍会报告失败。

## 输出结构

```text
output/
  项目名称/
    point could/           点云及完整 COLMAP 模型
    log/                   当前任务日志
    video/                 勾选导出时生成的 JPG 序列帧
    项目名称_4.2.blend      生成时包含所选 Blender 版本
    _cache/                数据库、处理帧及必要中间数据
```

相机背景引用 `_cache/images`，请保留缓存。重名项目自动增加序号，已有 Blender 文件不会覆盖。

## 文件夹

- `app/`：软件源码及 Blender 导出脚本。
- `runtime/`：独立 Python、PySide6 / Qt、PyTorch CUDA、COLMAP、GLOMap、FastMap 和 FFmpeg。
- `photogrammetry_importer/`：随包摄影测量插件。
- `config/`：默认设置、任务队列、项目记录和依赖版本信息。
- `output/` 与 `logs/`：首次使用后存放输出和软件日志。

发行包使用干净的默认配置，没有个人路径、历史任务、项目、视频素材、测试输出或开发备份。开源项目版本和地址见软件“关于”及 `THIRD_PARTY_NOTICES.md`。文件校验清单见 `RELEASE-MANIFEST.json`。
'''
    (destination / 'README.md').write_text(readme, encoding='utf-8')
    notices = '# PointStudio 1.0.1 · 第三方项目\n\n各组件遵循其各自许可。随包源码、Python 包元数据以及 FFmpeg / FastMap 原有许可文件均予以保留。以下为项目来源与版本索引；不替代组件附带的许可文本。\n\n'
    for name, version, purpose, url in OPEN_SOURCE_PROJECTS:
        notices += f'- **{name}**：{version}；{purpose}。项目：{url}\n'
    notices += '\n其他运行依赖的版本见 `config/runtime-requirements.txt`；Python 包的许可及来源保留在 `runtime/python/Lib/site-packages/` 的包目录和 `.dist-info` 中。PySide6 / Qt 来源：https://www.qt.io/qt-for-python ，Python 来源：https://www.python.org/ ，PyTorch 来源：https://pytorch.org/ 。\n'
    (destination / 'THIRD_PARTY_NOTICES.md').write_text(notices, encoding='utf-8')
    print('COPY_COMPLETE; verifying all copied files', flush=True)
    for index, source in enumerate(files, 1):
        target = destination / source.relative_to(ROOT)
        if source.stat().st_size != target.stat().st_size or digest(source) != digest(target):
            raise AssertionError(f'Copy verification failed: {target}')
        if index % 5000 == 0: print(f'VERIFY {index}/{len(files)}', flush=True)
    entries = []
    for path in sorted(destination.rglob('*')):
        if path.is_file():
            entries.append(dict(path=path.relative_to(destination).as_posix(), bytes=path.stat().st_size,
                                sha256=digest(path)))
    manifest = dict(application=APP_NAME, version=APP_VERSION, build_date=BUILD_DATE, platform='windows-x64',
                    file_count=len(entries), total_bytes=sum(item['bytes'] for item in entries),
                    clean_configuration=True, files=entries)
    write_json(destination / 'RELEASE-MANIFEST.json', manifest)
    print(json.dumps(dict(status='complete', destination=str(destination), files=len(entries),
                          gib=round(manifest['total_bytes'] / 2**30, 3)), ensure_ascii=False), flush=True)


if __name__ == '__main__': main()
