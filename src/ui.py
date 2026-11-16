"""tkinter 界面：下载页 + 设置页。所有 jmcomic 调用都在后台线程，UI 只消费事件队列。"""
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import settings
import logbook
from core import DownloadWorker, query_details, parse_jobs

POLL_MS = 100

# 优先使用系统里实际存在的雅黑字族
FONT_CANDIDATES = ('Microsoft YaHei UI', 'Microsoft YaHei', '微软雅黑')
NAMED_FONTS = ('TkDefaultFont', 'TkTextFont', 'TkHeadingFont',
               'TkMenuFont', 'TkCaptionFont', 'TkSmallCaptionFont',
               'TkIconFont', 'TkTooltipFont', 'TkFixedFont')

MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 24


def resolve_font_family(root) -> str:
    families = set(tkfont.families(root))
    for name in FONT_CANDIDATES:
        if name in families:
            return name
    return 'TkDefaultFont'


def apply_fonts(root, size):
    """把所有内置字体改成微软雅黑，返回基准字体对象。"""
    family = resolve_font_family(root)
    for name in NAMED_FONTS:
        try:
            tkfont.nametofont(name, root=root).configure(family=family, size=size)
        except tk.TclError:
            pass
    return tkfont.Font(root=root, family=family, size=size)


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title('JM 下载器')
        self.minsize(900, 620)

        self.cfg = settings.load()
        self._font_size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(self.cfg.font_size)))

        self._build_style()
        self._apply_theme_fonts()
        self.geometry(self._default_geometry())

        self.event_queue: 'queue.Queue' = queue.Queue()
        self.worker = None
        self.jobs = []
        self.querying = False
        self._last_save_path = self.cfg.save_dir

        self._build_layout()
        self._load_history_into_panel()
        self.after(POLL_MS, self._poll_events)

    # ---------------- 字体 / 缩放 ----------------

    def _default_geometry(self):
        """按字体大小估算窗口尺寸，字号变大时窗口同步变大，避免控件被挤扁。"""
        scale = self._font_size / 10.0
        width = int(min(1180, 980 * max(1.0, scale)))
        height = int(min(900, 680 * max(1.0, scale)))
        return f'{width}x{height}'

    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        self.style = style
        # 用系统真实 DPI 设置 Tk 缩放（main.py 已探测），避免文字过小/被系统二次放大而发虚
        try:
            dpi = int(os.environ.get('JM_SYSTEM_DPI', '0')) or self.winfo_fpixels('1i')
        except (TypeError, ValueError):
            dpi = 96
        self.tk.call('tk', 'scaling', dpi / 72.0)

    def _apply_theme_fonts(self):
        self.base_font = apply_fonts(self, self._font_size)
        family = self.base_font.actual('family')
        size = self._font_size
        row_height = int(self.base_font.metrics('linespace') * 1.6)

        self.style.configure('Treeview', rowheight=row_height, font=(family, size))
        self.style.configure('Treeview.Heading', font=(family, size, 'bold'))
        self.style.configure('TNotebook.Tab', font=(family, size))
        self.style.configure('TLabelframe.Label', font=(family, size, 'bold'))
        for widget_style in ('TButton', 'TLabel', 'TCheckbutton', 'TRadiobutton',
                             'TEntry', 'TCombobox', 'TSpinbox', 'Toolbutton'):
            try:
                self.style.configure(widget_style, font=(family, size))
            except tk.TclError:
                pass
        self._apply_widget_fonts()

    def _change_font_size(self, delta):
        self._set_font_size(self._font_size + delta)

    def _set_font_size(self, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return self.font_size_var.set(self._font_size)
        value = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, value))
        if value == self._font_size:
            return
        self._font_size = value
        self.font_size_var.set(value)
        self._apply_theme_fonts()
        self.cfg.font_size = value
        settings.save(self.cfg)

    def _apply_widget_fonts(self):
        """Text、表格列宽这类不跟 ttk 样式走的控件，手动刷新。"""
        if hasattr(self, 'log_font'):
            family = self.base_font.actual('family')
            self.log_font.configure(family=family, size=max(MIN_FONT_SIZE, self._font_size - 1))
        if hasattr(self, 'tree'):
            self._set_tree_columns()
        self.geometry(self._default_geometry())

    def _bind_font_shortcuts(self):
        self.bind_all('<Control-plus>', lambda _e: self._change_font_size(1))
        self.bind_all('<Control-equal>', lambda _e: self._change_font_size(1))
        self.bind_all('<Control-KP_Add>', lambda _e: self._change_font_size(1))
        self.bind_all('<Control-minus>', lambda _e: self._change_font_size(-1))
        self.bind_all('<Control-KP_Subtract>', lambda _e: self._change_font_size(-1))
        self.bind_all('<Control-0>', lambda _e: self._set_font_size(10))

        def on_wheel(event):
            if event.state & 0x0004:  # Ctrl 按下
                self._change_font_size(1 if event.delta > 0 else -1)
                return 'break'

        self.bind_all('<MouseWheel>', on_wheel)

    def _build_layout(self):
        self._bind_font_shortcuts()
        root = ttk.Frame(self, padding=12)
        root.pack(fill='both', expand=True)

        notebook = ttk.Notebook(root)
        notebook.pack(fill='both', expand=True)
        self.download_tab = ttk.Frame(notebook, padding=12)
        self.history_tab = ttk.Frame(notebook, padding=12)
        self.settings_tab = ttk.Frame(notebook, padding=12)
        notebook.add(self.download_tab, text='  下载  ')
        notebook.add(self.history_tab, text='  下载记录  ')
        notebook.add(self.settings_tab, text='  设置  ')
        self._build_download_tab()
        self._build_history_tab()
        self._build_settings_tab()

    def _build_download_tab(self):
        tab = self.download_tab

        # 车号输入
        input_frame = ttk.LabelFrame(tab, text='车号 / 链接（多个用空格或逗号分隔，p 前缀表示只下该章节）', padding=8)
        input_frame.pack(fill='x')

        self.id_entry = ttk.Entry(input_frame)
        self.id_entry.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.id_entry.bind('<Return>', lambda _e: self.on_query())
        self.id_entry.insert(0, '350234')

        ttk.Button(input_frame, text='查询详情', command=self.on_query).pack(side='left', padx=2)
        ttk.Button(input_frame, text='开始下载',
                   command=self.on_download).pack(side='left', padx=2)
        ttk.Button(input_frame, text='中止全部', command=self.on_cancel).pack(side='left', padx=2)
        ttk.Button(input_frame, text='清空', command=self.on_clear).pack(side='left', padx=2)

        # 任务表
        table_frame = ttk.LabelFrame(tab, text='任务列表（可选中某项后单独中止）', padding=8)
        table_frame.pack(fill='both', expand=True, pady=(10, 0))

        columns = ('kind', 'jm_id', 'title', 'chapters', 'pages', 'status')
        self.tree = ttk.Treeview(table_frame, columns=columns, show='headings', height=7)
        self._set_tree_columns()
        self.tree.pack(side='left', fill='both', expand=True)

        scroll = ttk.Scrollbar(table_frame, orient='vertical', command=self.tree.yview)
        scroll.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.tag_configure('running', background='#eef6ff')
        self.tree.tag_configure('done', background='#eefbf1')
        self.tree.tag_configure('failed', background='#fdeeee')
        self.tree.tag_configure('skipped', background='#f3f4f6')

        # 进度
        progress_frame = ttk.Frame(tab)
        progress_frame.pack(fill='x', pady=(10, 0))
        self.progress = ttk.Progressbar(progress_frame, mode='determinate', maximum=100)
        self.progress.pack(side='left', fill='x', expand=True)
        self.progress_label = ttk.Label(progress_frame, text='就绪', width=34, anchor='e')
        self.progress_label.pack(side='left', padx=(8, 0))

        # 操作行
        action_frame = ttk.Frame(tab)
        action_frame.pack(fill='x', pady=(8, 0))
        ttk.Button(action_frame, text='打开下载目录', command=self.on_open_dir).pack(side='left')
        ttk.Button(action_frame, text='中止选中任务',
                   command=self.on_abort_selected).pack(side='left', padx=(8, 0))
        ttk.Button(action_frame, text='查看日志文件',
                   command=self.on_open_log).pack(side='left', padx=(8, 0))
        self.dir_label = ttk.Label(action_frame, text=self.cfg.save_dir, foreground='#666')
        self.dir_label.pack(side='left', padx=10, fill='x', expand=True)

        # 日志
        log_frame = ttk.LabelFrame(tab, text='运行日志', padding=6)
        log_frame.pack(fill='both', expand=True, pady=(10, 0))
        self.log_font = tkfont.Font(family=self.base_font.actual('family'),
                                    size=max(MIN_FONT_SIZE, self._font_size - 1))
        self.log_text = ScrolledText(log_frame, height=9, wrap='word', state='disabled',
                                     font=self.log_font, borderwidth=0, relief='flat')
        self.log_text.pack(fill='both', expand=True)

    def _build_history_tab(self):
        tab = self.history_tab

        toolbar = ttk.Frame(tab)
        toolbar.pack(fill='x')
        ttk.Label(toolbar, text='历史下载记录（新→旧，最多 500 条）').pack(side='left')
        ttk.Button(toolbar, text='刷新', command=self._load_history_into_panel).pack(side='right')
        ttk.Button(toolbar, text='清空记录', command=self.on_clear_history).pack(side='right', padx=6)

        frame = ttk.Frame(tab)
        frame.pack(fill='both', expand=True, pady=(8, 0))

        columns = ('time', 'kind', 'jm_id', 'title', 'photos', 'pages', 'result', 'duration')
        self.history_tree = ttk.Treeview(frame, columns=columns, show='headings', height=14)
        scale = self._font_size / 10.0
        headings = {
            'time': ('时间', 150), 'kind': ('类型', 60), 'jm_id': ('车号', 90),
            'title': ('标题', 300), 'photos': ('章节', 60), 'pages': ('图片', 60),
            'result': ('结果', 90), 'duration': ('耗时', 80),
        }
        for key, (text, width) in headings.items():
            self.history_tree.heading(key, text=text)
            self.history_tree.column(key, width=int(width * max(1.0, scale)),
                                     anchor='center' if key != 'title' else 'w')
        self.history_tree.pack(side='left', fill='both', expand=True)

        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.history_tree.yview)
        scroll.pack(side='right', fill='y')
        self.history_tree.configure(yscrollcommand=scroll.set)
        self.history_tree.tag_configure('ok', background='#eefbf1')
        self.history_tree.tag_configure('bad', background='#fdeeee')

        self.history_tree.bind('<Double-1>', self._open_history_item)

    def _load_history_into_panel(self):
        if not hasattr(self, 'history_tree'):
            return
        self.history_tree.delete(*self.history_tree.get_children())
        for record in logbook.load_history():
            values = (
                record.get('time', ''), record.get('kind', ''), record.get('jm_id', ''),
                record.get('title', ''), record.get('photos', ''), record.get('pages', ''),
                record.get('result', ''), record.get('duration', ''),
            )
            tag = 'ok' if record.get('result') == '完成' else 'bad'
            self.history_tree.insert('', 'end', values=values, tags=(tag,))

    def _open_history_item(self, _event):
        sel = self.history_tree.selection()
        if not sel:
            return
        jm_id = self.history_tree.item(sel[0], 'values')[2]
        self.id_entry.delete(0, 'end')
        self.id_entry.insert(0, str(jm_id))

    def on_clear_history(self):
        if not messagebox.askyesno('清空记录', '确定清空所有下载记录？（不影响已下载文件）'):
            return
        logbook.clear_history()
        self._load_history_into_panel()
        self.log('下载记录已清空')

    def on_open_log(self):
        path = logbook.log_path()
        if not os.path.exists(path):
            messagebox.showinfo('日志', '今天还没有产生日志')
            return
        try:
            if sys.platform.startswith('win'):
                os.startfile(path)  # noqa: S606
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', path])
            else:
                subprocess.Popen(['xdg-open', path])
        except Exception as e:
            messagebox.showerror('打开失败', str(e))

    def _set_tree_columns(self):
        """列宽随字号缩放，字号变大时不会把标题挤成一格。"""
        scale = self._font_size / 10.0
        headings = {
            'kind': ('类型', 70), 'jm_id': ('车号', 100), 'title': ('标题', 320),
            'chapters': ('章节数', 80), 'pages': ('页数', 80), 'status': ('状态', 200),
        }
        for key, (text, width) in headings.items():
            self.tree.heading(key, text=text)
            self.tree.column(key, width=int(width * max(1.0, scale)),
                             anchor='w' if key == 'title' else 'center')

    def _build_settings_tab(self):
        tab = self.settings_tab
        tab.columnconfigure(1, weight=1)

        def add_row(row, label, widget, hint=''):
            ttk.Label(tab, text=label).grid(row=row, column=0, sticky='w', pady=4)
            widget.grid(row=row, column=1, sticky='ew', pady=4, padx=8)
            if hint:
                ttk.Label(tab, text=hint, foreground='#888').grid(row=row, column=2, sticky='w')
            return widget

        # 保存目录
        dir_frame = ttk.Frame(tab)
        self.save_dir_var = tk.StringVar(value=self.cfg.save_dir)
        ttk.Entry(dir_frame, textvariable=self.save_dir_var).pack(side='left', fill='x', expand=True)
        ttk.Button(dir_frame, text='浏览…', command=self._pick_dir).pack(side='left', padx=4)
        add_row(0, '保存目录', dir_frame)

        # 目录规则
        self.rule_var = tk.StringVar(value=self.cfg.dir_rule)
        add_row(1, '目录规则', ttk.Entry(tab, textvariable=self.rule_var),
                '默认: 车号-标题/images/章节名')

        # 客户端
        self.impl_var = tk.StringVar(value=self.cfg.impl)
        impl_box = ttk.Combobox(tab, textvariable=self.impl_var, state='readonly',
                                values=('api', 'html'))
        add_row(2, '客户端', impl_box, 'api=APP端(推荐) / html=网页端')

        # 图片格式
        self.suffix_var = tk.StringVar(value=self.cfg.image_suffix or '原图')
        suffix_box = ttk.Combobox(tab, textvariable=self.suffix_var, state='readonly',
                                  values=('原图', '.jpg', '.png'))
        add_row(3, '图片格式', suffix_box, '原图通常为 .webp')

        # 并发
        thread_frame = ttk.Frame(tab)
        self.image_threads_var = tk.IntVar(value=self.cfg.image_threads)
        self.photo_threads_var = tk.IntVar(value=self.cfg.photo_threads)
        ttk.Label(thread_frame, text='图片').pack(side='left')
        ttk.Spinbox(thread_frame, from_=1, to=64, width=6,
                    textvariable=self.image_threads_var).pack(side='left', padx=(4, 12))
        ttk.Label(thread_frame, text='章节').pack(side='left')
        ttk.Spinbox(thread_frame, from_=1, to=32, width=6,
                    textvariable=self.photo_threads_var).pack(side='left', padx=4)
        add_row(4, '并发数', thread_frame, '过大对服务器压力大')

        # 超时与重试
        net_frame = ttk.Frame(tab)
        self.timeout_var = tk.IntVar(value=getattr(self.cfg, 'timeout', 8))
        self.retry_var = tk.IntVar(value=getattr(self.cfg, 'retry_times', 2))
        ttk.Label(net_frame, text='超时(秒)').pack(side='left')
        ttk.Spinbox(net_frame, from_=3, to=60, width=6,
                    textvariable=self.timeout_var).pack(side='left', padx=(4, 12))
        ttk.Label(net_frame, text='重试').pack(side='left')
        ttk.Spinbox(net_frame, from_=0, to=10, width=6,
                    textvariable=self.retry_var).pack(side='left', padx=4)
        add_row(5, '请求策略', net_frame, '启动会自动测速优选域名')

        # 代理
        proxy_frame = ttk.Frame(tab)
        self.proxy_mode_var = tk.StringVar(value=self.cfg.proxy_mode)
        for text, value in (('系统代理', 'system'), ('不使用', 'none'), ('自定义', 'custom')):
            ttk.Radiobutton(proxy_frame, text=text, value=value,
                            variable=self.proxy_mode_var).pack(side='left', padx=(0, 10))
        self.proxy_value_var = tk.StringVar(value=self.cfg.proxy_value)
        ttk.Entry(proxy_frame, textvariable=self.proxy_value_var, width=22).pack(side='left')
        add_row(6, '代理', proxy_frame, '自定义格式: 127.0.0.1:7890')

        # 账号
        self.username_var = tk.StringVar(value=self.cfg.username)
        self.password_var = tk.StringVar(value=self.cfg.password)
        add_row(7, '账号', ttk.Entry(tab, textvariable=self.username_var), '可选，敏感题材/收藏夹需要')
        add_row(8, '密码', ttk.Entry(tab, textvariable=self.password_var),
                '明文显示并保存到 config.json')

        # 导出
        export_frame = ttk.Frame(tab)
        self.export_pdf_var = tk.BooleanVar(value=self.cfg.export_pdf)
        self.export_zip_var = tk.BooleanVar(value=self.cfg.export_zip)
        self.export_long_img_var = tk.BooleanVar(value=self.cfg.export_long_img)
        ttk.Checkbutton(export_frame, text='合并 PDF', variable=self.export_pdf_var).pack(side='left', padx=(0, 12))
        ttk.Checkbutton(export_frame, text='打包 ZIP', variable=self.export_zip_var).pack(side='left', padx=(0, 12))
        ttk.Checkbutton(export_frame, text='长图', variable=self.export_long_img_var).pack(side='left')
        add_row(9, '下载后导出', export_frame, '整本合并，存于保存目录')

        # 界面字号
        font_frame = ttk.Frame(tab)
        self.font_size_var = tk.IntVar(value=self._font_size)
        ttk.Button(font_frame, text='A-', width=3,
                   command=lambda: self._change_font_size(-1)).pack(side='left')
        self.font_spin = ttk.Spinbox(font_frame, from_=MIN_FONT_SIZE, to=MAX_FONT_SIZE,
                                     width=5, textvariable=self.font_size_var,
                                     command=lambda: self._set_font_size(self.font_size_var.get()))
        self.font_spin.pack(side='left', padx=6)
        self.font_spin.bind('<Return>', lambda _e: self._set_font_size(self.font_size_var.get()))
        self.font_spin.bind('<FocusOut>', lambda _e: self._set_font_size(self.font_size_var.get()))
        ttk.Button(font_frame, text='A+', width=3,
                   command=lambda: self._change_font_size(1)).pack(side='left')
        add_row(10, '界面字号', font_frame, '也可用 Ctrl+滚轮 / Ctrl+加号')

        # 按钮
        button_frame = ttk.Frame(tab)
        button_frame.grid(row=11, column=0, columnspan=3, sticky='w', pady=(16, 0))
        ttk.Button(button_frame, text='保存设置', command=self.on_save_settings).pack(side='left')
        ttk.Button(button_frame, text='恢复默认', command=self.on_reset_settings).pack(side='left', padx=8)

        ttk.Label(tab, text='设置保存在 jm-downloader/config.json，下次启动自动加载。',
                  foreground='#888').grid(row=12, column=0, columnspan=3, sticky='w', pady=(12, 0))

    # ---------------- 设置交互 ----------------

    def _pick_dir(self):
        path = filedialog.askdirectory(initialdir=self.save_dir_var.get() or os.getcwd())
        if path:
            self.save_dir_var.set(os.path.normpath(path))

    def _collect_settings(self):
        return {
            'save_dir': os.path.normpath(self.save_dir_var.get().strip() or settings.DEFAULT_SAVE_DIR),
            'impl': self.impl_var.get().strip() or 'api',
            'dir_rule': self.rule_var.get().strip() or 'Bd / {Aid}-{Atitle} / images / Ptitle',
            'image_suffix': '' if self.suffix_var.get() == '原图' else self.suffix_var.get(),
            'image_threads': max(1, int(self.image_threads_var.get())),
            'photo_threads': max(1, int(self.photo_threads_var.get())),
            'timeout': max(3, int(self.timeout_var.get())),
            'retry_times': max(0, int(self.retry_var.get())),
            'proxy_mode': self.proxy_mode_var.get(),
            'proxy_value': self.proxy_value_var.get().strip(),
            'username': self.username_var.get().strip(),
            'password': self.password_var.get(),
            'export_pdf': self.export_pdf_var.get(),
            'export_zip': self.export_zip_var.get(),
            'export_long_img': self.export_long_img_var.get(),
            'font_size': self._font_size,
        }

    def on_save_settings(self):
        try:
            cfg = settings.Settings(**self._collect_settings())
        except Exception as e:
            messagebox.showerror('设置错误', str(e))
            return
        try:
            os.makedirs(cfg.save_dir, exist_ok=True)
        except Exception as e:
            messagebox.showerror('保存目录不可用', str(e))
            return
        settings.save(cfg)
        self.cfg = cfg
        self.dir_label.configure(text=cfg.save_dir)
        self.log('设置已保存')

    def on_reset_settings(self):
        if not messagebox.askyesno('恢复默认', '确定把所有设置恢复为默认值？'):
            return
        cfg = settings.Settings()
        self.save_dir_var.set(cfg.save_dir)
        self.rule_var.set(cfg.dir_rule)
        self.impl_var.set(cfg.impl)
        self.suffix_var.set('原图')
        self.image_threads_var.set(cfg.image_threads)
        self.photo_threads_var.set(cfg.photo_threads)
        self.timeout_var.set(cfg.timeout)
        self.retry_var.set(cfg.retry_times)
        self.proxy_mode_var.set(cfg.proxy_mode)
        self.proxy_value_var.set(cfg.proxy_value)
        self.username_var.set('')
        self.password_var.set('')
        self.export_pdf_var.set(False)
        self.export_zip_var.set(False)
        self.export_long_img_var.set(False)
        self._set_font_size(cfg.font_size)

    # ---------------- 下载交互 ----------------

    def _parse_and_sync(self):
        try:
            jobs = parse_jobs(self.id_entry.get())
        except ValueError as e:
            messagebox.showwarning('车号无效', str(e))
            return None
        if not jobs:
            messagebox.showwarning('提示', '请先输入至少一个车号')
            return None
        self.jobs = jobs
        self._sync_tree(jobs)
        return jobs

    def _sync_tree(self, jobs):
        self.tree.delete(*self.tree.get_children())
        for index, job in enumerate(jobs):
            values = ('本子' if job.kind == 'album' else '章节', job.jm_id, '', '', '', '待处理')
            if index < len(self.tree.get_children()):
                iid = self.tree.get_children()[index]
                self.tree.item(iid, values=values, tags=())
            else:
                self.tree.insert('', 'end', iid=str(index), values=values)

    def _row(self, index):
        iids = self.tree.get_children()
        if 0 <= index < len(iids):
            return iids[index]
        return None

    def _set_status(self, index, status, tag=None):
        iid = self._row(index)
        if iid is None:
            return
        values = list(self.tree.item(iid, 'values'))
        values[5] = status
        self.tree.item(iid, values=values, tags=(tag,) if tag else ())

    def on_query(self):
        if self.querying:
            self.log('正在查询中，请稍候…')
            return
        jobs = self._parse_and_sync()
        if not jobs:
            return
        self.querying = True
        self.log(f'开始查询 {len(jobs)} 个任务…')
        query_details(jobs, self._collect_settings(), self.event_queue)

    def on_download(self):
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo('提示', '已有下载任务在进行中')
            return
        jobs = self._parse_and_sync()
        if not jobs:
            return

        cfg = self._collect_settings()
        try:
            os.makedirs(cfg['save_dir'], exist_ok=True)
        except Exception as e:
            messagebox.showerror('保存目录不可用', str(e))
            return
        settings.save(settings.Settings(**cfg))
        self.cfg = settings.Settings(**cfg)

        for index in range(len(jobs)):
            self._set_status(index, '排队中')
        self.progress.configure(value=0)
        self.progress_label.configure(text='准备中…')

        # 日志按下载会话独立成文件
        label = '-'.join(j.jm_id for j in jobs[:3])
        if len(jobs) > 3:
            label += f'-等{len(jobs)}个'
        logbook.start_session(label)

        self.worker = DownloadWorker(jobs, cfg, self.event_queue)
        self.worker.start()
        self.log(f'开始下载 {len(jobs)} 个任务')

    def on_cancel(self):
        if self.worker is None or not self.worker.is_alive():
            self.log('当前没有正在运行的任务')
            return
        self.worker.cancel()
        self.log('已发送中止信号，正在等待在途请求返回…')
        self.progress_label.configure(text='正在中止…')

    def on_abort_selected(self):
        """中止选中的单个任务，其余任务继续。"""
        if self.worker is None or not self.worker.is_alive():
            messagebox.showinfo('提示', '当前没有正在运行的任务')
            return
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo('提示', '请先在任务列表里选中一项')
            return
        index = int(self.tree.index(selection[0]))
        status = str(self.tree.item(selection[0], 'values')[5])
        if status in ('完成', '失败', '已跳过', '已中止'):
            return
        self.worker.abort_job(index)
        self.log(f'已请求中止任务 {index + 1}')

    def on_clear(self):
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo('提示', '请先取消当前下载')
            return
        self.tree.delete(*self.tree.get_children())
        self.jobs = []
        self.progress.configure(value=0)
        self.progress_label.configure(text='就绪')
        self._clear_log()

    def on_open_dir(self):
        path = self._last_save_path
        if not os.path.isdir(path):
            path = os.path.dirname(path) or os.getcwd()
        try:
            if sys.platform.startswith('win'):
                os.startfile(path)  # noqa: S606
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', path])
            else:
                subprocess.Popen(['xdg-open', path])
        except Exception as e:
            messagebox.showerror('打开失败', str(e))

    # ---------------- 事件轮询 ----------------

    def _poll_events(self):
        try:
            for _ in range(200):
                event = self.event_queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.after(POLL_MS, self._poll_events)

    def _handle_event(self, event):
        kind = event.get('kind')

        if kind == 'log':
            self.log(event['message'])

        elif kind == 'fatal':
            self.log(f'[错误] {event["message"]}')
            messagebox.showerror('错误', event['message'])

        elif kind == 'detail':
            self._apply_detail(event)

        elif kind == 'detail_failed':
            self.log(f'[查询失败] {event["jm_id"]}: {event["message"]}')

        elif kind == 'query_finished':
            self.querying = False
            self.log('查询完成')

        elif kind == 'job_start':
            index = event['index']
            self._set_status(index, '下载中', 'running')
            self.tree.see(self._row(index) or '')
            self.log(f'—— {event["label"]} ——')

        elif kind == 'cancelling':
            self.log('已发出中止信号（等待在途请求返回，可能需数秒）')
            self.progress_label.configure(text='正在中止…')

        elif kind == 'job_aborting':
            self.log(f'正在中止任务 {event["index"] + 1}…')

        elif kind == 'job_aborted':
            self._set_status(event['index'], '已中止', 'skipped')
            self.log(f'[已中止] 任务 {event["index"] + 1}')

        elif kind == 'album_start':
            self.log(f'本子: {event["title"]} | {event["photos"]} 章 / {event["pages"]} 页')
            self.progress.configure(value=0)
            self.progress_label.configure(text=f'0 / {event["pages"]} 页')
            if event.get('save_path'):
                self._last_save_path = event['save_path']

        elif kind == 'progress':
            done, total = event['done'], max(1, event['total'])
            self.progress.configure(value=min(100, done * 100 / total))
            pidx = event.get('photo_index') or 0
            ptotal = event.get('photo_total') or 0
            if ptotal > 1:
                self.progress_label.configure(
                    text=f'章节 {pidx}/{ptotal} · {done}/{total} 页')
            else:
                self.progress_label.configure(text=f'{done}/{total} 页')

        elif kind == 'album_done':
            self.log(f'完成: {event["title"]} → {event.get("save_path", "")}')
            if event.get('save_path'):
                self._last_save_path = event['save_path']
            self.progress.configure(value=100)

        elif kind == 'job_done':
            self._set_status(event['index'], '完成', 'done')

        elif kind == 'job_failed':
            self._set_status(event['index'], '失败', 'failed')
            self.log(f'[失败] 任务 {event["index"] + 1}: {event["message"]}')

        elif kind == 'job_skipped':
            self._set_status(event['index'], '已跳过', 'skipped')
            self.log(f'[跳过] 任务 {event["index"] + 1}')

        elif kind == 'worker_finished':
            self.worker = None
            elapsed = event.get('elapsed')
            spent = f'，耗时 {elapsed:.1f} 秒' if elapsed else ''
            if event.get('cancelled'):
                self.progress_label.configure(text='已取消')
                self.log(f'下载已取消{spent}')
            elif event.get('fatal'):
                self.progress_label.configure(text='已中止')
                self.log(f'下载中止{spent}')
                messagebox.showerror('已中止', event['fatal'])
            else:
                done = sum(1 for iid in self.tree.get_children() if '完成' in self.tree.item(iid, 'values'))
                self.progress_label.configure(text=f'结束：{done} 个任务成功')
                self.log(f'全部结束，成功 {done} 个任务{spent}')
            logbook.end_session()
            self._load_history_into_panel()

    def _apply_detail(self, event):
        jm_id = event['jm_id']
        for iid in self.tree.get_children():
            values = list(self.tree.item(iid, 'values'))
            if values[1] == jm_id:
                values[2] = event.get('title', '')
                values[3] = event.get('chapters', '')
                values[4] = event.get('pages', '')
                if values[5] in ('待处理', ''):
                    values[5] = '已查询'
                self.tree.item(iid, values=values)
                break
        self.log(f'查询成功: {jm_id} | {event.get("title", "")} | '
                 f'{event.get("chapters", 0)} 章 / {event.get("pages", 0)} 页')

    # ---------------- 日志 ----------------

    def log(self, message):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', message + '\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')
        logbook.write(message)

    def _clear_log(self):
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.configure(state='disabled')


def main():
    app = None
    try:
        app = App()
        app.mainloop()
    except ImportError as e:
        print(f'依赖缺失: {e}\n请先执行: pip install jmcomic pyyaml')
        raise SystemExit(1)
    except Exception:
        import traceback
        traceback.print_exc()
        if app is not None:
            messagebox.showerror('启动失败', traceback.format_exc())
        raise SystemExit(1)


if __name__ == '__main__':
    main()
