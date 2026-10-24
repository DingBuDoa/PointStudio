"""Release metadata and the upstream projects included in this distribution."""
APP_NAME = 'PointStudio'
APP_VERSION = '1.0.1'
BUILD_DATE = '2026-10-24'
SOLVER_CHOICES = {'fastmap': 'FastMap-更高速度', 'glomap': 'GLOMap-更高精度'}

# Versions checked against bundled source/package metadata and executable help.
OPEN_SOURCE_PROJECTS = (
    ('FastMap', '0.1 · 本地适配', '相机位姿与稀疏重建', 'https://github.com/pals-ttic/fastmap'),
    ('GLOMAP', '本地构建 · CUDA / cuDSS', '全局运动恢复', 'https://github.com/colmap/glomap'),
    ('COLMAP', '3.12.3 · 74b2be9', '特征提取与图像匹配', 'https://github.com/colmap/colmap'),
    ('FFmpeg', '7.1.1 · essentials build', '视频解码、缩放与抽帧', 'https://github.com/FFmpeg/FFmpeg'),
    ('Photogrammetry Importer', '3.2.1 · 本地适配', '摄影测量数据导入', 'https://github.com/SBCV/Blender-Addon-Photogrammetry-Importer'),
)
