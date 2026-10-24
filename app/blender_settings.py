"""Blender selection and export preferences, independent of reconstruction settings."""
from pathlib import Path
import os
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from blender_runtime import probe_blender, discover_blenders


class BlenderExportDialog(tk.Toplevel):
    def __init__(self, parent, settings, fps=30, opening=False):
        super().__init__(parent)
        self.title('选择 Blender 版本' if opening else '生成 Blender 文件')
        self.transient(parent)
        self.resizable(True, False)
        self.configure(bg='#FFFDF9')
        self.result = None
        self.poll_after = None
        self.settings = settings
        self.opening = opening
        self.records = []
        self.pending = queue.Queue()
        self.selection = tk.StringVar()
        self.fps = tk.StringVar(value=str(fps))
        self.mesh = tk.BooleanVar(value=settings.get('blender_as_mesh', True))
        self.draw_points = tk.BooleanVar(value=settings.get('blender_draw_points', True))
        self.warnings = tk.BooleanVar(value=settings.get('blender_suppress_warnings', True))
        self.background = tk.BooleanVar(value=settings.get('blender_background_images', True))
        self.radius = tk.StringVar(value=str(settings.get('blender_point_radius', 0.05)))
        body = ttk.Frame(self, padding=20)
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text='Blender 版本').grid(row=0, column=0, sticky='w')
        self.combo = ttk.Combobox(body, textvariable=self.selection, state='readonly', width=72)
        self.combo.grid(row=1, column=0, sticky='ew', pady=(6, 8))
        ttk.Button(body, text='添加版本…', command=self.browse).grid(row=1, column=1, padx=(10, 0))
        self.note = tk.StringVar(value='正在查找本机 Blender…')
        ttk.Label(body, textvariable=self.note, wraplength=640, style='Muted.TLabel').grid(row=2, column=0, columnspan=2, sticky='w')
        if not opening:
            card = ttk.LabelFrame(body, text='内置 Photogrammetry Importer', padding=14)
            card.grid(row=3, column=0, columnspan=2, sticky='ew', pady=16)
            ttk.Checkbutton(card, text='显示彩色点（插件 GPU 点云）', variable=self.draw_points).grid(row=0, column=0, columnspan=2, sticky='w')
            ttk.Checkbutton(card, text='同时作为网格（可编辑顶点 / 几何节点用于渲染）', variable=self.mesh).grid(row=1, column=0, columnspan=2, sticky='w', pady=6)
            ttk.Checkbutton(card, text='忽略提示（仅隐藏镜头畸变警告）', variable=self.warnings).grid(row=2, column=0, columnspan=2, sticky='w', pady=6)
            ttk.Checkbutton(card, text='相机背景使用抽帧图像', variable=self.background).grid(row=3, column=0, columnspan=2, sticky='w')
            ttk.Label(card, text='网格渲染点半径').grid(row=4, column=0, sticky='w', pady=(12, 0))
            ttk.Entry(card, textvariable=self.radius, width=12).grid(row=4, column=1, sticky='w', pady=(12, 0))
            ttk.Label(card, text='Blender FPS').grid(row=5, column=0, sticky='w', pady=(8, 0))
            ttk.Entry(card, textvariable=self.fps, width=12).grid(row=5, column=1, sticky='w', pady=(8, 0))
            ttk.Label(card, text='彩色点请从项目管理打开以加载内置插件。网格在视图中隐藏，渲染时保留；\n编辑网格可在 Blender 大纲中打开其眼睛图标。背景图引用 images 文件夹。', style='Muted.TLabel').grid(row=6, column=0, columnspan=2, sticky='w', pady=(12, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=2, sticky='e', pady=(16, 0))
        ttk.Button(buttons, text='取消', command=self.destroy).pack(side='left', padx=8)
        self.accept_button = ttk.Button(buttons, text='打开文件' if opening else '选择保存位置…', style='Accent.TButton', command=self.accept, state='disabled')
        self.accept_button.pack(side='left')
        self.grab_set()
        self.poll_after = self.after(100, self.receive)
        threading.Thread(target=self.discover, daemon=True).start()

    def discover(self):
        self.pending.put(discover_blenders(self.settings))

    def receive(self):
        self.poll_after = None
        if not self.winfo_exists():
            return
        try:
            records = self.pending.get_nowait()
        except queue.Empty:
            self.poll_after = self.after(100, self.receive)
            return
        selected = self.selected_record()
        selected_path = selected['path'] if selected else self.settings.get('blender_path')
        for record in records:
            if record['path'] not in [r['path'] for r in self.records]:
                self.records.append(record)
        self.combo['values'] = [f"{r['version']}  |  {r['path']}" for r in self.records]
        index = next((i for i, r in enumerate(self.records) if os.path.normcase(r['path']) == os.path.normcase(selected_path or '')), None)
        if index is not None:
            self.combo.current(index)
        elif len(self.records) == 1 and not selected_path:
            self.combo.current(0)
        self.note.set(f'找到 {len(self.records)} 个版本；请选择本次使用的版本。可添加其他安装位置。' if self.records else '未找到 Blender，请点击“添加版本…”选择 blender.exe。')
        self.accept_button.configure(state='normal' if self.records else 'disabled')
        self.poll_after = self.after(100, self.receive)

    def selected_record(self):
        index = self.combo.current()
        return self.records[index] if 0 <= index < len(self.records) else None

    def browse(self):
        value = filedialog.askopenfilename(parent=self, title='选择 Blender 程序', filetypes=[('Blender', 'blender.exe')])
        if not value:
            return
        self.note.set('正在验证所选 Blender…')
        def probe():
            try:
                record = probe_blender(value)
                self.pending.put([record])
            except Exception as error:
                self.pending.put([])
                self.error = str(error)
        self.error = None
        def done():
            if not self.winfo_exists():
                return
            if worker.is_alive():
                self.after(100, done)
                return
            if self.poll_after:
                self.after_cancel(self.poll_after)
            self.receive()
            if self.error:
                messagebox.showerror('无法使用 Blender', self.error, parent=self)
                return
            resolved = str(Path(value).resolve())
            index = next((i for i, r in enumerate(self.records) if r['path'] == resolved), None)
            if index is not None:
                self.combo.current(index)
        worker = threading.Thread(target=probe, daemon=True)
        worker.start()
        self.after(100, done)

    def accept(self):
        record = self.selected_record()
        if not record:
            messagebox.showinfo('选择版本', '请选择要使用的 Blender 版本。', parent=self)
            return
        if not self.opening and not self.mesh.get() and not self.draw_points.get():
            messagebox.showinfo('选择点云形式', '请至少勾选“显示彩色点”或“同时作为网格”。', parent=self)
            return
        try:
            fps, radius = float(self.fps.get()), float(self.radius.get())
            if not 0 < fps <= 240 or not 0 < radius <= 100:
                raise ValueError('FPS 需要在 0 到 240 之间，点半径需要大于 0 且不超过 100。')
        except ValueError as error:
            messagebox.showerror('参数无效', str(error), parent=self)
            return
        self.result = dict(record, fps=fps, as_mesh=self.mesh.get(), suppress_warnings=self.warnings.get(),
                           draw_points=self.draw_points.get(),
                           background_images=self.background.get(), point_radius=radius,
                           paths=[r['path'] for r in self.records])
        self.destroy()

    def destroy(self):
        if self.poll_after:
            self.after_cancel(self.poll_after)
            self.poll_after = None
        super().destroy()


def ask_blender_settings(parent, settings, fps=30, opening=False):
    dialog = BlenderExportDialog(parent, settings, fps, opening)
    parent.wait_window(dialog)
    return dialog.result
