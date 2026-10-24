# 源码开发

## 目录

```text
app/                        Qt 界面、求解调度、项目管理与 Blender 导出
runtime/fastmap/            随软件使用的本地适配 FastMap 源码
photogrammetry_importer/     随软件使用的本地适配 Blender 插件源码
config/runtime-*.{json,txt}  运行环境版本信息
tests/test_*.py              自动回归
tools/build_release.py      创建干净便携发行目录
tools/verify_release.py     检查便携版独立性与文件校验
```

`runtime/fastmap` 包含实际适配源码，不是只指向上游的占位目录。Python、Qt、CUDA DLL、COLMAP / GLOMap 和 FFmpeg 二进制不进入 Git；个人设置、任务历史、素材和输出同样不进入 Git。

## 准备环境

当前验证过的方式是复用同版本便携包的运行文件：

1. 克隆或解压源码。
2. 将 v1.0.1 便携包中的 `runtime/python`、`runtime/cuda`、`runtime/colmap`、`runtime/glomap-gpu`、`runtime/ffmpeg` 复制到源码的 `runtime` 下。保留仓库自己的 `runtime/fastmap`。
3. 双击 `Start.bat`，首次启动会建立默认配置。

嵌入式 Python 的搜索路径已相对于 `runtime/python` 配置，软件路径均以源码根目录为基准。运行依赖版本见 `config/runtime-manifest.json` 与 `config/runtime-requirements.txt`。仅执行 pip 安装不能替代原生求解器与 DLL。

GPU GLOMap 使用本地 CUDA / cuDSS 构建；本仓库目前没有完整原生构建工具链与全部补丁，**不宣称可仅凭本仓库从零复现该二进制**。FastMap 原有 `setup.py` 保留供参考，本地运行路径由 `app/fastmap_worker.py` 调用，不能直接用上游最新版替换后假定等价。

## 自动回归

在源码根目录运行：

```powershell
& .\runtime\python\python.exe -m unittest discover -s tests -p 'test_*.py' -v
```

测试使用 Qt offscreen 与临时数据；涉及视频处理的用例需要随包 FFmpeg。`tests/artifacts` 用于生成测试素材和截图，不纳入 Git。

## 构建便携版

准备完整运行环境后运行：

```powershell
& .\runtime\python\python.exe tools/build_release.py
& .\runtime\python\python.exe tools/verify_release.py
```

构建脚本创建 `PointStudio-1.0.1/`，复制独立运行文件、生成干净配置与 SHA-256 清单；目标非空时拒绝覆盖。验证脚本在受限 PATH 下检查 Qt、CUDA、FFmpeg、COLMAP、GLOMap、启动入口与清单，报告写在 `tests/artifacts`。

发布时将源码提交到 Git，便携压缩包放在 GitHub Release 附件。超过单附件 2 GiB 时使用分卷，并同时发布合并脚本和 SHA-256 清单。发布说明模板见 [RELEASE_v1.0.1.md](RELEASE_v1.0.1.md)。
