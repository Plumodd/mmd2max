# mmd2max v5

把 MMD 的 PMX/PMD 角色模型导成 3ds Max 2022 能直接用的东西：OBJ + 贴图 + 骨骼/权重/肢段数据表，再自动驱动 3ds Max 建 Biped、绑 Skin、做 Morpher 表情、可选物理代理。

v5 = v4 的**干净发行版**：只保留运行所需的核心代码（23 个文件 / 约 1.2MB），去掉全部运行产物、测试与开发期资产，另附一枚单文件 `mmd2max.exe`。功能与 v4 完全相同，源码与 exe 走同一份代码。

---

## 一、两种用法

### 1. 不需要 Python：双击 `mmd2max.exe`

- **双击** → 图形界面（选 PMX、调参数、点导出/装配）
- **命令行**（同一个 exe，三种子命令）：

```
mmd2max.exe --model "D:/models/角色.pmx"
mmd2max.exe --model "D:/models/角色.pmx" --drop-guides
mmd2max.exe --obj-run "产物目录/角色" 角色 --shot
```

### 2. 有 Python 3.8+（含 tkinter）：源码版

- 双击 `启动GUI.bat`，或 `python tools/gui.py [模型.pmx]`
- 命令行：`python tools/run_model.py <pmx> [参数]` 导出，`python tools/obj_run.py <产物目录> <模型名> [参数]` 装配

| 参数 | 作用 |
| --- | --- |
| `--drop-guides` | 不导出辅助骨（IK / 物理哑骨 / 表情骨等） |
| `--height N` | 目标身高厘米数，默认 160 |
| `--min-weight N` | 权重剪枝阈值，默认 0 |
| `--shot` | 装配结束后出视口截图 |
| `--physics` | 生成物理刚体代理 |
| `--no-skin` | 装配时不绑 Skin |
| `--gen-only` | 只生成 MAXScript，不启动 3ds Max |
| `--quit` | 装配结束后让 3ds Max 自己退出（默认**保留现场**供检视） |

GUI 里点「导出并装配」已把两步串好，一般不用手打命令行。

**附属骨改挂**（GUI 的 `nearest` / `hierarchy` 单选，命令行第 7 参数）：决定裙摆、
领口、头发等附属骨链根挂到哪根 Biped 骨。默认 `nearest` 只把「PMX 原父在腰以下
（Pelvis / Spine）」的链根按几何最近提级；原父已是胸腔及以上
（Spine1 / Spine2 / Neck / Head）的链根一律保持 PMX 原父，避免领口骨被几何吸附
到脖子上。`hierarchy` 则所有附属骨都跟随原作者层级。
**注意：策略是在导出时算进 `bones.tsv` 的 `max_parent` 列的，改策略或升级版本后
必须重新导出 PMX 才生效**；拿旧产物重新装配不会重算父级。

**锁骨权重裁剪**（可选，默认关）：MMD 原作者常把锁骨内侧到上胸一片刷给「左肩/右肩」
骨（抬臂时领口跟随）；转成 Biped 后 `Bip001 L/R Clavicle` 成了整条手臂的根，
转动会同时甩手臂并带上胸，观感上像带错了权重。这是**动画意图的取舍，不是缺陷**，
原 PMX 与导出产物逐顶点完全一致。若确实想让胸腔完全不跟锁骨走，可开这个开关：

| 入口 | 写法 |
| --- | --- |
| GUI | 「锁骨权重裁剪（cm，0=关）」输入框 |
| 命令行导出 | `python -m mmd2max.export_obj <pmx> <目录> ... --clavicle-trim 2` |
| 一条龙 / exe | `mmd2max.exe --model <pmx或目录> --clavicle-trim 2` |

`T` 是裁剪宽度（厘米）：右肩裁 `x > −T`、左肩裁 `x < T` 的顶点，被裁掉的肩骨权重
按该顶点其余骨的原权重比例重新分配（权重总和不变，主要由「上半身2 / Bip001 Spine1」
接手）。林尼 24037 顶点的实测影响面：

| T | 影响顶点 | 让出权重 | 其中主控(≥0.5) |
| --- | --- | --- | --- |
| 0（只削越中线） | 129 | 2.9 | 0 |
| 2（默认示例） | 404 | 26.2 | 0 |
| 4 | 941 | 102.4 | 12 |
| 6 | 1197 | 203.9 | 92 |

温和档（T≤2）不动任何主控顶点，大概率看不出变化；要明显改善需 T≥4，代价是
明显偏离原作者绑定。**改了必须重新导出 PMX 才生效**——阈值在导出时算进
`weights.tsv`，装配侧改不动；`MMD2MAX.CFG` 里没有这个键，避免误以为改配置能切换。
导出产物 `<名>_meta.tsv` 的 `clavicle_trim_cm` 一行记录这一份是按哪个 T 导的。

---

## 二、产物写在哪

默认全部写在 **exe / 仓库自己所在目录**：

```
test_runs/<模型名>/    OBJ / MTL / 贴图 / 各种 .tsv / mmd2max_run.ms / <模型名>.max / shots/
```

想让产物落到别的盘，设环境变量 `MMD2MAX_APP_DIR=D:/输出根`，不用改代码。

---

## 三、改骨骼分类 / Biped 映射，不用重新打包

在 exe 旁边建 `mmd2max\configs\` 并放入三份 JSON（`pmx_definition.json`、`biped_definition.json`、`biped_to_pmx_mapping.json`），程序优先读你放的这一份；没有才用包内自带那份。源码版直接改 `mmd2max/configs/`。

---

## 四、边界（必须说清）

1. **3ds Max 本体装不进 exe**。目标机器仍需 3ds Max 2022，程序会自动发现安装位置（不写死 C 盘路径）。没装 Max 时导出段照跑，装配段会报「找不到 3dsmax.exe」。
2. **onefile 是 2 个进程**（引导进程 + 真正干活的）。要强制结束请用「任务管理器 → 结束进程树」或 `taskkill /T`。
3. 杀毒软件 / SmartScreen 可能拦未签名的单文件 exe（onefile 会在临时目录自解压）。这是打包方式的固有现象；介意就用源码版 + `启动GUI.bat`。
4. 装配脚本开头会执行 `resetMaxFile #noPrompt` —— **当前 Max 场景会被清空且不弹提示**；装配结束后 3ds Max 默认带着装好的场景留在屏幕上（KEEPOPEN）。跑之前先存好手上的活。

---

## 五、目录结构（最小核心）

```
mmd2max_v5/
├── mmd2max.exe              单文件可执行（GUI + 命令行）
├── README_V5.md             本文
├── 启动GUI.bat              源码版 GUI 启动器
├── mmd2max/                 Python 包
│   ├── *.py                 导出、语义映射、坐标、判据、配置路径
│   └── configs/*.json       骨骼定义 / Biped 映射（唯一事实来源）
├── scripts/
│   └── max_import_obj.ms    3ds Max 装配脚本模板
└── tools/
    ├── mmd2max_exe.py       exe 入口（三种跑法）
    ├── gui.py / run_model.py / obj_run.py
    ├── desktop.py / uia_dialog.py    Windows 弹窗处理
    └── check_ms.py / check_verdicts.py  运行期静态体检与判据汇总
```

依赖：**全部 Python 标准库**（GUI 用 tkinter），无第三方包。PyInstaller 只是构建工具，不进运行路径。

---

## 六、重建 exe

在一次性 venv 里构建，避免污染运行目录。Windows 下 `--add-data` 必须用**绝对路径**（相对路径会按 `--specpath` 解析，实测报 `Unable to find …\scripts`）：

```bat
python -m venv %TEMP%\mmd2max_build_venv
%TEMP%\mmd2max_build_venv\Scripts\python -m pip install pyinstaller

%TEMP%\mmd2max_build_venv\Scripts\python -m PyInstaller --noconfirm --clean ^
  --onefile --console --name mmd2max --paths ".;tools" ^
  --add-data "<v5绝对路径>\scripts;scripts" ^
  --add-data "<v5绝对路径>\mmd2max\configs;mmd2max\configs" ^
  --distpath <构建目录>\dist --workpath <构建目录>\build --specpath <构建目录> ^
  tools\mmd2max_exe.py
```

几条不能改的口径：

- **`--console` 而非 `--windowed`**：窗口化会把 `sys.stdout` 变成 None，而导出/装配整条链路靠 print 报数。
- **不用 `-OO`**：`run_model.main()` 的用法文本就是它的 `__doc__`，优化掉 docstring 会删掉参数提示。
- **`multiprocessing.freeze_support()` 必须是入口 `__main__` 第一句**：`export_obj.export()` 用 `ProcessPoolExecutor`，Windows 只有 spawn，冻结后子进程就是这个 exe 自己；不先认下来会 `BrokenProcessPool`。
- 构建 Python 必须带 **tkinter**（打 GUI）。

---

## 七、自检口径

不是「exe 能双击就算好」，而是**源码跑 vs exe 跑逐字节对撞**：同一份 PMX、同一组开关，两边各导一遍，整目录（含贴图）比哈希；差异只允许来自根路径字符串。exe 还应能自己驱动 3ds Max 跑完整一轮装配并让判据全绿。
