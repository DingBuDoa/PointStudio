"""Export an allowlisted source tree; never publish local tasks or runtimes."""
from pathlib import Path
import os
import shutil

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'PointStudio-GitHub'
SKIP = {'__pycache__', '.git', '.pytest_cache', 'artifacts'}


def main():
    if TARGET.exists():
        raise FileExistsError(f'Preserving existing source export: {TARGET}')
    TARGET.mkdir()
    for name in ('app', 'photogrammetry_importer', 'runtime/fastmap'):
        for parent, directories, files in os.walk(ROOT / name):
            directories[:] = sorted(d for d in directories if d not in SKIP)
            for filename in sorted(files):
                source = Path(parent) / filename
                if source.suffix.lower() in {'.pyc', '.pyo', '.pyd', '.dll', '.exe'}:
                    continue
                if source.is_symlink():
                    raise ValueError(f'Unexpected symlink: {source}')
                target = TARGET / source.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    for name in ('Start.bat', 'config/runtime-manifest.json', 'config/runtime-requirements.txt',
                 'tests/test_code_health.py', 'tests/test_ui_rebuild.py',
                 'tools/build_release.py', 'tools/verify_release.py', 'tools/prepare_github.py'):
        source = ROOT / name
        target = TARGET / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copy2(ROOT / 'PointStudio-1.0.1/THIRD_PARTY_NOTICES.md', TARGET / 'THIRD_PARTY_NOTICES.md')
    (TARGET / 'tests/artifacts').mkdir()
    (TARGET / 'tests/artifacts/.gitkeep').touch()
    print(f'Exported {sum(p.is_file() for p in TARGET.rglob("*"))} files to {TARGET}')


if __name__ == '__main__':
    main()
