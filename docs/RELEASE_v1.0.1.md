# PointStudio 1.0.1

Windows x64 便携版 · 构建日期 **2026-10-24**

从视频重建相机轨迹与彩色点云，并导出 Blender 工程。提供 **FastMap-更高速度 / GLOMap-更高精度** 两种方案、自动任务队列、视频缩放与可选序列帧。

本版修复界面布局与列表缩放，加入任务优先级；统一项目输出结构，改善 Blender 4.x / 5.2 的彩色点云、灯光、同步背景与后台导出。GLOMap 退回 CPU 时提示并继续处理。

## 下载与启动

请下载以下 **4 个文件** 到同一个文件夹：

- `PointStudio-1.0.1-windows-x64.zip.001`
- `PointStudio-1.0.1-windows-x64.zip.002`
- `Merge-PointStudio.ps1`
- `SHA256SUMS.txt`

在该目录的 PowerShell 中运行：

```powershell
powershell -NoProfile -File .\Merge-PointStudio.ps1
```

校验并合并完成后，解压生成的 `PointStudio-1.0.1-windows-x64.zip`，双击 **Start.bat**。分卷是同一个 ZIP 的连续部分，不能单独解压。

需要 Windows 10 / 11 x64、兼容的 NVIDIA 显卡及驱动。Blender 导出需要另行安装 Blender 4.0+。运行文件解压后约 5.21 GiB；无需另装 Python 或 CUDA Toolkit。

GitHub 自动提供的 Source code 压缩包只含源码，不是可直接启动的便携版。相机背景引用项目 `_cache/images`，请保留缓存。没有完整的纯 CPU / AMD 重建模式。

验证：35 项自动回归、真实短片求解与 Blender 4.2 导出通过。各第三方组件保留原有许可与来源。
