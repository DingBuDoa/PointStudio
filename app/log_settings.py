"""Small on-demand retention dialog, kept out of the task controls."""
import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from paths import CONFIG, LOGS, OUTPUTS
from log_management import RETENTION_CHOICES, expired_logs, delete_expired


def log_roots(app):
    return [str(OUTPUTS), app.output.get(), *app.project_library.data.get('roots', [])]


def automatic_cleanup(app):
    days = app.config_data.get('log_retention_days', 0)
    if app.running or days not in (7, 30, 90):
        return
    roots = log_roots(app)
    def run():
        try:
            candidates = expired_logs(LOGS, roots, days)
            app.events.put({'type':'log_cleanup','candidates':candidates,'days':days})
        except OSError as error:
            app.events.put({'type':'log','text':f'[日志清理] {error}\n'})
    threading.Thread(target=run,daemon=True).start()


def show_log_settings(app):
    window = tk.Toplevel(app)
    window.title('日志管理')
    window.geometry('620x350')
    window.transient(app)
    body = ttk.Frame(window,padding=22)
    body.pack(fill='both',expand=True)
    ttk.Label(body,text='日志保留策略',font=('Microsoft YaHei UI',13,'bold')).pack(anchor='w')
    days = app.config_data.get('log_retention_days',0)
    current = next((name for name,value in RETENTION_CHOICES.items() if value == days),next(iter(RETENTION_CHOICES)))
    choice = tk.StringVar(value=current)
    ttk.Combobox(body,textvariable=choice,values=list(RETENTION_CHOICES),state='readonly',width=32).pack(anchor='w',pady=(14,10))
    ttk.Label(body,text='按日志最后修改时间计算。选择天数后，在启动时自动清理。\n默认保留全部；程序关闭期间不会运行清理。\n仅清理程序和已登记项目的诊断日志，不删除视频、点云、Blender 文件或项目记录。\n汇总日志每 8 MiB 分卷；“保留全部”时旧分卷也保留。',wraplength=530,justify='left').pack(anchor='w')
    status = tk.StringVar(value='任务运行时暂停清理。手动清理前会显示数量和大小。')
    ttk.Label(body,textvariable=status,wraplength=530).pack(anchor='w',pady=(12,6))
    def save():
        data = dict(app.config_data)
        data['log_retention_days'] = RETENTION_CHOICES[choice.get()]
        # Preserve all current saved preferences, including Blender paths.
        path = CONFIG/'settings.json'
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            saved = {}
        saved['log_retention_days'] = data['log_retention_days']
        path.write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')
        app.config_data['log_retention_days'] = data['log_retention_days']
        status.set('已保存。下次启动时按此规则自动清理；也可立即清理。')
    def clean():
        if app.running:
            status.set('任务运行中，请在任务结束后清理。')
            return
        days = RETENTION_CHOICES[choice.get()]
        if not days:
            status.set('当前选择保留全部，没有待删除日志。')
            return
        candidates = expired_logs(LOGS,log_roots(app),days)
        total = sum(item['bytes'] for item in candidates)
        if not candidates:
            status.set(f'没有超过 {days} 天的可清理日志。')
            return
        if not messagebox.askyesno('清理过期日志',f'删除 {len(candidates)} 个超过 {days} 天的日志，共 {total/2**20:.2f} MiB？\n只删除诊断日志，删除后不可恢复。',parent=window):
            return
        result = delete_expired(candidates,days)
        status.set(f"已清理 {result['removed']} 个日志，释放 {result['bytes']/2**20:.2f} MiB；跳过 {result['skipped']} 个。")
    buttons = ttk.Frame(body)
    buttons.pack(fill='x',side='bottom')
    ttk.Button(buttons,text='立即清理过期日志…',command=clean).pack(side='left')
    ttk.Button(buttons,text='保存策略',command=save).pack(side='right')
