"""Desktop front end for the existing COLMAP/GLOMAP and FastMap installations."""
from pathlib import Path
from dataclasses import asdict
from collections import deque
import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from tracker_core import ROOT, Options, Pipeline, Cancelled, VIDEO_EXTENSIONS, HIDE_WINDOW

from paths import APP, CONFIG, LOGS, OUTPUTS, PYTHON, BINARY_DIRS, runtime_environment, saved_path, portable_path
from estimate import estimate_jobs, save_sample
from hardware import get_hardware, describe_hardware
from log_management import rotate_log
from project_library import ProjectLibrary, model_ready
from project_panel import ProjectManagerMixin
from blender_settings import ask_blender_settings
from app_info import APP_NAME

SETTINGS = CONFIG / 'settings.json'
BACKENDS = {'GLOMAP · 原有方案': 'glomap', 'FastMap · PyTorch CUDA': 'fastmap'}
LOG_PREVIEW_CHARS = 128 * 1024
LOG_PREVIEW_CHUNKS = 1024


class App(ProjectManagerMixin, tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME + '  |  Camera & Point Cloud')
        self.geometry('1320x850')
        self.minsize(1180, 760)
        self.configure(bg='#EDF1ED')
        self.events = queue.Queue()
        self.pipeline = None
        self.running = False
        self.jobs = {}
        self.latest_model = None
        self.latest_completed_job = None
        self.latest_scene = None
        self.started = 0
        self.config_data = {}
        CONFIG.mkdir(exist_ok=True)
        LOGS.mkdir(exist_ok=True)
        OUTPUTS.mkdir(exist_ok=True)
        self.log_lines = deque()
        self.log_char_count = 0
        self.log_window = None
        self.log = None
        self.estimate_request = 0
        self.estimate_after = None
        self.project_library = ProjectLibrary()
        self.project_records = {}
        self.project_warnings = []
        self.project_loading = False
        self.project_refresh_pending = False
        try:
            self.config_data = json.loads(SETTINGS.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        cfg = self.config_data
        gpu_ready = (BINARY_DIRS['01 GLOMAP GPU'] / 'glomap.exe').is_file()
        self.backend = tk.StringVar(value=cfg.get('backend_label', 'GLOMAP · 原有方案' if gpu_ready else 'FastMap · PyTorch CUDA'))
        output_path = saved_path(cfg.get('output_root', 'outputs'), cfg.get('distribution_root'))
        python_path = saved_path(cfg.get('python', 'runtime/python/python.exe'), cfg.get('distribution_root'))
        self.output = tk.StringVar(value=str(output_path))
        self.python = tk.StringVar(value=str(python_path))
        self.fps = tk.StringVar(value=str(cfg.get('fps', 0)))
        self.max_size = tk.StringVar(value=str(cfg.get('max_size', 4096)))
        self.overlap = tk.StringVar(value=str(cfg.get('overlap', 15)))
        self.gpu_min = tk.StringVar(value=str(cfg.get('gpu_min_images', 50)))
        self.cuda_decode = tk.BooleanVar(value=cfg.get('cuda_decode', True))
        self.color = tk.BooleanVar(value=cfg.get('color', True))
        self.glomap_gpu = tk.BooleanVar(value=cfg.get('glomap_gpu', True))
        self.camera = tk.StringVar(value=cfg.get('camera_label', '自动估计畸变（默认）'))
        if self.camera.get() not in ('自动估计畸变（默认）', '近似针孔（无畸变）'):
            self.camera.set('自动估计畸变（默认）')
        self.blender_fps = tk.StringVar(value=str(cfg.get('blender_fps', 30)))
        self.status = tk.StringVar(value='就绪 · 添加视频或选择视频文件夹')
        self.timer_text = tk.StringVar(value='00:00')
        self.environment = tk.StringVar(value='环境检测中…')
        self.backend_note = tk.StringVar()
        self.estimate_text = tk.StringVar(value='预估任务时长：添加视频后自动估算')
        self.project_search = tk.StringVar()
        self.project_details = tk.StringVar(value='选择一个项目，查看相机、点云和 Blender 文件。')
        self.project_summary = tk.StringVar(value='自动收集输出目录中的重建结果')
        self.build()
        self.project_search.trace_add('write',lambda *_:self.filter_projects())
        self.backend.trace_add('write', lambda *_: self.refresh_note())
        self.glomap_gpu.trace_add('write', lambda *_: self.refresh_note())
        self.environment_request = 0
        for variable in (self.backend, self.glomap_gpu, self.fps, self.max_size, self.overlap,
                         self.gpu_min, self.cuda_decode, self.color, self.camera, self.python, self.output):
            variable.trace_add('write', lambda *_: self.schedule_estimate())
        self.refresh_note()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)
        self.after(300, self.check_environment)
        self.after(200, self.refresh_projects)
        from log_settings import automatic_cleanup
        self.after(1200,lambda:automatic_cleanup(self))

    def build(self):
        from ui_layout import build
        build(self, BACKENDS)

    def refresh_note(self):
        if BACKENDS.get(self.backend.get()) == 'fastmap':
            text = 'FastMap：PyTorch CUDA；Windows 适配。适合画面清晰、覆盖密集的素材，CPU 仍负责取色和读写。'
        elif self.glomap_gpu.get():
            ready = (BINARY_DIRS['01 GLOMAP GPU'] / 'glomap.exe').is_file()
            text = 'GLOMAP GPU：使用独立 CUDA/cuDSS 版本；运行前验证 GPU 求解器。' if ready else 'GLOMAP GPU 版本尚未构建完成；该选项会提示缺少程序，不会静默改用 CPU。'
        else:
            text = '原版 GLOMAP：SIFT 特征与匹配使用 GPU；现有安装包的全局定位和 BA 使用 CPU。'
        self.backend_note.set(text)
        self.gpu_check.configure(state='normal' if BACKENDS.get(self.backend.get()) == 'glomap' else 'disabled')

    def reset_defaults(self):
        if self.running:
            return
        defaults = Options()
        for variable,value in [(self.fps,defaults.fps),(self.max_size,defaults.max_size),
                               (self.overlap,defaults.overlap),(self.gpu_min,defaults.gpu_min_images),
                               (self.python,str(PYTHON)),(self.blender_fps,30),
                               (self.camera,'自动估计畸变（默认）')]:
            variable.set(str(value))
        self.cuda_decode.set(True)
        self.color.set(True)
        self.glomap_gpu.set(True)
        self.save_settings(self.options())
        self.check_environment()

    def schedule_estimate(self):
        self.estimate_request += 1
        if self.estimate_after is not None:
            self.after_cancel(self.estimate_after)
        if not self.jobs:
            self.estimate_after = None
            self.estimate_text.set('预估任务时长：添加视频后自动估算')
            return
        self.estimate_text.set('预估任务时长：正在读取视频信息…')
        self.estimate_after = self.after(250,self.update_estimate)

    def update_estimate(self):
        self.estimate_after = None
        generation = self.estimate_request
        jobs = list(self.jobs.values())
        try:
            options = self.options()
        except (ValueError,KeyError):
            self.estimate_text.set('预估任务时长：请检查高级参数中的数值')
            return
        def calculate():
            try:
                text = estimate_jobs(jobs,options)
            except Exception as error:
                text = '预估任务时长：计算失败，请查看日志'
                self.events.put({'type':'log','text':f'[耗时预估] {error}\n'})
            self.events.put({'type':'estimate','generation':generation,'text':text})
        threading.Thread(target=calculate,daemon=True).start()

    def browse_directory(self, variable):
        path = filedialog.askdirectory(initialdir=variable.get() if Path(variable.get()).is_dir() else str(ROOT))
        if path:
            variable.set(path)

    def browse_python(self):
        path = filedialog.askopenfilename(title='选择已有 CUDA PyTorch 的 python.exe', filetypes=[('Python', '*.exe')])
        if path:
            self.python.set(path)
            self.check_environment()

    def add(self, paths, scene=False):
        if self.running:
            return
        existing = {(job['path'], job['scene']) for job in self.jobs.values()}
        for path in paths:
            value = str(Path(path).resolve())
            if (value, scene) in existing:
                continue
            iid = self.tree.insert('', 'end', values=('已有场景' if scene else '视频', value, '等待'))
            self.jobs[iid] = {'path': value, 'scene': scene}
            existing.add((value, scene))
        self.schedule_estimate()

    def video_initial_directory(self):
        for path in (self.config_data.get('last_video_directory', ''), Path.home() / 'Videos', Path.home()):
            if path and Path(path).is_dir():
                return str(path)
        return str(Path.home())

    def add_videos(self):
        if self.running:
            return
        paths = filedialog.askopenfilenames(title='选择视频', initialdir=self.video_initial_directory(), filetypes=[('视频', '*.mp4 *.mov *.MOV *.mkv *.avi *.m4v *.webm *.mts *.m2ts *.wmv *.mpg *.mpeg'), ('所有文件', '*.*')])
        if paths:
            self.config_data['last_video_directory'] = str(Path(paths[0]).parent)
            self.add(paths)

    def add_video_folder(self):
        if self.running:
            return
        selected = filedialog.askdirectory(title='选择视频文件夹', initialdir=self.video_initial_directory())
        if not selected:
            return
        self.config_data['last_video_directory'] = selected
        try:
            paths = sorted((p for p in Path(selected).iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS), key=lambda p:p.name.casefold())
        except OSError as error:
            messagebox.showerror('无法读取视频文件夹', str(error))
            return
        if not paths:
            messagebox.showinfo('没有视频', '所选文件夹中没有支持的视频文件。请选择直接存放视频的文件夹。')
            return
        self.add(paths)

    def remove_selected(self):
        if not self.running:
            for iid in self.tree.selection():
                self.jobs.pop(iid, None)
                self.tree.delete(iid)
            self.schedule_estimate()
            self.refresh_export_state()

    def completed_model(self):
        selection = self.tree.selection()
        if len(selection) > 1:
            return None
        iid = selection[0] if selection else self.latest_completed_job
        result = self.jobs.get(iid, {}).get('result', {})
        return Path(result['model']) if model_ready(result) else None

    def refresh_export_state(self):
        ready = not self.running and self.completed_model() is not None
        self.blender_button.configure(state='normal' if ready else 'disabled')
        self.refresh_project_actions()

    def options(self):
        return Options(backend=BACKENDS[self.backend.get()], output_root=self.output.get(), python=self.python.get(),
                       fps=float(self.fps.get()), max_size=int(self.max_size.get()), overlap=int(self.overlap.get()),
                       cuda_decode=self.cuda_decode.get(), color=self.color.get(),
                       camera_mode={'自动估计畸变（默认）': 'radial', '近似针孔（无畸变）': 'pinhole'}[self.camera.get()],
                       gpu_min_images=int(self.gpu_min.get()), glomap_gpu=self.glomap_gpu.get())

    def save_settings(self, options):
        data = asdict(options)
        data.update({key:value for key,value in self.config_data.items() if key.startswith('blender_')})
        data.update(backend_label=self.backend.get(), camera_label=self.camera.get())
        data['log_retention_days'] = self.config_data.get('log_retention_days',0)
        if self.config_data.get('last_video_directory'):
            data['last_video_directory'] = self.config_data['last_video_directory']
        data['blender_fps'] = float(self.blender_fps.get())
        if not 0 < data['blender_fps'] <= 240:
            raise ValueError('Blender FPS 需要大于 0 且不超过 240。')
        for name in ('python','output_root'):
            data[name] = portable_path(data[name])
        data['distribution_root'] = str(ROOT)
        SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    def choose_blender(self, opening=False, preferred=None):
        settings = dict(self.config_data)
        if preferred:
            settings['blender_path'] = preferred
        chosen = ask_blender_settings(self, settings, self.blender_fps.get(), opening=opening)
        if not chosen:
            return None
        self.config_data.update(blender_path=chosen['path'], blender_paths=chosen['paths'])
        if not opening:
            self.config_data.update(blender_as_mesh=chosen['as_mesh'],
                blender_draw_points=chosen.get('draw_points',True),
                blender_suppress_warnings=chosen['suppress_warnings'],
                blender_background_images=chosen['background_images'],
                blender_point_radius=chosen['point_radius'])
            self.blender_fps.set(str(chosen['fps']))
        # Save the choice even if unrelated advanced parameters are temporarily invalid.
        try:
            data = json.loads(SETTINGS.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        data.update({key:value for key,value in self.config_data.items() if key.startswith('blender_')})
        data['blender_fps'] = float(self.blender_fps.get())
        SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return chosen

    def start(self):
        if self.running:
            return
        if not self.jobs:
            messagebox.showinfo('添加视频', '请先添加视频或选择视频文件夹。')
            return
        try:
            options = self.options()
            Pipeline(options).preflight()
            self.save_settings(options)
            Path(options.output_root).mkdir(parents=True,exist_ok=True)
            self.project_library.add_root(options.output_root)
        except Exception as error:
            messagebox.showerror('无法开始', str(error))
            return
        jobs = list(self.jobs.items())
        self.running = True
        self.latest_model = None
        self.latest_completed_job = None
        self.blender_button.configure(state='disabled')
        self.refresh_project_actions()
        self.started = time.perf_counter()
        self.start_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.progress.configure(value=0)
        for iid, job in jobs:
            job.pop('result', None)
            self.tree.set(iid, 'status', '等待')
        self.pipeline = Pipeline(options, self.events.put)
        threading.Thread(target=self.run_jobs, args=(jobs, self.pipeline), daemon=True).start()

    def run_jobs(self, jobs, pipeline):
        failures = 0
        try:
            for index, (iid, job) in enumerate(jobs, 1):
                pipeline.check_cancel()
                self.events.put({'type': 'job', 'iid': iid, 'status': f'运行 {index}/{len(jobs)}'})
                try:
                    result = pipeline.run(job['path'], existing_scene=job['scene'])
                    try:
                        save_sample(result)
                    except OSError as error:
                        self.events.put({'type':'log','text':f'[预估记录] {error}\n'})
                    self.events.put({'type': 'job', 'iid': iid, 'status': '完成', 'result': result})
                except Cancelled:
                    self.events.put({'type': 'job', 'iid': iid, 'status': '已停止'})
                    raise
                except Exception as error:
                    failures += 1
                    self.events.put({'type': 'log', 'text': traceback.format_exc()})
                    self.events.put({'type': 'job', 'iid': iid, 'status': '失败'})
            self.events.put({'type': 'finished', 'text': f'任务结束 · 失败 {failures} 项'})
        except Cancelled as error:
            self.events.put({'type': 'finished', 'text': str(error)})
        except Exception as error:
            self.events.put({'type': 'log', 'text': traceback.format_exc()})
            self.events.put({'type': 'finished', 'text': f'任务中断：{error}'})

    def stop(self):
        if self.pipeline:
            self.stop_button.configure(state='disabled')
            self.status.set('正在停止子进程…')
            threading.Thread(target=self.pipeline.cancel, daemon=True).start()

    def export_blender(self, result=None):
        if self.running:
            return
        model = (Path(result['model']) if model_ready(result) else None) if result is not None else self.completed_model()
        if model is None:
            self.refresh_export_state()
            messagebox.showinfo('请先完成重建', '请先成功生成点云，再选择一个已完成的任务生成 Blender 文件。')
            return
        try:
            for name in ('cameras.txt', 'images.txt', 'points3D.txt'):
                if not (model / name).is_file():
                    raise ValueError(f'缺少 {name}：请先完成求解和 TXT 导出。')
            fps = float(self.blender_fps.get())
            if not 0 < fps <= 240:
                raise ValueError('Blender FPS 需要在 0 到 240 之间。')
        except Exception as error:
            messagebox.showerror('无法导出', str(error))
            return
        if not (ROOT / 'photogrammetry_importer/__init__.py').is_file():
            messagebox.showerror('缺少内置插件', '请保留程序目录中的 photogrammetry_importer 文件夹。')
            return
        try:
            chosen = self.choose_blender()
        except (OSError, ValueError) as error:
            messagebox.showerror('无法保存 Blender 设置', str(error), parent=self)
            return
        if not chosen:
            return
        blender, fps = chosen['path'], chosen['fps']
        output = filedialog.asksaveasfilename(title='保存新的 Blender 场景', initialdir=str(model.parent.parent),
                                            initialfile='tracking_' + time.strftime('%H%M%S') + '.blend',
                                            defaultextension='.blend', filetypes=[('Blender', '*.blend')])
        if not output:
            return
        if Path(output).exists():
            messagebox.showerror('请选择新文件', '为保留已有文件，请使用新的文件名。')
            return
        pipeline = Pipeline(Options(), self.events.put)
        pipeline.log_dir = model.parent.parent / 'blender-logs'
        pipeline.log_dir.mkdir(exist_ok=True)
        self.pipeline = pipeline
        self.latest_model = model
        self.running = True
        self.refresh_export_state()
        self.started = time.perf_counter()
        self.start_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.status.set('生成 Blender 相机动画和点云…')
        def export():
            try:
                pipeline.command([blender, '--factory-startup', '--background', '--python-exit-code', '1',
                                  '--python', APP / 'blender_import.py', '--',
                                  '--model', model, '--output', output, '--fps', str(fps),
                                  '--as-mesh', str(int(chosen['as_mesh'])),
                                  '--draw-points', str(int(chosen.get('draw_points',True))),
                                  '--suppress-distortion-warnings', str(int(chosen['suppress_warnings'])),
                                  '--background-images', str(int(chosen['background_images'])),
                                  '--point-radius', str(chosen['point_radius'])], 'blender-import')
                self.events.put({'type': 'log', 'text': f'\n[Blender] 已保存：{output}\n'})
                self.events.put({'type':'blender_saved','model':str(model),'output':output,'blender':chosen})
                self.events.put({'type': 'finished', 'text': 'Blender 文件已生成'})
            except Exception as error:
                self.events.put({'type': 'log', 'text': traceback.format_exc()})
                self.events.put({'type': 'finished', 'text': str(error)})
        threading.Thread(target=export, daemon=True).start()

    def append_log(self, text):
        if not text:
            return
        rotate_log(LOGS / 'session.log',len(text.encode('utf-8')))
        with (LOGS / 'session.log').open('a',encoding='utf-8') as stream:
            stream.write(text)
        # Very long wrapped lines can stall Tk; the full text remains on disk.
        preview = ''.join(line if len(line) <= 2000 else line[:2000] + '…[长行已截断，完整内容见日志文件]\n'
                          for line in text[-LOG_PREVIEW_CHARS:].splitlines(keepends=True))
        self.log_lines.append(preview)
        self.log_char_count += len(preview)
        while len(self.log_lines) > LOG_PREVIEW_CHUNKS:
            self.log_char_count -= len(self.log_lines.popleft())
        while self.log_char_count > LOG_PREVIEW_CHARS:
            excess = self.log_char_count - LOG_PREVIEW_CHARS
            first = self.log_lines.popleft()
            if len(first) > excess:
                self.log_lines.appendleft(first[excess:])
                self.log_char_count -= excess
            else:
                self.log_char_count -= len(first)
        if self.log is None or not self.log.winfo_exists():
            return
        self.log.configure(state='normal')
        self.log.insert('end', preview)
        self.log.delete('1.0', f'end-{LOG_PREVIEW_CHARS + 1}c')
        self.log.see('end')
        self.log.configure(state='disabled')

    def poll(self):
        chunks = []
        for _ in range(500):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event['type']
            if kind == 'log':
                chunks.append(event['text'])
            elif kind == 'stage':
                self.status.set(event['text'])
                self.progress.configure(value=event['index'] - 1)
            elif kind == 'scene':
                self.latest_scene = Path(event['path'])
            elif kind == 'job':
                self.tree.set(event['iid'], 'status', event['status'])
                if 'result' in event:
                    self.jobs[event['iid']]['result'] = event['result']
                    self.latest_completed_job = event['iid']
            elif kind == 'complete':
                self.latest_model = Path(event['model'])
                self.progress.configure(value=5)
                chunks.append(f"\n[完成] Blender 模型目录：{event['model']}\n")
                metrics = event.get('metrics', {})
                chunks.append(f"[结果] 注册 {metrics.get('registered_images', '?')}/{event['database']['images']} 帧 · "
                              f"{metrics.get('points', '?')} 点 · 重投影误差 {metrics.get('mean_reprojection_error_px', '?')} px · "
                              f"求解耗时 {event['mapping_wall_seconds']:.2f}s · GPU 求解 {'已启用' if event.get('gpu_solver_active') else '未全部启用'}\n")
            elif kind == 'finished':
                self.running = False
                self.status.set(event['text'])
                self.start_button.configure(state='normal')
                self.stop_button.configure(state='disabled')
                self.schedule_estimate()
                self.refresh_projects()
            elif kind == 'projects':
                self.show_projects(event)
            elif kind == 'blender_saved':
                try:
                    self.project_library.record_export(event['model'],event['output'],event.get('blender'))
                except OSError as error:
                    chunks.append(f'[项目管理] Blender 已保存，但记录失败：{error}\n')
            if kind in ('job', 'complete', 'finished'):
                self.refresh_export_state()
            if kind == 'environment' and event.get('generation') == self.environment_request:
                self.environment.set(event['text'])
                self.schedule_estimate()
            elif kind == 'log_cleanup' and not self.running:
                from log_management import delete_expired
                result = delete_expired(event['candidates'],event['days'])
                chunks.append(f"[日志清理] 保留 {event['days']} 天；清理 {result['removed']} 个过期日志，跳过 {result['skipped']} 个。\n")
            elif kind == 'estimate' and event['generation'] == self.estimate_request:
                self.estimate_text.set(event['text'])
        if chunks:
            self.append_log(''.join(chunks))
        if self.running:
            elapsed = int(time.perf_counter() - self.started)
            self.timer_text.set(f'{elapsed//60:02}:{elapsed%60:02}')
        self.after(100, self.poll)

    def check_environment(self):
        python = self.python.get()
        self.environment_request += 1
        generation = self.environment_request
        self.environment.set('正在检测实际 GPU、显存与 CUDA…')
        def check():
            try:
                profile = get_hardware(python, force=True)
                text = describe_hardware(profile)
                text += '  ·  GLOMAP GPU ' + ('已构建' if (BINARY_DIRS['01 GLOMAP GPU'] / 'glomap.exe').is_file() else '待构建')
            except Exception as error:
                text = '环境检测：' + str(error)
            self.events.put({'type': 'environment', 'text': text, 'generation': generation})
        threading.Thread(target=check, daemon=True).start()

    def open_result(self):
        path = self.latest_model or self.latest_scene or Path(self.output.get())
        if path.exists():
            os.startfile(str(path))

    def open_logs(self):
        if self.log_window is not None and self.log_window.winfo_exists():
            self.log_window.deiconify()
            self.log_window.lift()
            return
        window = tk.Toplevel(self)
        self.log_window = window
        window.protocol('WM_DELETE_WINDOW', self.close_logs)
        window.bind('<Destroy>', self.log_window_destroyed, add='+')
        window.title('运行日志 · ' + APP_NAME)
        window.geometry('960x580')
        window.configure(bg='#FFFDF9')
        head = ttk.Frame(window,padding=14)
        head.pack(fill='x')
        ttk.Label(head,text='运行日志',font=('Microsoft YaHei UI',12,'bold')).pack(side='left')
        ttk.Button(head,text='打开日志文件夹',command=self.open_log_folder).pack(side='right')
        from log_settings import show_log_settings
        ttk.Button(head,text='日志管理…',command=lambda:show_log_settings(self)).pack(side='right',padx=(0,8))
        body = ttk.Frame(window,padding=(14,0,14,14))
        body.pack(fill='both',expand=True)
        self.log = tk.Text(body,bg='#F4F7F1',fg='#294334',font=('Consolas',9),wrap='word',relief='flat',padx=12,pady=10)
        scroll = ttk.Scrollbar(body,command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y')
        self.log.pack(side='left',fill='both',expand=True)
        self.log.insert('end',''.join(self.log_lines) or '尚无运行日志。\n')
        self.log.configure(state='disabled')
        self.log.see('end')

    def log_window_destroyed(self, event):
        if event.widget is self.log_window:
            self.log_window = None
            self.log = None

    def close_logs(self):
        if self.log_window is not None and self.log_window.winfo_exists():
            self.log_window.destroy()
        self.log_window = None
        self.log = None

    def open_log_folder(self):
        path = self.pipeline.log_dir if self.pipeline and self.pipeline.log_dir else LOGS
        path.mkdir(parents=True,exist_ok=True)
        os.startfile(str(path))

    def close(self):
        if self.running:
            messagebox.showinfo('任务仍在运行', '请先点击“停止任务”，等待任务停止后再关闭。')
            return
        try:
            self.save_settings(self.options())
        except (ValueError,KeyError,OSError):
            pass
        self.destroy()


def main():
    # Keep the original CLI and legacy UI source; the normal desktop entry is Qt.
    if not any(arg in ('--scene', '--video') or arg.startswith(('--scene=', '--video=')) for arg in sys.argv[1:]):
        from qt_app import main as qt_main
        return qt_main()
    parser = argparse.ArgumentParser(description=APP_NAME + ' GUI / shared pipeline CLI')
    parser.add_argument('--scene', type=Path)
    parser.add_argument('--video', type=Path)
    parser.add_argument('--backend', choices=['fastmap', 'glomap'], default='fastmap')
    parser.add_argument('--cpu-glomap', action='store_true')
    parser.add_argument('--no-color', action='store_true')
    parser.add_argument('--calibrated', action='store_true')
    parser.add_argument('--gpu-min-images', type=int, default=50)
    parser.add_argument('--image-scale', type=float, default=1.0)
    parser.add_argument('--export-sequence', action='store_true')
    parser.add_argument('--sequence-original', action='store_true')
    parser.add_argument('--smoke-test', type=Path)
    parser.add_argument('--check-ui', action='store_true')
    args = parser.parse_args()
    if args.scene or args.video:
        options = Options(backend=args.backend, glomap_gpu=not args.cpu_glomap, color=not args.no_color,
                          camera_mode='calibrated' if args.calibrated else 'radial', gpu_min_images=args.gpu_min_images,
                          image_scale=args.image_scale, export_sequence=args.export_sequence,
                          sequence_original=args.sequence_original)
        def report(event):
            if event['type'] == 'log':
                print(event['text'], end='', flush=True)
            else:
                print(json.dumps(event, ensure_ascii=False), flush=True)
        result = Pipeline(options, report).run(args.scene or args.video, existing_scene=bool(args.scene))
        save_sample(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    app = App()
    if args.check_ui:
        app.update()
        assert app.tabs.select() == str(app.task_tab) and not app.jobs
        app.tabs.select(app.advanced_tab)
        app.update()
        assert len(app.tabs.tabs()) == 3
        app.tabs.select(app.projects_tab)
        app.update()
        print('LAUNCHER_UI_PASS', flush=True)
        app.destroy()
        return
    if args.smoke_test:
        app.tabs.select(app.task_tab)
        app.update()
        from PIL import ImageGrab
        x, y = app.winfo_rootx(), app.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height())).save(args.smoke_test)
        app.tabs.select(app.advanced_tab)
        app.update()
        ImageGrab.grab(bbox=(x,y,x+app.winfo_width(),y+app.winfo_height())).save(args.smoke_test.with_name(args.smoke_test.stem+'-advanced.png'))
        app.tabs.select(app.projects_tab)
        app.refresh_projects()
        deadline = time.perf_counter() + 10
        while app.project_loading and time.perf_counter() < deadline:
            app.update()
            time.sleep(.02)
        app.update()
        ImageGrab.grab(bbox=(x,y,x+app.winfo_width(),y+app.winfo_height())).save(args.smoke_test.with_name(args.smoke_test.stem+'-projects.png'))
        print('GUI_SMOKE_PASS', app.winfo_width(), app.winfo_height(), 'jobs', len(app.jobs))
        app.destroy()
        return
    app.mainloop()


if __name__ == '__main__':
    main()
