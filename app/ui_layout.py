"""Light workspace cards, forest-green navigation, and separate professional controls."""
import tkinter as tk
from tkinter import ttk
from app_info import APP_NAME


BACKGROUND = '#EDF1ED'
PAPER = '#FFFDF9'
FOREST = '#16271F'
GREEN = '#496B5A'
INK = '#19312A'
MUTED = '#75857D'


class RoundedCard(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=BACKGROUND, highlightthickness=0, bd=0, **kwargs)
        self.body = ttk.Frame(self)
        self.bind('<Configure>', self.resize)

    def resize(self, event):
        self.delete('surface')
        w, h, r = event.width - 2, event.height - 2, 22
        self.create_polygon(1+r, 1, w-r, 1, w, 1, w, 1+r, w, h-r, w, h,
                            w-r, h, 1+r, h, 1, h, 1, h-r, 1, 1+r, 1, 1,
                            smooth=True, splinesteps=24, fill=PAPER, outline='#CCD7CE', tags='surface')
        self.tag_lower('surface')
        self.body.place(x=22, y=20, width=max(1, event.width-44), height=max(1, event.height-40))


def apply_theme(app):
    style = ttk.Style(app)
    style.theme_use('clam')
    style.configure('.', font=('Microsoft YaHei UI', 10), background=PAPER, foreground=INK)
    style.configure('TFrame', background=PAPER)
    style.configure('Shell.TFrame', background=BACKGROUND)
    style.configure('TLabel', background=PAPER, foreground=INK)
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('Kicker.TLabel', foreground='#AD8349', font=('Microsoft YaHei UI', 9))
    style.configure('Title.TLabel', font=('Microsoft YaHei UI', 19, 'bold'))
    style.configure('TButton', background='#F7F9F5', foreground=INK, padding=(13, 9),
                    borderwidth=1, bordercolor='#CFDAD1', lightcolor='#F7F9F5', darkcolor='#F7F9F5', relief='flat')
    style.map('TButton', background=[('active','#E5EDE5'),('disabled','#EDF0EB')], foreground=[('disabled','#A2ACA5')])
    style.configure('Accent.TButton', background=GREEN, foreground='white', font=('Microsoft YaHei UI',10,'bold'), bordercolor=GREEN)
    style.map('Accent.TButton', background=[('active','#365442'),('disabled','#B0BDB4')], foreground=[('disabled','#F7FAF7')])
    style.configure('TEntry', fieldbackground='#F8FAF6', foreground=INK, insertcolor=INK,
                    bordercolor='#CFDAD1', lightcolor='#CFDAD1', darkcolor='#CFDAD1', padding=8)
    style.configure('TCombobox', fieldbackground='#F8FAF6', background='#F8FAF6', foreground=INK,
                    arrowcolor=GREEN, bordercolor='#CFDAD1', lightcolor='#CFDAD1', darkcolor='#CFDAD1', padding=8)
    style.map('TCombobox', fieldbackground=[('readonly','#F8FAF6')], foreground=[('readonly',INK)],
              selectbackground=[('readonly','#E5EDE5')], selectforeground=[('readonly',INK)])
    style.configure('TCheckbutton', background=PAPER, foreground=INK, padding=(0,4), indicatorbackground='#F8FAF6', indicatorforeground=GREEN)
    style.map('TCheckbutton', background=[('active',PAPER)], indicatorbackground=[('selected',GREEN)], indicatorforeground=[('selected','white')])
    style.configure('Treeview', background='#F8FAF7', fieldbackground='#F8FAF7', foreground=INK,
                    rowheight=36, borderwidth=0, bordercolor='#D5DFD6')
    style.configure('Treeview.Heading', background='#EAF0E9', foreground='#52695B', relief='flat', padding=10, font=('Microsoft YaHei UI',9))
    style.map('Treeview', background=[('selected','#DCE9DD')], foreground=[('selected',INK)])
    style.configure('TNotebook', background=PAPER, bordercolor=PAPER, lightcolor=PAPER,
                    darkcolor=PAPER, borderwidth=0, tabmargins=0)
    style.layout('TNotebook.Tab', [])
    style.configure('Card.TLabelframe', background=PAPER, bordercolor='#D7E1D7',lightcolor='#D7E1D7',darkcolor='#D7E1D7',borderwidth=1,relief='solid')
    style.configure('Card.TLabelframe.Label',background=PAPER,foreground=GREEN,font=('Microsoft YaHei UI',11,'bold'))
    style.configure('TLabelframe',background=PAPER,bordercolor='#D7E1D7')
    style.configure('TLabelframe.Label',background=PAPER,foreground=GREEN)
    style.configure('Horizontal.TProgressbar',background=GREEN,troughcolor='#E4EBE2',borderwidth=0,thickness=5)
    style.configure('Vertical.TScrollbar',background='#D3DDD3',troughcolor=PAPER,borderwidth=0,arrowsize=12)
    app.option_add('*TCombobox*Listbox.background', '#F8FAF6')
    app.option_add('*TCombobox*Listbox.foreground', INK)
    app.option_add('*TCombobox*Listbox.selectBackground', '#DCE9DD')
    from rounded_theme import install
    install(app,style)


def build(app, backends):
    apply_theme(app)
    app.configure(bg=BACKGROUND)
    side = tk.Frame(app,bg=FOREST,width=184)
    side.pack(side='left',fill='y')
    side.pack_propagate(False)
    tk.Label(side,text='AT',bg=FOREST,fg='#EFDAA8',font=('Segoe UI',30,'bold'),anchor='w').pack(fill='x',padx=22,pady=(24,0))
    tk.Label(side,text=APP_NAME,bg=FOREST,fg='#E2EBDD',font=('Segoe UI',11),anchor='w').pack(fill='x',padx=24)
    tk.Label(side,text='CAMERA  /  POINT CLOUD',bg=FOREST,fg='#84998B',font=('Segoe UI',8),anchor='w').pack(fill='x',padx=24,pady=(6,28))
    nav = tk.Frame(side,bg=FOREST)
    nav.pack(fill='x',padx=14)
    tk.Label(side,text='本地工作空间\nGLOMAP  /  FastMap\n内置摄影测量导入插件',justify='left',anchor='w',bg=FOREST,fg='#91A596',font=('Microsoft YaHei UI',9)).pack(side='bottom',fill='x',padx=22,pady=24)
    shell = ttk.Frame(app,style='Shell.TFrame',padding=(20,16,20,14))
    shell.pack(side='left',fill='both',expand=True)
    status_bar = tk.Frame(shell,bg='#E3EBE2',highlightbackground='#CCD7CE',highlightthickness=1)
    status_bar.pack(side='bottom',fill='x',pady=(10,0))
    tk.Label(status_bar,text='●',fg=GREEN,bg='#E3EBE2').pack(side='left',padx=(10,4),pady=5)
    hardware_label = tk.Label(status_bar,textvariable=app.environment,bg='#E3EBE2',fg='#526A5A',font=('Microsoft YaHei UI',8),anchor='w',justify='left',wraplength=850)
    hardware_label.pack(side='left',fill='x',expand=True)
    hardware_label.bind('<Configure>',lambda event:hardware_label.configure(wraplength=max(200,event.width)))
    tk.Label(status_bar,textvariable=app.timer_text,bg='#E3EBE2',fg=GREEN,font=('Consolas',10)).pack(side='right',padx=12)
    surface = RoundedCard(shell)
    surface.pack(fill='both',expand=True)
    app.tabs = ttk.Notebook(surface.body)
    app.tabs.pack(fill='both',expand=True)
    app.task_tab = ttk.Frame(app.tabs,padding=0)
    app.projects_tab = ttk.Frame(app.tabs,padding=0)
    app.advanced_tab = ttk.Frame(app.tabs,padding=0)
    app.tabs.add(app.task_tab,text='任务面板')
    app.tabs.add(app.projects_tab,text='项目管理')
    app.tabs.add(app.advanced_tab,text='高级参数')
    app.tabs.select(app.task_tab)
    from project_panel import build_project_panel
    build_project_panel(app)
    nav_buttons = []
    for label,page in [('◈   任务面板',app.task_tab),('▤   项目管理',app.projects_tab),('⚙   高级参数',app.advanced_tab)]:
        button = ttk.Button(nav,text=label,command=lambda target=page:app.tabs.select(target),style='Nav.TButton',cursor='hand2')
        button.pack(fill='x',pady=5)
        nav_buttons.append((button,page))
    def changed(event=None):
        for button,page in nav_buttons:
            selected = app.tabs.select() == str(page)
            button.configure(style='Selected.Nav.TButton' if selected else 'Nav.TButton')
        app.project_tab_changed(event)
    app.tabs.bind('<<NotebookTabChanged>>',changed)
    changed()

    task = app.task_tab
    task.columnconfigure(0,weight=1)
    task.rowconfigure(2,weight=1)
    settings = ttk.Frame(task)
    settings.grid(row=0,column=0,sticky='ew',pady=(0,16))
    ttk.Label(settings,text='素材与队列',style='Kicker.TLabel').pack(anchor='w')
    ttk.Label(settings,text='创建重建任务',style='Title.TLabel').pack(anchor='w',pady=(3,4))
    ttk.Label(settings,text='选择视频与求解方案，完成点云后生成 Blender 场景。',style='Muted.TLabel').pack(anchor='w',pady=(0,20))
    solver_row = ttk.Frame(settings)
    solver_row.pack(fill='x')
    ttk.Label(solver_row,text='求解方案').pack(side='left',padx=(0,16))
    app.backend_box = ttk.Combobox(solver_row,textvariable=app.backend,values=list(backends),state='readonly',width=30)
    app.backend_box.pack(side='left',fill='x',expand=True,padx=(0,24))
    app.gpu_check = ttk.Checkbutton(solver_row,text='GLOMAP 使用 CUDA/cuDSS 求解',variable=app.glomap_gpu)
    app.gpu_check.pack(side='right')
    path_row = ttk.Frame(settings)
    path_row.pack(fill='x',pady=(12,0))
    ttk.Label(path_row,text='输出目录').pack(side='left',padx=(0,16))
    ttk.Button(path_row,text='浏览…',command=lambda:app.browse_directory(app.output)).pack(side='right',padx=(10,0))
    ttk.Entry(path_row,textvariable=app.output).pack(side='left',fill='x',expand=True)
    tools = ttk.Frame(task)
    tools.grid(row=1,column=0,sticky='ew',pady=(0,14))
    for label, command in [('+添加视频',app.add_videos),('选择视频文件夹…',app.add_video_folder)]:
        ttk.Button(tools,text=label,command=command).pack(side='left',padx=(0,8))
    app.blender_button = ttk.Button(tools,text='生成Blender文件…',command=app.export_blender,state='disabled')
    app.blender_button.pack(side='left',padx=(0,8))
    ttk.Button(tools,text='移除选中',command=app.remove_selected).pack(side='right')
    table = ttk.Frame(task,style='Table.TFrame')
    table.grid(row=2,column=0,sticky='nsew')
    app.tree = ttk.Treeview(table,columns=('kind','path','status'),show='headings',height=8,selectmode='extended')
    for key,label,width in [('kind','输入',72),('path','视频路径',570),('status','状态',110)]:
        app.tree.heading(key,text=label)
        app.tree.column(key,width=width,minwidth=70,stretch=key=='path')
    scroll = ttk.Scrollbar(table,orient='vertical',command=app.tree.yview)
    app.tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right',fill='y',padx=(0,10),pady=10)
    app.tree.pack(side='left',fill='both',expand=True,padx=(10,0),pady=10)
    app.tree.bind('<<TreeviewSelect>>',lambda _event:app.refresh_export_state())
    empty = tk.Label(table,text='等待添加素材\n\n选择视频或视频文件夹，任务会在这里排队',
                     bg='#F8FAF7',fg='#8A9B8E',font=('Microsoft YaHei UI',11),justify='center')
    def show_empty():
        if not app.winfo_exists():
            return
        if app.tree.get_children():
            empty.place_forget()
        else:
            empty.place(relx=.5,rely=.50,anchor='center')
        app.after(400,show_empty)
    app.after(100,show_empty)
    estimate = ttk.Frame(task)
    estimate.grid(row=3,column=0,sticky='ew',pady=(16,12))
    app.estimate_label = ttk.Label(estimate,textvariable=app.estimate_text,wraplength=1000)
    app.estimate_label.pack(anchor='w',fill='x')
    estimate.bind('<Configure>',lambda event:app.estimate_label.configure(wraplength=max(200,event.width)))
    ttk.Label(estimate,text='预估仅供参考，受画面复杂度与GPU性能影响',style='Muted.TLabel',font=('Microsoft YaHei UI',9)).pack(anchor='w',pady=(5,0))
    app.progress = ttk.Progressbar(task,maximum=5)
    app.progress.grid(row=4,column=0,sticky='ew',pady=(0,14))
    actions = ttk.Frame(task)
    actions.grid(row=5,column=0,sticky='ew')
    actions.columnconfigure(2,weight=1)
    app.start_button = ttk.Button(actions,text='开始重建',style='Accent.TButton',command=app.start)
    app.start_button.grid(row=0,column=0,sticky='w')
    app.stop_button = ttk.Button(actions,text='停止任务',command=app.stop,state='disabled')
    app.stop_button.grid(row=0,column=1,sticky='w',padx=(8,12))
    app.status_label = ttk.Label(actions,textvariable=app.status,wraplength=390)
    app.status_label.grid(row=0,column=2,sticky='w',padx=(0,8))
    actions.bind('<Configure>',lambda event:app.status_label.configure(wraplength=max(180,event.width-470)))
    ttk.Button(actions,text='打开日志',command=app.open_logs).grid(row=0,column=3,sticky='e',padx=(0,8))
    ttk.Button(actions,text='打开结果',command=app.open_result).grid(row=0,column=4,sticky='e')

    advanced = app.advanced_tab
    ttk.Label(advanced,text='处理设置',style='Kicker.TLabel').pack(anchor='w')
    ttk.Label(advanced,text='高级参数',style='Title.TLabel').pack(anchor='w',pady=(3,5))
    ttk.Label(advanced,text='专业参数集中在这里，任务面板保持简洁。',style='Muted.TLabel').pack(anchor='w',pady=(0,20))
    images = ttk.LabelFrame(advanced,text='图像匹配设置',style='Card.TLabelframe',padding=12)
    images.pack(fill='x',pady=(0,12))
    for column in (1,3,5):
        images.columnconfigure(column,weight=1)
    fields = [('抽帧FPS (0=全部)',app.fps),('SIFT最大尺寸',app.max_size),('相邻匹配范围',app.overlap),('GPU最少图像',app.gpu_min)]
    for index,(label,variable) in enumerate(fields):
        row, pair = divmod(index,3)
        ttk.Label(images,text=label).grid(row=row,column=pair*2,sticky='w',padx=(0,10),pady=(4,12))
        ttk.Entry(images,textvariable=variable,width=9).grid(row=row,column=pair*2+1,sticky='ew',padx=(0,20) if pair<2 else 0,pady=(4,12))
    ttk.Label(images,text='相机畸变').grid(row=1,column=2,sticky='w',padx=(0,10),pady=(4,12))
    ttk.Combobox(images,textvariable=app.camera,state='readonly',width=22,
                 values=['自动估计畸变（默认）','近似针孔（无畸变）']).grid(row=1,column=3,sticky='ew',padx=(0,20),pady=(4,12))
    ttk.Label(images,text='Blender FPS').grid(row=1,column=4,sticky='w',padx=(0,10),pady=(4,12))
    ttk.Entry(images,textvariable=app.blender_fps,width=9).grid(row=1,column=5,sticky='ew',pady=(4,12))
    ttk.Label(images,text='相机畸变选项用于 FastMap；GLOMAP 沿用匹配数据库的相机模型。',style='Muted.TLabel',font=('Microsoft YaHei UI',9)).grid(row=2,column=0,columnspan=6,sticky='w')
    decode = ttk.LabelFrame(advanced,text='解码与点云选项',style='Card.TLabelframe',padding=12)
    decode.pack(fill='x',pady=(0,12))
    ttk.Checkbutton(decode,text='CUDA视频解码',variable=app.cuda_decode).pack(side='left')
    ttk.Checkbutton(decode,text='FastMap点云取色',variable=app.color).pack(side='left',padx=(32,0))
    environment = ttk.LabelFrame(advanced,text='Python环境',style='Card.TLabelframe',padding=12)
    environment.pack(fill='x',pady=(0,12))
    environment.columnconfigure(1,weight=1)
    ttk.Label(environment,text='FastMap Python').grid(row=0,column=0,sticky='w',padx=(0,14))
    ttk.Entry(environment,textvariable=app.python).grid(row=0,column=1,sticky='ew')
    ttk.Button(environment,text='选择…',command=app.browse_python).grid(row=0,column=2,padx=(10,0))
    ttk.Label(environment,textvariable=app.environment,style='Muted.TLabel',wraplength=900).grid(row=1,column=0,columnspan=3,sticky='w',pady=(12,0))
    ttk.Label(environment,textvariable=app.backend_note,style='Muted.TLabel',wraplength=900).grid(row=2,column=0,columnspan=3,sticky='w',pady=(8,0))
    footer = ttk.Frame(advanced)
    footer.pack(fill='x')
    ttk.Button(footer,text='重置为默认参数',command=app.reset_defaults).pack(side='right')
    from log_settings import show_log_settings
    ttk.Button(footer,text='日志管理…',command=lambda:show_log_settings(app)).pack(side='left')
    ttk.Label(footer,text='参数自动保存 · 本地运行环境已整合',style='Muted.TLabel').pack(side='left',padx=(12,0))
