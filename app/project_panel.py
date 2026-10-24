"""Simple project browsing that survives application restarts."""
from pathlib import Path
import os
import threading
import subprocess
from tkinter import ttk, filedialog, messagebox, simpledialog


SOLVERS = {'glomap-gpu':'GLOMAP · GPU', 'glomap-cpu':'GLOMAP · CPU', 'fastmap':'FastMap'}


def build_project_panel(app):
    panel = app.projects_tab
    heading = ttk.Frame(panel)
    heading.grid(row=0,column=0,sticky='ew',pady=(0,16))
    ttk.Label(heading,text='重建结果',style='Kicker.TLabel').pack(anchor='w')
    ttk.Label(heading,text='项目管理',style='Title.TLabel').pack(anchor='w',pady=(3,4))
    ttk.Label(heading,text='管理相机、点云与 Blender 文件，每次重建单独保留。',style='Muted.TLabel').pack(anchor='w')
    panel.columnconfigure(0, weight=1)
    panel.rowconfigure(2, weight=1)
    tools = ttk.Frame(panel)
    tools.grid(row=1, column=0, sticky='ew', pady=(0,12))
    tools.columnconfigure(3, weight=1)
    app.project_refresh_button = ttk.Button(tools, text='刷新列表', command=app.refresh_projects)
    app.project_refresh_button.grid(row=0, column=0, padx=(0,8))
    ttk.Button(tools, text='添加项目目录…', command=app.import_project_directory).grid(row=0,column=1,padx=(0,20))
    ttk.Label(tools,text='搜索项目').grid(row=0,column=2,padx=(0,10))
    ttk.Entry(tools,textvariable=app.project_search).grid(row=0,column=3,sticky='ew')

    table = ttk.Frame(panel,style='Table.TFrame')
    table.grid(row=2,column=0,sticky='nsew')
    app.project_tree = ttk.Treeview(table,columns=('name','solver','frames','points','date','state'),
                                    show='headings',height=7,selectmode='browse')
    for key,label,width in [('name','项目名称',220),('solver','求解方案',135),('frames','注册帧',90),
                            ('points','点云数量',110),('date','完成时间',175),('state','状态',120)]:
        app.project_tree.heading(key,text=label)
        app.project_tree.column(key,width=width,minwidth=75,stretch=key=='name',anchor='e' if key in ('frames','points') else 'w')
    scroll = ttk.Scrollbar(table,orient='vertical',command=app.project_tree.yview)
    app.project_tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right',fill='y',padx=(0,10),pady=10)
    app.project_tree.pack(side='left',fill='both',expand=True,padx=(10,0),pady=10)
    app.project_tree.bind('<<TreeviewSelect>>',lambda _event:app.select_project())
    app.project_tree.bind('<Double-1>',lambda _event:app.open_project_directory())

    details = ttk.LabelFrame(panel,text='项目详情',style='Card.TLabelframe',padding=(14,10))
    details.grid(row=3,column=0,sticky='ew',pady=(14,12))
    app.project_details_label = ttk.Label(details,textvariable=app.project_details,wraplength=990,justify='left')
    app.project_details_label.pack(anchor='w',fill='x')
    details.bind('<Configure>',lambda event:app.project_details_label.configure(wraplength=max(200,event.width-35)))
    actions = ttk.Frame(panel)
    actions.grid(row=4,column=0,sticky='ew')
    app.project_actions = {}
    for key,label,command in [('open','打开结果目录',app.open_project_directory),
                              ('logs','打开项目日志',app.open_project_logs),
                              ('source','打开素材位置',app.open_project_source),
                              ('rename','重命名',app.rename_project)]:
        button = ttk.Button(actions,text=label,command=command,state='disabled')
        button.pack(side='left',padx=(0,8))
        app.project_actions[key] = button
    bottom = ttk.Frame(panel)
    bottom.grid(row=5,column=0,sticky='ew',pady=(10,0))
    app.project_export_button = ttk.Button(bottom,text='生成Blender文件…',style='Accent.TButton',
                                           command=app.export_project_blender,state='disabled')
    app.project_export_button.pack(side='left',padx=(0,8))
    app.project_blend_button = ttk.Button(bottom,text='打开 Blender 文件',command=app.open_project_blend,state='disabled')
    app.project_blend_button.pack(side='left')
    app.project_archive_button = ttk.Button(bottom,text='移入项目回收目录…',command=app.archive_project,state='disabled')
    app.project_archive_button.pack(side='right')
    ttk.Label(panel,textvariable=app.project_summary,style='Muted.TLabel').grid(row=6,column=0,sticky='w',pady=(10,0))


class ProjectManagerMixin:
    def refresh_projects(self):
        if self.project_loading:
            self.project_refresh_pending = True
            return
        self.project_loading = True
        self.project_summary.set('正在读取项目记录…')
        self.project_refresh_button.configure(state='disabled')
        output = self.output.get()
        def scan():
            try:
                records, warnings = self.project_library.scan(output)
                self.events.put({'type':'projects','records':records,'warnings':warnings})
            except Exception as error:
                self.events.put({'type':'projects','records':[], 'warnings':[str(error)]})
        threading.Thread(target=scan,daemon=True).start()

    def show_projects(self, event):
        self.project_loading = False
        self.project_refresh_button.configure(state='normal')
        self.project_records = {record['id']:record for record in event['records']}
        new_warnings = [warning for warning in event['warnings'] if warning not in self.project_warnings]
        self.project_warnings = event['warnings']
        self.filter_projects()
        if new_warnings:
            self.append_log('[项目管理] ' + '\n'.join(new_warnings) + '\n')
        if self.project_refresh_pending:
            self.project_refresh_pending = False
            self.refresh_projects()

    def filter_projects(self):
        previous = self.project_tree.selection()
        selected = previous[0] if previous else None
        query = self.project_search.get().strip().casefold()
        children = self.project_tree.get_children()
        if children:
            self.project_tree.delete(*children)
        count = 0
        for key, record in self.project_records.items():
            result = record['result']
            backend = SOLVERS.get(result.get('backend'),result.get('backend','未知'))
            if query and query not in (record['name']+' '+record['source']+' '+backend).casefold():
                continue
            metrics = result['metrics']
            state = 'Blender 已生成' if record['exports'] and record['ready'] else ('点云已完成' if record['ready'] else '文件不完整')
            self.project_tree.insert('', 'end', iid=key, values=(record['name'],backend,
                metrics.get('registered_images','—'),f"{metrics.get('points',0):,}",
                record['completed'].replace('T',' '),state))
            count += 1
        children = self.project_tree.get_children()
        if selected in children:
            self.project_tree.selection_set(selected)
        elif children:
            self.project_tree.selection_set(children[0])
        self.project_summary.set(f'显示 {count} / {len(self.project_records)} 项结果 · 每次重建单独保留'
                                 + (f' · {len(self.project_warnings)} 条读取提示，见日志' if self.project_warnings else ''))
        self.select_project()

    def selected_project(self):
        selection = self.project_tree.selection()
        return self.project_records.get(selection[0]) if selection else None

    def select_project(self):
        record = self.selected_project()
        if not record:
            self.project_details.set('选择一个项目，查看相机、点云和 Blender 文件。')
        else:
            result = record['result']
            metrics = result['metrics']
            seconds = result.get('task_wall_seconds',result.get('mapping_wall_seconds',0))
            minutes, seconds = divmod(round(seconds),60)
            error = metrics.get('mean_reprojection_error_px')
            error = f'{error:.3f} px' if isinstance(error,(int,float)) else '—'
            blend = record['exports'][0] if record['exports'] else '尚未生成'
            saved = self.project_library.data['export_settings'].get(str(Path(blend).resolve()), {}) if record['exports'] else {}
            version = saved.get('version', '')
            self.project_details.set(f"素材：{record['source']}\n"
                f"注册帧：{metrics.get('registered_images','—')} / {result.get('database',{}).get('images','—')}"
                f"    点云：{metrics.get('points',0):,}    平均误差：{error}    本次耗时：{minutes} 分 {seconds} 秒\n"
                f"结果：{result['model']}\nBlender：{blend}" + (f"\n生成版本：{version}" if version else ''))
        self.refresh_project_actions()

    def refresh_project_actions(self):
        record = self.selected_project()
        for button in self.project_actions.values():
            button.configure(state='normal' if record else 'disabled')
        ready = bool(record and record['ready'] and not self.running)
        self.project_export_button.configure(state='normal' if ready else 'disabled')
        self.project_blend_button.configure(state='normal' if record and record['exports'] else 'disabled')
        self.project_archive_button.configure(state='normal' if record and not self.running else 'disabled')
        self.project_actions['rename'].configure(state='normal' if record and not self.running else 'disabled')
        source = Path(record['source']) if record and record['source'] else None
        self.project_actions['source'].configure(state='normal' if source and source.exists() else 'disabled')

    def project_tab_changed(self, _event=None):
        if self.tabs.select() == str(self.projects_tab):
            self.refresh_projects()

    def import_project_directory(self):
        selected = filedialog.askdirectory(parent=self,title='选择项目或输出目录',initialdir=self.output.get())
        if not selected:
            return
        try:
            self.project_library.add_root(selected)
            self.refresh_projects()
        except (OSError,ValueError) as error:
            messagebox.showerror('无法添加项目目录',str(error),parent=self)

    def open_project_path(self, path):
        path = Path(path)
        if path.exists():
            os.startfile(str(path))
        else:
            messagebox.showinfo('文件不存在',f'文件可能已被移动或删除，请刷新列表。\n{path}',parent=self)

    def open_project_directory(self):
        record = self.selected_project()
        if record:
            self.open_project_path(record['directory'])

    def open_project_source(self):
        record = self.selected_project()
        if record and record['source']:
            source = Path(record['source'])
            self.open_project_path(source if source.is_dir() else source.parent)

    def open_project_logs(self):
        record = self.selected_project()
        if not record:
            return
        result = record['result']
        run = Path(record['directory'])
        log = Path(result['scene']) / 'logs' / f"{result['backend']}_{run.name}"
        self.open_project_path(log if log.is_dir() else Path(result['scene']) / 'logs')

    def rename_project(self):
        record = self.selected_project()
        if not record or self.running:
            return
        name = simpledialog.askstring('重命名项目','项目名称（不改变文件目录）：',initialvalue=record['name'],parent=self)
        if name is None:
            return
        try:
            self.project_library.rename(record,name)
            record['name'] = name.strip()
            self.filter_projects()
        except (OSError,ValueError) as error:
            messagebox.showerror('无法重命名',str(error),parent=self)

    def export_project_blender(self):
        record = self.selected_project()
        if record:
            self.export_blender(result=record['result'])

    def open_project_blend(self):
        record = self.selected_project()
        if record and record['exports']:
            blend = str(Path(record['exports'][0]).resolve())
            if not Path(blend).is_file():
                messagebox.showinfo('文件不存在', '请刷新项目列表。', parent=self)
                return
            from paths import APP, ROOT
            saved = self.project_library.data['export_settings'].get(blend, {})
            try:
                chosen = self.choose_blender(opening=True, preferred=saved.get('path'))
                if chosen:
                    # Register the bundled plugin before opening so OpenGL cloud handles redraw.
                    subprocess.Popen([chosen['path'], '--python', str(APP / 'blender_open.py'),
                                      '--', '--blend', blend], cwd=str(ROOT))
            except (OSError, ValueError) as error:
                messagebox.showerror('无法打开 Blender', str(error), parent=self)

    def archive_project(self):
        record = self.selected_project()
        if not record or self.running:
            return
        if not messagebox.askyesno('移入项目回收目录',f"将“{record['name']}”的这次重建结果移入项目回收目录？\n"
                '原视频、抽帧及匹配数据库都会保留。结果目录内的 Blender 文件一并移动。\n'
                '需要恢复时，可按 recovery.json 中的原路径移回。',parent=self):
            return
        try:
            destination = self.project_library.archive(record)
            self.append_log(f'[项目管理] 已移入回收目录：{destination}\n')
            self.project_details.set(f'已移入回收目录：{destination}')
            self.refresh_export_state()
            self.refresh_projects()
        except (OSError,ValueError) as error:
            messagebox.showerror('无法移除项目',str(error),parent=self)
