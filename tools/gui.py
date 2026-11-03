"""mmd2max 图形界面：调参 -> 立刻看结果 -> 一键出 .max。

为什么值得有个 GUI：管线的旋钮（骨架模式 / min_weight / 附属骨策略 / scale）对结果的
影响，以前只能「改命令行 -> 跑一遍 -> 读日志」，而真正的验收要开 3ds Max，一次几分钟。
这里把 `build_plan()`（纯计算、不写盘）搬到面板上：动一下控件，右侧数字立刻重算；
只有按「导出」才落盘，只有按「跑 3ds Max」才启动 Max（且执行前会二次确认）。

用法：python tools/gui.py [model.pmx]
依赖：只用标准库 tkinter（本机没装 PySide6 / PyQt）。
"""
from __future__ import annotations

import contextlib
import io
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mmd2max import export_obj                      # noqa: E402
from mmd2max.coords import convert_vec              # noqa: E402
from mmd2max.pmx import PMXError, load_pmx          # noqa: E402

DEBOUNCE_MS = 200
TOOLS = Path(__file__).resolve().parent
OBJ_RUN = TOOLS / "obj_run.py"
MODES = ("useful", "full", "humanoid")
MODE_HINT = {
    "useful": "剔除 IK / 物理哑骨 / 表情骨，衣服头发裙摆作附属骨保留（推荐）",
    "full": "全部骨骼都进骨架，最忠实 MMD，但 Skin 骨骼数最多",
    "humanoid": "只留标准人形骨，附属骨权重归并到最近祖先（骨架最干净，丢细节）",
}
ACCESSORY_HINT = {
    "nearest": "附属骨链根挂到几何最近的 Biped 节点（裙摆贴腰线，视觉最合身）",
    "hierarchy": "跟随 MMD 层级（动画归属与原作者一致，但可能浮在半空）",
}


def plan_rows(plan) -> list:
    """Plan -> [(分组, 指标, 数值, 显示文本)]；「与基准的差」由界面按数值列算。"""
    m = plan.model
    rows = [
        ("网格", "顶点", len(plan.positions), f"{len(plan.positions):,}"),
        # Max 的 OBJ 导入器会丢「不被任何面引用」的顶点，导出侧先替它丢掉并重编号；
        # 这一行不是 0 就说明模型里真有这种顶点，权重表才不会整体错位。
        ("网格", "丢弃未引用顶点", plan.dropped_verts, str(plan.dropped_verts)),
        ("网格", "三角面", len(plan.faces), f"{len(plan.faces):,}"),
        ("网格", "材质", len(m.materials), str(len(m.materials))),
        ("骨骼", "原骨骼", len(m.bones), str(len(m.bones))),
        ("骨骼", "保留进骨架", len(plan.kept), str(len(plan.kept))),
        ("骨骼", "加入蒙皮", plan.skin_count, str(plan.skin_count)),
        ("骨骼", "剔除 control", plan.n_control, str(plan.n_control)),
        ("骨骼", "剔除 morph", plan.n_morph, str(plan.n_morph)),
        ("Biped", "命中节点", len(plan.biped_map_lines) - 1,
         str(len(plan.biped_map_lines) - 1)),
        ("Biped", "缺失必需骨", len(plan.biped_missing), str(len(plan.biped_missing))),
        ("Biped", "语义冲突", len(plan.biped_conflicts), str(len(plan.biped_conflicts))),
        ("Biped", "脊柱节数 spine_links", plan.spine_links, str(plan.spine_links)),
        ("Biped", "手指 fingers", plan.fingers, str(plan.fingers)),
        ("Biped", "指节 finger_links", plan.finger_links, str(plan.finger_links)),
        ("Biped", "肢段表行数", plan.limb_count, str(plan.limb_count)),
        ("Biped", "手部粗细行数", plan.width_count, str(plan.width_count)),
        ("Biped", "Neck 修正", round(plan.neck_shift, 2), f"{plan.neck_shift:.2f} cm"),
        ("附属骨", "改挂链根", plan.reparented, str(plan.reparented)),
        ("表情", "Morpher 通道", plan.morph_count,
         f"{plan.morph_count} / {export_obj.MAX_MORPH_CHANNELS}"),
        ("表情", "顶点变形总数", plan.vertex_morphs, str(plan.vertex_morphs)),
        ("物理", "刚体", len(m.rigid_bodies), str(len(m.rigid_bodies))),
        ("物理", "关节", len(m.joints), str(len(m.joints))),
    ]
    return rows


# ---------------------------------------------------------------------------
# 预览：正/顶/侧三视图。手性（det=-1 的镜像）以前只能开 Max 用肉眼验，
# 这里在 Python 侧就能看出「正视图有没有脸、左腕是不是在屏幕右边」。
# ---------------------------------------------------------------------------

VIEWS = ("正视", "顶视", "侧视")
KIND_COLORS = {
    "biped": "#2e8b57",     # 命中 Biped 节点
    "reparent": "#d2691e",  # 附属骨链根且被改挂
    "accessory": "#4a7fb5",  # 其余保留骨（衣服/头发/裙摆）
    "dropped": "#9a9a9a",   # 被剔除的骨（IK / 哑骨 / 表情骨）
}


def project(point, view: str):
    """Max 世界坐标（Z-up）-> 视图平面坐标（u 右、v 上）。

    Max 的 Front 视图从 -Y 往 +Y 看：屏幕右 = +X、上 = +Z；
    Top 视图屏幕下 = +Y；Right 视图屏幕右 = +Y（角色背后方向）。
    """
    x, y, z = point
    if view == "正视":
        return (x, z)
    if view == "顶视":
        return (x, -y)
    return (y, z)


def build_preview(plan, mesh_limit: int = 1500) -> dict:
    """Plan -> 画布几何（Max 世界坐标）。

    坐标换算不便宜（2.5 万顶点），放在计算线程里做，界面线程只投影和画线。
    mesh 只按步长抽稀：预览要看的是轮廓与骨骼走向，不是完整网格。
    """
    scale = plan.scale
    # heads/tails 已经是 Max 世界坐标（bones.tsv 的 hx..tz 就取自它们），不能再换算一次
    heads = list(plan.heads)
    tails = list(plan.tails)
    biped_new = {plan.old_to_new[old] for old, target in plan.biped_mapping.items()
                 if target and old in plan.old_to_new}
    kinds = []
    for i in range(len(heads)):
        if plan.max_parent_col[i] >= 0:
            kinds.append("reparent")
        elif i in biped_new:
            kinds.append("biped")
        else:
            kinds.append("accessory")
    dropped = [convert_vec(bone.position, scale)
               for i, bone in enumerate(plan.model.bones) if i not in plan.old_to_new]
    verts = [convert_vec(p, scale) for p in plan.positions]
    step = max(1, len(plan.faces) // mesh_limit)
    mesh = []
    for fi in range(0, len(plan.faces), step):
        a, b, c = plan.faces[fi]
        pa, pb, pc = verts[a], verts[b], verts[c]
        mesh.extend(((pa, pb), (pb, pc), (pc, pa)))
    # 身高/厚度用全部顶点算（抽稀后的网格可能恰好漏掉极值），供画布角落自检
    zs = [p[2] for p in verts]
    ys = [p[1] for p in verts]
    return {"heads": heads, "tails": tails, "kinds": kinds, "dropped": dropped,
            "mesh": mesh, "max_parent": plan.max_parent_col,
            "body_height": (max(zs) - min(zs)) if verts else 0.0,
            "body_depth": (max(ys) - min(ys)) if verts else 0.0}


class App(tk.Tk):
    def __init__(self, pmx_path: Path | None = None):
        super().__init__()
        self.title("mmd2max · PMX -> 3ds Max 装配台")
        self.geometry("1180x760")

        self.busy = False
        self.token = 0                 # 计算任务序号：只渲染最新一次结果
        self.results = queue.Queue()
        self.debounce = None
        self.model_cache = None        # (路径, mtime, PMXModel)
        self.base_rows = None          # 「基准」：手动钉住的指标值
        self.shown_rows = {}           # 上一次计算显示的指标（没钉基准时的对照）
        self.last_plan = None
        self.preview = None            # build_preview() 的结果（Max 世界坐标）
        self.redraw_job = None
        self.output_q = queue.Queue()  # 导出线程 / 子进程吐出来的日志行
        self.done_q = queue.Queue()    # (退出码, 一句话总结)
        self.job_title = ""            # 非空 = 有动作在跑，按钮锁住
        self.job_done = None           # 完成时生成状态行的回调
        self.main_ident = threading.get_ident()   # 界面线程 id：日志走哪条路看它

        self._vars()
        self._build_ui()
        if pmx_path:
            self.path_var.set(str(pmx_path))
        self.after(60, self._poll)
        if pmx_path and pmx_path.is_file():
            self._kick()

    # ---------------------------------------------------------------- 参数变量
    def _vars(self):
        self.path_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="useful")
        self.accessory_var = tk.StringVar(value="nearest")
        self.min_weight_var = tk.StringVar(value="0.0")
        self.height_var = tk.StringVar(value="160")
        self.auto_scale_var = tk.BooleanVar(value=True)
        self.scale_var = tk.StringVar(value="8.0")
        self.outdir_var = tk.StringVar()
        self.view_var = tk.StringVar(value="正视")
        self.show_mesh_var = tk.BooleanVar(value=True)
        self.show_dropped_var = tk.BooleanVar(value=True)
        self.show_link_var = tk.BooleanVar(value=True)
        self.physics_var = tk.BooleanVar(value=False)
        self.shot_var = tk.BooleanVar(value=True)
        self.noskin_var = tk.BooleanVar(value=False)

    # ------------------------------------------------------------------ 界面
    def _build_ui(self):
        top = ttk.Frame(self, padding=(10, 8, 10, 0))
        top.pack(fill="x")
        ttk.Label(top, text="PMX 文件").pack(side="left")
        ttk.Entry(top, textvariable=self.path_var).pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="浏览…", command=self._browse).pack(side="left")

        # 底部两条横条先 pack(side="bottom")，再放会伸缩的 body：
        # pack 是按申请顺序切分空间的，先占住才能保证窗口缩小时
        # 被裁掉的是中间区域，而不是「③ 跑 3ds Max」这颗按钮。
        bottom = ttk.Frame(self, padding=(10, 0, 10, 8))
        bottom.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar(value="选择 PMX 文件后自动开始计算")
        ttk.Label(bottom, textvariable=self.status_var, anchor="w").pack(
            side="left", fill="x", expand=True)
        self._build_actions()          # 也在底部，所以落在状态条上方

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10, pady=8)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_params(body)

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        nb = ttk.Notebook(right)
        nb.grid(row=0, column=0, sticky="nsew")
        self.nb = nb
        # 切到预览页时画布才真正有尺寸，靠这个事件补一次绘制
        nb.bind("<<NotebookTabChanged>>", lambda _e: self._schedule_redraw())
        table_wrap = ttk.Frame(nb)
        table_wrap.columnconfigure(0, weight=1)
        table_wrap.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table_wrap, columns=("group", "name", "value", "base", "delta"),
            show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="分组")
        self.tree.column("#0", width=70, stretch=False)
        for key, label, width in (("name", "指标", 190), ("value", "当前", 90),
                                  ("base", "基准", 90), ("delta", "Δ", 80)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="e" if key != "name" else "w",
                             stretch=key == "name")
        scroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.tag_configure("group", font=("", 9, "bold"))
        nb.add(table_wrap, text=" 数字对照 ")

        self.output_tabs(nb)   # 警告 / 预览 / 输出 三个标签页

        # 下限按控件申请值算：写死的数字换台机器（字体/DPI 不同）就会裁掉内容。
        # 高度 = 所有横条申请值之和（保证「动作」行不会被裁到屏幕外），
        # 宽度取中间区；只在建窗时算一次，之后不随表格内容爬升。
        self.update_idletasks()
        self.minsize(body.winfo_reqwidth() + 20, self.winfo_reqheight())

    def output_tabs(self, nb):
        """警告页 + 预览页 + 输出页（导出/子进程的日志）。"""
        self.warn_text = tk.Text(nb, wrap="word", height=8, relief="flat",
                                 bg="#2b2b2b", fg="#f0d090", insertwidth=0)
        nb.add(self.warn_text, text=" 警告 0 ")
        # ttk.Notebook.add() 的返回值不可靠（这个版本返回 None），用控件反查下标
        self.warn_tab = nb.index(self.warn_text)

        page = ttk.Frame(nb)
        bar = ttk.Frame(page, padding=(4, 4, 4, 0))
        bar.pack(fill="x")
        for view in VIEWS:
            ttk.Radiobutton(bar, text=view, value=view, variable=self.view_var,
                            command=self._draw_preview).pack(side="left")
        for label, var in (("网格", self.show_mesh_var),
                           ("剔除骨", self.show_dropped_var),
                           ("改挂连线", self.show_link_var)):
            ttk.Checkbutton(bar, text=label, variable=var,
                            command=self._draw_preview).pack(side="left", padx=(10, 0))
        self.canvas = tk.Canvas(page, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=6, pady=6)
        self.canvas.bind("<Configure>", lambda _e: self._schedule_redraw())
        nb.add(page, text=" 预览 ")
        self.preview_frame = page

        self.log_text = tk.Text(nb, wrap="word", height=8, relief="flat",
                                bg="#222222", fg="#d6d6d6", insertwidth=0,
                                # Consolas 没有 ①②③，日志里会被画成「?」；这页几乎都是中文
                                font=("Microsoft YaHei UI", 9), state="disabled")
        nb.add(self.log_text, text=" 输出 ")
        self.log_tab = nb.index(self.log_text)

    def _build_actions(self):
        """三步动作区：导出 -> 生成脚本 -> 跑 Max。任务在跑时整排控件锁住。"""
        panel = ttk.LabelFrame(self, text="动作", padding=(10, 6))
        panel.pack(side="bottom", fill="x", padx=10, pady=(0, 2))
        self.btn_export = ttk.Button(panel, text="① 导出 OBJ+TSV",
                                     command=self._export)
        self.btn_ms = ttk.Button(panel, text="② 生成 .ms", command=self._gen_ms)
        self.btn_max = ttk.Button(panel, text="③ 跑 3ds Max → .max",
                                  command=self._run_max)
        for button in (self.btn_export, self.btn_ms, self.btn_max):
            button.pack(side="left", padx=(0, 6))
        ttk.Button(panel, text="打开输出目录",
                   command=self._open_outdir).pack(side="right")
        self.job_locks = []
        for label, var in (("--no-skin 冒烟", self.noskin_var),
                           ("--shot 截图", self.shot_var),
                           ("--physics 刚体", self.physics_var)):
            check = ttk.Checkbutton(panel, text=label, variable=var)
            check.pack(side="right", padx=(10, 0))
            self.job_locks.append(check)      # 跑到一半改标志会串产物文件名

    # ---------------------------------------------------------- 任务与日志
    def _log(self, text: str) -> None:
        """任何线程都能调；工作线程的行排队等 _poll，界面线程直接落屏（不等 100ms）。"""
        lines = str(text).splitlines()
        if threading.get_ident() == self.main_ident:
            for line in lines:
                self._append_log(line)
            return
        for line in lines:
            self.output_q.put(line)

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        total = int(self.log_text.index("end-1c").split(".")[0])
        self.nb.tab(self.log_tab, text=f" 输出 {total} ")

    def _drain_log(self) -> None:
        try:
            while True:
                self._append_log(self.output_q.get_nowait())
        except queue.Empty:
            pass

    def _start_job(self, title: str, work, done) -> None:
        """work 在工作线程跑，done(退出码) 回界面线程生成状态行（要读产物文件）。"""
        if self.job_title:
            self.status_var.set(f"「{self.job_title}」还在跑，先等它结束")
            return
        self.job_title = title
        self.job_done = done
        self._set_job(True)
        self.status_var.set(title)
        self._log(f"━━ {title} ━━")
        threading.Thread(target=work, daemon=True).start()

    def _set_job(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for widget in [self.btn_export, self.btn_ms, self.btn_max, *self.job_locks]:
            widget.configure(state=state)

    def _finish_job(self, code: int) -> None:
        title, self.job_title = self.job_title, ""
        done, self.job_done = self.job_done, None
        self._set_job(False)
        self._log(f"━━ {title} {'完成' if code == 0 else f'失败（退出码 {code}）'} ━━")
        summary = done(code) if done else ""
        if code == 0:
            self.status_var.set(summary)
        else:
            self.status_var.set(f"失败（退出码 {code}）：{summary}")
            self.nb.select(self.log_tab)      # 出错时把日志翻到眼前

    def _run_script(self, argv: list, title: str, done) -> None:
        def work():
            code = 1
            try:
                with subprocess.Popen(
                        [str(a) for a in argv], cwd=str(ROOT),
                        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace",
                        bufsize=1) as proc:
                    for line in proc.stdout:
                        self.output_q.put(line.rstrip())
                    code = proc.wait()
            except OSError as exc:
                self.output_q.put(f"启动失败：{exc}")
            self.done_q.put((code,))
        self._start_job(title, work, done)

    def _checked(self) -> tuple[Path, Path, dict] | None:
        """动作区共用校验：模型文件 + 参数 + 导出目录。

        _source()/_params() 已经写过具体提示（缺文件 / 数字没填对），这里不重复覆盖。
        """
        source = self._source()
        params = self._params() if source else None
        if source is None or params is None:
            return None
        if self.busy:
            self.status_var.set("还在计算，稍等一下再导出")
            return None
        return source, self._job_outdir(source), params

    def _export(self) -> None:
        checked = self._checked()
        if checked is None:
            return
        source, outdir, params = checked

        def work():
            try:
                buffer = io.StringIO()
                # export() 里还有 print（进度/警告），一起收进「输出」页
                with contextlib.redirect_stdout(buffer):
                    export_obj.export(
                        source, outdir, export_obj.build_plan(source, **params))
                self._log(buffer.getvalue())
                self._log(f"下一步：② 生成 .ms（obj_run 会读 {source.stem}_*.tsv）")
                self.done_q.put((0,))
            except Exception as exc:                     # 线程边界：错误要可见
                self._log(f"{type(exc).__name__}: {exc}")
                self.done_q.put((1,))

        self._start_job(f"导出 {source.name} → {outdir}", work,
                        lambda code: f"已导出到 {outdir}")

    def _gen_ms(self) -> None:
        checked = self._checked()
        if checked is None:
            return
        source, outdir, _params = checked
        script = outdir / "mmd2max_run.ms"
        argv = [sys.executable, OBJ_RUN, outdir, source.stem, "--gen-only"]
        self._run_script(argv, f"生成装配脚本 → {script}",
                         lambda code: (f"脚本已生成：{script}" if code == 0
                                       else "MAXScript 体检没过，见「输出」页"))

    def _max_argv(self, outdir: Path, source: Path) -> list:
        argv = [sys.executable, OBJ_RUN, outdir, source.stem]
        if self.noskin_var.get():
            argv.append("--no-skin")
        if self.physics_var.get():
            argv.append("--physics")
        if self.shot_var.get():
            argv.append("--shot")
        return argv

    def _run_max(self) -> None:
        checked = self._checked()
        if checked is None:
            return
        source, outdir, _params = checked
        weights = outdir / f"{source.stem}_weights.tsv"
        if not weights.is_file():
            self.status_var.set(f"还没有导出产物：缺 {weights.name}，先按 ①")
            return
        from obj_run import MAX_EXE_HINT, find_max_exe   # 借同一套发现逻辑
        max_exe = find_max_exe()
        if max_exe is None:
            messagebox.showerror("找不到 3ds Max", MAX_EXE_HINT)
            return
        argv = self._max_argv(outdir, source)
        shown = "python tools/obj_run.py " + " ".join(str(a) for a in argv[2:])
        max_out = outdir / f"{source.stem}{self._max_suffix()}.max"
        if not messagebox.askokcancel(
                "确认启动 3ds Max",
                f"将执行：\n  {shown}\n\n输出文件：{max_out}\n\n"
                "这一步有两件事会波及你在 Max 里的其它工作，先说清楚：\n"
                "  1) 装配脚本开头执行 resetMaxFile #noPrompt —— 当前 Max 场景直接"
                "清空，未保存的内容不会弹提示；\n"
                "  2) 结束后脚本自己 quitMax 正常退出（只有超时没退才兜底 taskkill /F "
                "强杀）；期间请不要另外开 3ds Max 场景。\n\n"
                f"启动的可执行文件：{max_exe}\n"
                "期间请不要动鼠标键盘（脚本要自己处理启动弹窗、抓截图）。\n"
                "整个过程可能持续几分钟到几十分钟。",
                # 默认焦点给「取消」：回车键不该把 Max 拉起来
                icon="warning", default=messagebox.CANCEL):
            self.status_var.set("已取消，没有启动 Max")
            return
        self._run_script(argv, f"3ds Max 装配 · {source.stem}",
                         lambda code: self._max_report(outdir, source, max_out, code))

    def _max_report(self, outdir: Path, source: Path, max_out: Path,
                    code: int) -> str:
        """跑完在「输出」页交代清楚：.max 在不在、成功标记、报错文本、截图几张。

        这些信息散在 obj_run 的日志里，用户只想知道一句「到底成没成、去哪看」。
        """
        name = source.stem + self._max_suffix()
        marker = outdir / f"{name}.success.json"
        error_file = Path(str(marker) + ".error.txt")
        shots = sorted((outdir / "shots").glob("*.png"))
        report = []
        report.append(f"退出码 {code}")
        report.append(f"{'有' if max_out.is_file() else '没有'} {max_out.name}"
                      + (f"（{max_out.stat().st_size // 1024} KB）"
                         if max_out.is_file() else ""))
        report.append(f"成功标记 {'在' if marker.is_file() else '不在'}：{marker.name}")
        if error_file.is_file():
            report.append("Max 端报错：" + error_file.read_text(
                encoding="utf-8", errors="replace").strip())
        report.append(f"截图 {len(shots)} 张：{outdir / 'shots'}")
        self._log("\n".join("  " + line for line in report))
        if shots:
            self._log("  逐张：" + "、".join(p.name for p in shots))
        if code == 0 and max_out.is_file():
            return f"完成：{max_out}　截图 {len(shots)} 张"
        if max_out.is_file():
            # 产物在但退出码非 0：7/8 = 判据没过或两张判据表不可用，4 = 后置步骤抛错。
            # 这时候说「没生成」是假话，说「完成」更糟 —— 报清楚「文件能用但这轮没通过」。
            return (f"{max_out.name} 已生成，但这一轮没通过（退出码 {code}），"
                    "看「输出」页末尾的判据汇总")
        return f"{max_out.name} 没生成，看「输出」页末尾的报错"

    def _max_suffix(self) -> str:
        """跟着 obj_run 的命名规则走：--no-skin / --physics 会加后缀。"""
        parts = []
        if self.noskin_var.get():
            parts.append("noskin")
        if self.physics_var.get():
            parts.append("phys")
        return ("_" + "_".join(parts)) if parts else ""

    def _open_outdir(self) -> None:
        raw = self.outdir_var.get().strip()
        outdir = Path(raw) if raw else None
        if outdir is None or not outdir.is_dir():
            self.status_var.set(f"输出目录还不存在：{outdir or '（未填）'}")
            return
        os.startfile(str(outdir))               # 用资源管理器打开

    def _job_outdir(self, source: Path) -> Path:
        raw = self.outdir_var.get().strip()
        return Path(raw) if raw else ROOT / "test_runs" / source.stem

    # -------------------------------------------------------------- 预览绘制
    def _schedule_redraw(self):
        """合并连续的重绘请求（拖窗口、切控件会连着触发好几次）。"""
        if not hasattr(self, "canvas"):
            return          # 首次 nb.add() 就会发 TabChanged，那时画布还没建
        if self.redraw_job is not None:
            self.after_cancel(self.redraw_job)
        self.redraw_job = self.after(50, self._redraw_now)

    def _redraw_now(self):
        self.redraw_job = None
        self._draw_preview()

    def _draw_preview(self):
        cv = self.canvas
        cv.delete("all")
        view = self.view_var.get()
        width, height = cv.winfo_width(), cv.winfo_height()
        if width < 60 or height < 60:      # 尚未完成布局，winfo 给的是占位值
            return
        if self.preview is None:
            cv.create_text(width / 2, height / 2, text="等待计算结果…", fill="#777")
            return
        prev = self.preview
        nodes = [project(p, view) for p in prev["heads"]]
        tips = [project(p, view) for p in prev["tails"]]
        # 网格始终投影：取景范围要固定，勾掉「网格」时画面不该跟着缩放
        mesh = [(project(a, view), project(b, view)) for a, b in prev["mesh"]]
        dropped = ([project(p, view) for p in prev["dropped"]]
                   if self.show_dropped_var.get() else [])
        everything = nodes + tips + dropped + [p for seg in mesh for p in seg]
        if not everything:
            return

        u0 = min(p[0] for p in everything)
        u1 = max(p[0] for p in everything)
        v0 = min(p[1] for p in everything)
        v1 = max(p[1] for p in everything)
        pad = 24.0
        zoom = min((width - 2 * pad) / max(u1 - u0, 1e-6),
                   (height - 2 * pad) / max(v1 - v0, 1e-6))
        room_u = (width - 2 * pad - (u1 - u0) * zoom) / 2
        room_v = (height - 2 * pad - (v1 - v0) * zoom) / 2

        def spot(pt):
            # 屏幕 y 向下、Max 的 v 向上，所以要翻转；水平垂直各自居中
            return (pad + room_u + (pt[0] - u0) * zoom,
                    height - pad - room_v - (pt[1] - v0) * zoom)

        if self.show_mesh_var.get():
            for a, b in mesh:
                xa, ya = spot(a)
                xb, yb = spot(b)
                cv.create_line(xa, ya, xb, yb, fill="#3c3c3c")
        for pt in dropped:
            x, y = spot(pt)
            cv.create_rectangle(x - 1.5, y - 1.5, x + 1.5, y + 1.5,
                                fill=KIND_COLORS["dropped"], outline="")
        kinds = prev["kinds"]
        for i, (head, tail) in enumerate(zip(nodes, tips)):
            x0_, y0_ = spot(head)
            x1_, y1_ = spot(tail)
            cv.create_line(x0_, y0_, x1_, y1_, fill=KIND_COLORS[kinds[i]],
                           width=3 if kinds[i] == "biped" else 1, capstyle="round")
        for i, target in enumerate(prev["max_parent"]):
            if target < 0:
                continue
            x0_, y0_ = spot(nodes[i])
            x1_, y1_ = spot(nodes[target])
            cv.create_line(x0_, y0_, x1_, y1_, fill=KIND_COLORS["reparent"],
                           dash=(2, 3))

        x = pad
        for kind, label in (("biped", "Biped 命中"), ("reparent", "改挂链根"),
                            ("accessory", "附属骨"), ("dropped", "剔除")):
            cv.create_line(x, pad, x + 14, pad, fill=KIND_COLORS[kind], width=3)
            text = cv.create_text(x + 18, pad, anchor="w", fill="#c8c8c8",
                                  text=label, tags="hud")
            x = cv.bbox(text)[2] + 16
        caption = (f"{view}　保留 {len(nodes)} 骨　剔除 {len(prev['dropped'])} 骨"
                   f"　网格段 {len(mesh) if self.show_mesh_var.get() else 0}")
        if view == "顶视":
            caption += f"　前后厚度 {prev['body_depth']:.1f} cm"
        else:
            caption += f"　身高 {prev['body_height']:.1f} cm"
        cv.create_text(pad, height - pad, anchor="w", fill="#c8c8c8",
                       text=caption, tags="hud")

    def _build_params(self, parent):
        panel = ttk.LabelFrame(parent, text="参数", padding=10)
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        panel.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(panel, text="骨架模式").grid(row=row, column=0, sticky="w")
        for mode in MODES:
            row += 1
            ttk.Radiobutton(panel, text=mode, value=mode, variable=self.mode_var,
                            command=self._changed).grid(row=row, column=0, sticky="w",
                                                         padx=(10, 0))
        row += 1
        self.hint_var = tk.StringVar(value=MODE_HINT["useful"])
        ttk.Label(panel, textvariable=self.hint_var, wraplength=300,
                  foreground="#666").grid(row=row, column=0, columnspan=2,
                                         sticky="w", pady=(0, 8))
        self.mode_var.trace_add("write", lambda *_: self.hint_var.set(
            MODE_HINT.get(self.mode_var.get(), "")))

        row += 1
        ttk.Label(panel, text="附属骨改挂").grid(row=row, column=0, sticky="w")
        for value in ("nearest", "hierarchy"):
            row += 1
            ttk.Radiobutton(panel, text=value, value=value,
                            variable=self.accessory_var,
                            command=self._changed).grid(row=row, column=0, sticky="w",
                                                        padx=(10, 0))
        row += 1
        ttk.Label(panel, textvariable=self._accessory_hint(), wraplength=300,
                  foreground="#666").grid(row=row, column=0, columnspan=2,
                                         sticky="w", pady=(0, 8))

        row += 1
        ttk.Label(panel, text="min_weight（低于此总权重的骨不蒙皮）").grid(
            row=row, column=0, sticky="w")
        row += 1
        spin = ttk.Spinbox(panel, from_=0.0, to=1.0, increment=0.0005,
                           textvariable=self.min_weight_var, width=10,
                           format="%.4f", command=self._changed)
        spin.grid(row=row, column=0, sticky="w", padx=(10, 0))
        spin.bind("<Return>", self._changed)
        spin.bind("<FocusOut>", self._changed)

        row += 1
        ttk.Checkbutton(panel, text="按目标身高自动算 scale",
                        variable=self.auto_scale_var,
                        command=self._changed).grid(row=row, column=0, sticky="w",
                                                   pady=(8, 0))
        row += 1
        grid = ttk.Frame(panel)
        grid.grid(row=row, column=0, sticky="w", padx=(10, 0))
        ttk.Label(grid, text="身高cm").pack(side="left")
        h = ttk.Spinbox(grid, from_=80, to=240, increment=5,
                        textvariable=self.height_var, width=6, command=self._changed)
        h.pack(side="left", padx=(2, 10))
        h.bind("<Return>", self._changed)
        ttk.Label(grid, text="scale").pack(side="left")
        self.scale_entry = ttk.Entry(grid, textvariable=self.scale_var, width=9,
                                     state="disabled")
        self.scale_entry.pack(side="left", padx=2)
        self.scale_entry.bind("<Return>", self._changed)
        self.auto_scale_var.trace_add(
            "write", lambda *_: self.scale_entry.configure(
                state="normal" if not self.auto_scale_var.get() else "disabled"))

        row += 1
        ttk.Label(panel, text="输出目录").grid(row=row, column=0, sticky="w",
                                               pady=(10, 0))
        row += 1
        wrap = ttk.Frame(panel)
        wrap.grid(row=row, column=0, columnspan=2, sticky="we")
        wrap.columnconfigure(0, weight=1)
        ttk.Entry(wrap, textvariable=self.outdir_var).grid(row=0, column=0,
                                                           sticky="we")
        ttk.Button(wrap, text="…", width=3, command=self._browse_dir).grid(
            row=0, column=1, padx=(4, 0))

        row += 1
        btns = ttk.Frame(panel)
        btns.grid(row=row, column=0, columnspan=2, sticky="w", pady=(14, 0))
        ttk.Button(btns, text="钉住为基准", command=self._pin_base).pack(side="left")
        ttk.Button(btns, text="清除基准", command=self._clear_base).pack(
            side="left", padx=6)
        self.base_label = ttk.Label(panel, text="基准：未钉住（Δ 与上一次计算相比）",
                                    foreground="#666")
        row += 1
        self.base_label.grid(row=row, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _accessory_hint(self):
        var = tk.StringVar(value=ACCESSORY_HINT["nearest"])
        self.accessory_var.trace_add(
            "write", lambda *_: var.set(ACCESSORY_HINT.get(self.accessory_var.get(), "")))
        return var

    # ------------------------------------------------------------ 交互 -> 计算
    def _changed(self, *_):
        """控件变化：先取消上一个待办，再延迟触发（避免拖 Spinbox 时狂算）。"""
        if self.debounce is not None:
            self.after_cancel(self.debounce)
        self.debounce = self.after(DEBOUNCE_MS, self._kick)

    def _browse(self):
        path = filedialog.askopenfilename(
            title="选择 PMX", filetypes=[("PMX 模型", "*.pmx"), ("所有文件", "*.*")])
        if path:
            self.path_var.set(path)
            stem = Path(path).stem
            if not self.outdir_var.get():
                # 与 tools/run_model.py 同一个落点，产物不必分两棵树
                self.outdir_var.set(str(ROOT / "test_runs" / stem))
            self._kick()

    def _browse_dir(self):
        path = filedialog.askdirectory(title="导出目录")
        if path:
            self.outdir_var.set(path)

    def _params(self) -> dict | None:
        mode = self.mode_var.get()
        try:
            scale = float(self.scale_var.get())
            min_weight = float(self.min_weight_var.get())
        except ValueError:
            self.status_var.set("scale / min_weight 需要填数字")
            return None
        if scale <= 0:
            self.status_var.set("scale 必须大于 0")
            return None
        return {"scale": scale, "min_weight": min_weight,
                "humanoid_only": mode == "humanoid",
                "filter_useless": mode != "full",
                "accessory_parent": self.accessory_var.get()}

    def _source(self) -> Path | None:
        raw = self.path_var.get().strip()
        if not raw:
            self.status_var.set("先选择 PMX 文件")
            return None
        path = Path(raw)
        if not path.is_file():
            self.status_var.set(f"文件不存在：{path}")
            return None
        return path

    def _model(self, path: Path):
        """按 路径+mtime 缓存解析结果：调参不该重复解析 2MB 的 PMX。"""
        mtime = path.stat().st_mtime_ns
        if self.model_cache and self.model_cache[0] == path \
                and self.model_cache[1] == mtime:
            return self.model_cache[2]
        model = load_pmx(path)
        self.model_cache = (path, mtime, model)
        return model

    def _kick(self):
        self.debounce = None
        source = self._source()
        params = self._params() if source else None
        if source is None or params is None:
            return
        if self.auto_scale_var.get():
            try:
                model = self._model(source)
                target = float(self.height_var.get())
            except (PMXError, ValueError, OSError) as exc:
                self.status_var.set(f"读取失败：{exc}")
                return
            self.scale_var.set(f"{export_obj.auto_scale(source, target, model):.4f}")
            params["scale"] = float(self.scale_var.get())
        self.token += 1
        mine = self.token
        self.busy = True
        self.status_var.set("计算中…")
        threading.Thread(target=self._worker, args=(mine, source, params),
                         daemon=True).start()

    def _worker(self, mine: int, source: Path, params: dict):
        started = time.perf_counter()
        try:
            plan = export_obj.build_plan(source, **params)
            # 坐标换算（2 万多顶点）也不便宜，一起放在线程里，界面只负责画线
            preview = build_preview(plan)
            payload = (mine, plan, preview, params, None,
                       time.perf_counter() - started)
        except Exception as exc:                      # 线程边界：异常要送回界面
            payload = (mine, None, None, params, f"{type(exc).__name__}: {exc}", 0.0)
        self.results.put(payload)

    def _poll(self):
        try:
            while True:
                payload = self.results.get_nowait()
                if payload[0] == self.token:
                    self._render(*payload[1:])
        except queue.Empty:
            pass
        self._drain_log()
        try:
            code, = self.done_q.get_nowait()
        except queue.Empty:
            pass
        else:
            self._finish_job(code)
        self.after(100, self._poll)

    # ------------------------------------------------------------------ 渲染
    def _render(self, plan, preview, params, error, elapsed):
        self.busy = False
        if error:
            self.status_var.set(f"计算失败：{error}")
            self._set_warnings([error])
            return
        self.last_plan = plan
        self.preview = preview
        rows = plan_rows(plan)
        base = self.base_rows if self.base_rows is not None else self.shown_rows
        self._fill_table(rows, base)
        if self.base_rows is None:
            self.base_label.configure(text="基准：上一次计算（可点「钉住为基准」固定）")
        self._set_warnings(plan.warnings)
        changed = [f"{k}={v}" for k, v in params.items()]
        self.shown_rows = {name: value for _g, name, value, _t in rows}
        self._schedule_redraw()
        if not self.job_title:      # 动作在跑时别把「3ds Max 装配…」覆盖掉
            self.status_var.set(
                f"{' · '.join(changed)}    用时 {elapsed * 1000:.0f} ms")

    def _fill_table(self, rows, base):
        self.tree.delete(*self.tree.get_children())
        seen_group = None
        for group, name, value, text in rows:
            shown = group if group != seen_group else ""
            seen_group = group
            base_value = (base or {}).get(name)
            if base_value is None:
                base_text, delta = "", ""
            else:
                diff = value - base_value
                base_text = f"{base_value:g}"
                # 用三角而不是 +30：Tk 会把看着像数字的串转回 int，正号会丢
                delta = "—" if diff == 0 else ("▲" if diff > 0 else "▼") + f"{abs(diff):g}"
            tags = ("group",) if shown else ()
            self.tree.insert("", "end", text=shown, tags=tags,
                             values=(name, text, base_text, delta))

    def _set_warnings(self, warnings):
        self.warn_text.configure(state="normal")
        self.warn_text.delete("1.0", "end")
        self.warn_text.insert("1.0", "\n".join(warnings) if warnings
                              else "（无警告）")
        self.warn_text.configure(state="disabled")
        self.nb.tab(self.warn_tab, text=f" 警告 {len(warnings)} ")

    def _pin_base(self):
        if self.last_plan is None:
            self.status_var.set("还没有可钉住的结果")
            return
        self.base_rows = dict(self.shown_rows)
        self.base_label.configure(text="基准：已钉住（下面钉住时的参数组合）")
        self._fill_table(plan_rows(self.last_plan), self.base_rows)

    def _clear_base(self):
        self.base_rows = None
        self.base_label.configure(text="基准：未钉住（Δ 与上一次计算相比）")


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else None
    App(path).mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
