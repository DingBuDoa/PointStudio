# PointStudio 1.0.1 · 第三方项目

各组件遵循其各自许可。随包源码、Python 包元数据以及 FFmpeg / FastMap 原有许可文件均予以保留。以下为项目来源与版本索引；不替代组件附带的许可文本。

- **FastMap**：0.1 · 本地适配；相机位姿与稀疏重建。项目：https://github.com/pals-ttic/fastmap
- **GLOMAP**：本地构建 · CUDA / cuDSS；全局运动恢复。项目：https://github.com/colmap/glomap
- **COLMAP**：3.12.3 · 74b2be9；特征提取与图像匹配。项目：https://github.com/colmap/colmap
- **FFmpeg**：7.1.1 · essentials build；视频解码、缩放与抽帧。项目：https://github.com/FFmpeg/FFmpeg
- **Photogrammetry Importer**：3.2.1 · 本地适配；摄影测量数据导入。项目：https://github.com/SBCV/Blender-Addon-Photogrammetry-Importer

其他运行依赖的版本见 `config/runtime-requirements.txt`；Python 包的许可及来源保留在 `runtime/python/Lib/site-packages/` 的包目录和 `.dist-info` 中。PySide6 / Qt 来源：https://www.qt.io/qt-for-python ，Python 来源：https://www.python.org/ ，PyTorch 来源：https://pytorch.org/ 。
