# mmd2max v3 — 交接说明

> 派生自 v2：`C:\Users\Administrator\Desktop\mmd2max_v2_src_2026-11-01`（原样保留，未改动）。
> 目标不变：把 MMD 的 `.pmx` 变成 **3ds Max 2022 里 Biped 骨架 + 附属 Bone + Skin 权重 + Morpher 表情** 的 `.max`。
> v3 只做一件事：**把 v2 踩出来的坑修补掉，并把不再维护的路线删干净**。

---

## 1. v3 相对 v2 的改动（本轮全部落地）

### 1.1 P0：PMX 骨变形的字段宽度错了
变形（morph）`type==2`（骨变形）每一项是
`bone_index + offset(vec3) + rotation(vec4 四元数)`，即 **bs+28 字节**；
v2 按 vec3（bs+24）读。错 4 字节 × N 项，把后面的显示枠/刚体/关节全部读歪 —— 香菱-海灯节.pmx 因此直接崩。

- 现在按规范读 `vec4`；未知 morph 类型（规范只定义 0..10）不再「保守丢弃」而是**抛错**，因为丢弃会把错位藏起来。
- 新增**末尾不变式**（`pmx._parse_tail`）：尾部四段解析必须正好停在文件最后一字节，否则报 `PMXError` 并打印读到的位置。这是任何「变长段字段宽度写错」的廉价全局验尸器。
  - 注意：**不要**给它加「换一种布局重试」的兜底 —— 错位后的字节恰好凑到 EOF 是完全可能的，兜底会把真 bug 洗成合法文件。
- 断言在 `tests/test_validation.py`（vec4 布局、vec3 布局被拒、尾部垃圾被拒）与 `tests/test_real_model.py`（真文件跑通）。

### 1.2 手性：v2 得到的是镜像，v3 换成正影
MMD 是左手系（+X 屏幕右、+Y 上、+Z 朝屏幕内，角色朝 −Z，PMX 里 左腕 x>0）；
Max Front 视图是右手系（相机在 −Y 朝 +Y 看，+X 屏幕右、+Z 上）。要在 Max 里看到**同一张脸**，映射只能是

```
(x, y, z) -> (x, z, y)      行列式 = −1（一次镜像，保住左右）
```

v2 用的是 `(-x, z, y)`（det=+1，纯旋转）——旋转不改变手性，所以画出来是 MMD 的镜像，代价是两处补偿 hack：Max 侧把网格再转 180°，外加**骨名左右互换**。

v3 改成 det=−1，于是：

| 位置 | 改动 |
| --- | --- |
| `mmd2max/coords.py`（新增） | 坐标/手性的**唯一事实来源**：`convert_vec`（Z-up 数据）、`obj_vec`（OBJ 文本）、`obj_delta`/`max_delta`/`horizontal`、`determinant()`、`max_from_obj()` |
| OBJ 数值 | 写 `(x, y, −z)`。Max 2022 的 OBJ 导入器把 Y-up `(a,b,c)` 变成 Z-up `(a,−c,b)`，两者复合后网格正好落在 `convert_vec` 上 —— 所以 **MaxScript 里不再有任何补偿旋转**，第三方工具打开 OBJ 看到的也不是镜像 |
| 三角面 | 绕序**恒定**翻转一次（`_worker_faces` 不再看开关；PMX 正面是顺时针） |
| `semantic.py` | `_lr_is_mirrored` 降级为「自检哨兵」：正确手性下应当永远返回 False；`measure_forward` 改用 `coords.horizontal` |
| `max_import_obj.ms` | 删掉 180° 网格旋转块；`mmd2maxCoordMatrix` 改成 `[1,0,0][0,0,1][0,1,0]`（det=−1，必须和 `coords.py` 一致）；`mmd2maxRotToMax` 的共轭 `M*R*Mᵀ` 对镜像正交阵仍成立 |
| `tests/make_sample.py` | 修正样本骨骼：MMD 角色朝 −Z，所以 `左つま先` 必须在 **z=−1**（v2 样本写 +1 = 脚尖朝后，导致新自检误报「左右反了」） |
| `tests/test_coordinate.py` | 重写：up→+Z、朝向→−Y、**+X（角色自身左）不被镜像**、det=−1、`max_from_obj(obj_vec(v)) == convert_vec(v)` |

实测（香菱 `bones.tsv`）：`左腕 (x=+10.34) -> Bip001 L UpperArm`、`右腕 (x=−10.34) -> Bip001 R UpperArm`、`左足 -> L Thigh`、`右足 -> R Thigh` —— 名称与几何两侧都自洽，没有再互换。

### 1.3 删掉的 v2 路线（v3 目录里已不存在，v2 仍有原件）
| 删除 | 原因 |
| --- | --- |
| `mmd2max.py`、`mmd2max/core.py` | Blender/FBX 旧路线；Max 2022 FBX 导入 555 骨/12 万面会卡死，只剩 OBJ 路线 |
| `mmd2max/export_split.py`、`scripts/max_import_split.ms` | 分材质/分块路线，没人用；文案里「改用分块模式」的建议已一并改掉 |
| `tools/spring_bake.ms` | **飘带解算**：逐帧弹簧烘焙计算量太大，做不了（香菱光裙子就 432 根骨） |
| 纯 Bone 骨架模式 | 用户定的目标骨架是「**Biped + 附属骨骼**」，纯 Bone 不是交付物。`max_import_obj.ms` 删 `mmd2maxBipedEnabled()`/`MMD2MAX_MODE`，建 Biped 变成强制路径；`tools/obj_run.py` 删第三个位置参数（旧 `bone`/`biped`），现在多传位置参数**直接报错**而不是静默忽略；success 标记里不再有 `mode` 字段 |
| `tools/check_ms_syntax.py` | 与 `check_ms.py` 重复。它的三条规则（`if…do` 接 `else`、`localTime()`、行首 `+ - * /` 续行）已合并进 `check_ms.py`，对外统一入口 `check_source(src)`，obj_run 改用它 |

### 1.4 其余缺陷
- **Morpher 100 通道上限**：`export_obj.MAX_MORPH_CHANNELS = 100`，超出的顶点变形在 `morphs.tsv` 标 `CHANNEL_FULL` 并在 `export()` 里给 warning；Max 端把 `SKIP` 和 `CHANNEL_FULL` 一起跳过（旧代码只跳 `SKIP`，会给被截断的表情建出一个和基准网格重合的**假通道**，还白占名额）。
- **截图**：目录只取 `MMD2MAX_SHOTDIR`，取不到就落在 `.max` 同级的 `shots/`；删掉写死的 `…\WorkBuddy\2026-10-30-23-05-55\…` 兜底；文件名跟着模型走（不再叫 `kerei_front.png`）。
- **`run_model.py`**：不再提前 `mkdir`（目录交给 `export()` 建），崩了不会留下空目录让人以为跑过了。
- **`run_model.py` 的子进程输出**：原来 `subprocess.run(text=True, capture_output=True)` 在 Windows 上按 GBK 解码 UTF-8 中文 → `UnicodeDecodeError` → `result.stdout is None` → `write()` 抛 `TypeError`（实跑撞到）。改成不 capture，子进程直接继承 stdio。
- **`run_model.py` 体检**：裙/发骨数现在同时匹配 `name` 和 `name_en`。香菱的日文名是无意义代号（`wt_0_0`），旧代码报「裙 0 根」，真实是 **432 根**。

---

## 2. 环境（本机实测，别再猜）

| 项 | 值 |
| --- | --- |
| 解释器 | **Python 3.8+ 就行，不是只能 3.12**。本机跑的是 `C:/Users/Administrator/.local/bin/python3.12.exe`（uv 装的 CPython 3.12.14），而 `python` / `py` / `python3` 都不在 PATH，所以命令里写全路径。别人那台机器只要有任意 3.8+；`启动GUI.bat` 自己会探（顺序与理由见 [README_V4 §7](file:///C:/Users/Administrator/Desktop/mmd2max_v4_src_2026-11-03/README_V4.md)）。第三方库为零 |
| 编码 | 每条命令都要 `PYTHONIOENCODING=utf-8`，否则中文 print 在 GBK 控制台炸 |
| 第三方库 | 无，纯标准库 |
| 3ds Max | 本机实测装在 `C:\Program Files\Autodesk\3ds Max 2022`（注册表 `HKLM\SOFTWARE\Autodesk\3dsMax\24.0` 的 `Installdir` 与逐盘 glob 两条路都指向它；这台机器上**没有** `D:\Programs\Autodesk`）。装在别的盘/别的根**不必改代码**：`tools/obj_run.py:find_max_exe()` 自动发现，也可用环境变量 `MMD2MAX_MAX_EXE` 指过去。发现顺序、为什么优先 2022、找不到时的退出码见 [README_V4 §7](file:///C:/Users/Administrator/Desktop/mmd2max_v4_src_2026-11-03/README_V4.md) |
| 跑测试 | `cd tests && PYTHONIOENCODING=utf-8 python -m unittest discover -s . -p "test_*.py"`（**必须先进 tests 目录再 discover**：目录里没有 `tests/__init__.py`，从项目根 `-s tests` 会报 `Start directory is not importable`） |

```bash
cd C:/Users/Administrator/Desktop/mmd2max_v4_src_2026-11-03
cd tests && PYTHONIOENCODING=utf-8 ~/.local/bin/python3.12.exe -m unittest discover -s . -p "test_*.py"   # 509 用例全绿
PYTHONIOENCODING=utf-8 ~/.local/bin/python3.12.exe tools/check_ms.py                                       # MAXScript 静态体检
```

当前：**50 用例全绿**（coordinate 7 / export 12 / limbs 8 / pmx 6 / real_model 4(跳过 1) / semantic 5 / validation 8）。

---

## 3. 目录结构

```
mmd2max_v4_src_2026-11-03/
├── mmd2max/
│   ├── pmx.py            # PMX 2.0/2.1 二进制解析；含骨变形 vec4 + 末尾不变式
│   ├── coords.py         # ★ 坐标/手性唯一事实来源（det=−1）
│   ├── semantic.py       # MMD 骨名 → 语义角色 → Biped 目标；手指/脊柱去重；左右自检
│   ├── targets.py        # ★ M2a：五个容差/开关的唯一来源 → 进 MMD2MAX.CFG
│   ├── verdicts.py       # ★ M2b：判据规格表（gate/lock/report）+ 报告通道 + 线格式 v2 读写
│   ├── passes.py         # ★ M3：pass 注册表（requires/writes/reads/invalidates/gate）+ 时间轴审计
│   ├── export_obj.py     # ★ OBJ/MTL/贴图 + 全部 TSV（多进程写权重）
│   └── configs/          # biped_definition / pmx_definition / biped_to_pmx_mapping
├── scripts/
│   └── max_import_obj.ms # ★ Max 端装配：建 Biped → 肢段拟合 → 附属 Bone → Skin → Morpher → (物理)
├── tools/
│   ├── run_model.py      # ★★ 一键：模型目录 → 导出 → 生成 .ms（--gen-only，不启动 Max）→ 体检
│   ├── gui.py            # ★★ 调参台：build_plan 即时数字对照 + 三视图预览 + ①导出②生成脚本③跑 Max
│   ├── obj_run.py        # 生成 mmd2max_run.ms；驱动 Max 轮询/截图（不传 --gen-only 才动进程）
│   ├── check_verdicts.py # ★ M2c：拼规格表 × 报告 ⇒ 逐行状态 + 非 0 退出码（判闸）
│   ├── diff_round.py     # ★ 两轮日志归一对比（判「重构有没有动几何」就靠它，别手搓 sed）
│   ├── uia_dialog.py     # UIA 读写启动弹窗：按**确切按钮名** Invoke（Qt 弹窗没有子 HWND，坐标法点不准）
│   ├── check_ms.py       # MAXScript 静态体检（保留字/括号/字符串/if-do-else/localTime/续行）
│   ├── make_ascii_copy.py# 中文名导出件 → ASCII 副本
│   └── desktop.py        # 窗口枚举/截图/剪贴板（ctypes，非必需）
├── tests/                # unittest + fixtures/sample_model（合成 PMX 由 make_sample.py 生成）
├── baselines/            # 各轮冻结件（日志 / CFG / 判据三张表），回归锁只指这里，不指 test_runs/
├── test_runs/            # 运行产物，可随时删
├── 研究笔记_联网核查60条.md   # 联网核查结论（PMX 规范 / Biped API / 坐标系）
├── v2_改进落地说明.txt / v2_交叉验证与改进计划.txt  # v2 历史；其中命令含旧 `biped` 位置参数，已不适用
├── README_V4.md          # ★ v4 的架构决定与里程碑（M1…M5），先读它再动代码
└── README_HANDOVER.md    # 本文件
```

---

## 4. 数据契约（Python 写 → Max 读）

全部落在 `test_runs/<模型名>/`。**线格式 v2（M1）**：每份 TSV 的第 1 行是 tab 分隔的 ASCII 列名，
Max 侧统一用 `mmd2maxReadTable` 读回来，取数一律 `mmd2maxColPos tbl "列名"`（缺列**直接抛**，
不会再静默读到隔壁列）；`#` 开头的行是给人看的注释，读取时跳过。
同一文件里若有**另一种记录**（morphs 的顶点偏移、physics 的关节），它的列名单独写在
`@<记录名>\t列名…` 这一行里，名字带 `rigid_` / `joint_` 前缀 —— 因为同一个列号在两种记录里
含义不同（physics 第 4 列 RIGID 是 `rigid_name_en`、JOINT 是 `joint_type`），一行表头装不下。
`obj_run.py` 会把它们复制成 ASCII 定名（`model.obj`/`bones.tsv`/…）再喂给 Max —— 中文路径/文件在 Max 侧按 GBK 解析会失败。
拆列只用 `mmd2maxSplit`（按 `\t` 切、**保留空字段**）；`filterString` 会丢掉空字段导致整行列号左移 —— 物理表有刻意的空列（`name_en` 可能为空），用错就全歪。
回归判据：`python tools/check_baseline.py` 会按基线里的 scale 重新导出一次，要求 6 份基线 TSV 的数据行**逐字节不变**（只允许多出一行表头）。

| 文件 | 列名（= 文件第 1 行） |
| --- | --- |
| `<名>.obj` / `.mtl` / `textures/` | 数值为 **`(x, y, −z)` Y-up**；面绕序已翻一次；`vt u (1−v)`；材质只交 `.mtl`，Max 端 `#maxOpsFileOp.loadMaterials` 直接吃，没有别的旁路清单 |
| `<名>_bones.tsv` | `idx bone_name parent role biped_target head_x head_y head_z tail_x tail_y tail_z skin_flag max_parent`（Z-up，13 列；`role` ∈ skeleton/control/morph；`max_parent` −1 表示按 `parent` 挂） |
| `<名>_biped_map.tsv` | `bone_index biped_node pmx_bone_name` —— **biped 唯一路径，缺条目 = Max 侧直接抛错** |
| `<名>_weights.tsv` | `vertex_index bone_weight_pairs`，第 2 列是变长的 `骨序号:权重` 对（序号是**全局骨序号**，Max 侧必须经 `globalToSkin` 映射） |
| `<名>_limbs.tsv` | `parent_target child_target desired_len mmd_parent mmd_child child_x child_y child_z mode` + 仅 `mode=foot` 行才有的 9 列 `foot_len toe_len tip_x tip_y tip_z toe_w toe_h foot_w foot_h`（脚段多给的列，是 Max 端闭环缩放的唯一事实来源） |
| `<名>_widths.tsv` | `biped_target width` —— Biped 默认手指比网格粗得多，按此收缩横截面 |
| `<名>_morphs.tsv` | `row_kind morph_index morph_name morph_name_en morph_panel morph_type item_count_or_flag`，`@V vertex_index offset_x offset_y offset_z` 为其顶点明细 |
| `<名>_physics.tsv` | `row_kind rigid_index rigid_name rigid_name_en rigid_bone rigid_group rigid_nocollide_mask rigid_shape rigid_size_* rigid_pos_* rigid_rot_* rigid_mass rigid_move_damping rigid_rotation_damping rigid_repulsion rigid_friction rigid_physics_mode`，`@JOINT joint_*` 为关节明细；尺寸与位置同 `scale`；骨序号已映射到保留骨 |
| `<名>_meta.tsv` | `key value` 两列：`scale has_sdef has_qdef filter_useless spine_links fingers finger_links kept_bones role_* morph_count rigid_count joint_count vertex_count dropped_verts foot_box_flat` —— 装配侧只取 `spine_links fingers finger_links foot_box_flat` 四个键，写进 `MMD2MAX.CFG`；**缺任一个键 = 直接中止**（不接受默认值，见 §4b） |

判据的三张表（M2b/M2c，同在 `test_runs/<模型名>/`，ASCII 定名、不带模型前缀）：

| 文件 | 谁写 | 列名 |
| --- | --- | --- |
| `MMD2MAX.VERDICTS` | Python（`verdicts.spec_text`，每轮开头由 `obj_run` 重写） | `phase check subject expected tol kind when` |
| `MMD2MAX.REPORT` | **Max 只追加**（`mmd2maxReportInit` 每轮截断重建，`mmd2maxReport` 一次调用一行） | `phase check subject actual` |
| `MMD2MAX.CHECK` | Python（`check_verdicts` 判完落盘） | `status phase check subject expected tol kind when actual note` |

前三列是两张表的**拼接键**，逐字相同；第四列刻意不同名 —— `expected` 是尺、`actual` 是读数，
同名会让拼表时把尺当成读数。`kind` ∈ `gate`（真要求）/ `lock`（已定价的缺陷，PASS 只等于没退步）/
`report`（只要求这一行出现）；`when` ∈ `always`（没报就是 FAIL）/ `if_present`（按设计可以合法缺席）。
报告带 UTF-8 BOM + CRLF：那是 `createFile … encoding:#utf8` 的指纹，用来证明这张表**真是 Max 写的**，
不是 Python 事后补的（`FrozenRoundReplayTests` 钉住这一点）。

## 4b. 配置单源：`MMD2MAX.CFG`（M1b + M2a + M2b + M3）

生成的 `mmd2max_run.ms` 只注入**一行**`global MMD2MAX_CFG = "<cfg 路径>"`，其余 30 个输入与开关
全部从 `test_runs/<模型名>/MMD2MAX.CFG` 读（同样是线格式 v2：第 1 行 `key<TAB>value`）。
键名 = MAXScript 全局名，两边同一套词，没有任何映射表可写错：

`MMD2MAX_OBJ / BONES / WEIGHTS / MAP / LIMBS / WIDTHS / MORPHS / PHYSICS / OUTPUT / MARKER / LOG /
VERTS / SPINE_LINKS / FINGERS / FINGER_LINKS / FOOTBOXFLAT / SKIN / SHOT / SHOTDIR / INTERACTIVE`
（以上 M1b；`QUIET` 已在 #56 删除 —— obj_run 恒写 true，它唯一的用处是把「重置场景前问一句」短路）+ `DIRTOL / ROLLTOL / TOETOL / GAPTOL / FOOTREACH`（以上 M2a，见 `mmd2max/targets.py`）
+ `VERDICTS / REPORT`（以上 M2b：两张判据表的路径，见 `mmd2max/verdicts.py`）
+ `CONVERGE_MAX`（以上 M3：收敛循环上限，唯一来源是 `passes.max_iters("converge")`，`.ms` 里那圈 `for`
  读它而不是写死 3 —— 循环次数与注册表分叉时 `audit_gate` 会直接报「超过注册表声明的上限」）

规则（违反任何一条都在 **Python 侧**就中止，不留到 Max）：

- **没有默认值**。`mmd2maxLoadCfg` 读不到文件、缺键、值不合法一律 `throw`；
  以前「缺 `spine_links` 退回硬编码 3」就是 v4 要根除的那类「配置有默认值」——日志全绿、几何是另一套。
- **允许空串的只有 `MMD2MAX_MORPHS` / `MMD2MAX_PHYSICS` / `MMD2MAX_WIDTHS`**（该阶段整段跳过）；
  其余路径为空 = 抛错。这份名单在 `obj_run.CFG_EMPTY_OK` 与 Max 端 `mmd2maxLoadCfg` 各写一次，
  由 `test_maxscript_contract.ConfigSingleSourceContract` 钉住两边一致。
- **值写字面量**：bool 写 `true/false`（不再 `0/1` 再取反——`FOOT_BOX_FLAT→foot_pitch→FOOTPITCH→if not`
  曾连反三次），路径写原始反斜杠（转义是 `.ms` 那层 `mmd2maxCfgStr` 的事，不污染数据文件）。
- **容差类数字也进 CFG（M2a）**：五个数（`DIRTOL 3.0 / ROLLTOL 1.0 / TOETOL 0.5 / GAPTOL 0.5 / FOOTREACH true`）
  的唯一来源是 [`mmd2max/targets.py`](file:///C:/Users/Administrator/Desktop/mmd2max_v4_src_2026-11-03/mmd2max/targets.py)，
  `obj_run.build_cfg` 把它的 `cfg_values()` 直接并进 CFG；`.ms` 里那五个全局**只许出现一次赋值、
  且赋值右边必须是 `mmd2maxCfgNum/Bool "<同一个键>"`**（`test_targets.MaxScriptConsumerTests` 逐键钉）。
  搬这一层的理由不是整洁：M2b/c 的 `verdicts.tsv` 要在 **Python 侧**拿「期望值 + 容差」去判 Max 报回来的数，
  数字留在 `.ms` 里就等于「写的人手里一份尺、判的人手里另有一份」—— 正是 §1 那个病根的第二次发作。
  浮点走新函数 `mmd2maxCfgNum key low high`：`"abc" as float` 在 Max 里**给 0 不报错**，
  所以必须自己验「全是数字 + 至多一个小数点」再用 `isNumber` 复查，超范围同样抛。
- **校验上限写 9999 而不是 90**：`DIRTOL/ROLLTOL/TOETOL` 同时是**回滚开关**（文档里写死的「改成 999
  就等于整段不动」），上限若卡在「合理值」附近，回滚位本身会被 CFG 校验拦下 —— 这个矛盾是
  M2a 新写的 `test_rollback_switch_is_reachable` 在第一次实跑之前就抓住的。
- **CFG 的内容必须落在日志里**：`MMD2MAX_CFGSUMMARY` 在 `mmd2maxLoadCfg` 里拼好，由 `mmd2maxMain`
  **在 `mmd2maxInitLog()` 之后**打出。缺陷是 M2a 查出来的：`InitLog` 用 `createFile`（截断建文件），
  汇总行却早于它就写完了 —— 第十九轮及以前**任何一轮的日志里都没有「配置: …」这一行**，
  五个容差到底是不是我以为的那五个，日志回答不了。契约测试钉住两行的先后。
- **自检**：`obj_run.build_cfg` 断言 CFG 的键集合 == `CFG_KEYS`；`test_obj_run_guard.CfgContractTests`
  反向从 `.ms` 里 grep 出所有 `mmd2maxCfgStr|Bool|Int|Num "MMD2MAX_*"` 调用，要求与 CFG 覆盖同一份名单
  （两套独立实现，任一边漏一个键就红）。
- **CFG 读失败也留证据**：错误写进 `mmd2max_cfg.error.txt`（此时 `MMD2MAX_MARKER` 还没值，
  所以不能用 `<标记>.error.txt`）；`obj_run` 认这个文件，退出码 4，GUI 不许报「完成」。
- **判据的两张表也走同一条路（M2b）**：`MMD2MAX_VERDICTS` / `MMD2MAX_REPORT` 是 CFG 的两个键，
  文件名单源在 `verdicts.SPEC_NAME` / `REPORT_NAME`，`obj_run` 拼路径、`.ms` 只管往里写行。
  报告**每轮开头重建**（`mmd2maxReportInit` 用 `createFile` 截断），规格表由 Python 先写；
  两个键都不在 `CFG_EMPTY_OK` 名单里，也不许「没路径就跳过」—— 通道坏了这一轮就没法验收，
  退化成绿日志 + 没证据正是 §1 那个病根。契约测试两头钉：`test_obj_run_guard` 从 CFG 反查
  这两个键与文件名，`test_verdicts.MaxChannelContractTests` 拿规格表的 49 个通道与 `.ms` 里
  `mmd2maxReport "…" "…"` 的字面量逐一对撞，并要求 `.ms` 全程不出现 PASS/FAIL/verdict 字样。
- **报告的行序也是数据（M3）**：`phase=pass / check=ran / subject=<id>` 那 23 行不是装饰。Max 一路追加，
  所以**行序就是执行顺序**，`passes.audit` 拿它做覆盖 / 恰好一次 / `requires` 拓扑 / STALE / 归因 +
  闸门自洽五判定，`CHECK` 里独立成 kind=`timeline`（有问题 ⇒ `check_verdicts` 退 1 ⇒ `obj_run` 退 7）。
  `ran` 行排在该 pass **收工**那一刻，所以它天然是分段边界：一条读数落在哪个段里，就等于谁量的它。
  规格里没有 `pass/ran` 行的旧轮次（M3 之前冻结的基线）打一行 SKIP「时间轴审计未参与」——
  静默通过会被读成「审计过了」，那句话必须说出来。
- **退出码（`tools/obj_run.py`）**：0 = 产物齐**且判据全过**；2 = 用法/规格表来源缺失；
  3 = 报错弹窗；4 = error.txt；5 = 超时；6 = 有标记但 `.max` 缺失；
  **7 = 判据有 FAIL**；**8 = 两张表不可用**（缺文件、列名分叉、报告比规格旧）。
  7/8 是 M2c 新加的，刻意不复用 3~6：那是「Max 出事了」，这是「Max 没事但验收没过」。

手跑 `scripts/max_import_obj.ms` 与自动跑 `mmd2max_run.ms` 现在读到的是**同一份** CFG，
所以「手动 fileIn 摆出不同几何」这条路在结构上不存在了。


---

## 5. 流水线

```bash
# 1) 只做 Python 侧 + 生成 .ms，绝不碰 Max 进程（安全，随时可跑）
python tools/run_model.py "C:/Users/Administrator/Desktop/test3"          # 香菱-海灯节
python tools/run_model.py "C:/Users/Administrator/Desktop/test2"          # 克雷薇

# 2) 单独导出（末 4 个参数都可省）
python -m mmd2max.export_obj "<pmx>" "<outdir>" [scale|auto] [min_weight] \
       [mode=useful|full|humanoid] [accessory=nearest|hierarchy]
#   useful（默认）= 剔除 IK/物理哑骨/调试骨，权重归并到最近保留骨
#   full = 保留全部骨（v1 行为）；humanoid = 只留标准人形骨
#   accessory = 附属骨链根的挂法：跟随 MMD 层级 / 挂到几何最近的 Biped

# 3) Max 端装配（resetMaxFile 只作用于它自己新起的实例；跑完脚本末尾 quitMax）
python tools/obj_run.py <导出目录> <模型名> --shot [--physics] [--no-skin] [--manual] [--gen-only]

# 4) 判「这一轮有没有动几何」——用工具，别手搓 sed 裁时间戳
python tools/diff_round.py --allow "^配置:" baselines/round18_objrun.log test_runs/<模型名>/<模型名>_objrun.log
```

`--shot` 抓正/顶视图线框图到 `<导出目录>/shots/<输出名>_front.png`、`_top.png`（`<输出名>` = 模型名，带 `--no-skin`/`--physics` 时含 `_noskin`/`_phys` 后缀）。

**关于「先征询用户」**：2026-11-02 起用户已长期授权直接启动 3ds Max（理由：`obj_run` 新起的实例不会杀掉别的
Max 进程，也不点任何弹窗）。仍然成立的硬约束有两条 ——
① **MAXScript 报错弹窗只截图、绝不点、绝不关**（把现场留给用户看），
② 只有 `Startup Failure` / `Heads Up` 这两个**启动**弹窗按 UIA **确切按钮名**点白名单那一个，
`Restore Factory Settings…` 永远不碰。`taskkill /F` 只是 `quitMax` 没生效时的兜底（它会污染 Max 的崩溃计数，
攒够就每次启动弹崩溃框，把 `-U MAXScript` 自动执行整个挡死）。

### 5.1 图形界面（调参优先用它，别反复跑 CLI）

```bash
python tools/gui.py ["C:/…/克雷薇.pmx"]        # 标准库 tkinter，本机无 PySide6/PyQt
```

`export_obj` 已拆成两层，GUI 的即时预览就靠这个拆分：

| 层 | 作用 | 副作用 |
| --- | --- | --- |
| `build_plan(source, scale, **参数) -> Plan` | 全部计算：网格、骨骼、Biped 映射、各 TSV 正文行 | **无**（不碰磁盘，`tests/test_plan.py` 钉住） |
| `export(source, outdir, plan)` | 把 Plan 落盘 | 只写文件 |

所以「预览看到的数字/行 = 点导出后 TSV 里的内容」是**结构性保证**，`test_plan.py::test_plan_lines_are_exactly_what_gets_written` 逐行比对 7 个 TSV。

界面三块：
- **数字对照**：22 项指标随参数即时重算（200ms 防抖，计算在工作线程）；可「钉住为基准」看 Δ。
- **预览**：正/顶/侧三视图，骨杆按类别着色（Biped 命中 / 改挂链根 / 附属骨 / 被剔除的骨）+ 抽稀网格线框。手性（det=−1）在这里就能核：`左` 字骨一定落在屏幕右、身高读数应等于你填的目标身高 —— 不必开 Max。**注意** `plan.heads/tails` 已经是 Max 世界坐标（`bones.tsv` 的 `hx hy hz` 就是它们），预览直接取用，别再 `convert_vec` 一次（`tests/test_preview.py` 钉住这条）。
- **动作**：① 导出（只写文件）→ ② 生成 .ms（`obj_run --gen-only`，不碰进程）→ ③ 跑 3ds Max。③ 之前一定弹确认框，写明脚本会 `resetMaxFile #noPrompt` 清空场景、结束后 `taskkill /F /IM 3dsmax.exe` 强杀 Max，且**默认焦点在「取消」**（回车不许把 Max 拉起来）；跑完在「输出」页汇报 `.max` 是否存在、成功标记、Max 端报错文本、截图张数与目录。

排版两条硬约定（`test_gui_actions.py` 钉住，改 UI 别把它们改回去）：
- 底部横条（状态条 + 「动作」）一律 `pack(side="bottom")` 且**先于**中间伸缩区 pack。pack 是按申请顺序切空间的，顺序反了一缩小窗口第一个被裁到屏幕外的就是 ③ 按钮。
- 窗口下限取控件申请值（`minsize(body 宽, toplevel 高)`），不写死数字 —— 换台机器字体/DPI 不同，写死就会裁内容。
- 「输出」页字体用 Microsoft YaHei UI：Consolas 没有 ①②，日志里会画成「?」。

界面本身已用真窗口截图核过（1180x760 与缩到下限两种）：三块页面、按钮行、确认框文案与默认按钮都可见。截图脚本是临时的（`Temp/gui_shot.py`、`Temp/gui_dialog_shot.py`，后者用 `desktop` 抓屏并在 30s 内自动点「取消」），不进仓库。

无显示环境（或不想装 Tk）时 `tests/test_gui_actions.py` 自动 skip，其余测试照跑。

---

## 6. 硬知识（别再推翻）

- **手性**：保住「屏幕上的样子」只能选 det=−1；于是必须翻一次面，且**不能**再交换左右骨名。详见 `coords.py` 头注释。
- Max 2022 OBJ 导入器：Y-up `(a,b,c)` → Z-up `(a,−c,b)`。
- **Max 2022 OBJ 导入器只按面建顶点**：`v` 行里没被任何 `f` 引用的会被直接丢掉（UV 缝造成的**重复** `v` 行它不合并）。所以导出侧必须先紧凑化 + 重编号，否则权重表按 PMX 原序号写，从第一个孤儿顶点起整体错位（实测香菱 19212 行 → 网格 18892，差 320）。见 `export_obj._drop_unreferenced`，`plan.dropped_verts` 与 `_meta.tsv` 的 `dropped_verts` 是它的读数。
- 窗口探测：`find_windows("MAXScript")` 按**标题或类名**子串匹配，而插件浮动面板（BsKeyTools 等）的类名恰是 `MAXScriptDialog`、标题为空 —— 会被当成报错弹窗。判定必须要求标题非空且含 MAXScript、并排除 `MAXScriptDialog` 类（`obj_run.is_maxscript_error_dialog`）。
- **Biped 节点（包括 COM `$Biped_Object:Bip001`）没有 `.pos` 属性**：位置一律取 `.transform.translationpart`。凡是遍历 `objects` 的代码都可能踩 —— 01:10 香菱那轮就是取景用的 `mmd2maxSceneBox` 写了 `o.pos`，在 `saveMaxFile` 之前抛异常，整轮连 `.max` 都没落盘（文件里另外两处早就注释过这个坑，这是漏网第三处）。
- **Morpher 只有 0..99 共 100 个通道**；要全部表情只能分组存多份 `.max`。
- **`WM3_NumberOfChannels` 返回的是「已分配容量」，不是「已用通道数」**（实测：空 Morpher 报 100，7 个表情报 800，60 个报 6100，61 个报 6200 —— 每次正好 +100）。两个后果：① 通道号**必须自己数**（`built + 1`），旧写法 `WM3_NumberOfChannels + 1` 把 61 个表情全写到 101/201/…/6101 上，而 `WM3_MC_GetName 1..8` 读回来是 `- empty -`、`WM3_MC_GetTarget` 全是 undefined，日志却照样报 `Morpher 通道：61 / 61` 全成功；② 旧的自检 `if chTotal < blocks.count` 拿容量比表情数，恒不成立 = 根本没有自检。真判据分两级：`built == 0`（两个建通道接口逐个抛异常）才拆修改器，`built > 0` 但回读 `1..blocks.count` 一个目标/名字都拿不到时**只打警告、保留现场** —— 读接口在本环境通不通没探过，不能拿它当拆除依据。`tests/test_maxscript_contract.py::MorphChannelContract` 钉住这两条。
- **Morpher 的通道名 = 目标节点名**（08:14 实测：61 个通道建成，`WM3_MC_GetName` 回来的是 `default001..default008`，即网格对象 `default` 的副本自动编号名），`WM3_SetChannelName` 在本环境**61/61 全部抛异常**（08:20 实测；旧代码包在裸 `try catch` 里，所以这个事实藏了好几轮）。要把表情名（あ/い/う…）落到通道上，只能在 `copy` 之后、`BuildFromNode` 之前先 `target.name = 表情名` —— 普通属性，不赌任何 WM3 接口；08:20 轮 `有目标=61 有名=61` 就是这么来的。顺带一条：**自检里「有名」的判据必须是「逐个等于表情名」，按「非空」判会被 `default001` 蒙过去**（这正是 `61/61 + 有名=61` 那轮看起来全绿的原因）。
- Biped：`createNew` spineLinks 1–5、neckLinks 1–25、legs 3–4、fingers 0–5、fingerLinks 1–4、*TwistLinks 0–10（默认 0）；`triangleNeck` 默认 false（锁骨挂脖子根，转头不带肩）；`angle` 的零点是 **+X**；`setTransform #scale` 只在 **Figure 模式**有效；`setTransform #pos` 只挪 pivot、**不改骨长**（所以肢长必须靠 #scale 闭环拟合）；`biped.getTransform node #rot` 在 Max 2022 会抛异常，取轴要用 `.transform` 行向量；**转骨头用的 token 是 `#rotation`（不是 `#rot`）** —— Max 自带 `C:\Program Files\Autodesk\3ds Max 2022\scripts\BulletScripts\BsRetargetTools.ms:1828` 与 `BsBipedTools.ms:1496`（`Straighten`/`SpreadFingers`/`BendFingers`）全是 `biped.setTransform node #rotation <quat> <bool>`，值给的是**全局转角**（`源骨.transform.rotation`，或 `补转角 * 父骨.transform.rotation`），`Straighten` 就是拿它给 Toe 补那 90° 的；`#rotation` 只在 **Figure 模式**里改的是结构性姿态，退出之后同一条调用就成了做动作（插 key）。**10:17 探针实测**：`#rotation` 确实是被接受的 token（喂 point3 报 `Unable to convert: [0,0,30] to type: Quaternion` ⇒ 只吃 quat），喂 quat 不抛错但局轴 `q=0.0度`。**10:34 第三轮把这条推翻了**：那次的 `q=0.0度` 是**假数** —— `eulerToQuat` 只吃 `EulerAngles`，`eulerToQuat [0,0,30]` 自己先抛 `Unable to convert: [0,0,30] to type: EulerAngles`，quat 压根没构造出来，`#rotation` 从来没被真正调用过一次。正确写法是 `eulerToQuat (eulerAngles 0.0 0.0 30.0) order:1`（`BsBipedTools.ms:1491` `SpreadFingers` 就是这么写的）。**教训：任何「接口不通」的结论都要先看它抛的是哪一段** —— 10:17 那轮没打 `q抛错=`，把参数构造失败读成了接口失败。**同一轮的另一条实测**：在 **Figure 模式内**，`plf.transform = rotateZMatrix 30.0 * plf.transform` 读到 `0.0度/留=0.0度`（Biped 直接把整块 TM 写回算掉），而 `rotate plf (angleaxis 30.0 [0.0,0,1.0])` 读到 `30.0度/留=30.0度`（局轴真动，且 `biped.setTransform #pos` + `update 0` 刷新后仍在）—— 所以探针采用 `#rotateCmd`。**注意 `rotate` 是增量算子**（轴+角），吃不了绝对矩阵，所以 `#rotateCmd` 这条路必须走「绕 `当前轴 × 目标轴` 的轴转 `夹角`」，正负各试一次再取更小的那个残差；10:34 那轮 `mmd2maxSetBoneRot` 把 `#rotateCmd` 误实现成写 TM，于是 `转角 pass：接口=rotateCmd 尝试=35 改善=0 未改善=35 抛错=0`（回滚逻辑生效，场景没被改坏）。**11:11 结案**：quat 用对形式之后 `rotation … q=120.0度/留=120.0度`、探针 `采用=bipedRotation`、真骨架 `尝试=36 改善=34 未改善=2`、`结构向 复核` 的超标 **35 → 2** —— Biped 的单骨转角旋钮是存在的，`biped.setTransform node #rotation <世界quat> <bool>` 就是它，前三轮所有「转不动」的读数都是参数构造失败的假否决。副作用是**转角绕骨自身 pivot 转，会把子骨关节甩出 PMX 的 head**（同轮 `Neck 4.90352 / Head 4.15381 / L Toe0 5.99721 / L Finger32 7.82856`），所以顺序必须是「先转方向、再摆落位」，`#pos` 不吃转角已由探针证实。
- **`getNodeByName` 不支持 `*` 通配符**（10:17 `hand 取景：手指节点=1` 就是这么来的）：`getNodeByName "Bip001 L Finger*"` 返回 `undefined`，不报错、不返回数组，所以那段取景静默只剩 Hand 一个点、`if handPts.count >= 2` 不成立、框退回整场景边界，`hand_front`/`hand_side` 两张特写等于没拍。按名字模式收节点只能自己 `for n in objects` 筛 `classOf n == Biped_Object` 再 `matchPattern`。**10:34 又踩第二层**：`matchPattern` 的第二个参数是**带标签的** `pattern:` —— Max 自带脚本里全是 `matchPattern s pattern:"*"`（`BsOpenTools.ms:72`、`MotionReverse_Tool.ms:165`），位置写法 `matchPattern n.name "Bip001 L Finger*"` 不抛错、恒返回 false，于是那轮 `手指命中=0`。另外本环境 **没有 `isNode`**（`-- Type error: Call needs function or class, got: undefined`），判节点用 `!= undefined` / `classOf … == Array`；逐节点 `try catch` 会把异常咽掉、日志只留一条「取点异常」，所以取景段现在额外打 `Biped节点=` 计数。
- **Biped 的 Pelvis pivot 只跟 COM（Bip001 对象）走，`setTransform <Pelvis> #pos` 实测无效**：香菱给腰 z=83.44，Pelvis 仍停在 センター 的 68.29。髋关节挂在骨盆盒上，骨盆低 15cm 就把整条腿拽低，而 `setTransform <Foot> #pos` 是按 link 长度硬约束的 —— 于是踝被摆到正确高度、膝只能折起来（腿段偏差报表里 Thigh 13.7 / Calf 17.3 就是这么来的，链长本身 36.38/38.07 全对）。唯一的把手是 COM：`createNew` 用腰的位置，摆位后再按「Pelvis 实际 vs 目标」平移 COM 并重摆一遍（`mmd2maxFitPelvis`）。**摆位名单里绝不能有裸 "Bip001"**，否则下一轮会按 センター 把 COM 压回去（`tests/test_maxscript_contract.py` 钉住）。
- **骨盘子段（L/R Thigh、Spine）的期望长度按「髋中点」量，不照抄 PMX 腰→子骨**（`mmd2maxHipAnchor`）。COM 被抬到髋中点后，Biped 只会绕 COM 转这两节 link、长度受 `#scale` 硬约束：照抄 9.03 就把两髋对称撑到 ±8.97，而 PMX 要相对髋中点 ±7.0013、dy/dz 全 0（实测左右各偏 2.2288）；`Pelvis -> Spine` 同理，表里 6.49、摆位后只剩 3.51。这条改写依赖 `limbs.tsv` **每行**都带 `child_x child_y child_z mode`（普通段 mode=link）—— 曾经只有脚段补这 4 列，childPos 恒为 undefined，改写静默不触发，日志却全是「成功」。
- MAXScript 的调用是**解析期绑定**：函数体里裸调 `f a b`（或写 `f(a)`）时，`f` 必须**已经定义**，否则运行期拿 undefined 去 apply → `-- Type error: Call needs function or class, got: undefined`，而 `classof f` 事后一查仍是 `MAXScriptFunction`。这就是截图段查了三轮的真凶：`mmd2maxSaveDib/ShotOne/Screenshot` 排在 `mmd2maxLog` 之前，函数体第一行日志就炸，所以「开始抓取」之后一行都没有。日志三兄弟现在钉在文件开头，`check_ms.check_forward_reference` 负责拦。
- **`setTransform #scale` 是三根轴各自的比例，不是厘米**：要把一节骨头的盒尺寸落到某个 cm 值，只能「量本地包围盒跨度 → 单轴乘 target/cur → 复测」闭环（`mmd2maxFitBoneExtent`，4 次上限 + 不收敛就停）。Toe0 就靠它按 Python 量好的三个厘米目标逐轴归位（趾长 / 脚尖处宽 / 脚尖处高，长度最后缩 —— 先缩长度会被宽高的二次缩放带跑）。鞋的量法必须由「踝→脚掌」方向推，不能取「+y 最大」：手性修正后脚掌指向 **−y**，+y 最大命中的是脚后跟/裙摆（香菱因此 toe_len=12.28 > 整只脚 11.23，Toe0 被撑成一根大骨头；改对后趾长 3.57）。
- **⚠️「Biped 的骨盒绕骨轴中心对称」是错的（第十四轮 `nodeGetBoundingBox node node.transform` 在 `Bip001 L Foot` 上实测推翻）**：脚骨自己坐标系里长度轴 Y ∈ [−2.37011, +7.93483] = **[−21.11%, +70.67%] × 段长**，竖直方向 X ∈ [−1.44334, +8.05423] = **15.2% 在轴上方、84.8% 在轴下方**，只有横向 Z 是对称的；`#scale` 逐字保持这几个比例。#19/#37 一直按「上下各一半」量，把一只本来就挂在轴下方的盒子塞进对称余量，取 min 后只剩 1.53055 —— 就是用户那张「一根牙签板斜搭在脚背上」（#39）。正确的量法：高 = 下方净空 ÷ 0.848（盒底贴鞋底，上方那一小条允许伸进靴筒），沿轴窗 = [−21.11%, +70.67%] × 段长（盒尾自己就往后探出 2.37cm）。**「脚跟靠它框住」这句只对平轴成立，#40 之后轴回到斜轴，盒底是斜的、探到踝那一端就抬到 z=7.0，框不住脚跟了**（详见 §7 第 22 条那个「装不下」的证明）。**Toe0 那一路（`_foot_profile`）仍是上下各一半的口径，没有对趾骨做过这个回读**。
  同一条上还留着两个旧结论，只对**趾骨**成立、对脚骨已经不成立：照「与 Foot 末端同高」量出来的截面是**脚掌那一片**的（香菱 4.19cm），而鞋面是一条向前下坡的曲面，盒子前端就戳出鞋面（实测上沿凸出 1.66cm）；改成沿趾方向**切三角形横截面**取最低鞋面（`_toe_sections`，采样到 0.9×趾长，圆头收口不算）后是 1.86cm，凸出降到 0.5cm。两条踩过的坑仍然有效：① 按顶点分带不够用 —— 香菱整只鞋落进窗口的顶点只有 266 个，0.5cm 一带只剩 2~14 个点，max/min 会被单个装饰顶点带跑，必须插值出截面；② **宽度不能照同样的严格口径逐截面取**：鞋头向前收窄又向外偏，笔直的趾骨永远进不去，逐截面取紧会把趾骨压成牙签（香菱只剩 0.12cm），所以宽度取「整段跨度的最外侧」（正视轮廓包住即可）。
- PMX：骨变形含 **vec4**；关节 `type` 是 **u8**；面**顺时针为正面**；`左` 的 x>0（左手系）。
- MAXScript：`off/ok/mapped/total/mod/fn` 等保留字不能当 `local`；`if…do` 没有 `else` 分支；`localTime` 是变量不是函数；`animationRange.end as integer` 是 ticks（160/frame）；`.ms` 必须**无 BOM** UTF-8；`queryBox` 在批模式下要绕开。
- Windows 多进程：worker 必须模块级 + `if __name__ == "__main__":`，否则 spawn 重跑主脚本 → `BrokenProcessPool`。
- 大网格 + 数百骨的 Skin 会卡死，所以 `--no-skin` 冒烟先跑；权重写失败/顶点数不符一律抛错，绝不写 success 标记。
- **Max 2022 视口 API（三轮探针实测，`Temp/mmd2max_probe/viewport_probe*.ms`，别再凭印象写）**：`viewport` 结构体有 `ZoomToBounds <bool> <Point3> <Point3>`（**要两个角点，不是 box3**；`zoomToBounds false b` 报 Argument count error）、`Zoom <k>`（视野尺寸 ×k）、`SetFOV/GetFOV`、`SetShowEdgeFaces/GetShowEdgeFaces`、`SetTransparencyLevel`、`IsWire`、`SetType/GetType`、`SetTM/GetTM`、`activeViewport/activeViewportId/numViews`、`getViewportDib`、`getHWnd`、`SetBlowupRect`、`SetRegionRect`、`resetAllViews`；**没有** `getViewSize/setViewSize/setZoomRegion/setViewport`，也没有 `maxOps.zoomExtentsAllViews`。节点级 `o.wire`、`o.seeThru` **不存在**（`o.isHidden` 存在）——「让骨架看得见」只能用 `viewport.SetShowEdgeFaces true`（实体上叠三角边）+ 把网格 `isHidden`（只留骨架）两招。`max zoomextents all` 这类 MAX 命令在脚本里根本编译不过（Compile error: Unrecognized MAX command）。
- **抓图前必须 `completeRedraw()`**：`gw.getViewportDib()` 取的是**上一帧**，探针 v4 缩放前后的两张 PNG 字节级相同，等于白抓。`zoomToBounds` 只把区域「撑满」画面、**不留边距**（128cm 处的头顶块正好被裁在画面外），所以取景后要再 `viewport.zoom 1.35`。
- **位图落盘**：`bmp.filename = path` 后 `save bmp` 可用；`save bmp fileName:path` 在 Max 2022 抛「Bitmap requires a fileName for save()」。判是否真落盘**只能**比对 `(dotNetClass "System.IO.File").GetLastWriteTime path).Ticks` —— `getOsProperty / fileLength / getSecurityAttribute` 三个「按理该有」的全局函数统统不存在，一调就是 `Type error: Call needs function or class, got: undefined`；而 `doesFileExist` 会被上一轮的同名旧图蒙过（实测日志 `落盘=true`、PNG 时间戳停在 19:45）。
- **脚骨偏航是这套校验的盲区（#26）**：`pivot 落位` 与 `两点距离` 在脚骨上是**恒等式** —— Toe0 的 head 就是 Foot 的 tail，把它摆到目标，`段校验 Foot -> Toe0` 自然等于目标长度，所以 08:20 那轮 `pivot偏差=9.6e-07`、`段校验误差 3e-05`、`校验 Bip001 R Foot 偏差 3.9e-05` 全绿，而用户截图里 `Bip001 R Foot` 整根朝人物中线偏。唯一能看见这个错的量是**夹角**：实际 (Toe0head - Foothead) 与目标 (つま先 - 足首) 的 `acos(点积)`。另外 limbs 里 Toe0 被 `flatChildren` 排除在摆位名单之外，最终位置校验也不覆盖它 —— 双重盲区。
- **Biped 只有一个全局偏航旋钮，它必须取两脚的角平分线**：`biped.createNew` 的 `angle` 是整副骨架的朝向，`setTransform #pos` 摆的是落位、摆不出单根骨头的偏航。香菱两脚各外八 6.69°，旧代码（`max_import_obj.ms` 实测朝向段）只按 **左脚** 测 → 骨架拧向左脚方向 → 左脚正好、右脚偏 13.4°（Front 视图投影 11.4°，与用户红/蓝箭头的张角吻合）。改成两脚单位向量相加再归一，误差才对半摊到左右各 6.7°。
- **网格跟的是 PMX 的 `tail - head`，不是「head → 下一个 head」（#27）**：PMX 每根骨自带 head 和 tail，**显示方向 = tail − head**，网格是照着它做的，用户眼里的「结构」就是它；而脚本只把 pivot 摆到 head，Biped 画出来的段是 head → 下一节的 head，**这两个方向是不是同一个只有量局轴才知道**（`#pos` 摆位、`段校验` 距离、`校验 … 偏差` 落位全都答不了方向）。离线按 `香菱_bones.tsv` 算过：手指链其实**是同一个方向**（左中指１ 的 tail 就是左中指２ 的 head，PMX 手指按 head-to-tail 串），躯干链也是（上半身2 的 tail = 首 的 head ⇒ 画出来倾 +17.42°，与 PMX 结构倾完全一致）。所以这张表真正的用途是判一件事：**骨盒到底跟不跟 pivot 转** —— 全表读 0 度 = 跟，用户的抱怨就落在别处（0.5cm 落位差 / 掌心 / 白锥）；手指或躯干读出大夹角 = 不跟（#26 的脚骨正是这个机制），那时要修的是**转角**，而转角的写法已经查到手了（Max 自带脚本用的是 `#rotation` + 全局 quat，见上面 §6 Biped 那条），脚本里仍然不猜死：探针在临时骨架上现场选写法、`转角 pass` 只在探针通过时才动真骨架。三类天生不可比的段要知道：`Bip001 L/R Hand`（一父带五子，方向没定义）、掌心跨度（腕→各指根，PMX 里没这根骨）、`Bip001 Spine`（PMX 下半身 骨指向下、Biped 段指向上，预测差 169.9°）。可量的拆法是把三维夹角投影到两个面 —— `mmd2maxLeanDeg v = atan2 v.y v.z`（矢状面，+ 后仰 / − 前倾）与水平偏航 `atan2 v.y v.x`；三维夹角会把「后仰 17°」和「绕竖轴转 17°」混成同一个数，读了也没法对着截图讲。
- **但「显示方向 = tail − head」对脚链不成立，一刀切会把脚转进地里（11:11 之后用户反馈）**：香菱 `足首` 的 tail 就是**踩地点**（`左足首` head z=7.53、tail z=1.94，比踝低 5.59cm），三维方向天生下扎 30°；`つま先` 的 tail 又从踩地点**竖直向上 7.66cm**（和上面那条 `指先` stub 同一个约定）。转角 pass 照着 tail−head 转完，`转角 Bip001 L Foot 夹角=30.5448->0.0`、`结构向 … 目标倾=-120.042` —— 骨盒从原来的水平（`实测倾=-90`）被拧成插进地板，趾盒竖直朝天，用户看到的正是「两边的脚也没有落在地面上，Y没有90度」。**修法 = 脚链单独定判据**（`mmd2maxBoxWantDir`）：`* Foot` 用 PMX 来向的**水平投影**（偏航原样保住，#26 修好的外八不会丢；俯仰钉成 ±90），`* Toe0` 用**网格实测鞋尖**的水平投影（PMX 那根是竖直 stub，没有方向信息）；压不出水平分量时返回 `undefined` = 这根骨整段跳过并打一行「转角不做」。转角、`结构向` 复核、`画线` 复核三处**必须共用这一个判据**，否则「脚修平了、复核仍报它 30 度」，下一轮就会照着那个数把地又踩穿。`mmd2maxFlatDir` 返回时保留原长度，因为 `结构向` 靠 `length want < 1.0` 判短骨 stub，单位化会把脚误判成 stub 而**静默不计入超标**。
  > **⚠️ 这条的「压平」半边第十三轮翻掉、第十五轮翻回来、第十六轮（#40）又翻回去（见 §7 第 19~22 条）**：第十三轮的理由是「压平留下骨盒被钉成踝高度的水平板、前端离趾骨盒差 5.6cm 高」—— 第十四轮回读证明那只账记错了对象：盒子本来就 84.8% 挂在轴下方，是 Python 按对称量把它压成 1.53cm 的牙签板，与俯仰无关。按真实摆位重算后盒高要 8.66 才够到鞋底，斜轴容不下（盒底顶穿鞋底 5.74cm、盒角从脚后跟后面戳出 2.7cm），**压平才装得下**，同时正好回到 #28 那句「平踩地面」—— 于是第十五轮压平。**#40 把这一半又翻回来了，因为压平有一个没被任何校验看见的代价：骨盒的底面永远垂直于骨轴，轴一压平，盒子就不再指向自己的子关节**（踝→球 的俯仰是 29.93°，压平后 `链向 Bip001 L Foot -> L Toe0 骨轴与子关节夹角` 会读 29.93 度、垂距 5.59cm = 踝比脚掌高出的那一截）。所以现在的口径 = **脚链用踝→球的三维方向（斜轴）**，`mmd2maxBoxWantDir` 里 `if not MMD2MAX_FOOTPITCH` 那一支降级成回滚位；45 度斜度上限继续留着（斜度陡过 45 度判成「tail 是关节/踩地点约定」）。**开关从这一个变成两个，各管各的**：`MMD2MAX_FOOT_HEELBOX` 管「高/量带」（False ⇒ 第十四轮的对称内切），`MMD2MAX_FOOT_FLATTEN` 专管轴的俯仰（True ⇒ 第十五轮那个框住脚跟、但链断开的水平板）；`meta.tsv: foot_pitch` 现在只写 `MMD2MAX_FOOT_FLATTEN` 的反值。**仍然成立的两半**：脚链必须单独规定「取哪两个点」（Toe0 用网格实测鞋尖，PMX 那根是竖直 stub），以及三处判据必须同源、`want` 必须保留原长度。
  >
  > **（#41 更正 + M1b 改名，这段是本节的当前口径）**：① 上面「现在的口径 = 斜轴」已经被 **#41 又翻回压平**，理由是「平轴必断链」的前提（盒子沿轴的长度改不了）被探针推翻 —— 长度轴乘 k 时盒远端与子节点同步乘 k，所以 Max 端 `足盒接趾` 能把平轴那只厚盒子的前端面拉到 PMX 球部，链就接上了。**当前默认 = 压平（`foot_box_flat = true`），而 `盒隙结论 没接上=0 根` 才是这一版脚链的真判据**（见 §6 第 3 条）。② 这一节里的开关名在 M1b 之后全部作废，改名对照：**`MMD2MAX_FOOT_HEELBOX` → 导出侧 `FOOT_HEELBOX`**（管高/量带，未进 CFG）；**`MMD2MAX_FOOT_FLATTEN`（导出侧）+ `meta.tsv: foot_pitch` + `MMD2MAX_FOOTPITCH`（Max 端注入）三层 → 一个词 `FOOT_BOX_FLAT` → `meta.tsv: foot_box_flat` → CFG `MMD2MAX_FOOTBOXFLAT`**，同向不反转；`MMD2MAX_FOOTREACH` / `MMD2MAX_GAPTOL` 当时仍是 Max 端全局（M1b 只合并了「每条链上都参与取反」的那三个，其余留在脚本内），**M2a 之后这两个连同 `DIRTOL/ROLLTOL/TOETOL` 一起进了 CFG**，来源是 `mmd2max/targets.py`（见 §4b 与 §7 第 25 条）。翻回滚位现在意味着改 `mmd2max/export_obj.py` 里那一个词然后**重跑导出**，因为 CFG 是由 meta 生成的，直接编辑 `MMD2MAX.CFG` 会被下一次导出踩掉；唯独上面那五个容差不走 `export_obj.py`，改 `targets.py` 里的一个数字再跑 `tools/obj_run.py` 即可（同样别手改 CFG）。
- **骨盒「指不指向自己的子关节」是 pivot 落位与两点距离永远量不出来的（#40，项目记忆里「恒等式校验看不见偏航」的第二次）**：`段校验 Foot -> Toe0 误差=6.7e-06`、`校验 Bip001 L Foot 偏差=6.5e-06`、`脚趾复摆 摆后=1.9e-06` 三行在第十五轮全绿，而用户截图里 Toe0 的盒子明摆着没接上 Foot 的盒子末端 —— 因为 **link 的末端天生就是子关节的 pivot**（Biped 的 IK 结构保证），量它等于量自己；被转走的是**画出来的盒子**，它躺在 `mmd2maxBoxWantDir` 返回的那根轴上，而那一轮所有脚相关的复核（`转角` / `画线` / `结构向` / `脚骨偏航结论`）比的目标**也是同一根压平的轴**。修法不是再补一条日志，是换一个**独立**的量：`链向 <Foot> -> <Toe0>` 拿 `mmd2maxBipedAxes fnode` 三根局轴各自与 `cnode.transform.translationpart - fnode.transform.translationpart` 求夹角、取最小，>3 度记一根「断开」，并打 `链向结论 断开=N 根（门槛 3.0 度）`。这个量**不许**调 `mmd2maxBoxWantDir`（调了就又变成自己跟自己比），契约测试 `test_foot_chain_continuity_readback_targets_the_real_child` 钉住这一点。
- **不用开 Max 就能核实场景里到底有哪些节点名**（回答「这根骨叫什么」这类问题够用）：`.max` 末尾的 `Objects` 汇总段把每个节点名写成 `00 <len:int32LE> <UTF-8 字节>`，用 UTF-8 解码即可（`香菱.max` 190854000 附近）。三条踩过的坑：① `grep -I` 会因为二进制直接跳过整个文件；② **别拿 GBK 解** —— 那是 UTF-8 字节当 GBK 读，`右腕` 会变成 `鍙宠厱`，看着像乱码其实文件是好的（本轮差点据此报一个「附属骨全名乱码」的假缺陷）；③ 名字在文件里另有 UTF-16 形式（节点实体段），两处计数不同不代表名字有问题。实测 11:11 那具场景里 `Bip001 L Clavicle` 与 `Bip001 R Clavicle` 都在、就是这俩名（`左肩/右肩` 是 PMX 骨名，脚本只给 BoneGeometry 改名，Biped 节点名一律保留：`if (classOf nodes[i]) != BoneGeometry do continue`），全场景没有任何名为 `FGGG` 的对象，`WeaponL`/`WeaponR` 是模型自带的两根 ASCII 名附属骨（挂在左右手臂链上）。
- **`指先` 骨是模型自己的显示 stub，不是结构**：香菱共 10 根（左右各 5，全部没有 Biped 映射），实测每根的 `tail - head = (0, 0, +7.656)` —— 笔直朝 **+Z（正上方）穿出指尖 7.66cm**，而真指节只有 1.37~2.62cm；`左中指先` 的 head 就等于 `左中指３` 的 tail。用户截图里那些**白锥**就是它们（我们按 PMX 原样做成 BoneGeometry），黄色 Biped 指链停在最后一节的 pivot 上。所以「手指对不上白锥」不能当成指链转角的证据 —— 白锥本身就不沿着手指；判手指贴合要拿**指链 vs 网格轮廓**（新的 `hand_side` / `hand_front` 特写）。09:37 的局轴表进一步说明黄节自己也偏：`结构向 Bip001 L Finger1 轴1 … 实测倾=179.988 差偏航=4.16593`、`… Finger4 轴1 … 实测倾=179.988 差偏航=-15.6435 夹角=13.5473` —— 骨盒笔直朝下、各指水平还差 4°~16°，而白锥笔直朝上，两者本来就该分叉，用户「手指没贴合」看到的正是这条分叉，不能拿白锥当方向基准。
- **校验名单写死 = 没打印被当成没问题**：最终那段 `校验` 只遍历 19 个硬编码名字，`Bip001 Spine1` 和 30 根手指骨从来没进过检查，日志里没有它们 ≠ 它们是对的（#27 用户的三条反馈有两条正好落在名单外）。复核必须遍历 `targetNames` 全部行（只排除 COM `Bip001`，它由骨盆闭环负责），并把「量不出来」计成 -1 而非 0 —— dummy 骨的 tail 与 head 重合，返回 0 度就是假绿。
- MAXScript 另有三条**编译期**陷阱（一条错就整份文件载入不了，而且错误框挡住自动执行）：`fn () (...)` 匿名零参函数非法（`Syntax error: at (), expected name`）；链式调用后紧跟属性 `viewport.getTM().translation` 非法（`Syntax error: at ., expected <factor>`），必须先 `local t = viewport.getTM()`。零参调用要写成 `f()`，裸 `f` 是**函数值**（拼进字符串报 `Unable to convert: p3Fov() to type: String`）。**`fn f a b [x:false] =` 这种可选参数写法非法**（13:06 实测 `-- Syntax error: at [, expected =`：解析器把 `false` 读成类型名、然后等 `=`；带类型的 `[x:boolean=false]` 本项目没验过，别再拿一轮去试）—— 要开关就加**位置参数**，调用点显式传值。三条分别由 `check_ms.check_empty_fn_params` / `check_chained_call_property` / `check_ms.check_optional_params` 拦。
- **启动弹窗只能按按钮名点，且根因在退出方式**：`Startup Failure Detection` 是 Qt 模态框（实测 0 个子 HWND），它把 `-U MAXScript` 的自动执行整个挡死。旧代码「截图找最右侧色块 + 按坐标 click」有概率点到 `Restore Factory Settings…`（真会清零用户的 Max 设置）；现在走 UIA：`tools/uia_dialog.py` 按**确切按钮名** Invoke，且只认 `obj_run.SAFE_STARTUP_BUTTONS` 白名单（`Continue Without Restore` / `Heads Up!` 的 `Continue`），名字对不上就只记日志、什么都不点。根治办法是让装配脚本跑完自己 `quitMax quiet:true`：`taskkill /F` 被记成异常终止，`HKCU\Software\Autodesk\3dsMax\24.0\MiniDump\24.3.0.3404` 的 `SessionStartCount` 与 `SessionCleanCloseCount` 差得越多就越必弹（实测曾 167 vs 50）；干净退出后两者追平，下一轮启动直接跑脚本。裸 `quitMax`（不带括号）在 `-U` 模式下实测退不出去。

---

## 7. 状态：已验证 vs 待验证

**已验证（Python 侧 + 真模型导出）**
- 香菱-海灯节：24921 顶点 / 32897 面，骨 610→577，morph 通道 60，刚体 493 / 关节 860，肢段 38，手部粗细 12；尾部不变式满足（此前直接崩的文件）。
- 克雷薇：刚体 363 / 关节 700，肢段 24，手部粗细 6。
- 左右映射：`左腕/右腕/左足/右足` 与几何同侧，未交换；GUI 预览层同样核过（`左` 字骨 100% 在屏幕右，克雷薇正视图身高读数 160.0cm）。
- 187 单测全绿（`tests/test_preview.py` 管投影视图与骨杆同源，`tests/test_gui_actions.py` 管动作区：①②只写文件、③必先确认且答“取消”就不起进程，外加两条排版契约；`tests/test_plan.py::VertexCompactionTests` 管未引用顶点紧凑化后 OBJ/权重/morph 三方序号一致；`tests/test_obj_run_guard.py` 管报错弹窗判定与退出码（含「别的 Max 实例留下的窗口不算报错」）；`tests/test_maxscript_contract.py` 管 COM/骨盆闭环、截图日志、Toe0 三轴闭环、Morpher 通道连号 / 真表情名 / 内容回读自检（`MorphChannelContract`）、脚骨偏航（`FootYawContract`：角平分线取值 + 退出 Figure 模式后才量夹角）、结构方向复核（`StructDirectionContract`：目标方向由调用方传入（多数骨就是 PMX 的 tail−head）、遍历全部映射节点、拆成倾角+偏航两个数、短骨单独计数不算红灯、脚本里不许出现 `isNode`、手部取景必须走 `matchPattern … pattern:"…"` 而不是 `getNodeByName` 通配符）、旋转接口探针与转角 pass（探针只碰必删的临时骨架、判据是局轴转角而非「没抛错」、**立刻转了 + 刷新后仍在**两条一起卡、必须先在 Figure 模式内测并在退出后补对照、quat 必须用 `eulerToQuat (eulerAngles …) order:1` 构造、通过的写法交给 `转角 pass` 且整段受探针门控、`#rotateCmd` 走的是**增量 `rotate` 命令**而不是写 TM（正负角各试一次取残差小者）、矩阵写法必须按原行长缩放回去并保留平移、写完必须回读且不许留下更差的姿态、转角必须排在退出 Figure 模式之前、**转角之后必须重摆一次 pivot**（`#rotation` 绕自身 pivot 转，父骨一转就把子骨关节甩出 PMX 的 head：11:11 `Neck 4.90 / Head 4.15 / L Finger32 7.83`，而 `#pos` 重发不吃转角））、脚链方向单独定判据（`StructDirectionContract` 新增三条：`* Foot` 用 PMX 来向的**水平投影**、`* Toe0` 用网格鞋尖的水平投影、压不出水平分量就 `undefined` 不转；`mmd2maxLogStructDir` 的方向必须**由调用方传入**而不许自己 `tailPos - headPos`；转角 / 结构向复核 / 画线复核三处必须共用同一个判据；压平后必须保留原长度否则短骨判定把脚误判成 stub）、前向引用等脚本契约；`tests/test_limbs.py` 管肢段表列数、鞋的量法与颈轴量法）；生成的 `.ms` 过静态体检。
- 香菱（用户实跑 ③ 暴露的缺陷）：`-- Runtime error: 网格顶点数 18892 与期望 19212 不一致` → 根因是 320 个未被面引用的顶点被导入器吞掉；重导出后 OBJ `v` 行 18892 = 权重行 18892 = `meta.vertex_count`，面索引无越界、morph 偏移无越界，`dropped_verts=320`。
- 顺带修掉两个「骗人」的观感：插件浮动面板被误判成 MAXScript 报错弹窗（每轮白喊一次并保留 Max 现场）；`obj_run` 在既无 `.max` 又无成功标记时仍返回 0，GUI 横幅因此写「完成」。现在退出码按产物判定：0=标记+.max 都在，3=报错弹窗，4=error.txt，5=超时/自行退出，6=有标记但 `.max` 缺失。
- #17 颈部：`_center_neck` 不再猜「上半身↔頭 的中点」，改成量**颈部网格实测中线**（`_neck_axis_y`：band 内按 1cm 分层，每层只取 |x| ≤ 该层半宽中位数的顶点，再对各层前后缘中点取中位数），偏离 > 1.5cm 才动、单次最多 3cm，`_balance_center_chain` 整条删除。香菱复跑：`首` y 从 -3.06 回到 PMX 原位 -0.04（实测颈轴 +0.08，差 0.12 → 规则不触发），`上半身` 回到 -5.09；躯干链逐节对上网格中线（Spine2 处 Δ≈0.1、Neck 处 Δ≈-0.2），胸腔中心成为上链最靠前的点 = 用户要的「带一点挺胸」，不需要额外转角。侧视对照图 `test_runs/香菱/preview_neck_before_after.png`。

**已验证（完整 3ds Max 实跑，两份都写了成功标记）**
- 香菱-海灯节（17:59）：24921 顶点 / 577 骨全部进 Skin，权重失败顶点 0（写入 29s），肢段拟合 38/38、累计误差 0.095，手部粗细 12/12，摆位 50/50，Morpher 60/60 通道，652 对象，`.max` 263MB。
- 香菱（18:29）：18892 顶点 / 325 骨，权重失败 0，肢段 39/39、误差 0.134，脚掌两段收敛（趾长 12.28 = 目标），Morpher 61/61，401 对象，`.max` 193MB。
- 这两轮的偏差报表暴露出**只有实跑才能发现的两个结构问题**（不是拟合没收敛，而是 Biped 根本不接受那次 setTransform）：① 骨盆低 15cm → 髋低 → 膝折叠（见 §6 硬知识，已按 COM 闭环修）；② 脚趾 EX 辅助骨与撑满鞋头的 Toe0 重叠（已按骨名整段剔除，香菱 356→321 骨）。

**已验证（3ds Max 实跑 · 香菱 `--no-skin --shot`，01:15 那轮，退出码 0）**
- 手性观感：Front 视图看到的是**脸**（眼睛/嘴清晰），角色自身左侧（`L Foot` x=+5.33）落在屏幕右；网格正面无破面、着色正常。
- 骨盆闭环后的腿链：`Pelvis -> Spine` 目标 3.50962 / 实际 3.50962；`L/R Thigh` 目标 ±7.00125 → 实际 ±6.90849（偏差 0.045，不再是对称撑到 ±8.97）；`Calf -> Foot` 38.0685 误差 7.6e-06；两腿笔直落地，膝折叠消失。`Pelvis` **pivot** 仍比目标高 5.7cm —— 这是 Biped 的既成事实（pivot 只跟 COM，髋关节挂在骨盆盒上），要看的量是髋中点，`Biped 摆位: 髋中点残余偏差=2.4e-06`。
- `--shot` 截图：9 张全部落盘（`front/side/top/bone_front/bone_side/neck_side/neck_front/foot_side/foot_front`），日志逐张有「截图开始 / 视口已缩放 / 取图 class=BitMap / 落盘=true」，`shots/` 里文件时间戳就是本轮时间 —— mtime 判据、`completeRedraw`、`zoomToBounds`、`SetShowEdgeFaces`、`isHidden` 五条 API 全部实测有效。
- 非交互跑完 `quitMax quiet:true` 自己退出：上层日志出现「3ds Max 已正常退出（崩溃计数未被污染）」，整轮没有 `taskkill /F`。
- #17 颈部：`校验 Bip001 Neck 偏差=0.0`，落在 y=-0.0366（Python 实测颈轴 +0.08，差 0.12 → 不触发修正，就是 PMX 原位）；`Head` 偏差 1.2e-07。侧视 `香菱_noskin_neck_side.png` 里颈骨立在颈筒中央。
- #19 Toe0（第一轮，按字面「同 Foot 末端」）：`pivot偏差=9.5e-07`（就是 Foot link 末端），趾长 1.044 → 3.569（目标 3.56919），截面 1.032/1.030 → 5.863/4.192（目标同值），轴=1、迭代 1 次收敛；`Foot -> Toe0` 段长 11.2281 误差 3.2e-05。侧视 `香菱_noskin_foot_side.png`：Toe0 不再撑成大腿那么粗，但 4.19 的盒高戳出鞋面 → 触发第二轮反馈。
- #19 第二轮（用户：「我要整块骨头呆在鞋子里」）**已实跑验收**（08:01，香菱 `--no-skin --shot`）：新目标 趾长 3.56919 / 宽 5.31702 / 高 1.86092，Max 端一次收敛 `截面 1.03211/1.03002 -> 5.31703/1.86092`，`pivot偏差=9.5e-07` 不变。日志里 `宽高轴=3/2` 与第一轮 `轴=1(按粗细猜)` **正好对调** —— 旧的「谁粗谁配大值」在 5.86/4.19 这种接近的值上蒙对了，换成 5.32/1.86 就会把骨头立起来；改判世界 Z 之后侧视/正视图里趾骨盒都是「扁而宽、整块在鞋里」。离线凸出量：上沿 1.66 → 0.50cm、下沿 1.64 → 0.48cm，剩下的凸出集中在最前 0.35cm 的圆头（矩形盒跟不上弧面，再收就把趾骨压成薄片）。其他三个模型重导出的新目标（趾长×宽×高）：克雷薇 7.57×6.57×1.22、林尼 2.23×5.72×3.55、香菱-海灯节 0.57×3.64×3.47 —— 只有香菱跑过 Max。

- 完整带 Skin 的那轮（01:18，同一模型去掉 `--no-skin`）：`加入 Skin 的骨骼数 321/321`、`权重写入耗时 0.848s，已写入顶点 18892，失败顶点 0`、`Morpher 61/61`、`场景对象数 397`、`香菱.max` 193MB，9 张截图同样全部落盘，退出码 0，Max 自己退的。
- 连跑两轮（01:15 noskin / 01:18 完整）都没有再弹 `Startup Failure Detection`：`SessionCleanCloseCount` 从 0xad 追到 0xaf（两次干净退出），上一轮 `taskkill /F` 留下的差额正在被填平。
- **#19 第二轮的完整带 Skin 复跑（08:04–08:05，`python tools/obj_run.py "test_runs/香菱" 香菱 --shot`，退出码 0）**：脚趾行与 noskin 轮逐字一致 —— `Bip001 L Toe0 pivot偏差=9.53674e-07 趾长 1.04444 -> 3.56919 目标=3.56919 截面 1.03211/1.03002 -> 5.31703/1.86092 宽高轴=3/2 长度轴=1 迭代=1`，即挂上 Skin 之后闭环收敛不受影响；`加入 Skin 的骨骼数 321/321`、`已写入顶点 18892，失败顶点 0`、`场景对象数 397`、9 张截图全落盘、`quitMax` 干净退出。带网格的 `香菱_foot_side.png` / `香菱_foot_front.png`：趾骨盒扁而宽，整块在鞋里；`香菱_front.png`/`香菱_side.png` 全身骨架贴合网格、手指齐全、无回归。
- **#24（Morpher 通道号写在空索引上）已实跑验收**（08:14 那轮，同一命令重跑，退出码 0）：改成 `built + 1` 连号之后日志变成 `Morpher 通道：61 / 61，有目标=61 有名=61，通道容量=100`、`通道明细： [1]default001/Y [2]default002/Y … [8]default008/Y` —— 通道确实建在 1..61 上（每个都读得到目标），容量停在 100 也反过来证明「61 个通道全在初始 100 槽内」，旧的 `容量+1` 写法确实把表情全甩到了 101/201/… 上。同一轮其余数字未变：Skin 321/321、顶点 18892 / 失败 0、脚趾行 `pivot偏差=9.5e-07 截面 -> 5.31703/1.86092 宽高轴=3/2 迭代=1`（左脚）与 `5.31693/1.85908`（右脚 —— 目标本来就按左右各自的网格量，两边差 0.002 以内）、397 对象、9 张截图全落盘、`quitMax` 干净退出。
- **#25 已实跑验收**（08:20 那轮，同一命令，退出码 0）：`通道明细： [1]あ/Y [2]い/Y [3]う/Y [4]え/Y [5]お/Y [6]にやり/Y [7]ワ/Y [8]ん/Y`、`Morpher 通道：61 / 61，有目标=61 有名=61，改名接口抛异常=61，通道容量=100` —— 「有名」这时已经是**逐个和 `blocks[k][1]` 比过**的硬判据，61/61 说明 61 个通道名全是真表情名。同一行还顺手把 `WM3_SetChannelName` 钉死：61 次全抛异常（旧代码包在裸 `try catch` 里所以从没进过日志），改名靠 `target.name` 就够了 → 该调用已删除，`nameSetErr` 计数与日志字段一并撤掉（这一步是验收之后的纯删除，脚本未再重跑；契约测试改成断言它不许被加回来）。其余数字与 08:14 轮一致：Skin 321/321、顶点 18892 / 失败 0、脚趾 L `5.31703/1.86092` / R `5.31693/1.85908`、397 对象、9 张截图、`quitMax` 干净退出。
- **#26 第一轮（角平分线 `facing`）已实跑**（08:59 那轮，同一命令，退出码 0）：`实测朝向=(-1.7e-05,-1.0) 目标角=-90.001 … 采用 facing=-90.001 两脚外八： L=6.68931 R=-6.68932` —— 骨架不再被拧向左脚（旧值 -83.3117 = 左脚方向），两脚各差 6.69°、一正一负，正是 PMX 的外八量；`Biped 初始: L Foot=[6.95582,1.86419,…] R Foot=[-6.95583,1.86442,…]` 两踝 y 已对称（旧轮 2.674 / 1.054）。脚趾三轴、`段校验`、Skin 18892 顶点全部无回归，10 张截图落盘（新增 `foot_top`）。**但同轮的 `脚骨朝向 … 偏航=L 0.0198° / R 0.0°` 是假的**：那条比的是两个自己摆到位的 pivot，恒等式，量不到骨头画出来的方向。
- **#27 转角接口第一轮探针（10:17，同一命令，退出码 0）**：结论是**token 找到了、写法还没定**。`旋转接口探针：  rotation p3=0.0度 q=0.0度 抛错=-- Unable to convert: [0,0,30] to type: Quaternion  rot p3=0.0度 q=0.0度 抛错=-- Runtime error: Unknown transform type, unsupplied  Rotate …  move …  worldRot …  rotEuler …  .transform=30.0度  rotate命令=30.0度` —— 六个候选里只有 `#rotation` 不是 `Unknown transform type`（它要 Quaternion，point3 被类型转换挡掉），其余 `#rot`/`#rotate`/`#move`/`#worldRot`/`#rotEuler` 全部非法；两条不经过 `biped.setTransform` 的直写路（`.transform` / `rotate` 命令）**立刻**读到 30 度。门控按设计生效：`转角 pass：探针未跑通（没有任何 token 把局轴转过 15 度），整段跳过`，所以真骨架一行没被转，`结构向 复核：可测=51 夹角>5度=35 短骨不计=1` 与逐骨读数和 09:37 **逐字相同**（`Spine2 差倾=-17.4667`、`Neck 夹角=8.53907`），`画线`/`脚骨偏航结论` 也未变 —— 没有回归，也没有进展。其余数字全对齐上一轮：肢段拟合 39/39 误差 0.224741、摆位 30/30 髋中点 2.6e-06、Skin 321/321、顶点 18892 / 失败 0、Morpher 61/61 有名=61、397 对象、12 张截图 `落盘=true`、`quitMax` 干净退出。同轮抓到的第二个缺陷：`hand 取景：手指节点=1 框=[-48.7416,-26.9349,-0.464519]..[48.7417,46.5363,140.513]` —— 框就是整场景边界，原因不是 `isNode`（那条上一轮已修）而是 **`getNodeByName` 不认通配符**，见 §6。
- **#26/#27 的局轴实测已完成**（09:37 那轮，同一命令，退出码 0，12 张截图落盘、397 对象、Skin 18892 顶点 / 失败 0、`quitMax` 干净退出），结论是**一条推翻性事实：Biped 的骨盒不随 pivot 走**。`结构向 复核：可测=51 夹角>5度=35 短骨不计=1`，而同一批行的 `pivot偏差` 普遍在 1e-5 量级 —— 落位全对、盒子不动：脊柱四节 `实测倾` 全是 `-0.0456`（竖直；PMX 该 `上半身2 +17.42` / `首 -8.58`）、`Head 夹角=48.9`、五根手指 `Finger1~4 轴1 实测倾=179.988`（笔直朝下；PMX 各指 `差偏航` +4.17/+4.68/-2.41/-15.64，拇指 `Finger0 目标倾=-139.465 实测倾=-167.678`）、`Foot 实测倾=-90`（平放；PMX 足首→つま先 向下 30°，`夹角=30.5`）。副作用不止难看：指节被压在同一条竖直线上，`pivot偏差` 从 `中指１ 0` 累积到 `親指２ 2.176`。脚骨 `画线`：`L Foot 最近轴=2 轴2=30.54度/偏航-6.689`、`R Foot … 偏航+6.689`（外八已按 PMX 对称摊开）、`Toe0 最近轴=1 偏航 L=-11.31 / R=+11.31`。同轮 `#rot` 探针：三参报 `Argument count error: setTransform wanted 4, got 3`，四参（与好用的 `#pos`/`#scale` 同形状）报 `Runtime error: Unknown transform type, unsupplied` 且局轴 0.0 度未动 ⇒ **`#rot` 不是合法 transform_state**。另有一条脚本缺陷被这轮抓到：MAXScript **没有 `isNode`**，手部取景整段被 catch 掉（`手指节点=0`），`hand_front`/`hand_side` 退化成全身图。
- **#26/#27 转角接口第三轮探针（10:34，同一命令，退出码 0）**：结论是**「Figure 模式内只有 `rotate` 命令动得了局轴」，但 `#rotation` 至今仍未被真正测过**。`旋转接口探针：Figure模式=true  rotation p3=0.0度 q=0.0度/留=0.0度  p3抛错=-- Unable to convert: [0,0,30] to type: Quaternion  q抛错=-- Unable to convert: [0,0,30] to type: EulerAngles  rot …  Rotate …  move …  worldRot …  rotEuler …（五个 token 全报 Unknown transform type，quat 侧同样是 EulerAngles 转换错）  .transform=0.0度/留=0.0度  rotate命令=30.0度/留=30.0度  退出Figure(#rotation)=0.0度/留=0.0度 退出后抛错=-- Unable to convert: [0,0,30] to type: EulerAngles  采用=rotateCmd`。三条新事实：① 10:17 那句「喂 quat 不抛错但 0 度」是假数 —— `eulerToQuat [0,0,30]` 自己就抛 `Unable to convert: [0,0,30] to type: EulerAngles`，quat 没构造出来，`#rotation` 一条也没被执行过（改成 `eulerToQuat (eulerAngles 0.0 0.0 30.0) order:1` 才是 Max 自带脚本的写法）；② Figure 模式内 `.transform =` 整块写回被求解器吃掉（`0.0度/留=0.0度`），`rotate node (angleaxis …)` 局轴立刻 30 度、`#pos`+`update 0` 刷新后仍是 30 度；③ 门控与回滚按设计工作 —— 探针采用 `#rotateCmd` 后 pass 跑了，但 10:34 的 `mmd2maxSetBoneRot` 把 `#rotateCmd` 误实现成写 TM（正是②当场否掉的那条路），所以逐骨 `转角 Bip001 Spine2 夹角=17.4668->17.4668`、`Neck 8.53907->8.53907`、`Head 48.889->48.889`、`L Finger0 23.0244->23.0244`、`R Finger42 35.4197->35.4197` 全部原地，汇总 `转角 pass：接口=rotateCmd 尝试=35 改善=0 未改善=35 抛错=0`。`结构向 复核：可测=51 夹角>5度=35 短骨不计=1` 与 09:37/10:17 **逐字相同** —— 回滚逻辑守住了场景，没留下更差的姿态。手部取景这轮换了个失败原因：`hand 取景：手指命中=0 取景点数=1 框=[-48.7416,-26.9349,-0.464519]..[48.7417,46.5363,140.513]` —— `getNodeByName` 已换成遍历 `objects`，但 **`matchPattern` 的第二个参数是带标签的 `pattern:`**，位置写法不抛错、恒返回 false（见 §6），15 根指骨仍一根没命中；`Biped节点=` 计数是这轮之后才加的字段，用来区分「一个 Biped 都没遍历到」和「名字模式没命中」。无回归：肢段拟合 39/39 误差 0.224741、摆位 30/30 髋中点 2.6226e-06、Skin 321/321、顶点 18892 / 失败 0、Morpher 61/61 有名=61、通道容量 100、397 对象、12 张截图 `落盘=true`、`quitMax` 干净退出。
- **#26/#27/#17 转角接口第四轮（11:11，同一命令，退出码 0）＝ 结案：Biped 的单骨转角旋钮存在，`#rotation` + 正确 quat 一次跑通**。`旋转接口探针：Figure模式=true  rotation p3=0.0度 q=120.0度/留=120.0度  p3抛错=-- Unable to convert: [0,0,30] to type: Quaternion  …（其余五个 token 照旧 Unknown transform type）  .transform=0.0度/留=0.0度  rotate命令=30.0度/留=30.0度  退出Figure(#rotation)=30.0度/留=30.0度  采用=bipedRotation` —— quat 换成 `eulerToQuat (eulerAngles 0.0 0.0 30.0) order:1` 之后局轴立刻转 120 度、`#pos`+`update 0` 刷新后仍是 120 度，退出 Figure 模式后同样留得住；前三轮那句「`#rotation` 转不动」被彻底否掉（是参数构造失败，见 §6）。真骨架 `转角 pass：接口=bipedRotation 尝试=36 改善=34 未改善=2 抛错=0`，**`结构向 复核：可测=51 夹角>5度=2 短骨不计=1`（上一轮 35）**：`Spine2 目标倾=17.4211 实测倾=17.4211 差倾=0.0 夹角=0.0`（#17 的挺胸 / #27 的后仰终于出现在骨盒上）、`Neck 目标倾=-8.5847 实测倾=-8.5847 夹角=0.0`（颈前倾）、`Head 夹角=48.889 -> 0.0`、`L/R Foot 目标倾=-120.042/-120.063 实测=同值 夹角=0.0`（#26 的足骨俯仰）、五根手指 `Finger0~4` 与全部 `Finger01~42` 的 `夹角` 基本归零（`L Finger1 -174.251 实测 -174.251`）。`脚骨偏航结论 L Foot 骨=7.6e-06度`（上一轮 -6.689）、`R Foot 骨=7.6e-06度`。剩下 2 个超标是 `L/R Clavicle 夹角=13.64`（`实测倾=-999.0` = 最近轴与矢状面退化，pass 里 `转角 Bip001 L Clavicle 夹角=6.60331->6.60331` 未改善后按设计回滚）。手部取景第一次真跑通：`hand 取景：Biped节点=54 手指命中=15 取景点数=16 框=[31.2389,-9.20983,76.5628]..[50.7185,9.89882,95.4929]`（`pattern:` 标签写法生效，15 根指骨全命中），`香菱_hand_front/side.png` 第一次是手部特写而不是全身图。**代价 = 落位被转角带走**（新缺陷，下一轮的主攻）：`校验 Bip001 Neck 偏差=4.90352`、`Head 4.15381`、`L/R Clavicle 4.26276`、`L Toe0 pivot偏差=5.99721`、`L Finger32 7.82856`，而转角前这些都在 1e-5 量级 —— `#rotation` 绕骨自身 pivot 转，父骨一转子骨关节就离开 PMX 的 head（Spine2 转 17.4° × 段长 11cm ≈ 3.4cm，量级吻合）；`Pelvis 5.6995` 不算这条，它是 `校验` 拿 PMX 腰 比 髋中点 的既有口径差。修法已进脚本：转角 pass 之后重跑一次 `mmd2maxPlaceBipedNodes`（探针证明 `#pos` 不吃转角），日志字段 `转角后重摆: 成功=…`。其余数字无回归：肢段拟合 39/39 误差 0.224741、摆位 30/30 髋中点 2.6226e-06、Skin 321/321、顶点 18892 / 失败 0、Morpher 61/61 有名=61、397 对象、12 张截图落盘、`quitMax` 干净退出。

**待验证 / 待用户判定**
1. #17 的「挺胸」只做到一半：`校验 Bip001 Spine2 目标 y=-4.92164 / 实际 y=-4.42176`，偏差 0.508（`Spine2 -> Neck` 段校验误差 0.1696 同源）。胸腔仍是全链最靠前的点（Neck 在 -0.037、Pelvis pivot 在 +1.86），所以侧视看得出胸口前倾，但比 Python 算出来的量小 0.5cm —— Biped 不接受那次 `setTransform`，要不要再补一轮转角等用户看图决定。**这条已被 #27 吸收并有实测**（09:37）：`结构向 Bip001 Spine2 轴1 目标倾=17.4211 实测倾=-0.0456257 差倾=-17.4667` —— 画出来的脊柱是竖直的，挺胸（以及后仰）根本没出现在骨盒上，跟 0.5cm 落位差无关；`Neck 目标倾=-8.5847 实测倾=-0.0456257 夹角=8.53907 pivot偏差=0.0` 同理。修法与 #26/#27 同一个阻塞项：等旋转接口探针（见 5）。**11:11 已解**：转角跑通后 `结构向 Bip001 Spine2 轴1 目标倾=17.4211 实测倾=17.4211 差倾=0.0 夹角=0.0`、`Neck 目标倾=-8.5847 实测倾=-8.5847 夹角=0.0` —— 挺胸/后仰第一次出现在骨盒上；剩下的 0.508 是落位（`pivot偏差=0.507733`，PMX 腰→髋中点 的既有口径差那一类），跟转角无关。
2. 启动弹窗那条 UIA 通路只做过单元测试（`tests/test_obj_run_guard.py`），实跑两轮都没再弹框，所以「按名字点掉 Continue Without Restore」还没被真实窗口验证过。
3. 换模型（克雷薇 / 香菱-海灯节）的 `--shot` 复跑：截图段与视口 API 现在只在香菱上验过。
4. **#25 之后的纯删除已闭合**（09:37 那轮顺带验了，无需专门重跑）：`WM3_SetChannelName` 与 `nameSetErr` 计数删掉之后 `Morpher 通道：61 / 61，有目标=61 有名=61，通道容量=100`、`通道明细： [1]あ/Y [2]い/Y …`，日志里没有 `Morpher 通道构建失败` 行 —— 通道连号与真表情名都没受影响。
5. **#26/#27 的转角接口已结案（11:11：`采用=bipedRotation`、`改善=34/36`、`结构向` 超标 35→2），下一步 = 第五轮「转角后重摆位」**。前三轮那些「Biped 转不动」的读数全部是 `eulerToQuat` 参数构造失败造成的假否决（见 §6），补上正确 quat 之后方向一次性对上：`Spine2 差倾=0.0`、`Neck 夹角=0.0`、`Head 48.889->0.0`、`L/R Foot 差倾=0.0`、指节基本归零，`脚骨偏航结论 … 骨=7.6e-06度`。**新 blocker 换成落位**：转角绕自身 pivot 转，父骨一转就把子骨关节甩出 PMX 的 head —— `Neck 4.90352 / Head 4.15381 / L/R Clavicle 4.26276 / L Toe0 5.99721 / L Finger32 7.82856`（转角前都在 1e-5）。脚本已按「先转方向、再摆落位」改好：转角 pass 之后重跑 `mmd2maxPlaceBipedNodes`（同一个先父后子 `passList`、受 `rotTotal > 0` 门控），依据是探针里 `q=120.0度/留=120.0度` 那一步正是「重发 `#pos` + `update 0` 之后」读到的 ⇒ `#pos` 不吃转角。**第五轮判读三件事**：① `转角后重摆: 成功=30 失败=0 节点缺失=0`；② `校验`/`结构向 … pivot偏差` 那批 4~8 的数回到 1e-5 量级；③ `结构向 复核：夹角>5度=` 仍是 2（若重摆把转角吃回去，这个数会弹回 35，那就说明 Biped 会按子关节位置反推父骨朝向，方向与落位只能二选一，得让用户判定取舍）。剩下的 2 个超标是 `L/R Clavicle 夹角=13.64`（`实测倾=-999.0` = 最近轴落在水平 X 上、矢状面倾角退化；pass 内 `6.60331->6.60331` 未改善后按设计回滚），锁骨是 Biped 唯一带额外约束的骨，要不要单独立一轮看③的结果再定。**同一轮用户又指出脚被转进地里（「两边的脚也没有落在地面上，Y没有90度」），代码已按 §6「脚链方向单独定判据」改掉**：`L/R Foot` 的转角目标从三维 tail−head 换成水平投影（11:11 那轮的 `目标倾=-120.042` 会应变为 `-90`，`画线 … 轴2` 的来向仍保住外八偏航），`L/R Toe0` 换成网格鞋尖的水平投影（11:11 趾盒被转成 `目标倾=0.0` 竖直朝天，`脚骨偏航结论 … 趾=-6.28度`）。**所以第五轮判读再加两条**：④ `结构向 Bip001 L/R Foot … 实测倾` 必须是 ±90（±0.5 以内）而不是 ±120，`Bip001 L/R Toe0` 同理，且 `脚骨偏航结论` 的 `趾=` 从 -6.28 收敛到 ~0；⑤ `结构向 复核：夹角>5度=` 里脚链四条不该再冒出来（判据已与转角同源，出现即说明某处还在用三维 tail−head）。
6. **#27「Spine2 没后仰 / 颈没前倾 / 手指不贴合」的机制已定案：`setTransform #pos` 摆得准关节但骨盒不随 pivot 走，方向只能靠 `#rotation`（11:11 已跑通）**。09:37 那轮的实测把离线预测表（可测 38 / 超标 1）推翻：`结构向 复核：可测=51 夹角>5度=35 短骨不计=1` 而 `pivot偏差` 普遍 1e-5 —— 落位全对、盒子纹丝不动，逐条对上用户三处反馈（`Spine2 目标倾=17.4211 实测倾=-0.0456257`、`Neck 目标倾=-8.5847 实测倾=-0.0456257`、`Finger1~4 实测倾=179.988` = `createNew` 默认笔直朝下）。11:11 转角跑通后这三个数全部对上目标（`夹角=0.0`），手指 `Finger0~42` 也基本归零，`香菱_hand_front.png` 里指节第一次顺着手袖方向走。**还欠两条**：① 转角带来的落位漂移（见 5，本轮已改代码待实跑）；② **手指贴合的最终判据是网格轮廓，不是 `指先` 白锥** —— 那 10 根是模型自己的显示 stub，`(0, 0, +7.656)` 笔直朝上 7.66cm，本来就不沿手指，`hand_front`/`hand_side` 现在能看了，但「贴不贴合」要用户看图判定。可测分母 38→51 是因为新表真的遍历了 `targetNames` 全部行（旧 `校验` 只查 19 个写死的名字，`Spine1` 与 30 根指骨从来没被看过）；`短骨不计=1` 正好是预测的 `R Hand`（`右手首` 0.38cm vs `左手首` 3.26cm，模型本身不对称）。手部取景这条线修了三轮才通：09:37 缺 `isNode`（整段被 catch）→ 10:17 `getNodeByName` 不认 `*`（`手指节点=1`）→ 10:34 `matchPattern` 缺 `pattern:` 标签（`手指命中=0`）→ 11:11 `Biped节点=54 手指命中=15 取景点数=16` 才第一次拍到特写。

7. **「Bip001 R Clavicle 为什么叫 FGGG」这条查不动，需要用户给一张截图**：按 §6「不用开 Max 就能核实节点名」把 11:11 的 `香菱.max` 名字表整份解出来 —— 397 个对象里 `Bip001 L Clavicle` 与 `Bip001 R Clavicle` 都在、就是这两个名，**没有任何对象叫 FGGG**；全场景只有 `WeaponL`/`WeaponR` 两根 ASCII 名附属骨（模型自带的武器挂点，挂在左右手臂链上，位置就在锁骨/上臂附近），其余附属骨名全是模型自己的中文/日文名（`左辫_0_1`、`前摆_0_0`、`センター2`、`にやり`…）。Biped 节点不改名是设计决定（`if (classOf nodes[i]) != BoneGeometry do continue`：改名会破坏 Biped 内部结构），所以用户看到的 `FGGG` 只可能是①右肩附近那根**附属骨**的名字在他机器上的显示（字体/输入法回退）、②他自己那一版场景里的手动命名，或者③另一个模型的场景。要么拿截图上的名字与位置，要么等下一轮截图对着看，别再凭猜改脚本。

8. **第五轮（12:04）实跑：方向与落位第一次同时成立，脚踩回地面；剩下的偏差全在手臂链上，且全部指向锁骨**。判读 5 条逐条对上：① `转角后重摆: 成功=30 失败=0 节点缺失=0` ✅。② 落位回到 1e-5 量级 ✅ —— `L/R Clavicle 偏差 4.26276 -> 4.02e-07 / 6.85e-07`、`Neck 4.90352 -> 7.63e-06`、`Head 4.15381 -> 0.0`、`L/R Foot -> 1.01e-05 / 4.22e-05`（`Spine2` 仍是 0.5077，那是第 1 条的既有口径差，与转角无关）。③ `结构向 复核：夹角>5度=` **没有**弹回 35，而是 2→5 ⇒ Biped 不会按子关节位置反推父骨朝向，方向与落位不是二选一，「先转角再重摆」这个顺序可以长期用 ✅。④ `结构向 Bip001 L Foot 轴2 目标倾=-90.0 实测倾=-90.0 差倾=0.0 差偏航=0.0 夹角=0.0198`、`R Foot … 夹角=0.0`，两根 Toe0 同样 `实测倾=-90.0` ⇒ 脚链四根骨盒全部水平；`香菱_foot_side.png` / `香菱_foot_front.png` 里鞋底贴着 z=0 网格线、趾盒横在鞋内 —— **用户「两边的脚也没有落在地面上，Y没有90度」这条已解** ✅。⑤ 脚链四条没再出现在超标里（判据已与转角同源）✅，但 `脚骨偏航结论 Bip001 L Foot 骨=0.0度 趾=-4.61816度` 的 `趾=` 没收敛到 0：转角 pass 的 `> 5.0` 门控把 Toe0 挡在门外（pass 内只测到 4.62°），折角来自 PMX 足首偏航 6.7° 与网格鞋尖偏航 11.3° 不一致；要不要给脚链单独降到 2 度等用户判。
   **新残差 = 手臂链**：重摆之后 `校验` 仍留着 `L/R UpperArm 偏差=1.95304 / 1.95291`、`Forearm 1.48452 / 1.48595`、`Hand 1.01014 / 1.01008`，而 `结构向 L/R Clavicle 轴1 目标倾=168.386 实测倾=-999.0 夹角=13.6419 / 13.6411`、pass 内 `6.60331->6.60331` 未改善后按设计回滚 ⇒ 锁骨骨盒留在水平 +X，PMX 要它向下压 13.6°，Biped 的肩关节沿骨盒 +X 推出去就比 PMX 肩点高约 2cm，整条手臂跟着抬高（用户说的「肩膀错位/手臂错位」在落位层面已经修好，这一条是它剩下的那一半）。锁骨是 Biped 唯一带额外约束的骨，两条路互斥：a) 单独给锁骨再想办法（Biped 约束/滑动轴）；b) 接受锁骨骨盒不转、改成按骨盒 +X 反解 UpperArm 的落位目标。**要用户判定取舍，别再自己选。**
   另两条小的、留给下一轮：`超标 5` 里 3 条是手指 —— `L Finger32 夹角=5.63298 pivot偏差=6.47056`、`L Finger42 5.1387 / 4.79943`（转角前只有 3.5/3.1 ⇒ **重摆对指链净收益为负**，指节被父骨转角甩出去后 `#pos` 没摆回来）、`R Finger1 6.04744 pivot=7.63e-06`（这根**从来没被转角尝试过**：pass 内测 ≤5°、退出 Figure 后读到 6.05° ⇒ 门控放在 Figure 模式内不可靠，判据应当与 `结构向` 表同一时刻）。`香菱_foot_top.png` 拍废：`foot_top 取景：脚骨节点=4 框=[-13.5813,-14.458,-0.464519]..[13.5812,7.01684,19.2724]` 的 XY 框是对的，但 Top 是**透视**视口，帽子（z≈160）离相机最近、糊满整幅，脚完全看不见 ⇒ 顶视看外八这条要么切正交、要么承认拍不到。

9. **第六轮要跑的东西已落地（用户选 b，并要「机制层 + 探针层都尝试」）——静态全绿，等一轮实跑**。四条改动：
   - **机制层 = 一张取舍表替掉所有按骨名的特例分支**：`mmd2maxChainPriority`（`Finger*/Foot/Toe0 → #dir`、`Clavicle → #pos`、其余 `#both`），摆位与转角只问「这根骨保什么」，控制流里不许再出现 `matchPattern 骨名`（测试 `test_priority_table_is_the_only_per_bone_branch` 直接数分支）。原来的「一次转角 + 一次重摆」改成**有界收敛循环**（最多 3 遍，`尝试=0` 就 `break`）：每遍 = 先按优先级重摆（跳过 `#dir`）→ 再转还超标的骨 → 打一行 `收敛 第N遍: 转角尝试=… | 方向超标=… 落位偏差和=… 落位最大=…`，退出循环后再收尾摆一遍。
   - **b 方案**：`UpperArm` 的落位目标不再抄 PMX 肩点，改成按**锁骨骨盒长度轴 × PMX 段长**反解（`mmd2maxShoulderRayTarget`；轴号仍由 `mmd2maxBipedAxis` 按参考方向挑，不写死 X）。读不到节点/轴/目标与 pivot 重合 ⇒ 返回 `undefined` ⇒ 用回 PMX 原点，绝不猜。日志 `摆位按骨盒射线 … PMX肩点/射线肩点/差` 三个数并排，`校验` 那行对 UpperArm 额外打 `射线目标= … 对射线偏差= …（b 方案认这个）` —— **下一轮的真判据是 `对射线偏差` 回到 1e-5**，而 `偏差=`（对 PMX）留着不删，射线判据万一错了要能看出来。
   - **探针层（只在一次性探针 Biped 上跑，真骨架不碰）**：① `mmd2maxDumpBipedApi` 用 `classOf biped` + `getMethodCount`/`getMethod` 把整份接口反射出来，落盘 `test_runs/香菱/biped_api.txt`，并把命中 `rubber/tie/length/slide/mode/figure/update/scale` 的行打进日志 —— 以后「Biped 到底有没有橡皮筋开关」不用开 Max 就能回答（Max 自带 `scripts/` 里 grep 不到 rubber/tieDown，所以不许再猜方法名，测试 `assertNotIn` 钉住了 `biped.setRubberBandMode` 这类猜测）；② **橡皮筋基准**：在探针 Biped 上把父骨 `#pos` 抬 5cm，量子骨跟不跟（`橡皮筋基准 … 父动=… 子跟=…cm`，4 组配对：小腿→脚、锁骨→上臂、脚→趾、指节），然后复位；③ **锁骨射线自检**：`锁骨射线自检 射线反推肩点 与 真实肩点差=…cm` —— b 的前提（Biped 把肩关节放在骨盒射线上）如果这条量出来差得远，b 就是错的，得回 a。
   - **方向容差收成一个数**：`global MMD2MAX_DIRTOL = 3.0`，转角 pass 的尝试门控、收敛循环的打分、退出 Figure 后的 `结构向 复核：夹角>N度=` 三处共用（原来各写死一份 `5.0`）。**5.0→3.0 是我替你做的决定**，依据就是第 8 条 ⑤：`趾=-4.61816度` 刚好被旧门控挡在门外、永远不会被尝试；再往下压到 1~2 度则是把测量噪声当缺陷转（回滚阈值才 0.5°）。这条**吸收了**第 8 条末尾那句「要不要给脚链单独降到 2 度等用户判」——现在不是脚链单独降，是全局降到 3 度。不认的话只改这一个数字即可。
   顺带删掉 166 行死代码（`mmd2maxFootYawError`/`mmd2maxLevelFoot`/`mmd2maxAlignFootYaw`/`mmd2maxAlignFoot`：#28 之后没有任何调用点，脚链方向已统一走 `mmd2maxBoxWantDir` + 转角 pass）。静态：`check_ms [OK]`、`Ran 202 tests OK`。
   **第六轮判读六件事**：① `Biped接口 方法数=N 取到=M 命中关键词=K`（K=0 就说明反射没拿到东西，`biped_api.txt` 也别说「没有开关」）+ 整份 `test_runs/香菱/biped_api.txt`（有 ⇒ 下一轮单独立一轮探它，b 退化成兜底）；② `橡皮筋基准 … 父动=5.0 子跟=…cm` 四组，量出 Biped 的传播行为（这是「父骨一转就把子骨甩出去」的定量版本）；③ `锁骨射线自检 … 差=…cm` 必须小，否则 b 的前提不成立；④ `收敛 第1/2/3遍` 三条的 `方向超标=` 要单调下降、`落位偏差和=` 不反弹（反弹 = 循环在 A/B 之间来回，上限 3 遍不够，得让用户在取舍表上定优先级）；⑤ `收尾重摆(方向优先的骨跳过): 成功=…` 与 `结构向 复核：可测=51 夹角>3.0度=…`（阈值从 5 变 3，条数上升是预期，别当回归）；⑥ 脚链四根仍不该进超标，`脚骨偏航结论 … 趾=` 应从 -4.62 收敛到 ~0（这条就是容差降到 3 度要换回来的东西）。

10. **第六轮（13:11，香菱，退出码 0，397 对象，12 张截图落盘，`quitMax` 干净退出）：b 方案与容差 3.0 都兑现了；新的 blocker 是「摆位↔转角」在三根骨上打转，而 Biped 接口反射失败**。
    - **先记一次编译期翻车（13:06，22 秒就弹框、日志一行没写）**：`fn mmd2maxPlaceBipedNodes ... [skipDir:false] =` → `-- Syntax error: at [, expected =`（解析器把 `false` 当类型名、然后等 `=`）。MAXScript 的可选参数写法本项目从没验过 ⇒ 已改成**位置参数**（四个调用点显式传 true/false），并给 `check_ms.py` 加了 `check_optional_params`：顶格 `fn` 的参数表里出现 `[...]` 直接拦。这条与 `f(a,b)`、`local x = try(...)` 同一类：**整份脚本一行不跑，弹窗只给标题**。
    - **b 方案 = 成立**：`锁骨射线自检 射线反推肩点 与 真实肩点差=0.0cm`（弃用骨架上量的前提）；真骨架 `校验 Bip001 L UpperArm ... 对射线偏差=1.90944e-06（b 方案认这个）` —— 肩点真的钉在锁骨骨盒射线上。代价是 `偏差=`（对 PMX 肩点）从 1.95 变成 0.955，**注意射线目标本身只有 0.48cm 的位移**（12:04 那 1.95 是「Biped 每轮把肩点拽回去」的拉扯量，认了射线之后拉扯消失，剩下的就是几何本身的 0.955）。下游全部跟着变好：`Forearm 1.48→1.015`、`Hand 1.01→0.143`、`UpperArm 1.95→0.955`，`段校验 UpperArm->Forearm 误差 3.8e-06`。
    - **容差 5.0→3.0 兑现**：`脚骨偏航结论 L/R Foot 骨=0.0度 趾=0.0度`（12:04 是 `趾=-4.61816度`）—— 趾盒的折角就是被旧门控挡在门外那一档，降到 3 度以后转角 pass 愿意去转它了。`结构向 复核：可测=51 夹角>3.0度=5 短骨不计=1`，五条分别是 `L/R Clavicle 6.66/6.54`（#pos，按设计不转）、`L/R Forearm 3.44/3.42`、`L Calf 3.12`，**手指一根都不在超标里**（12:04 有 3 根）。
    - **新 blocker = 收敛循环没收敛**：`收敛 第1遍 转角尝试=39 改善=39 未改善=0 | 方向超标=2` → `第2遍 尝试=3 改善=3 | 超标=3` → `第3遍 尝试=3 改善=3 | 超标=3`。三根骨（`L/R Forearm`、`R Calf`）每遍都被 `转角3:L Forearm 夹角=3.44475->0.0` 转到 0，然后**下一遍开头的重摆又把它们甩回 3.4°**，跑满 3 遍上限；`收尾重摆` 是最后一道写，所以退出 Figure 模式后读到的仍是 3.4°。⇒ 这三根骨上「方向」与「落位」是真的互斥（`#both` 在这里不成立），而 `改善=39/39 未改善=0 抛错=0` 说明机制本身没坏，是取舍表需要按链细分。**待用户判定**：`Forearm/Calf` 改 `#dir`（保方向，代价是肘/膝 pivot 回到 ~1.0cm）还是保持 `#both`（保落位，代价是骨盒方向差 3.4°）。
    - **`#dir` 的代价第一次被量出来**：`校验 Bip001 L Foot 偏差=1.44357` 而 `R Foot 0.108101` —— 方向优先的骨在重摆那一遍整根跳过，父骨（Calf 转角 3.4°）甩出去的 1.4cm 再没人摆回来。`香菱_foot_side.png` 里绿盒/黄盒仍在鞋内、鞋底贴 z=0（#19/#28 没被推翻），但**左右不对称**是这条的实证。
    - **探针层：接口反射失败**：`Biped接口 反射失败：getMethodCount 两次都拿到 0`，`biped_api.txt` 没落盘。离线能查的都查完了 —— Max 自带 `scripts/` + `stdplugs/` 全文 grep `rubber|tieDown` 只命中 `PickObject Rubberband:`（拖拽橡皮筋，与 Biped 无关）；安装目录里那份 30MB 的 `MAXScript-Help_2024_ENU.chm` 是 LZX 压缩的，本机没有 7z、`hh.exe -decompile` 不出文件。**下一步不要再猜名字**：改成用异常文字分辨「属性根本不存在」和「存在但参数不对」（`getProperty biped #"setTieDowns"` / 直调一次把 `getCurrentException()` 打进日志），并且**用一次性探针脚本跑，不要烧完整轮**。
    - **橡皮筋基准（第一次量到传播行为）**：`L Foot->L Toe0 父动=5.0 子跟=5.0cm`、`L Clavicle->L UpperArm 5.0/5.0`、`L Finger3->L Finger31 5.0/5.0` —— 子骨跟着父骨**平移等量**；但 `L Calf->L Foot 父动=10.2115 子跟=5.0` —— 让 Calf 走 5cm，Biped 自己把大小腿重解成动了 10.2cm。这就是「父骨一动子骨关节就离开 PMX head」的定量版本，也是 `落位最大` 在循环里从 8.84 涨到 11.85 的来源。

11. **第七轮的三条改动已落地（用户判定：`Forearm/Calf` 保持 `#both`、不打取舍表的补丁、去打 0.955 的因、收尾「顺序换过来」）——静态全绿，等一轮实跑**。
    - **先更正第 10 条那句「`#dir` 的代价」的量级**：写那条时只看了 `校验` 表（19 个写死的名字，指骨从来不在里面）。把 `结构向` 复核表按 `pivot偏差` 排开才知道真实代价：**左手整条指链 4.3~10.7cm 不在模型手上** —— `L Finger32 pivot偏差=10.6619`、`L Finger42=8.70352`、`L Finger31=7.96936`、`L Finger0=2.45893`，而右手同批全在 `0.039~0.157`。原因是确定的一条：`转角1:Bip001 L Hand 夹角=43.1174->0.0` 把左手掌转了 43 度，指链是 `#dir`、之后每一遍重摆都整根跳过它，于是再没人把它们摆回来（右手那一遍没转 `R Hand` ⇒ 毫发无伤）。量级也对得上：掌指距 ~14cm 的关节被 43 度转角甩出去的弦长 = `2·14·sin(21.5°) ≈ 10.3cm`。`落位最大=11.85` 从头到尾就是这根指骨，不是脚。截图 `香菱_hand_front.png` 里紫色指骨挤在腕部、不跟着青色网格手指走，是同一件事的正面证据。
    - **改动一 = 收尾「先全摆，再只转 `#dir`」**（替掉原来的「重摆但跳过 `#dir`」）：`mmd2maxPlaceBipedNodes ... passList false` 之后接 `mmd2maxRotatePass ... (lastPass + 1) true`，`onlyDir` 是**位置参数**（§6 那条编译期陷阱）。循环内部那一遍仍传 `true`（跨轮读数才可比），变的只是**最后一道写是谁**：`#dir` 的骨最终=方向+落位，`#both`/`#pos` 的骨最终=落位。日志两行新读数：`收尾重摆(全摆，含方向优先的骨): 成功=` 与 `收尾转角(只转方向优先的骨): 尝试=`。
    - **改动二 = 给锁骨射线加「轴夹角」**：`摆位按骨盒射线 … 差=` 这一条 13:11 里从 0.477 涨到 0.955，光看「差」分不清是**骨盒轴在转**还是**段长在变**（`差 ≈ 2·段长·sin(半角)`）。现在同一行打 `轴夹角=`（锁骨长度轴 与「锁骨 pivot→PMX 肩点」的夹角，`acos (dot …)`，helper 已经挡过 pivot 重合，不重复判）。**这就是「打 0.955 的因」的第一步**：轴夹角不变 ⇒ 涨的不是锁骨，去查胸椎转角把锁骨框架带偏（#17 挺胸的副作用）；轴夹角跟着涨 ⇒ 锁骨盒子确实在被 Biped 重解，下一轮谈「先转胸椎再算射线」。
    - **改动三 = 反射换工具**：`getMethodCount` 面向 struct 实例，`biped` 是 **interface 值**，枚举它要用 `dumpInterface`（直接返回文本）。现在先 `dumpInterface biped`、一行没拿到再退回 `getMethodCount`，**两条路的 `getCurrentException()` 文字都进日志**，汇总行改成 `Biped接口 来源=<dumpInterface|getMethodCount> 行数=N 命中关键词=K`。这条不额外烧轮 —— 探针块本来就在同一轮里，失败也只多两行日志。
    - **第七轮判读六件事**：① `结构向 … L Finger32 pivot偏差=` 从 10.66 回到 1e-3 量级、`校验 Bip001 L Foot 偏差=` 从 1.44 回到 ~0.1，且**右手不许变差**（它现在是好的，收尾全摆把手指摆回 PMX 之后只靠 `收尾转角` 补方向）；② `收尾转角(只转方向优先的骨): 尝试=` 的数 —— 全摆之后仍超标的 `#dir` 骨数，理想是它把方向也一并归零；③ `结构向 复核：可测=51 夹角>3.0度=`：`Forearm/Calf` 那 3 条**预期仍在**（用户已定保持 `#both`，不再和落位抢），若消失是白赚；④ `摆位按骨盒射线 … 轴夹角=` 在三个时刻（首摆 / 循环内 / 收尾）的读数，按改动二那两条分支判；⑤ `Biped接口 来源=…` + `test_runs/香菱/biped_api.txt` 是否落盘（还是失败就看两个异常文字，别再猜名字）；⑥ **`香菱_hand_front.png` 是这一轮的正面判据**（左手五根指骨在不在手袖里），数字全绿而图不对仍以图为准（见 memory「验收只认回读真值和截图」）。
    - 静态：`check_ms [OK]`、`Ran 205 tests OK`。新增/改写的契约：`test_final_rotation_pass_touches_only_direction_bones`、`test_interface_value_is_dumped_not_counted`、`test_rotation_pass_replaces_pivots_afterwards`（收尾改成认「全摆 + 只转 `#dir`」两行）、`test_direction_priority_bones_survive_the_replacement`（`passList false` 三处 / `passList true` 一处）。

12. **第七轮（14:08:33→14:09:24，香菱，退出码 0，`香菱.max` 落盘，12 张截图）：收尾「顺序换过来」完全兑现；`0.955` 的因判到第二条分支；顺带挖出一个更大的东西 —— 转角判据一直在给自己打假分**。
    - **改动一（先全摆再只转 `#dir`）判据全中**：`收尾重摆(全摆，含方向优先的骨): 成功=30 失败=0 节点缺失=0` → `收尾转角(只转方向优先的骨): 尝试=0`（全摆之后没有一根 `#dir` 的骨还超标，方向那一遍是空跑 = 上一遍没被抢走）。回读：`校验 Bip001 L Foot 偏差=1.44357 → 6.50319e-06`、`R Foot → 4.19813e-05`；第 10 条那批手指账也清了 —— `L Finger32 10.6619 → 3.8e-06` 级的**指根**，`夹角>3.0度=5 → 4`，`脚骨偏航结论 骨=0.0 趾=0.0` 两侧都在。左右不对称消失：`L Toe0 1.13608` vs `R Toe0 1.13581`。
    - **改动二（轴夹角）判据落进「锁骨确实被重解」这一支 —— 但它指向的那条杠杆当场就死了，两条独立证据**：`摆位按骨盒射线 … 差=0.477369 轴夹角=3.32696度`（首摆）→ `差=0.955353 轴夹角=6.66109度`（此后每一遍）。两个数咬死：`2·8.22213·sin(6.66109/2°) = 0.9553` —— **`差` 就是锁骨轴偏角的弦长，0.955 不是段长问题、不是落位问题，全部是锁骨骨盒的方向**。而 6.66109 与 `结构向 Bip001 L Clavicle 夹角=6.66109` 是同一个数（`R` 侧 6.54362），即复核表与射线诊断互证。第 11 条预登记的分支是「轴夹角跟着涨 ⇒ 下一轮谈先转胸椎再算射线」，**这一轮同时把它否掉了**：① 日志顺序已经证明射线是在所有转角之后重算的 —— `转角1:Bip001 Spine2 夹角=16.1557->0.0198` 在第 101 行，而 `轴夹角=6.66109` 的最后一行（154）就在 `收尾重摆(全摆…): 成功=30`（156）之前，也就是说**射线用的就是转完胸椎之后的锁骨轴**，再调顺序不会变这个数；② 锁骨自己转不动是两轮实测过的（取舍表里记着 `转角 Bip001 L Clavicle 夹角=6.60331->6.60331` 后按设计回滚）。⇒ **#29 的 0.955cm（占身高 0.85%）是 Biped 锁骨约束的定价，不是待修的 bug**：要么接受，要么放弃射线回到「手臂链整体偏高」（用户 11:11 否过的那件事）。建议按接受结案，把这一轮的时间留给指骨。
    - **改动三（反射换工具）：两个符号在 Max 2022 都是未定义的**，`Biped接口 反射失败：dumpInterface=-- Type error: Call needs function or class, got: undefined | getMethodCount=0 -- Type error: Call needs function or class, got: undefined`，`biped_api.txt` 没落盘。**这条改动本身值回票价**：以前只报「=0」，现在能分清「拿不到方法表」和「名字不存在」，并且一次跑就把 `dumpInterface` 这个猜测也排掉了。接口层到此为止，别再猜第三个名字。
    - **新 blocker（这一轮最大的收获，且不用开 Max 就能证死）**：复核表按 `pivot偏差` 排下来，左手仍剩四根 —— `L Finger32=6.39328`、`L Finger42=4.73953`、`L Finger31=3.61871`、`L Finger41=2.85972`，而它们的**指根** `L Finger3=3.8e-06`、`L Finger4=7.2e-07` 在 PMX 上，`段校验` 说段长也对（`L Finger3 -> L Finger31 误差=0.0278664`，其余 0.0/4e-06）。**指根对 + 段长对 + 指尖差 3.6cm** 只可能是方向：拿余弦定理反解，`cos φ = (2.58958² + 2.56171² − 3.61871²) / (2·2.58958·2.56171) = 0.0130` ⇒ **φ = 89.26 度**；`L Finger4` 那条同样算法给 89.26 度。而 89.26 = 90 − 0.74，正好是同一行报的 `夹角=0.789815` 的补角 —— 也就是说**转角把「另一根局部轴」对齐到了 PMX 方向，骨头的长度轴反而被转到垂直方向**，子关节就此横着飞出去 3.6cm。
    - **为什么会挑错轴，代码里是一条明写的设计**：`mmd2maxStructErr`（门控）和 `mmd2maxRotateBoneToDir`（写入）都在**三根局轴里挑与 `want` 点积最大的那根**去对齐，`mmd2maxLogStructDir` 也只报那根的夹角（`bestAx`）。多数骨的最顺轴 == 长度轴，所以一直是对的；对不上的骨在日志里自己喊出来了 —— `结构向 Bip001 L Finger3 轴3` 而 `肢段 Bip001 L Finger3 -> Bip001 L Finger31 … 轴=1`，左右手还不对称（`R Finger3` 报 `轴1`，所以右手整只干净）。**这条设计让 6 根骨（`L Finger3/4/31/41/32/42`）拿到 0.79 度的假绿灯**，`收尾转角 尝试=0` 里就有它们。截图 `香菱_hand_front.png` / `香菱_hand_side.png` 里白色指骨盒竖直朝天、不贴着青色网格手指，就是这个 89 度的样子（图与数一致）。
    - **第八轮已落地（代码 + 契约，静态全绿，等一轮实跑）**：开关一个 `global MMD2MAX_LENAXIS = true`；账在 `mmd2maxFitSegments` 里记 —— `append MMD2MAX_LENAXIS_MAP #(pnode.name, usedAxis)`，三个准入条件 `err <= 0.05`（没收敛的 `usedAxis` 是试出来的假轴）、`usedAxis` 在 1..3（4 = 三轴等比例，没指出长度在哪根轴上）、`kids.count == 1 and kids[1] == cnode`（只给独子骨记账），函数开头 `MMD2MAX_LENAXIS_MAP = #()` 清账防同一 Max 会话重跑串账。键用 **Max 节点名**不是 PMX 骨名（场景里已有同名 Biped 时节点会被改名，两边都用 `node.name` 才对得上）。查表 `fn mmd2maxLenAxisOf nm` 三处消费一起换：`mmd2maxStructErr`（门控 + `mmd2maxFitScore` 打分）、`mmd2maxRotateBoneToDir`（`bestAx = la`，写入与门控同轴）、`mmd2maxLogStructDir`（复核，行里打 `按长度轴`，与最顺轴不一致时再打 `最近轴=N`）。**helper 的定义位置踩过一次 #20**：第一次插在 `mmd2maxBipedAxes` 之后，`check_ms` 立刻报它被 30 行前的 `mmd2maxLogStructDir` 前向引用 —— 已挪到 `mmd2maxLogStructDir` 之前并加了防回归断言。
    - **这一轮改动的作用面不用猜，拿第七轮的日志就能算**：逐骨比 `肢段 … 轴=N` 与 `结构向 … 轴N`，**不一致的恰好只有 4 根** —— `L Finger3`、`L Finger4`、`L Finger31`、`L Finger41`。`L Finger32/42` 是叶子（无子可量）不记账；`Pelvis` 三子、`Hand` 五子被独子条件挡在外面；`Foot` 记到 `轴2`、与复核一致 ⇒ **脚链零改动**（#26 #28 的地盘不动）；躯干/颈/锁骨/腿/**整只右手**全部一致 ⇒ **右手就是这一轮的对照组**。预期读数：`L Finger31/32/41/42 pivot偏差` 从 3.62/6.39/2.86/4.74 掉到 **0.03 量级**（0.0279 = `段校验 L Finger3 -> L Finger31` 的段长误差，是这条链的物理下限），代价是 `转角1:L Finger3 夹角=` 从 47.9 变成 ~89 度并被真转一遍，循环内 `夹角>3度` 先涨后落 —— 那是读数变诚实，不是变差。
    - **预登记：这一轮故意没覆盖叶子骨**。`Finger32/42`、`Toe0`、`Head` 没有子关节，长度轴量不出来，仍走最顺轴 ⇒ 它们的 **pivot 会被父骨修对，但自己的骨盒方向可能仍指偏**（`L Finger32` 那一行还会报 `轴3`）。下一轮若截图仍见指节盒横着，就给叶子补一条实测路：`mmd2maxLocalExtent` 已经在算三轴跨度，取最长那根即长度轴（Foot 会给 轴2、手指给 轴1，与现有读数一致，所以不冲突）。
    - **仍在账上的小项**：`L Calf pivot偏差 0.633266 → 1.36298`（这一轮回归，`R Calf 1.25129`，两侧对称 ⇒ 更像小腿被 `#both` 的落位/转角抢，不是新机制）；`Bip001 R Hand 夹角=90.5409` 是 0.14cm 显示 stub，被 ≥1cm 门挡掉、`短骨` 标注在案；`落位最大=8.84383 → 11.8502`（第 2 遍起）就是上面那根指骨，不是脚。`L/R Toe0 ≈ 1.136` **这一条已经算清、不是缺陷**：`段校验` 给 `L Foot -> L Toe0 实际=11.2672` 而 PMX 要 11.2281，两点距离对得上、`L Foot pivot偏差=6.5e-06` 也在，剩下 1.136 反解成方向角 = 5.33 度 —— 正是 #28 把脚盒压平（`want` 取水平投影）时舍掉的那段俯仰（PMX 足首 tail 落在踩地点、比踝低 5.59cm），是设计的结果不是漏修。
    - 静态：`check_ms [OK]`、`Ran 210 tests OK`。新增契约类 `LengthAxisContract`（5 条：门控量长度轴 / 写入与门控同轴 / 账从摆位前的 `肢段` 实测来 + 三个准入条件 + 清账 / helper 定义顺序防 #20 / 一个开关且三处消费点计数），`test_struct_direction_target_is_caller_supplied` 随签名加 `nodeNm`；模块 docstring 加规则 28（含余弦定理那一步的完整算式）。

13. **第八轮（14:42:05→14:43:22，香菱，退出码 0，`香菱.max` 落盘，12 张截图，`quitMax` 干净）：#28 的假绿灯被修掉，左手四根指骨按预测落在预测的下限上；这一轮的账基本清完，剩的都是已定性的设计代价**。
    - **正面判据（第 12 条预建的数）逐条对上**：`L Finger31 pivot偏差 3.61871 → 0.0451497`、`L Finger32 6.39328 → 0.0688667`、`L Finger41 2.85972 → 0.0281579`、`L Finger42 4.73953 → 0.0471501` —— 预测是「掉到 0.03 量级，下限 0.0279 = 段长误差」，实测 0.028~0.069，**四条全在预测区间内、没有一条靠巧合**。四行的 `轴` 标注都变成 `轴1按长度轴`（父骨）或 `轴1`（叶子，仍走最顺轴但 pivot 已被父骨带对）。对照组成立：**整只右手一根没变差**（`R Finger31/32/41/42` 0.044/0.067/0.027/0.046，与第七轮同级）。
    - **收敛与落位总量**：`转角尝试 39 → 41`、`累计尝试=47 改善=47 未改善=0 抛错=0`；`落位偏差和` 第 1/2/3 遍 = 150.165 / 124.043 / 122.139（第七轮第 1 遍是 8.84 的最大值，本轮 `落位最大=5.6995`）。**`结构向 复核：可测=51 夹角>3.0度=4`**，四条 = `L/R Clavicle 6.66/6.54`（`#pos`，按设计不转）+ `L/R Forearm 3.44/3.42`（用户判定保持 `#both`）⇒ **指骨一根不超标、脚链一根不超标**。第七轮那条「`R Finger1` 在 Figure 模式内测 ≤3 度、退出后读到 6.05 度」的门控分歧**本轮自己消失了**（`R Finger1 轴1按长度轴 夹角=0.7677 pivot偏差=0`），不用单独排它。
    - **脚链零改动，兑现**：`L/R Foot` 现在记的是 `轴2按长度轴`（与复核同源，`轴=2` 来自 `肢段 L Foot -> L Toe0 … 轴=2`）、`夹角=0.0198/0.0`、`pivot偏差=6.8e-06/4.2e-05`，`脚骨偏航结论 骨=0.0 趾=0.0` 两侧都在，`香菱_foot_front.png` 里两只鞋仍平踩 z=0 网格线、趾盒横在鞋内（#19/#26/#28 没被这一轮动到）。
    - **一处计划外：`Pelvis` 也拿到了长度轴账**（第 12 条预测它被独子条件挡在外面，**挡错了**）。原因查清：准入条件用的是**场景子节点** `pnode.children`，不是 Biped 的肢段子骨列表 —— `肢段 Pelvis -> L Thigh / R Thigh / Spine` 三条都在跑（所以我看成「三子」），但 `Pelvis` 的场景子节点只有 `Spine` 一根，于是 `Pelvis -> Spine`（`轴=1`、`误差=1.26e-05`）通过。后果量过：`转角1:Bip001 Pelvis 夹角=75.8042->0.0`（第七轮同一行是 14.2184，即旧判据只对齐了另一根轴），骨盆盒绕对齐轴多滚了约 61 度；**下游读数与第七轮逐位相同**（`Spine1 pivot偏差=0.00591421`、`Neck 0.0`、`L Thigh 0.105656`、`L Calf 1.36299`、`L Foot 6.79e-06`）⇒ 本轮无害，但这是一条**没人批准过的行为变化**，骨盆盒的显示姿态若被用户判为不对，回滚就是 `MMD2MAX_LENAXIS = false` 一个开关。
    - **顺带把「谁没被覆盖」钉死，别再把口径读错**：本轮 35 根记账 / 16 根走最顺轴。没记账的 16 根 = `Spine`、`Spine2`、`Head`、`L Hand` + 10 根指节叶子 `FingerN2` + `L/R Toe0`。其中 `Spine` 是**意外**：`肢段 Spine -> Spine1` 已收敛（`轴=1`、`误差=9.5e-07`）却没记账 ⇒ 它的场景子节点不止一个（Biped 会在躯干链上挂辅助节点），独子条件把它挡了；`Spine2`（Neck + 两锁骨）、`Hand`（五指）符合预测；`Head`/`FingerN2`/`Toe0` 是第 12 条预登记的叶子。**这 16 根本轮全部夹角 ≤0.79 度、pivot ≤0.069 ⇒ 零影响**，但「记账覆盖 = 所有独子骨」这句话是错的，正确说法是「35 根」。
    - **`落位最大=5.6995` 不是回归、也不是这一轮弄出来的，已证死**：`校验 Bip001 Pelvis 目标=[0,1.86431,83.4439] 实际=[0,-3.54249,81.6409] 偏差=5.6995` —— 前者是 PMX `腰` 骨位置，后者是 `骨盆闭环 第2轮 … 偏差=2.62e-06` 命中的**两髋中点**，两个参考点本来就差 5.7cm。拿 08:01 那份 `香菱_noskin_objrun.log` 对同一行：`偏差=5.6995` 一模一样 ⇒ 与长度轴改动无关，是既有口径差。**以后看 `落位最大` 要先减掉这根**，否则每轮都会误报。
    - **`hand_front` / `hand_side` 里那些竖直朝天的白色大盒子不是指骨、不是缺陷，查到底了**：`bones.tsv` 里 10 根 `左/右 親指先…小指先` **没有映射到人形链**（`Bip001` 那列是空）⇒ 按设计落成 `BoneGeometry` 附属骨，而它们的 PMX 显示向量逐字是 `pos → tail = (0, 0, +7.66)`（长度 7.66cm、笔直向上），MMD 就是这么画指先骨的。取景框 `hand 取景：… 框=[31.79,-10.20,77.23]..[53.06,9.53,95.86]` 与 `左中指２/３` 的 PMX 位置（X 45.3→47.1、Z 84.5→83.2）对得上 ⇒ Biped 指骨确实在手指上。**下一轮别再从这张图判指骨方向**；要判叶子骨盒朝向，用 `mmd2maxLocalExtent` 那条实测路（第 12 条已写方案），或者直接问用户要一张他自己视口里选中指骨的截图。
    - **本轮之后 #29 的建议**：`0.955cm`（占身高 0.85%）是 Biped 锁骨约束的定价，第 12 条已用两条独立证据封掉「先转胸椎再算射线」这条杠杆；`校验 … 对射线偏差=1.9e-06（b 方案认这个）` 两侧都在，`Forearm 1.015/1.010`、`Hand 0.1435` 是它的下游。**建议按「接受 0.955」结案**，剩下的 `L/R Forearm 3.4 度`、`L/R Calf 1.36/1.25cm` 都是用户已定的 `#both` 代价。
    - 静态：`check_ms [OK]`、`Ran 210 tests OK`（本轮没改代码，只读账）。

14. **第九轮（15:57:56→15:58:58，香菱，退出码 0，`香菱.max` 190,877,696 字节落盘，12 张截图，`quitMax()` 干净）：用户报的三条（左腕错位 / 脚尖扳不动 / `Bip001 Head` 歪）各自的补丁全部兑现，回读真值与预登记数字逐位对上**。
    - **落地的是三处**：取舍表加两行 `* Hand` / `* Head` → `#pos`（#33 #34，只保落位、不写转角）；`export_obj._split_toe_weights`（#35，Python 侧主动把足首权重的一部分划给 `Toe0`，开关 `TOE_SPLIT`）。
    - **预验证阶段抓到一个单位坑，它没烧轮 —— 记下来防再犯**：`heads` / `tails` 存的是**已乘 scale 的导出坐标**，不是厘米。第一版把过渡带写成 `band = 2.0`「厘米」、可信门写成 `span < 1.0`，实测 `足轴只有 0.12cm` 全量跳过 —— 真相是香菱 `L Foot -> L Toe0` = 0.1201 **导出单位** = 12.01cm（`build_plan(scale=0.08)`），而 `run_model.py` 走 `auto_scale(target_cm=160)` ⇒ scale=7.6563、同一段是 11.73 单位。**结论：`export_obj` 里任何绝对长度阈值都是错的，一律用比例**。现在 `TOE_SPLIT_BAND = 0.2`（足长的 20%）、`TOE_SPLIT_MIN_RATIO = 0.05`（足轴短于小腿 5% 就不认它指得出脚尖方向），并由 `test_band_is_relative` 钉住（scale×10 后每个顶点的份额必须逐位不变）。
    - **#35 正面判据（离线算 → 实跑读回，逐位相同）**：`脚尖改绑 Bip001 L/R Toe0：从 Bip001 L/R Foot 手里拿到 74 个顶点（其中 60 个在过渡带 = 足长 ±20% 里混权重）` ⇒ `蒙皮覆盖 Bip001 L Toe0 顶点=74 / R Toe0 顶点=74`，`L Foot 377→363`、`R Foot 380→366`。**自洽性算式**：74 个里 60 个是过渡带内的**混权重**（仍算 Foot 命中），只有 14 个整份划走 ⇒ 377−14=363、380−14=366，一涨一落、总数不变；`已写入顶点 18892，失败顶点 0`。契约在 `ToeSkinProbeContract`（`boneVertHits[sid] = boneVertHits[sid] + 1` 显式赋值，不用数组下标复合赋值 —— 那是 MAXScript 边界情况，不值得拿一轮实跑试）+ `test_real_model.test_toe0_is_never_silently_unbound`（`Toe0` 命中为 0 时**必须**有「脚尖改绑跳过」那行，换模型不许静默失败）。
    - **#33 判据**：`转角不做 Bip001 L Hand 落位优先` / `转角不做 Bip001 R Hand 落位优先`（第 103/122 行），`校验 Bip001 L Hand 偏差=0.143507` vs `R Hand 0.140112` —— **左右对称**，第七轮那一下 `转角1:Bip001 L Hand 夹角=43.1174->0.0` 没了。**左手四根指骨与第八轮逐位相同**（`L Finger31/32/41/42 pivot偏差` = 0.0451513 / 0.0688677 / 0.0281579 / 0.0471489，第八轮是 0.0451497 / 0.0688667 / 0.0281579 / 0.0471501）⇒ 腕不转了，指链也没有再被甩出去；30 行指骨 `夹角` 全部 ≤0.793 度。
    - **#34 判据**：`转角不做 Bip001 Head 落位优先`，`校验 Bip001 Head 目标=[8.8e-05,-1.03841,122.965] 实际=… 偏差=0.0`（第七/八轮它是被 49 度转角写过的）。复核表现在诚实地留着 `结构向 Bip001 Head 轴2 … 夹角=48.889` —— 这一条**不是新缺陷**，是「不转 Head」的另一面：Biped 头骨盒的 `轴2` 与 PMX `頭` 显示向量本来就差 49 度（PMX 頭骨 tail 在头顶偏后），以前是靠转角硬拧对齐、代价就是用户看到的「向后仰了好多」。
    - **`结构向 复核：可测=51 夹角>3.0度=4 → 6`，涨的两条都是读数变诚实**：新增 = `Head 48.889`、`L Hand 43.7967`（这两根本轮起不转了）；同时 `L/R Calf 3.12 → 2.05128 / 1.88336` **自己掉出超标名单**（腕不转 ⇒ 上肢不再抢躯干，小腿跟着回落）。超标的 6 条 = `L/R Clavicle 6.66/6.54`（`#pos` 按设计不转）+ `L/R Forearm 3.44/3.42`（用户判定保持 `#both`）+ `Head` + `L Hand`；`短骨不计=1` 仍是 `R Hand 90.5409`（0.14cm 显示 stub）。
    - **无回归（逐条对上，不是「大概没坏」）**：`脚骨偏航结论 L/R Foot 骨=0.0度 趾=0.0度`；`校验 L Foot 6.79e-06 / R Foot 4.2e-05`；趾盒仍在鞋内且尺寸未变（`趾长 1.04444 -> 3.56919 目标=3.56919`、`截面 … -> 5.31702/1.86092`、`鞋尖=[7.04908,-11.5871,1.459]`）；`转角 pass：接口=bipedRotation 累计尝试=45 改善=45 未改善=0 抛错=0`；`收尾重摆(全摆): 成功=30 失败=0` → `收尾转角(只转方向优先的骨): 尝试=0`；`落位最大=5.6995` 就是第 13 条那根 `Pelvis` 口径差（两髋中点 vs PMX `腰`），没变；`Morpher 通道：61 / 61`。
    - **截图判读（验收只认图）**：`neck_front` / `neck_side` —— 绿色颈+头骨笔直立在中线，无左倾；`bone_front` / `bone_side` —— 头骨盒在颈正上方，白色那团是脸部附属骨（按 PMX 位置直落，不经 Biped）；`hand_front` / `hand_side` —— 紫色指骨盒沿手套走，白色大锥体是第 13 条查清的 `親指先…小指先` 附属骨（PMX 显示向量 `(0,0,+7.66)`），**不是缺陷**；`foot_front` / `foot_side` —— 两只鞋平踩 z=0、趾盒横在鞋内。**唯一图证不了的是 #35**：静态图看不出脚尖跟不跟 `Toe0` 动，机械证据是 `顶点=74`，最终判定要用户在 Max 里扳一下 `Bip001 L Toe0`。
    - **换模型不闯祸（离线验过）**：克雷薇（`test_real_model` 默认夹具）打的是 `脚尖改绑跳过 Bip001 L/R Toe0：源文件已给它 45 个顶点（Bip001 L/R Foot 121 个），不改作者权重` —— 作者自己绑好的模型一律不插手（判据 `pre_toe * 20 >= pre_foot`，即 Toe0 已拿到足首命中数 5% 以上就算作者有意图）。
    - **回滚各一处，都是一行**：脚尖改绑 `TOE_SPLIT = False`（关掉后权重与源文件逐字节相同）；取舍表删掉 `* Hand` / `* Head` 那两行即回到第七/八轮行为。
    - 静态：`check_ms [OK]`、`Ran 221 tests OK (skipped=1)`（基线 210 → 新增 `ToeSkinTests` 7 条 + `ToeSkinProbeContract` 3 条 + 真实模型 1 条 + 取舍表契约扩 2 处断言：`assertEqual(self.prio.count("return #pos"), 3)` 锁「保落位的骨只有锁骨/腕/头三组」）。

15. **第十轮（香菱，退出码 0，12 张截图 18:07 落盘，`quitMax` 干净）：#36 小腿内八与 #37 脚穿模的两条新判据同时落地，#36 当场闭环；代价是挖出 #38 —— 一条由 #36 自己带出来的回归**。

    - **#36 正面判据（大腿扭转角闭环）逐条对上**：`大腿扭转 Bip001 L Thigh 起始膝偏差=1.3208 第1遍 转=-28.0269度 膝偏差1.3208->0.158355 踝偏差=7.05655e-06 第2遍:残差=-0.434811度 已达容差1.0 收敛 迭代=1 净改善`；`R Thigh 起始膝偏差=2.43138 第1遍 转=45.894度 ->0.776808 第2遍 转=-13.0735度 ->0.246829 迭代=2 (两遍未收敛) 净改善`。落位端 `校验 L/R Calf = 0.158318 / 0.24703`（预登记门槛 ≤0.30，第九轮是 1.36/1.25 ⇒ **内八是真的被转掉的，不是被容差掩盖的**）；`香菱_bone_front.png` 里两条腿竖直平行、不再向内撇。诚实账：`结构向 L/R Thigh 夹角 = 0.392669 / 0.543926`，右侧略超预登记的 ≤0.5（0.044 度），按「读数不许美化」记在案上。
    - **#37 的量具是对的，症状没清**：`足背 Bip001 L Foot 截面 9.49756/6.87028 -> 宽=3.92599 高=1.53055 宽高轴=3/1 长度轴=2 迭代=1/2`（`R` 3.92605/1.53067）—— 骨盒截面从 8.00×6.61cm 收到鞋内切盒子的 3.93×1.53cm，第九轮那个从脚后跟顶出来的楔形在侧视里没了。但截图上换成了**一根骨盒浮在脚背上方**，于是顺着回读表去查，才发现不是量具的问题。
    - **#38（本轮新 blocker，由 #36 带出）**：`段校验 Bip001 L Foot -> Bip001 L Toe0 误差=1.49461`（`R` 1.49644），而第九轮同一行是 3.2e-05；`结构向 Bip001 L Toe0 pivot偏差=4.7362`。把踝 `[5.32852,1.01683,7.53468]` 与球部目标 `[6.46249,-8.653195,1.942268]`（|F−T|=11.2281）拿来做分解：趾关节实际落在**沿轴 8.83cm、垂直轴 4.09cm** 的位置 —— 也就是趾骨被抬到了脚背上面。机制：脚趾例程在收敛循环**之前**摆 Toe0，之后的转角若干遍、新加的大腿扭转、`收尾重摆(全摆): 成功=30` 一路把这片链尾拽走，而 `收尾转角(只转方向优先的骨): 尝试=0` 证明**再没有任何一道写回头管它**。⇒ 定了一条规则（契约第 31 条）：**链尾的落位归链尾自己管**，排在所有收尾写之后。
    - 静态：`check_ms [OK]`、`Ran 233 tests OK (skipped=1)`。

16. **第十一轮（19:19:55→19:21:22，香菱，退出码 0，`香菱.max` 落盘，12 张截图 19:21，`quitMax` 干净，无弹窗）：#38 按预登记判据全绿闭环，#36/#37 的既有读数逐位未退**。

    - **#38 正面判据（`脚趾复摆`，`MMD2MAX_TOETOL = 0.5`）**：`脚趾复摆 Bip001 L Toe0 起始偏差=4.7362 摆后=0.0 踝偏差6.50319e-06->6.50319e-06 足长=11.2281 净改善`、`R … 起始偏差=4.73899 摆后=9.61096e-07 踝偏差3.38764e-05->3.38764e-05 足长=11.2281 净改善`、汇总 `净改善 2 根`。**写前=写后的踝偏差**是这条改动的附带伤害证明：只动链尾，没有把踝顶走。下游三行同步归零：`段校验 Foot -> Toe0 误差 1.49461/1.49644 → 4.76837e-06/2.19345e-05`（第九轮水平）、`结构向 L/R Toe0 pivot偏差 4.7362/4.73899 → 0.0/1.07288e-06`、`夹角 → 0.0/0.0`。
    - **不许退的账逐位对上**：`校验 L/R Calf 0.158318 / 0.24703`（与第十轮**完全相同** ⇒ #36 没被这次复摆碰掉）、`校验 L/R Foot 6.48569e-06 / 3.38328e-05`、`结构向 L/R Foot 夹角 0.0197823 / 0.0`、`脚骨偏航结论 骨=0.0 趾=0.0`、`足背 L/R 3.92599/1.53055 · 3.92605/1.53067`（与第十轮逐位相同 ⇒ #37 的截面判据没被改写）、`结构向 复核：可测=51 夹角>3.0度=6 短骨不计=1`（还是第 12/14 条那六条已定价的）、`校验 Pelvis 偏差=5.6995`（第 13 条的口径差）、`Morpher 通道：61 / 61`、`蒙皮覆盖 L/R Toe0 顶点=74`。
    - **截图取景同时改了（#37 的教训）**：`foot_front` / `foot_side` 原先按**全身** X/Y 边界取景，整只鞋在 984px 画面里只占 100px（≈7.4px/cm），4.7cm 的骨盒错位画面上只有十几个像素 —— 第十轮那张「脚穿模」在图上根本看不动。现在按四只脚骨节点收紧到 ±6cm，日志 `foot_top 取景：脚骨节点=4 框=… 侧/正特写框=[-12.4628,-14.6559,-2]..[12.4625,7.01683,19.6719]`，同一张约 22px/cm。**新坑一条**：`foot_top` 那张这轮被裙摆糊住了 —— 俯视取景框只到 z=19.67，但 Max 的 zoom 不裁剪高度，裙子在脚的正上方，投影下来完全遮住两只鞋 ⇒ 下一轮判脚要么补一张藏网格的脚部特写（`mmd2maxSetMeshHidden` + `footLo/footHi`），要么别再用 `foot_top` 判脚。
    - **图证仍未闭环的一条（诚实记账）**：正视图里两只鞋各能认出两个骨盒截面（踝上一条、前掌一条），两者中心的竖直距离实测约 3.1cm，与「盒随 pivot 走」的预测（踝 7.53 → 趾 1.94，中点 4.74，与趾盒中心差 2.8cm）吻合、与「盒只按骨自身局轴画成水平」的预测（差 5.6cm）不吻合 ⇒ 支持 #38 修的是真因。但侧视图被鞋面网格遮住、比例尺又只能从图上估，**这条不足以单独判 #37 结案**；要一锤定音就补一张藏网格的脚部侧视特写。
    - 静态：`check_ms [OK]`、`Ran 248 tests OK (skipped=1)`（基线 233 → 新增 `ToeSnapBackContract` 7 条 + `FootCloseupFramingContract` 2 条 + 既有分段标记随新函数位移的三处修正）。回滚一个开关：`MMD2MAX_TOETOL = 999` 整步跳过。

17. **第十二轮（19:58:36→19:59:47，香菱，退出码 0，15 张截图落盘，`quitMax` 干净，无弹窗）：#37 的根因换人了 —— 第十一轮那张「图证仍未闭环」的账，被三张藏网格的脚部特写一锤定音**。

    - **#38/#36 逐位未退**（与第十一轮**完全相同**，含起始偏差 ⇒ 这一轮的写是确定性的）：`脚趾复摆 L/R Toe0 起始偏差=4.7362/4.73899 摆后=0.0/9.61096e-07 足长=11.2281`、`段校验 Foot -> Toe0 误差=4.76837e-06/2.19345e-05`、`画线 … 脚骨偏航结论 骨=0.0 趾=0.0`、`结构向 L/R Foot 夹角 0.0197823/0.0`、`结构向 复核：可测=51 夹角>3.0度=6 短骨不计=1`。
    - **新加的骨盒世界框回读是一条死路，别再试第二次**：四行全打成 `骨盒框 … 尺寸=0.0x0.0x0.0` —— `node.min` / `node.max` 在 `Biped_Object` 上退化成同一个点（`[8.18693,3.59763,7.76727]..[8.18693,3.59763,7.76727]`），量不到**显示盒**；`nodeGetBoundingBox` 的矩阵参数口径又没验过，不值得为它开一轮。这段已从脚本删掉，死路写进注释 + `FootBoneBoxReadbackDeadEnd` 契约（防止下一轮手痒补回来）。
    - **三张藏网格脚部特写给出的图，是回读表给不出的**：`test_runs/香菱/shots/香菱_bone_foot_side.png` 里 `Bip001 L/R Foot` 是**一根水平的板**卡在脚踝/脚背高度，前端与趾骨盒之间隔着一眼可见的台阶；`_bone_foot_front` 同一现象。对着日志才看懂：`画线 Bip001 L Foot 目标=[0.116468,-0.993194,0]` 的 **z 分量恒为 0**、`结构向 L/R Foot 目标倾=-90.0` ⇒ 盒子被 #28 的「一律压平」钉死在水平，而踝 z=7.53、球 z=1.94 —— 它的下沿永远够不到脚底。用户那句「Bip001 Foot的底部没有够到脚的下面」量的从来不是落位（#38 已闭环、`校验 Foot ≤3.4e-05`），也不是截面（第十轮已收到 3.93×1.53），是**俯仰**。
    - 静态：`check_ms [OK]`、`Ran 254 tests OK (skipped=1)`（基线 248 → 新增 `FootBoneBoxReadbackContract` 4 条 + `FootCloseupFramingContract` 2 条；那 4 条本轮已被证伪，见第 18 条）。

18. **第十三轮的改动落地（静态全绿，实跑见第 19 条）：脚链骨盒不再压平，俯仰还给结构；`MMD2MAX_FOOTPITCH` 是这一条的单一回滚开关**。

    - **改的是判据，不是数值**：`mmd2maxBoxWantDir` 对 `* Foot` / `* Toe0` 返回**三维**结构方向（Foot = 踝→球、Toe0 = 球→网格实测鞋尖），`mmd2maxFlatDir` 整个删掉（压平只留在函数内部当回滚分支）。地面保护从「钉成水平」换成**斜度上限**：`if (abs raw.z) > horiz do return undefined` —— 踝→球 物理上不可能陡过 45 度，陡过就判「这根骨的 tail 是关节/踩地点约定，不是盒子走向」，退路沿用 #28 那条「宁可不转」。
    - **偏航一个字没动**（所以 #26 不会被这次改动翻案）：默认分支原样返回 `raw`，既不单位化也不改水平分量，`length want` 还是原长 ⇒ 复核表靠它判短骨 stub 的逻辑不受影响。
    - **单一开关 + 两端同源**：`global MMD2MAX_FOOTPITCH = true`，写 `false` 逐字回到第十二轮那张图。写方向（转角 pass / 收敛打分）与读方向（`画线`/`脚骨偏航结论`、`结构向` 复核）全走同一个函数；本轮顺手把 `画线` 里 Toe0 那行从「自己减两点」改成走函数（`mmd2maxBoxWantDir cName toeTgt toeTgt tipPos`），否则翻开关时「写」变了、「读」没变。Python 端量鞋内切盒子的 `_foot_link_box(ankle, ball)` 用的**本来就是这根斜轴** ⇒ 两端判据这一轮才第一次一致。**（第十五轮更正：最后这半句当时就不成立 —— 斜轴两端各量各的没错，但两端都建在「盒子绕轴对称」这个错前提上，见第 20 条；轴现在两头一起压平。）**
    - 静态：`check_ms [OK]`、`Ran 253 tests OK (skipped=1)`（删 4 条已被证伪的死探针契约，加 3 条方向契约：`test_foot_chain_box_direction_follows_the_3d_structure` / `test_foot_pitch_flattening_is_the_single_rollback_switch` / `FootBoneBoxReadbackDeadEnd` 2 条，另把压平日志文案与 `fn mmd2maxStructErr` 的切片终止标记跟着改）。
    - **预登记第十三轮的判据**（按香菱实测点算出来的期望值，不是「希望它好」）：
      1. `画线 Bip001 L Foot 目标` 的 z 从 `0` → **≈-0.498**（三维归一化期望 `(0.100994,-0.861286,-0.498073)`，R 只改 x 的符号）；`Toe0` 期望 `(±0.193545,-0.968043,-0.159455)`。
      2. `结构向 L/R Foot 目标倾` **−90.0 → ≈−120.0**；`L/R Toe0 目标倾` **−90.0 → ≈−99.3**。这两行是「压平真的取消了吗」的直接读数。
      3. `脚骨偏航结论 骨=0.0 趾=0.0` **不许变**（偏航同源，变了就是这次改动动了不该动的东西）。
      4. **本轮唯一的真风险 = Biped 肯不肯给脚骨这个俯仰**。读数分两支：`差倾≈0.0` 且 `转角1:Bip001 L/R Foot 夹角 → 0.0x度` ⇒ 转成功；若像锁骨那样 `夹角=x->x` 纹丝不动，`mmd2maxRotateBoneToDir` 的「不比原来好就写回原矩阵」会把场景留成第十二轮的样子 ⇒ 设计内失败，一轮不亏，但 #37 得换题（改 Figure 模式内踝的 Z 转角，而不是骨盒转角）。
      5. **落位不许被新转角拖走**（#38 复发监测）：`段校验 Foot -> Toe0 误差 ≤0.05`、`脚趾复摆 摆后 ≤0.01`、`校验 L/R Foot ≤1e-4`、`校验 L/R Calf ≤0.30`。
      6. `结构向 复核：可测=51 夹角>3.0度` **仍 =6**；脚链若进名单（7/8）就是第 4 支的另一种写法。
      7. 图：`香菱_bone_foot_side.png` 里 Foot 盒应从踝**斜插**到球、与趾骨盒首尾相接，那个 5.6cm 的台阶消失。

19. **第十三轮（20:17:01→20:17:34，香菱，退出码 0，`香菱.max` 落盘，15 张截图 20:17，`quitMax` 干净，无弹窗）：#37 结案 —— 脚链骨盒的俯仰还给结构，Biped 给得起这个转角，落位一位没退**。

    - **预登记 7 条逐条对上**（第 18 条那串期望值）：
      1. `画线 Bip001 L Foot 目标=[0.100994,-0.861234,-0.498073]`（期望 `(0.100994,-0.861286,-0.498073)`，z 分量从第十二轮的**恒为 0** 变成 −0.498）；`R` = `[-0.101003,-0.861053,-0.498383]`；`Toe0` = `[0.193549,-0.968046,-0.159456]` / `[-0.193584,-0.967981,-0.159805]`（期望 ±0.1935/−0.9680/−0.1595）。
      2. `结构向 Bip001 L/R Foot 目标倾=-120.042/-120.063 实测倾=-120.042/-120.063 差倾=0.0/-1.52588e-05`（期望 −120.0）；`L/R Toe0 目标倾=-99.3537/-99.3744 差倾=0.0/7.62939e-06`（期望 −99.3）。**压平确实被取消了，而且骨盒真的跟着斜下去了。**
      3. `脚骨偏航结论 L 骨=7.62939e-06度 趾=0.0度`、`R 骨=7.62939e-06度 趾=0.0度`（第十二轮是 0.0/0.0）⇒ 偏航在测量噪声内没动，#26 没被翻案。
      4. **真风险判掉了：Biped 肯给脚骨这个俯仰** —— `转角1:Bip001 L Foot 夹角=30.5448->0.0`、`R 30.5647->0.0`。起始那 30.5 度正是 #28 当年压平掉的角度（按踝→球算 29.93 度，差 0.6 度是骨盒原本也不是严格水平），写完归零 ⇒ 不是「设计内回滚」那一支。
      5. 落位没被新转角拖走：`段校验 L/R Foot -> Toe0 误差=5.72205e-06/2.38419e-05`（门槛 ≤0.05）、`脚趾复摆 摆后=1.01852e-06/9.83025e-07`（≤0.01）、`踝偏差 6.50319e-06->6.50319e-06 / 3.38764e-05->3.38764e-05`（写前=写后，零附带伤害）、`校验 L/R Foot 6.48569e-06/3.38328e-05`。**顺带一条副产品**：`脚趾复摆 起始偏差` 从第十二轮的 4.7362/4.73899 掉到 **1.91311/1.91492** —— 转角指向球部之后，链尾被拽走的路程短了一半多，「转角与落位自相矛盾」这件事本身就是被压平撑着的。
      6. `结构向 复核：可测=51 夹角>3.0度=6 短骨不计=1` ⇒ 脚链没进超标名单，那六条还是第 12/14 条已定价的（锁骨×2 / Head / Forearm×2 / Hand）。
      7. 图：`test_runs/香菱/shots/香菱_bone_foot_front.png` 里两只脚都是「小腿竖直下来 → 脚骨**斜插**到前掌 → 趾骨盒平放在地面上」，首尾相接，第十二轮那根浮在脚背上的水平板和 5.6cm 台阶没了；`香菱_foot_side.png`（带网格）里斜下去的脚骨盒与橙色趾骨盒都落在鞋腔内。
    - **不许退的账逐位相同**：`校验 L/R Calf 0.158318/0.24703`（= 第 10~12 轮，#36 未退）、`校验 L/R Thigh 0.105656/0.10566`、`足背 L/R 截面 3.92599/1.53055 · 3.92605/1.53067`（#37 的截面判据没被改写）、`校验 Pelvis 偏差=5.6995`（口径差）、`校验 L/R UpperArm 0.955353/0.938523 对射线 1.90944e-06/1.91107e-06`、`Morpher 通道：61 / 61`、`蒙皮覆盖 L/R Toe0 顶点=74`。
    - **回滚仍然只有一处一个词**：`global MMD2MAX_FOOTPITCH = false` ⇒ 写方向与读方向一起逐字回到第十二轮。**（第十五轮更正：这一条的「5.6cm 台阶没了」不是结案，是换了一个错 —— 见第 20 条；回滚那个词现在也搬到导出侧 `MMD2MAX_FOOT_HEELBOX` 了。）**
    - ⇒ **#37 结案**（用户那句「Bip001 Foot的底部没有够到脚的下面」与「脚穿模」的机制解释完毕：截面收紧（第 10 轮）+ 链尾复摆（第 11 轮）+ 俯仰还给结构（第 13 轮）三件事各治一段，缺一都还是错的）。#12 的视觉验收仍开着，等用户对这一版脚的最终判断。

20. **#39 的改动落地（第十四轮回读 + 第十五轮预验证，静态全绿）：脚盒量的不是「对称内切」，是 Biped 自己钉的那 84.8%/15.2% 摆位；轴两端一起压平**。

    - **用户原话**：「第一张是你现在的脚掌的位置和模型大小，第二张是我想要的像一个框一样框住脚的脚跟」。第一张 = 第 10/13 轮那根 `3.93×1.53` 的牙签板斜搭在脚背上；第二张画的是一个**竖直边 + 水平边的矩形**，从踝一直落到地面、把脚后跟框进去（允许它的前上角悬在斜下来的脚背上方）。
    - **第十四轮回读（`.max` 里对 `Bip001 L Foot` 用 `nodeGetBoundingBox node node.transform`）把 §6 那条「骨盒绕骨轴中心对称」直接推翻**：本地 min/max = `[−1.44334, −2.37011, −3.40802] … [8.05423, 7.93483, 3.46225]`，即长度轴 [−21.11%, +70.67%] × 段长、竖直 **15.2% 在上 / 84.8% 在下**、横向对称。`#scale` 逐字保持这些比例（第十五轮 `足盒缩后` 再读一遍，比例仍是 1.31545 : 7.34056 = 15.2 : 84.8）。⇒ #19/#37 的 `高 = 2 × min(上, 下)` 从第一天起就把一只本来就挂在轴下方的盒子按对半砍，1.53055 是这么来的，**不是压平造成的**（第 18/19 条那句「压平留下 5.6cm 台阶」因此作废）。
    - **改判据（`mmd2max/export_obj.py`）**：`_foot_link_box(pts, ankle, ball, heelbox=None)` 在 `heelbox=True` 时 ① 轴取踝→球的**水平投影**；② 沿轴窗 = `[FOOT_BOX_BACK_FRAC, FOOT_BOX_FWD_FRAC] × 段长` = [−21.11%, +70.67%]，即骨盒真正存在的那一段（盒尾自己就往后探出 2.37cm，**框住脚跟靠的就是它**）；③ `高 = 窗内最低点离轴的距离 ÷ FOOT_BOX_DOWN_FRAC(0.848)`，封顶段长 ⇒ 盒底正好落在鞋底；④ **宽只许盒子里面的网格投票**（`o ∈ [−下, +上余量]` 才进带）。
    - **④ 是预验证时新发现的坑，不是装饰**：轴一压平，量带从斜轴那条 11.2cm 挪成一个更宽的横条，香菱的裙摆顶点（离轴 `o=+136cm`、横向 `s=+4.2`）混进带里，把宽从 3.71 撑到 **8.44 —— 比整只鞋（6.41）还宽**，左右还差 2.2cm（`L 8.436 / R 6.245`，正是 #26 修过的那类左右不一致）。探针 `tmp/probe_obox.py` 逐带给数：第 05 带「全部」= `s[−6.918,4.218]`（宽 8.44）vs「盒内」= 5 点太稀不投票；闸加进去后 L/R 宽 = `3.71424 / 3.71432`（差 8e-05）。
    - **两端同源**：轴的口径只写在 Python 一处 —— `MMD2MAX_FOOT_HEELBOX` → `meta.tsv: foot_pitch` → `obj_run.build_script` 注入 `MMD2MAX_FOOTPITCH = false` → `mmd2maxBoxWantDir` 的压平分支。Max 脚本的声明改成 `global MMD2MAX_FOOTPITCH` + `if … == undefined do … = true`（单独 fileIn 这份 .ms 时仍是第十四轮口径；无条件赋值会把注入值踩掉，`test_foot_pitch_flattening_is_the_single_rollback_switch` 钉住这两行）。
    - **出货前静态核对（`tmp/probe_final_box.py`，按回读比例把盒子摆进世界坐标）**：L 盒 AABB `x[3.208,8.097] y[−7.080,3.587] z[0.194,8.850]`；盒底 z=0.194 vs 鞋面最低 z=0.193 ⇒ **戳底 −0.001（贴住鞋底）**；盒后 y=3.587 vs 鞋最后 y=4.006 ⇒ 差 **0.418cm**（这是 Biped 固定 −21.11% 的天花板，再往后只能加长踝→球段，而那是 PMX 的足长，不许动）；盒顶 z=8.85 在靴筒里（靴筒到 z≈17）不算穿模。
    - 静态：`check_ms [OK]`、`Ran 263 tests OK (skipped=1)`。改动：`FootLinkBoxTests` 六条按新口径重登记（每条顺手钉 `heelbox=False` 回滚位）+ 新增 3 条（段长封顶、平轴量带、盒顶以上网格不许撑宽）+ `FootHeelBoxHandoffTests` 4 条（四个回读比例钉成用例、meta 记轴、注入头压平、老 meta 无 `foot_pitch` 时保持 true）+ 契约测试 1 条改判声明写法。

21. **第十五轮（21:25:41→21:26:40，香菱，退出码 0，`香菱.max` 落盘，15 张截图 21:26，`quitMax` 干净，无弹窗）：#39 按预验证的数逐位落地 —— 脚盒从牙签板变成框住脚跟的厚框，盒底贴住鞋底**。

    - **新机制的读数全中**：`足背 Bip001 L Foot 截面 9.49756/6.87028 -> 宽=3.71424 高=8.65601 宽/高目标=3.71425/8.65601 … 迭代=1/1`（R `3.71432/8.65963`）—— 高从 1.53055 → **8.65601（5.66 倍）**，宽 3.92599 → 3.71424，两侧一次收敛。
    - **回读比例被 Max 自己复现**：`足盒缩后 Bip001 L Foot 本地 min=[−1.31545,−2.37011,−1.84246] max=[7.34056,7.93483,1.87178]` ⇒ 竖直 1.31545 : 7.34056 = **15.2% : 84.8%**、沿轴 [−2.37011, +7.93483] = **−21.11% : +70.67% × 11.2281**，与第十四轮那次回读逐位相同；盒底 = 踝 z 7.53468 − 7.34056 = **0.19412**，与出货前静态预测 0.194 对上（鞋底 0.193）⇒ **盒底贴着鞋底、没有戳穿**（#19/#37 那句「底部没够到脚的下面」到这里才是真修掉）。
    - **压平确实生效，而且是两端一起压**：`画线 Bip001 L Foot 目标=[0.116468,−0.993194,0]`（z 分量回到 0）、`结构向 L/R Foot 目标倾=−90.0 实测倾=−90.0 差倾=0.0`、`转角1:Bip001 L/R Foot 夹角=6.68932/6.68929->0.0` —— 起始那 6.69 度现在**只剩外八**（第十三轮是 30.5 度，里面 29.9 度是俯仰）。
    - **偏航与落位一条没退**：`脚骨偏航结论 L/R 骨=0.0度 趾=0.0度`（#26）、`段校验 L/R Foot -> Toe0 误差=4.76837e-06/2.19345e-05`、`脚趾复摆 摆后=0.0/9.61096e-07`、`校验 L/R Foot 6.48569e-06/3.38328e-05`（与第 10~13 轮逐位相同）。**副产品**：`脚趾复摆 起始偏差` 1.91311/1.91492 → **0.791129/0.789865** —— 转角不再下扎之后，链尾被拽走的路程又短了一半多，这是「压平让转角与落位更不自相矛盾」的直接证据。
    - **不许退的账逐位相同**：`校验 L/R Calf 0.158318/0.24703`、`校验 L/R Thigh 0.105656/0.10566`、`校验 Pelvis 偏差=5.6995`（口径差）、`校验 L/R UpperArm 0.955353/0.938523 对射线 1.90944e-06/1.91107e-06`（#29 定价）、`结构向 复核：可测=51 夹角>3.0度=6 短骨不计=1`（还是那六条）、`Morpher 通道：61 / 61`、`蒙皮覆盖 L/R Toe0 顶点=74`。
    - **图**：`shots/香菱_foot_side.png`（带网格侧视）里脚盒是一坨**从踝落到地面、把脚后跟整块框住的厚框**，前上角悬在斜下来的脚背上方（用户那张手绘矩形就是这个形状）；`香菱_foot_front.png` 两脚镜像、红盒都在鞋腔内不出侧沿；`香菱_bone_foot_front.png` 两踝的盒底都坐在地上。
    - **单一回滚开关**：`mmd2max/export_obj.py` 里 `MMD2MAX_FOOT_HEELBOX = False` ⇒ 轴回到斜轴、高回到 `2 × min(上, 下)`、`meta.tsv: foot_pitch` 写回 1 ⇒ Max 端 `MMD2MAX_FOOTPITCH = true`，写方向与读方向一起逐字回到第十四轮那张牙签板。**已知的账（不是缺陷）**：① 盒后离鞋最后一点还差 0.418cm，是 Biped 固定 −21.11% 的天花板；② 盒顶前角悬在脚背上方约 4cm，是「一个矩形框住 L 形脚」的必然代价，也是用户手绘图的样子；③ 换成落地长裙摆垂到脚背以下的模型，`down` 会被裙摆拉大（封顶段长），届时按 §6 那条重看；④ 静止姿态压平之后，若以后要把 MMD 动作重定向进来，脚骨的俯仰得由动作自己给。
    - ⇒ **#39 结案（图与数一致）**。#12 的视觉验收仍开着，等用户判这一版脚。
    - **⚠️ 这一条的结案被 #40 推翻**：「图与数一致」里的「数」全是拿压平的轴当目标量的（自己跟自己比），用户下一句就是「为什么你的 Bip001 Toe0 的起始轴就不能接上 Bip001 Foot 的末端」。第 22 条把轴翻回斜的，代价是这一条「框住脚跟」不再成立 —— **两者不可兼得，证明在第 22 条**。

22. **#40（用户：「为什么你的 Bip001 Toe0 的起始轴就不能接上 Bip001 Foot 的末端呢？为什么明明那么简单的一个要求，不要把这个改了之后别的又毁坏了」）：骨盒必须指向自己的子关节 —— 斜轴回归，同时把「看不见这个错」的校验盲区补掉**。

    - **机制（一句话）**：link 的末端天生就是子关节的 pivot（`段校验 Foot -> Toe0 误差=6.7e-06` 是恒等式，不是成绩），但**画出来的骨盒躺在被转的那根轴上**。第十五轮为了框住脚跟把轴压平，于是盒子与自己的子关节差着踝→球那 29.93° 俯仰、垂距 5.59cm（= 踝比脚掌高出的那一截）—— 用户看到的正是 Toe0 那截悬在旁边。
    - **为什么之前每一轮都没发现**：脚链上每一条复核（`转角` / `画线` / `结构向` / `脚骨偏航结论`）比的目标都由 `mmd2maxBoxWantDir` 给出，压平它 = 同时压平「写」和「读」，日志因此全绿。这是项目记忆「恒等式校验看不见偏航」的**第二次**（第一次是 #26 的偏航）。⇒ 新增独立量 `链向 <Foot> -> <Toe0>`：三根局轴各自与**真实子节点位移**求夹角取最小，>3 度记一根断开；判据里不许出现 `mmd2maxBoxWantDir`（`test_foot_chain_continuity_readback_targets_the_real_child` 钉住，含「声明必须在用量之前」）。
    - **为什么 #39 和 #40 只能选一个（出货前用 `tmp/probe_chain.py` 在香菱真网格上算的，不是推测）**：骨盒底面永远垂直于骨轴 ⇒ 轴斜多少度，底面就斜多少度。① 要链上（轴斜 29.93°）还想让盒底够到鞋底，高得 8.87 —— 实测那一格 `斜轴/global/box` 给 `宽=2.2723 高=8.8699`，**盒角最低 z=−2.94（戳进地里）**、后端底 z=2.19，而鞋只在踝后 1.5cm 内有肉；② 要框住脚跟（盒底水平贴地，`平轴/global/box` = `3.7142 × 8.6560`、盒角最低 z=+0.194 贴住鞋底 0.190）就必须压平，压平即断链。⇒ **默认选 #40**（链是结构，用户这次要的是「接上」；脚跟那一框是观感，且 Biped 的 −21.11%/+70.67% 沿轴窗本来就框不满一只 L 形脚）。
    - **改的只有轴的俯仰，量带口径留着**：`_foot_link_box(pts, ankle, ball, heelbox=None, flatten=None)` 新增 `flatten`（默认取 `MMD2MAX_FOOT_FLATTEN=False`）。高的算法按轴分两条：**斜轴 ⇒ 逐带取最紧的下方净空 ÷0.848**（斜面必须在每一带都不穿鞋底，全局最低点那条口径在斜轴上就是上面①那格 −2.94）；**平轴 ⇒ 仍按窗内最低点**（逐带口径在平轴上量不到，`平轴/band/box` 实测返回 None，这条别顺手统一）。宽仍是「只许盒内网格投票」（第 20 条④ 那道闸）。
    - **两个开关各管一件事，不再共用一个**：`MMD2MAX_FOOT_HEELBOX`（高/量带，False ⇒ 第十四轮 `3.9260 × 1.5305`）+ `MMD2MAX_FOOT_FLATTEN`（轴俯仰，True ⇒ 第十五轮 `3.7142 × 8.6560` 的厚框）。`meta.tsv: foot_pitch` 现在只写 `MMD2MAX_FOOT_FLATTEN` 的反值 → `obj_run` 注入 `MMD2MAX_FOOTPITCH`。两个回滚位都**离线逐位复现**过（上面那两格就是），翻一个词即可，不需要重导模型以外的东西。
    - **第十六轮（22:03:07→22:04:06，香菱，退出码 0，`香菱.max` 190877696 字节 / 397 对象，15 张截图 22:03~22:04，`quitMax` 干净，无弹窗）**：
        - **新账（要修的那件事）**：`链向 Bip001 L Foot -> Bip001 L Toe0 骨轴与子关节夹角=0.0度 连线长=11.2281`、R `0.0197823度`、`链向结论 断开=0 根（门槛 3.0 度）`。第十五轮同样的量会读 29.93 度。
        - **盒子尺寸**：`足盒缩后 Bip001 L Foot 本地 min=[−0.352721,−2.37011,−2.33491] max=[1.96828,7.93483,2.37206]` ⇒ 竖直 0.3527 : 1.9683 = **15.2 : 84.8**、沿轴 **−21.11% : +70.67% × 11.2281**（回读比例逐位复现，`#scale` 只改了竖直/横向）；高 1.5305 →（第十四→十六）**2.3210**、宽 3.9260 → **4.7070**，L/R 差 1.2e-4 / 9.3e-5（#26 的账安全）。趾盒 `5.31702/1.86092`、`5.31693/1.85908` 一字未动。
        - **代价（明码，不是缺陷）**：`脚趾复摆 起始偏差` 0.791129/0.789865（第十五轮）→ **2.21343/2.21529** —— 踝→球转 29.9° 会把链尾甩远 2.2cm，复摆后回到 1.9e-06/9.8e-07，落位仍然全中；`结构向 … Toe0 目标倾=−99.3537`（不再是 −90）；脚跟不再被框住，盒底最低角 z=1.876（鞋底 0.190）。
        - **不许退的账逐位相同**：`脚骨偏航结论 L/R 骨=7.62939e-06度 趾=0.0度`、`段校验 L/R Foot -> Toe0 6.67572e-06/2.38419e-05`、`校验 L/R Foot 6.48569e-06/3.38328e-05`、`校验 L/R Calf 0.158318/0.24703`、`Thigh 0.105656/0.10566`、`Pelvis 5.6995`、`UpperArm 0.955353/0.938523`（#29 定价）、`转角2 Forearm 3.44469/3.42171->0.028/0.02`、`转角2 Calf 3.07103->0.0`、`结构向 复核：可测=51 夹角>3.0度=6 短骨不计=1`、`Biped 肢段拟合 成功=39 失败=0`、`摆位 成功=30 髋中点残余=2.6226e-06`、`Morpher 61/61`、`蒙皮覆盖 L/R Toe0 顶点=74`。
        - **图**：`shots/香菱_bone_foot_front.png` / `_bone_foot_side.png` —— 腿→脚盒→趾盒**三段连成一条下坡的链**（第十五轮那两张里趾盒是脱开的一块）；`shots/香菱_foot_side.png` 带网格侧视：脚盒沿鞋背下坡、整个在鞋腔内，趾盒压在鞋头里，没有戳出鞋面；`香菱_foot_front.png` 两脚镜像、趾盒左右各一且不越鞋沿；`香菱_bone_foot_top.png` 两只趾盒按外八各自偏出，与 #26 修好的偏航一致。
    - 静态：`check_ms [OK]`、`Ran 267 tests OK (skipped=1)`。改动：`FootLinkBoxTests` 新增/替换 3 条（默认斜轴 vs 回滚平轴必须给出不同的盒、斜轴逐带口径的盒角不许穿鞋底且比「全局口径 + 斜轴」那格更浅、平轴回滚位仍框住脚跟 z≈0），`FootHeelBoxHandoffTests` 改口（默认 `foot_pitch=1`、新增「注入头保持斜轴」一条、保留「写 false 时压平」一条），契约测试 2 条（声明区改按 `脚链骨盒的俯仰` 取段并钉 `#40`；新增 `链向` 那条）。
    - ⇒ **#40 结案（链连上，图与数一致）**。#39 的「框住脚跟」在这里被明确判为**与 #40 互斥**、默认放弃；要回那一版：`mmd2max/export_obj.py` 里 `MMD2MAX_FOOT_FLATTEN = True` 重导即可（离线逐位复现第十五轮的 3.7142×8.6560）。#12 的视觉验收仍开着。
    - **⚠️ 这一条的「只能选一个」证明被 #41 推翻**：第 411 行那句「要框住脚跟就必须压平、压平即断链」漏算了一件事 —— 断链的不是压平，是**父骨的长度没人补**。斜轴/平轴都能让盒前端面正好过球部（探针 `biped_gap2.ms`），所以 #39 与 #40 其实**可以兼得**，见第 23 条。另外「图与数一致」里的「数」当时仍然全是拿 `mmd2maxBoxWantDir` 的目标量出来的，第十六轮用户下一句就是「左边的是你的结构，右边的是我想要的结构」—— 真正看得见的「盒与盒之间那截空气」没有任何一行日志量过。

23. **#41（用户：「左边的是你的结构，右边的是我想要的结构，你懂吗」+ 两张图）：脚骨盒从「牙签 + 空 3.29cm」改成「厚块落地 + 趾盒接上」—— 补上唯一量得见的判据 `盒隙`，并把 #39/#40 那笔「只能选一个」的账作废**。

    - **用户比的到底是哪两件事**：左图（第十六轮）`Bip001 L Foot` 是一根从踝斜插下去、**末端悬空**的牙签（4.707 × 2.321），`Bip001 L Toe0` 是**脱开的一块**，中间 3.29cm 空气；右图（参考）是一坨**从踝落到地面、沿 heel→ball 铺开的厚块**，趾块接在它的**前下**角上。=> 要的是「厚 + 落地 + 接上」，三件事。
    - **为什么第十六轮全绿却挨了这一句（「恒等式校验看不见偏航」第三次）**：脚链上每一条复核量的都不是那截空气 —— `段校验 Foot -> Toe0` 比的是两 pivot 的间距（link 的末端天生就是子关节的 pivot，恒等式），`链向` 比的是**骨轴与子关节的夹角**（角度，不是距离），`画线`/`偏航` 比的目标由 `mmd2maxBoxWantDir` 给出（自己跟自己比）。**没有任何一行日志量过「父盒前端面到子关节还剩几厘米」**，于是 3.29cm 在日志里根本不存在。⇒ 新增独立量 `盒隙 <Foot> -> <Toe0>`：沿**实测长度轴**算 `子沿轴 − 盒远端`，并要求子关节落在**端面矩形内**（三根局轴各自比 `min−0.05 .. max+0.05`），>0.5cm 或出矩形记一根没接上；判据里不许出现 `mmd2maxBoxWantDir`（`test_box_gap_readback_measures_faces_not_axes` 钉住，含「读数必须在退出 figure 模式之后」）。
    - **第 22 条那句「#39 与 #40 只能选一个」是错的，错在漏算一条**（探针 `tmp/…/biped_gap2.ms`，stock Biped、脚本一次没碰）：① stock 脚骨的**盒远端面 == 子节点沿轴坐标，差 = 0.0** —— Biped 本来就把骨头画到自己的子关节，「接不上自己的孩子」不是它的画法，是我们自己的流水线：**肢段拟合跑在摆位之前**、判据是两 pivot 的三维间距，摆位随后把子节点搬到 PMX 目标，却没人回头补父骨的长度；② 长度轴 `#scale` 乘 k，**盒远端与子节点同步乘 k**（×1.3 那格 `childMoved 3.42361 == boxFarDelta 3.42361`），退出 figure 模式仍在 ⇒ 沿轴长度**可写**。既然长度可写，平轴那只厚盒子就能沿水平轴拉到「前端面正好过球部」，#39 的落地框与 #40/#41 的接趾**同时成立**。
    - **改法（三处，只有脚链）**：① 导出侧 `MMD2MAX_FOOT_FLATTEN` 翻回 **True**（轴压平、高按踝→鞋底净空 ÷0.848 = 8.656，盒底 z=0.194 ≈ 鞋底 0.190）；② Max 端新增 `足盒接趾` 一趟 `mmd2maxFitBoneReach`（≤4 次迭代，目标 = **PMX 球部**而不是回读到的子节点 —— 缩放会把子节点沿轴一起推走，拿它当目标就是自己追自己的尾巴；相对收敛 `|gap| ≤ 2% × 沿轴`，带停滞闸），**必须跑在收尾重摆 / 脚趾复摆之后、退出 figure 模式之前**（在那之前踝 pivot 和长度轴都还会动），并且**重新实测长度轴**（`mmd2maxBipedAxis`）+ 再补一次 `脚趾复摆`；③ 新增 `盒隙` 读数当闸。只乘**单根**局轴（`mmd2maxScaleBoneAxis`），整体乘会把刚缩好的宽/高又带跑。
    - **第十七轮（23:03:49→23:04:18，香菱，退出码 0，`香菱.max` 191111168 字节落盘，15 张截图，`quitMax` 干净，无弹窗）**：
        - **要修的三件事各自的中线**：厚 —— `足背 Bip001 L/R Foot 截面 9.49756/6.87028 -> 宽=3.71424/3.71432 高=8.65601/8.65963 宽高轴=3/1 长度轴=2 迭代=1/1`（高从第十六轮的 2.3210 回到 8.656，5.6 倍）；落地 —— `足盒拉后 Bip001 L Foot 本地 min=[−1.31545,−2.90819,−1.84246] max=[7.34056,9.73629,1.87178]` ⇒ 竖直 15.2% : 84.8% 逐位复现，盒底 = 踝 7.53468 − 7.34056 = **0.19412**（鞋底 0.190）；接上 —— `足盒接趾 … 差=−9.53674e-07 目标沿轴=9.73629 盒远端=9.73629 迭代=1`、`盒隙 … 子沿轴=9.73629 盒远端=9.73629 端面覆盖=true` ⇒ **`盒隙结论 没接上=0 根`**（第十六轮这一格是 3.29cm 空气）。R 侧 `目标沿轴=9.73431 差=0.0`。
        - **沿轴被拉长之后 heel 端自己跟着长**：`足盒缩后` 的沿轴后端 −2.37011 → `足盒拉后` **−2.90819**（−21.11% 的固定窗 × 新长度），脚跟那框不但没退、还往后多盖了 0.54cm。
        - **代价（明码，不是缺陷）**：`链向 Bip001 L/R Foot -> Toe0 骨轴与子关节夹角=29.8725/29.8929度 **断开**` ⇒ `链向结论 断开=2 根` —— 这是**压平水平轴的定义**（踝比脚掌高 5.592cm，水平轴天生不经过球部），不是新断链；`盒隙` 才是这一版的真判据。`脚趾复摆 起始偏差` 0.791129/0.789865 → 接趾后 **2.21044/2.20759**（把盒拉长就是把链尾推远），复摆后回到 4.76837e-07/9.61096e-07、`足盒接趾复摆：净改善 2 根`。
        - **不许退的账逐位相同**：`画线 … 目标=[0.116468,−0.993194,0]`（z=0 ⇒ 平轴）、`脚骨偏航结论 L/R 骨=0.0度 趾=0.0度`（#26）、`段校验 L/R Foot -> Toe0 4.76837e-06/2.38419e-05`、`校验 L/R Foot 6.48569e-06/3.38328e-05`、`校验 L/R Calf 0.158318/0.24703`、`Thigh 0.105656/0.10566`、`Pelvis 5.6995`、`UpperArm 0.955353/0.938523`（#29 定价）、`结构向 复核：可测=51 夹角>3.0度=6 短骨不计=1`、`Biped 肢段拟合 成功=39 失败=0`、`摆位 成功=30 髋中点残余=2.6226e-06`、`Morpher 61/61`、`蒙皮覆盖 L/R Toe0 顶点=74`、趾盒 `5.31702/1.86092` 一字未动。
        - **图**：`shots/香菱_bone_foot_side.png` —— 腿→脚块→趾块**连成一坨**，脚块后厚前薄、底边贴地面线，趾块接在前下角（用户右图的拓扑）；`香菱_bone_foot_front.png` 两脚镜像、黄/紫两块都在鞋腔内不越侧沿、盒底坐在地上；`香菱_foot_side.png` 带网格侧视整坨包在鞋腔里。
    - **最后一个观感问题（前端收成楔形）当场探明写不了**：探针 `biped_taper.ms` 对 `Bip001 L Foot` 节点本体（`classof = Biped_Object`）逐个 `getProperty` 试 `#Taper/#width/#height/#banSize/#banPos/#boneType/#linkLength/#boxWidth/#boxHeight/#endWidth/#startWidth` —— **全部 Unknown property**（`BoneSys` 那套 `bone.taper = 90` 只对 BoneGeometry 有效，Biped 节点不通），控制器侧 `setBoneParams/getBoneParams/setTaper/setBox/…` 同样不存在。⇒ 楔形是 Biped 画骨骼的**固定显示形状**，不是我们缩出来的；参考图那只脚块同样是后厚前薄，所以这一条**不是残余差异**。（顺带：`dumpInterface` 在 Max 2022 里根本不是函数，反射接口只能逐个名字 `getProperty` 试 —— 别再接着猜 API 名。）
    - **回滚位（三个，各管一件事，翻一个词即可）**：`MMD2MAX_FOOTREACH = false`（Max 端）⇒ 整段跳过接趾，回到第十六轮那根牙签；`MMD2MAX_FOOT_FLATTEN = false`（导出侧）⇒ 轴翻回斜的（第十六轮 4.707×2.321）；`MMD2MAX_FOOT_HEELBOX = false` ⇒ 回到第十四轮 3.9260×1.5305。`MMD2MAX_GAPTOL` 只当判据用，不改写法。
    - 静态：`check_ms [OK]`、`Ran 273 tests OK (skipped=1)`。改动：新增 `FootReachContract` 6 条（不许调任何不存在的盒体积 API / 接趾目标必须是 PMX 球部 / 读数与写法用同一根手柄 / **接趾必须在收尾重摆与脚趾复摆之后**且自带第二次复摆 / `盒隙` 量面不量轴且不许出现 `mmd2maxBoxWantDir` / 开关只声明一次），`FootHeelBoxHandoffTests` 与 `FootLinkBoxTests` 按「默认平轴」改口并各自钉住回滚位。
    - ⇒ **#41 结案（三件事各有中线，图与数一致）**。#39 与 #40 从「互斥」改判为「同时成立」。**#12 的视觉验收仍开着** —— 这一版脚链是不是用户要的结构，只有他能判。

24. **M1b「配置单源」落地（2026-11-03 01:57 第十九轮，香菱，退出码 0，`香菱.max` 191111168 字节落盘，Max 自行 `quitMax`，无弹窗）：注入的 22 个 global 收成一个 `MMD2MAX_CFG`，缺配置改成中止而不是退回默认**。

    - **为什么非做不可**：v3 在生成的 `.ms` 头部逐行注入 22 个 `global MMD2MAX_*`，于是「自动跑的」与「人手 `fileIn scripts/max_import_obj.ms` 的」是**两套配置来源** —— 手跑漏一个 flag 就摆出不同的几何，两边日志照样全绿；另一半是「默认值就是那条日志绿、场景错的路」：旧 `read_meta` 缺 `spine_links` 退 3、缺 `foot_pitch` 退 true，每一次退都改变几何，而改变本身**不留任何一行日志**。现在 `.ms` 头部只有 `global MMD2MAX_CFG = "<路径>"` 一行，其余 21 项全从 `<目录>/MMD2MAX.CFG`（线格式 v2，键名 = MAXScript 全局名）读，两条路径共用同一份文件，结构上不可能分叉。
    - **缺块 = 中止，不是退回**：CFG 文件读不到、缺键、值不合法，Python 侧 `build_cfg` / `read_meta` 直接 `SystemExit`；Max 端 `mmd2maxLoadCfg` 一律 `throw`。允许空串的只有 `MMD2MAX_MORPHS` / `MMD2MAX_PHYSICS` / `MMD2MAX_WIDTHS`（该阶段整段跳过），名单在 `obj_run.CFG_EMPTY_OK` 与 Max 端各写一次；`MMD2MAX_LIMBS` 以前被 `try` 包着、读不到就打一行「未读到肢段表」继续跑（= Biped 保持默认人体比例的静默几何错误），现在路径非空校验 + `limbs.count == 0` 都抛错。
    - **删掉的死分支**：`global MMD2MAX_LENAXIS`（没有任何路径会写 false，留着就是一条从没跑过也从没验过的分支）、`mode=="flat"`、双四元数（DQ）分支；`FOOT_BOX_FLAT → meta foot_pitch → MMD2MAX_FOOTPITCH → if not` 那**三重反转**压成一条直读 `MMD2MAX_FOOTBOXFLAT`（值写字面量 `true/false`，不写 0/1、不取反）。契约测试 `LengthAxisContract` / `StructDirectionContract` 钉住「声明区 0 个带值的 `global MMD2MAX_*`、正好 22 个裸声明」「`mmd2maxLenAxisOf node` 恰好 3 个读点」「`_code(SCRIPT)` 里不许再出现 `MMD2MAX_FOOTPITCH`」。
    - **CFG 读失败也得能报出来（本轮补的真缺陷）**：外层 `catch` 原来写 `MMD2MAX_MARKER + ".error.txt"` —— 而 `MMD2MAX_MARKER` 恰恰是 CFG 才填得上的，配置一失败这里就 `undefined + string` 二次抛错，本轮连一行错误文本都留不下；`if MMD2MAX_INTERACTIVE do …` 同理是 `if undefined`。新增 `mmd2maxErrorFilePath()`（MARKER 有就用它，否则退到 CFG 同目录的 `mmd2max_cfg.error.txt`）与 `mmd2maxIsInteractiveNow()`（undefined ⇒ false）。`obj_run` 配套：清理陈旧文件时一起删 `mmd2max_cfg.error.txt`、轮询里认它并 break（原来只有标记/弹窗两条出口，配置失败会空转到 3600 秒再被 `taskkill /F`，把崩溃计数弄脏）、`result_code` 把 **error 文件排在 marker/.max 之前**（脚本报错绝不允许因为 .max 恰好落了盘就返回 0）。
    - **两侧名单互相独立核对**：`test_obj_run_guard.CfgContractTests.test_cfg_covers_every_key_the_maxscript_reads` 从 `.ms` 里 grep 出全部 `mmd2maxCfgStr|Bool|Int "MMD2MAX_*"` 调用，与 `obj_run.CFG_KEYS` 求集合相等；`build_cfg` 另断言生成的键集合 == `CFG_KEYS`。任一边漏一个键，红的都是这一条而不是几何。
    - **静态**：`check_ms [OK]`（非严格债 64 条，见 #54）、`Ran 297 tests OK (skipped=2)`。顺手修了 `check_ms.strip_comments` 的**行号错位**（整段注释替换成等长空格时没保留 `\n`，报「行 553」实际在 720）—— 改成 `_blank_out` 只把非换行符变空白。
    - **第十九轮读数（`baselines/round19_objrun.log`）**：与 `round18_objrun.log` 逐行 diff（先剥掉行首时间戳），**实质差异只有一行** `配置: …\MMD2MAX.CFG`，其余四行是耗时抖动。再与 v3 的 `round17_objrun.log` 对照（额外归一 `mmd2max_v3/v4_src_…` 路径与「X.X 秒」）：**同样只剩那一行新增** ⇒ M1 与 M1b 两步都判为几何中性，一次都没改变验收读数。不许退的账全部逐位相同：`Biped 肢段拟合 成功=39 失败=0 累计误差=0.224741`、`Biped 摆位 成功=30 失败=0 节点缺失=0 髋中点残余偏差=2.6226e-06`、`收敛 转角尝试=39+3+3 改善=45 未改善=0 抛错=0`、`盒隙结论 没接上=0 根`、`权重写入 已写入顶点 18892 失败顶点 0`、`Skin 内骨骼数 321`、`.max` 字节数与第十八轮一致。
    - **比对用的归一命令（下一轮照抄，别再手工裁时间戳）**：
      `python tools/diff_round.py --allow "^配置:" baselines/round18_objrun.log test_runs/香菱/香菱_objrun.log`
      ⇒ exit 0 = 除放行名单外没有实质差异；exit 1 = 有，并把每一处 +/- 列出来。
      手写 sed 在这台机器上踩过两种坑（都写进了 `tools/diff_round.py` 的文件头）：① 时间戳**不补零**，
      同一份日志里它的字符数会变（晚 11 点那轮比下午那轮多一个字符），按固定宽度裁一定裂；
      ② 日期分隔符的**字节是 `0x2F`（斜杠）、终端回显成短横线**，照着显示写 `^[0-9]{4}` 后面接短横线
      就**静默不匹配** —— 不报错、只是什么都没剥，于是整份 413 行日志被读成「全变了」；
      第十九轮第一次核对真的就是这么错的，`list(Path(f).read_bytes()[i:i+20])` 才给出真相。
      本工具的剥离规则只认「四位年 + 任意非数字分隔 + 月 + 日 + 空格 + 时:分:秒」，与分隔符和补零无关。
    - **写这个对比器时抓出它自己的两个缺陷**（都有测试钉住：`tests/test_diff_round.py`）：
      ① 剥离规则最初是「行首两个非空格 token」，于是 `香菱.max 191111168 字节` 这种不以时间戳开头的行
      也被吃掉行头 —— 两行不同的日志能塌成同一行 ⇒ **漏判**；改成必须先认得出「日期+时间」才动手。
      ② 屏蔽字节数最初用「单词边界 + 6 位以上数字」，它把 `累计误差=0.224741` 的小数部分
      也一起遮成 `<N>` —— **正好遮掉「证明重构没动几何」所依赖的那个读数**，
      这是本项目最不能犯的错（验收层自己就变成恒等式）；
      改成前后都不许挨着数字或小数点。改完用三处注入变异复核：把 `累计误差` / `转角…改善` / `失败顶点`
      各改一个数字，三次都必须报实质差异 —— 确认这台判据不是永远说「没变化」。
    - **仍然欠着的（不算缺陷，是明账）**：`MMD2MAX_FOOTREACH = true` / `MMD2MAX_GAPTOL = 0.5`（`max_import_obj.ms:2309`）还是脚本内硬赋值的全局，没进 CFG —— M1b 只合并了「每条链上都参与取反」的那三个开关，这两个没有反转链、手跑与自动跑读到的是同一个字面量，所以不构成本轮那个分叉；但它们仍是「翻开关要改 .ms」，归 #52/#56 一起收。**（M2a 已收，见第 25 条。）**`meta.tsv` 里的 `has_sdef` / `has_qdef` 从 M1b 起**没有任何读点**（导出侧已改为如实打印「按普通线性混合装配」的提示），归 #56 删。

25. **M2a「目标值搬到 Python」落地（2026-11-03 02:26 第二十轮，香菱，退出码 0，`香菱.max` 落盘，Max 自行 `quitMax`，无弹窗）：五个容差/开关从 `.ms` 硬赋值搬进 `mmd2max/targets.py` → `MMD2MAX.CFG`，且实跑证明几何中性**。

    - **为什么这一步不是搬家**：M2b/c 要在 **Python 侧**拿「期望值 + 容差」重算 PASS/FAIL（判据通道）。
      数字留在 `.ms` 里，Python 想判就只能①抄一份（两份尺，改了 `.ms` 那份不会跟着改）或②去 grep
      自己生成的脚本（那就是把恒等式换了个方向再写一遍）。`targets.py` 是**唯一持有者**：
      `TARGETS[key] = (值, 类型, 下限, 上限)`，`cfg_values()` 出字面量，`obj_run.build_cfg` 并进 CFG。
    - **`.ms` 侧的写法被测试钉成一条形状**：五个全局在声明区是**裸声明**（`global MMD2MAX_DIRTOL` 不带值），
      全文件对每个名字**只有一行赋值**，且那行必须是 `MMD2MAX_DIRTOL = mmd2maxCfgNum "MMD2MAX_DIRTOL" 0.0 9999.0`
      （`tests/test_targets.py`：`MaxScriptConsumerTests` 逐键查，`assertNotIn` 查任何残留字面量）。
      查残留那条**第一次就把自己绊了**：我写的解释性注释里原样抄了旧写法 `global MMD2MAX_DIRTOL = 3.0`，
      于是测试报违规项 `['DIRTOL']` —— 修法是①注释别再出现「名字 = 值」这个形状，②测试改用剥注释后的
      `_code(SCRIPT)`。这条不是洁癖：注释里的旧值会在下一次 grep 时被人当成真值源。
    - **新增 `mmd2maxCfgNum key low high`**：`"abc" as float` 在 Max 里返回 **0 而不是抛错**，
      所以浮点键不能像 v3 那样直接 `as float`。函数自己验「逐字符是数字或小数点 + 至多一个小数点 +
      允许前导负号」，再 `isNumber` 复查，然后查范围，任何一环不满足 = `throw`（错误文本带键名与实际读到的串）。
      测试用三条钉住它拒收非数字、拒收 `1.2.3`、以及两个数的范围与 `targets.py` 逐字相同
      （**这是本项目第一次出现「同一份约束在两侧各写一遍、并由测试保证它们不分叉」**）。
    - **上限为什么是 9999**：`DIRTOL/ROLLTOL/TOETOL` 三个数同时是**回滚开关**（「改成 999 就等于整段不动」
      是文档里承诺过的操作）。我一开始给它们写了 90.0/50.0 这种「合理上限」，自家新写的
      `test_rollback_switch_is_reachable` 立刻红 —— 上限会恰好把唯一的回滚位拦在 CFG 校验外面。
      这条错误的价值在于它是**静态**抓到的，一次实跑都不用烧。
    - **顺手抓到一条真缺陷（第十九轮及以前所有日志都缺「配置: …」那行）**：`mmd2maxInitLog()` 用
      `createFile` **截断**建日志，而配置汇总在那之前就已经写出去了 ⇒ 写进的是一个随后被清掉的文件；
      于是「本轮到底用了哪份 CFG、五个容差是多少」在日志里**一行证据都没有**。现在汇总存进
      `MMD2MAX_CFGSUMMARY`，由 `mmd2maxMain` 在 `mmd2maxInitLog()` **之后** `mmd2maxLog` 出来；
      契约测试比的是两处的先后位置。第二十轮日志正是靠这一点多了**唯一一行**新内容。
    - **CFG 行的顺序变成契约**：`cfg_text` 原先按 dict 插入顺序落盘，而 `cfg.update(...)` 会把新键
      **追加到末尾** ⇒ 「两次重跑的 CFG 逐行可比」这条测试红（第 17 行一边是 `MMD2MAX_SKIN`、
      一边是 `MMD2MAX_DIRTOL`）。改成按 `CFG_KEYS` 排序输出、缺键直接 `SystemExit`：顺序即名单，
      两轮的 CFG 可以逐行 diff。
    - **静态**：`check_ms [OK]`（非严格债仍 64 条，未增未减）、`Ran 332 tests OK (skipped=2)`
      （297 → +14 条 `test_targets.py` + 更新 5 处旧契约的口径 + 2 条新断言）。
    - **第二十轮一跑就把一条旧回归锁踩红了（真缺陷，已修）**：`tests/test_diff_round.RealLogTests` 里的
      `RE19` 指的不是 `baselines/round19_objrun.log`，而是 `test_runs/香菱/香菱_objrun.log`
      —— 「第十九轮的基线」被写成了「当前那一轮」。第十九轮时两者恰好是同一个文件所以全绿，
      第二十轮一覆盖它就像「M2a 改坏了几何」一样红。这类缺陷比写错数字难发现，因为**它只在下一轮
      实跑之后才发作，而且发作时报的是别人的错**。修法不只是改路径：新增
      `test_regression_locks_point_at_baselines_not_the_live_run` 逐条断言这四把锁的父目录名 ==
      `baselines`（写「上一轮」的路径时手滑指向实跑目录，当场就红，不用等下一轮）。
      同一条口径也写进了 M2b/c 的设计：报告文件按轮次命名并落到 `baselines/` 才算基线。
    - **第二十轮读数（`baselines/round20_objrun.log` / `round20_success.json` / `round20_MMD2MAX.CFG`）**：
      `python tools/diff_round.py --allow "^配置" baselines/round19_objrun.log test_runs/香菱/香菱_objrun.log`
      ⇒ **归一后行数 414 vs 415，无实质差异，exit 0**。多出的那一行就是新可见的配置汇总行；
      CFG 逐行 diff 只多 5 行（`DIRTOL/ROLLTOL/TOETOL/GAPTOL/FOOTREACH`）。
      不许退的账全部逐位相同：`肢段拟合 成功=39 失败=0 累计误差=0.224741`、`摆位 成功=30 失败=0 节点缺失=0 髋中点残余偏差=2.6226e-06`、
      `转角尝试=39+3+3 改善=45 未改善=0 抛错=0`、`盒隙结论 没接上=0 根`、`权重写入 已写入顶点 18892 失败顶点 0`、`Skin 内骨骼数 321`。
      ⇒ M2a 判为**几何中性**：搬数字没有改变任何一个读数，也没有改变脚链。
    - **本轮没做的事（别当成已完成）**：判据通道（M2b 的 `verdicts.tsv` + Max 侧只报数）与 Python 侧
      校验器（M2c）**尚未实现** —— 现在这些数仍然只是「进了 CFG、进了日志」，验收仍然是读日志里
      的成功计数，也就是 §1 那个病根还没被结构性地治掉。M2a 是它的前置。

26. **M2b + M2c「判据拆成两张表 + Python 侧判闸」落地（2026-11-03 03:28 第二十二轮，香菱，
    退出码 0，`香菱.max` 落盘，Max 自行 `quitMax`，无弹窗）：Max 从此只报数，判定与退出码在 Python**
    - 通道：`MMD2MAX.VERDICTS`（Python 写的规格，121 行 × 7 列）+ `MMD2MAX.REPORT`（Max 只追加的
      读数，121 行 × 4 列）+ `MMD2MAX.CHECK`（判完落盘的逐行状态）。前三列是两张表的拼接键，
      第四列刻意不同名（`expected` = 尺，`actual` = 读数）。
    - 三种 kind：`gate` 真要求 / `lock` 已定价的缺陷（PASS 只等于没退步）/ `report` 只要求出现。
      开关只**降级**行的 `when`，从不删行；锁只在 `PRICED_MODEL`（香菱）上是锁，换模型退化成 `report`。
    - `tools/check_verdicts.py` 只打挂掉的行 + 一句汇总（含 lock 警示），`obj_run` 在产物齐之后
      再过这道闸：**新增退出码 7（有 FAIL）/ 8（两张表不可用）**，GUI 那条「完成」的文案同时改掉
      —— 现在「`.max` 在但没判过」不可能被读成交付成功。
    - **实跑当场抓到三条只有真跑才会现形的毛病**（写日志/静态检查都看不出来）：
      ① 表头写成了函数名。`format "%\n" (mmd2maxReportHeader)` 少一对括号，落地的是**函数本身**
      （字面量 `mmd2maxReportHeader()`），列名行变 1 列 ⇒ 后面 121 行「每行都比列名多 3 列」，
      `parse_table` 直接抛。第二十一轮就是这么退 1 的；`mmd2maxReportInit` 自己一行错误都没记。
      ② `链向 断开` 的阈值在两边各刻了一遍：`.ms` 写死 3.0，Python 侧的尺用 `DIRTOL` ⇒ 换成
      `MMD2MAX_DIRTOL`，契约测试钉「这一段不许出现字面量阈值」。
      ③ **骨盆三段有一条永远不可能通过的 gate**：Max 在 `mmd2maxHipAnchor` 把 Pelvis→{L Thigh,
      R Thigh, Spine} 的目标改写成「离两髋中点的距离」（9.0278→7.0013、6.4912→3.5096），
      Python 的尺还按 PMX 腰→子骨刻 ⇒ 第二十一轮判红 3 行。改的是**期望值的口径**
      （`verdicts.segment_expectation` 用同一批 limbs 坐标列、同一个 0.05 门槛、同一对中点重算），
      不是把判据放松去迁就 Max：Max 报的仍是场景里量到的长度。方向搞反就成了「拿读数刻尺」。
    - 中性证明：`tools/diff_round.py --allow "^配置" baselines/round20_objrun.log
      baselines/round22_objrun.log` ⇒ 归一后 415 vs 415 行、exit 0。放行的那一行只装了
      「CFG 26→28 项」与汇总尾部新增的「规格=… 报告=…」。
    - 离线回归（`tests/test_check_verdicts.py::FrozenRoundReplayTests`）：拿冻结在 `baselines/` 的
      第二十二轮三张表重放判据，逐行状态必须和实跑当时落盘的 `CHECK` 完全一致。
      这条只能抓「判据变严」；**变松**由同一文件里那 19 条「坏读数必须红」的用例抓 ——
      已用注入变异核对：把 `grade` 的超差分支改成 PASS ⇒ 5 条用例立刻红。
    - 报告文件带 UTF-8 BOM + CRLF 是 **Max 侧写的指纹**（`createFile … encoding:#utf8` 落 BOM，
      Python 永远不写）。`parse_table` 剥 BOM；这条指纹单独钉了一条用例，防止哪天这张表被
      Python 覆盖写出去 —— 那它就不再是「Max 报的数」了。
    - 本轮之后仍然没做：M3 的 pass 注册表（谁写了什么、谁作废谁）、M4 的真回读 + 超差回滚
      （现在的 `actual` 里有几项仍是 pass 内部自量，不是独立回读）、`--strict` 那 64 条结构性债。

27. **M3「pass 注册表 + 时间轴审计」落地（2026-11-03 04:29 第二十三轮，香菱，退出码 0，
    `香菱.max` 落盘，Max 自行 `quitMax`，无弹窗）：循环第一次自己说出「我没收敛」**
    - 新文件 `mmd2max/passes.py`：23 个 pass，每个声明 `requires`（结构前提）/ `writes`（这段量哪些
      phase）/ `reads`（**检查级**归属，只给多作者 phase 用 —— `toe_snap` 的两道复摆）/
      `invalidates`（物理上作废谁，`moves=True` 展开成 `POSE_FACTS` 并**排序**）/ `optional` /
      `max_iters`（只有 `converge` = 3）/ `gate`（只有 `converge`：`("converge","exit_reason","gate")`）。
      `validate_registry()` 在 **import 期**跑，注册表自相矛盾就直接抛 —— 一张自相矛盾的注册表
      比没有更糟，它让 `audit` 的每条结论都失去意义。
    - Max 侧只加一件事：每个 pass 干完活报一行 `mmd2maxReport "pass" "ran" <id> 1`。
      **ran 排在收工那一刻**，所以报告表的**行序**天然就是时间轴，不需要时间戳（时间戳会被
      `diff_round` 当噪音遮掉，也更难对撞）。`passes.audit()` 五条规则：覆盖 / 恰好一次 /
      `requires` 拓扑 / STALE（某 phase 最后一条读数排在作废它的 pass 之前）/ 归因 + 闸门自洽。
      归因把 `ran` 行当分段边界：每条读数必须落在声明它的那个 pass 的段里 —— 治的是
      「数值照样对但量的不是最终场景」（#13 那类权重错位只有在这段里量才看得见）。
    - **收敛循环的出口判据换源**：以前是 `if rp[1] == 0 do break`（写入侧自己数「这遍没骨要转」就宣布
      收敛），现在是 `mmd2maxConvergeGate dirOver == (dirOver == 0)`（场景里量到的方向超标数）。
      第二十三轮的日志因此出现 `收敛闸门: 出口=ceiling 遍数=3/3 方向超标=5 判据达标=false` ——
      **这是预期读数**：第十七轮那次「三遍落位最大 5.6995 一动不动、方向超标 4→5→5」本来就是撞上限，
      从前它报成「达标」。真缺陷（5 根超标 + 5.6995 落位）仍在 `lock` 账上，一行没松。
    - `MMD2MAX_CONVERGE_MAX` 走 CFG 下行（注册表 → `MMD2MAX.CFG` → Max），循环次数不再由 `.ms`
      写死；`audit_gate` 反过来核对 `iters ≤ max_iters`、`出口=ceiling ⇒ 遍数==上限`、
      `出口=gate ⇒ gate_satisfied=true`，三数互不打脸。闸门三行的 kind 是 `report`（只要求出现），
      **自洽性由时间轴判**，不在期望值里刻死 —— 否则又是恒等式。
    - 判据层的接线：`CHECK` 新增 kind `timeline`，问题行按条计入 FAIL ⇒ `check_verdicts` 退 1 ⇒
      `obj_run` 退 7。
    - **两条只有写代码时才会撞上的坑**：
      ① 产品侧：`ran physics_build` 原来写在 `if physFile != ""` **块外**，没给 physics 文件也照样
      宣称跑过。「没跑却报 ran」比「没跑没报」难查 —— 它给 STALE 一个根本不存在的段。已挪进块里。
      ② 判据侧：时间轴审计**只按本轮的规格参与**。注册表是今天的代码，规格表是那一轮的 Python
      写的；拿 23 条 pass 行去判第二十二轮 = 拿新合同判旧产物，会把每轮冻结基线全判红。所以规格里
      没有 `pass/ran` 行时打一行 SKIP「时间轴审计未参与」。这条通道两头夹住：
      `FrozenRoundReplayTests` 断言冻结轮**唯一**允许多出的就是这行 SKIP，
      `test_pre_m3_spec_is_not_judged_by_todays_registry` 钉住「本轮规格有 pass 行 ⇒ 审计必须参与、
      坏时间轴必须退非 0」。
    - 中性证明：`tools/diff_round.py --allow "^配置" --allow "^收敛闸门"
      baselines/round22_objrun.log baselines/round23_objrun.log` ⇒ 归一后 415 vs 416 行、**exit 0**。
      去掉遮罩只剩 3 行：CFG 汇总「28 项→29 项」（多 `收敛上限=3`）+ 上面那行新闸门读数。
      行数账：规格 121→**147** 行（+23 `pass/ran`、+3 converge 闸门），CFG 28→**29** 键，
      报告 146 行读数，`CHECK` 146 PASS / 0 FAIL / **1 SKIP**（那行 SKIP 是
      `pass/ran physics_build`，`when=if_present` 退成未跑，不是判据缺失）。
    - `tests/test_passes.py`（新，451 行 / 38 条）分三层：声明层（`validate_registry` 空、id 唯一且
      ASCII、每个 phase 有作者、多作者 phase 必须用 `reads`、`invalidates` 只许是 `POSE_FACTS` 全集或空、
      声明作废必须有人在其后重量、只有 `converge` 能 `max_iters>1`/带 `gate`）；脚本对撞层（`.ms` 里的
      `ran` 行与声明**逐一对齐、各一次**、每条读数排在它自己作者的收工边界之前、`"MMD2MAX_CONVERGE_MAX"`
      必须出现在代码而不是注释里）；变异层（见 README_V4 §4）。加上冻结轮对撞：`report_order(第二十三轮
      报告)` 必须是**不动点**、`passes.audit(第二十三轮报告)` 必须为空 —— 这两条是「注册表声明的时间轴
      与实跑分叉了」唯一的离线探针。全套 438 条 OK（2 条 skip 与 M3 无关：缺样例 PMX / 该模型无骨变形）。
    - 本轮之后仍然没做：M4 的真回读 + `saveFigFile`/`loadFigFile` 超差回滚（`actual` 里仍有 pass
      内部自量，时间轴只保证「量在哪一段」，不保证「量法独立」）、M5 的测试重写、`--strict` 那 64 条
      结构性债。

28. **#66~#70「胸部骨指向乳头 + 特写取景」落地（2026-11-04 10:45→10:51 第三十七轮，香菱 `--shot`，
    退出码 0，`香菱.max` 191111168 字节落盘，Max 自行 `quitMax`，无弹窗）：`CHECK` 489 PASS / 0 FAIL / 22 SKIP**
    - **#66 = 胸骨的 tail 不再抄 PMX 的默认尾长**。PMX 里 `右胸/左胸`（idx 9/10）的 `tail_offset` 是一根
      纯竖直短棒（head→tail = +Z 一个 scale，作者只是留默认值），Max 按 head→tail 画 BoneGeometry 就在
      胸腔上长出两根向上的牙签。现在 `export_obj._aim_breast_bones` 把 tail 换成**从网格量出来的乳头点**：
      取「以这根骨为最大权重、且权重 ≥0.5」的顶点当它自己的皮肤区域（香菱左右各 18 点），沿
      `head→区域质心` 方向投影最外的 25% 求均值、迭代两轮 —— 不取「最靠前那个点」（单点会被野顶点带跑），
      也不取质心（质心在鼓面内部，实测比顶点靠后 1.2cm、方向差 13°）。这条估计只用骨自己的 head 和它绑着的点，
      **不假设角色朝哪个轴**，所以换模型、换朝向都成立。香菱实跑回读：
      `右胸 head (-4.554963, -7.239760, 105.732491) → tail (-4.915695, -11.745141, 105.225345)`（左同镜像）。
      两道护栏：只在「这根骨没有保留的后代」时动手（有后代时 tail 已指向子关节，覆盖它就是把骨链画错），
      区域 <6 点原样留着；`BREAST_SKIP` 挡住 `上半身/下半身/胸腔/胸郭/胸骨` —— 它们也带「胸」字，
      但是躯干中轴，改指向就搞坏整条脊柱。
    - **#67 = 辅助骨骼开关**：`tools/run_model.py --drop-guides` / GUI「保留辅助骨骼」勾选框，
      判据在 `semantic.guide_bone_indices`（既无 Biped 目标、又无蒙皮权重、tail 是默认值的骨 ——
      `センター2 / WeaponL·R / 十根指先`）。砍在**导出层**，不是 Max 层的显示开关：不进 `bones.tsv`
      就对象数、Skin 骨骼数、`bone_rb` 行数一起干净。默认 `keep_guides=True`，历轮基线不变。
    - **#68 = `bone_rb` 通道**：附属骨的 head/tail 从 Max 场景真回读（不是抄导出值），配 4 张胸部特写
      `chest_front / chest_side / bone_chest_front / bone_chest_side`。
    - **#69 = 特写填充率**：`zoomToBounds` 拿框里最大的那一维贴视口，所以胸带框 119cm 宽 × 19.7cm 高时
      纵向只剩 18%，4.55cm 的骨在图上 20px —— 「骨指着乳头」这件事在像素上不存在。`mmd2maxAspectBox`
      只把**水平**跨度收到刚好装下纵向（只收、且朝框中心收 ⇒ 数学上不可能裁掉内容），水平轴归
      `mmd2maxHAxis` 一处说（正视=X、左视=Y、俯视=X），Python 侧 `verdicts.SHOT_H_AXIS` 与它由契约测试对撞。
      每张图报 `shot_rb / fill_pct`。
    - **#70 = 取景框的三根轴全部由导出侧量，尺用骨极值、不用框**（这是本轮的真修）。第三十六轮的
      `chest_side` 紧框 y = `-0.405685..20.0071`，而两根胸骨在 y = `-11.745..-7.240` —— **骨在框外**，
      那张图里根本没有骨。根因是框的 y/z 由场景 AABB + 高度比例猜，只有 x 是量的。现在
      `meta.tsv` 出 12 行（`chest_box_{x,y,z}_{lo,hi}` 给脚本当框 + `chest_bone_{axis}_{min,max}` 给规格当尺），
      CFG 多 6 键，规格多 18 行（6 行 `chest_box_*` 是 `min/max` 单边卡，12 行 `tight_h_*` 只有胸部 4 张参与判定、
      颈部 4 行仍是 `report` 等 #71）。第三十七轮实测：框 `x -19.349..19.349  y -26.1785..7.19358  z 96.5653..114.393`，
      紧框 y = `-19.6989..0.713949` ⇒ 三轴都框住骨，18 行全 PASS。**反恒等式在结构上成立**：
      expected 来自导出的骨坐标，got 来自 Max 真正用来取景的那个框，两个来源不同表（`test_the_ruler_does_not_follow_the_box`
      把框改成 `y_lo=-5.0` 后断言尺仍是 `-11.7451`）。
    - **本轮烧掉的一次实跑（10:46，一行日志没写）**：`if (mmd2maxChestGiven) then` —— MAXScript 里裸写零参
      `fn` 的名字拿到的是**函数对象**而不是返回值，`-- Type error: if-test requires BooleanClass, got: mmd2maxChestGiven()`
      炸在 `mmd2maxLoadCfg` 里 ⇒ 整轮没导入、`REPORT` 空。修法是把两处调用点写成 `mmd2maxChestGiven()`，
      并给 `tools/check_ms.py` 加第 14 条拦路规则 `check_bare_fn_reference`（剥掉注释**和字符串**之后，
      零参 `fn` 的名字后面没跟 `(` 就报行号；字符串必须剥，否则 `throw "mmd2maxLoadCfg 必须排在…"` 会被误判）。
      与 `f(a,b)`、`local x = try(...)` 同一类：报错只给标题，不给行号。
    - **几何中性证明**：`diff_round` 对撞 round36 基线 ⇒ 归一后 481 vs 481 行，**24 行实质差异全部是取景行**
      （`chest 取景` + 4 张胸图的紧框/填充）。`neck_side / foot_side / hand_side` 三行填充率也变了，但
      框逐字未变、三个数同乘 1.000991 ⇒ 视口高度差 1 个像素（窗口边框），不是几何。骨位、权重、转角、
      骨盒一行都没进差异表。
    - **图证（验收只认图）**：`香菱_bone_chest_side.png` 里那根骨是一个**横指前方**的锥 = 指向乳头，
      第三十六轮这张图里骨压根不在画面内；`香菱_bone_chest_front.png` 里两根骨读成两个小三角 ——
      正对镜头的前向骨按投影必然短路，**侧视才是这条判据的正面图**（要一眼看清 3D 指向就得换 3/4 视角，已开 #72）。
    - 静态：`check_ms [OK]`、`Ran 599 tests OK (skipped=2)`。CFG 36 键 / meta 12 行 / 规格 511 行 /
      通道 112 个，`test_the_script_reads_exactly_the_keys_the_launcher_writes` 把两侧键集对撞（差集必须为空）。
    - **回滚各一处**：`MMD2MAX_CHEST_*` 六键留空 ⇒ `mmd2maxChestGiven()` 为 false，脚本退回按高度比例那只框
      （取景与摘要走同一条退回路径，半只框比没框更坏）；`--drop-guides` 不加就是原样。

29. **#71「颈部特写取景框按高度比例猜」落地（2026-11-04 11:36 第三十八轮，香菱 `--shot`，退出码 0，
    `香菱.max` 落盘，Max 自行 `quitMax`，无弹窗）：`CHECK` 495 PASS / 0 FAIL / 22 SKIP，
    规格 517 行 / 通道 114 个 / CFG 42 键 / meta 41 条数据行**
    - **这一步真正的产出是「一套机制」而不是「第二个框」**：#70 那套（导出量骨极值 → meta 双份 12 行 →
      CFG 6 键 → 脚本 zoom + 报 6 个角点 → 规格 18 行）在代码里只有胸部在用，颈部那两张图
      （`neck_front` / `neck_side`，#69 起就参与紧框收框）用的仍是 `.ms` 里 `h × 0.55..0.98` 猜的带 ——
      与第 35 轮胸部**同一件事**：分母是整场景高，量的是别的骨。现在三处都改成按 `FRAMINGS = ("chest","neck")`
      循环：导出侧 `FramingBand` / `_framing_band` / `band_meta_pairs(prefix, band)`，装配侧
      `obj_run.box_keys/bone_keys/framing_cfg_keys/_ruler(prefix)`，判据侧 `verdicts.box_rows(prefix, subject)` /
      `tight_box_rows(rulers)`。**两把尺各自独立**：颈部量不出骨只降级颈部那 12 行，胸部照旧硬判
      （`test_one_framing_can_be_missing_without_the_other`）。
    - **颈部按 Biped 目标认，绝不按骨名**：`NECK_TARGETS = ("Bip001 Spine2","Bip001 Neck","Bip001 Head")`
      从 `biped_map` 反查 PMX 骨。名字规则在颈部是陷阱 —— `左手首 / 右手首 / 左足首 / 右足首` 都含「首」字，
      按名匹配会把腕/踝当成颈（香菱实测：腕骨 x=+31.8，匹配上就把取景框整个拽到手臂上）。
      契约 `test_the_neck_is_recognised_by_the_biped_target_not_by_name` 直接拿 `左手首` 混进名单里断言
      `band.b_max[0] < 1.0`。
    - **框必须在 `_center_neck` 之后算**（`build_plan` 里就排在那一步之后）：#17 会把 `首` 的 head 沿 y 挪
      ≤3cm，先量后挪 ⇒ 交出去的尺与画面里的骨不是同一只。用例
      `test_the_band_follows_a_bone_moved_earlier_in_the_same_pass` 把 `頭` 的 head 挪到 (0,-30,130)，
      断言 `b_min[1]=-30.0`、`b_max[2]=130.0` 跟着走而 `b_min[0]/b_min[2]` 不动。
    - **余量仍是比例不是厘米**（§6 那条老规矩）：颈部用与胸部**同一对**常数 `CHEST_BAND_PAD=0.06`（z）/
      `CHEST_BOX_PAD=0.10`（xy），分母是网格实测高 144.33 ⇒ 外扩 8.66 / 14.43cm。scale×10 时框逐位×10
      （`test_pad_follows_the_measured_height_not_an_absolute_cm` 现在两张特写都跑）。
    - **香菱实测（`baselines/round38_*`）**：骨端点极值 `x -3.842957..0.000088  y -6.766848..-0.036633
      z 100.760711..128.286294` ⇒ 框 `x -18.276298..14.433429  y -21.200189..14.396708
      z 92.100706..136.946299`。z 跨度从猜的 60.42cm 收到 **44.85cm**，上沿只降 0.75cm（不新增裁头），
      下沿抬上去 14.8cm —— 之前那 14.8cm 全是腰。日志两行新读数：
      `neck 取景：框=[-18.2763,-21.2002,92.1007]..[14.4334,14.3967,136.946]` 与
      `截图 neck_side / neck_front 紧框 … -> …`（这两张收框前后一致 ⇒ 视口宽高比不再需要砍水平跨度）。
      判据端 12 行从 `report` 升级成硬判：`shot_rb neck_box_{lo,hi}` 六行 + `tight_h_{lo,hi}` 的
      `neck_front/neck_side` 四行 + 原有 `fill_pct` 两行，`CHECK` 全 PASS。
    - **两条只有写代码时才撞上的坑（都改成了结构，不是改数字）**：
      ① `CFG 键集与 CFG_KEYS 不一致：缺 ['MMD2MAX_NECK_X_HI', …6 键]` —— `build_cfg` 的字面量里手抄了
      胸部那 6 行，而 `CFG_KEYS`/`META_TO_CFG` 已经长出颈部的。改成
      `**{cfg_key: meta[meta_key] for meta_key, cfg_key in META_TO_CFG.items()}`：凡从 meta 搬进 CFG 的键
      只有 `META_TO_CFG` 这一处来历，手抄这条路整个堵掉。
      ② `AssertionError: '8.8e-05' != '0.000088'` —— `_ruler` 原本返回 `float`，规格表落盘时 `str(float)`
      把颈链贴着中线的 `x_max = 0.000088` 打成 `8.8e-05`。改成**交回 meta 里的文本**，`float()` 只留着
      做「读得开 + min ≤ max」两条校验。这不是排版洁癖：尺的字符串一旦被重新格式化，「规格表里那一行」
      与「meta 里那一行」就不再逐字可比。
    - **几何中性证明**：`diff_round --allow "^配置" …` 对撞 round37 基线 ⇒ 归一后 481 vs 482 行，
      **17 行实质差异全部是取景行**（1 行新 `neck 取景` + 4 行 neck 紧框/填充 + 4 行别的图的填充抖动
      ×2）。骨位、权重、转角、骨盒、`盒隙`、`链向` 一行都没进差异表。填充抖动那几行同乘 ~1.0006 且
      框逐字未变 ⇒ 视口高度差 1 像素（#69 记过同一现象）。
    - **图证（验收只认图）**：`shots/香菱_neck_side.png` / `香菱_neck_front.png` 现在拍的是**头→颈→胸**
      这一段，颈链三节骨在画面中央；第三十七轮那两张里整个上半身缩在画面中间偏下，`#17 挺胸`、
      `#27 颈前倾` 想在这张图上判都判不动（框里目标只占几十像素）。
    - 静态：`check_ms [OK]`（非严格债仍 64 条）、`Ran 606 tests OK (skipped=2)`（599 → +7：
      `FramingBandTests` 颈部 5 条、`FramingSpecTests` 的 `test_each_framing_uses_its_own_ruler` /
      `test_row_count_does_not_depend_on_the_ruler`、`ConfigSingleSourceContract` 的
      `test_every_framing_box_enters_the_config_summary`）。新增契约两条口径：摘要里每张特写各一条
      `mmd2maxBoxSummary` 调用、`无(退回场景包围盒带)` 这句文案在代码里只许出现一次。
    - **基线目录跟着合同走了一次**：`baselines/香菱_meta.tsv` 从 17 条数据行刷到 41 条（#70 的 12 + #71 的 12），
      `tools/check_baseline.py 香菱` 重新全绿 —— 其余 5 个文件逐位不变。**注意基线文件是不带表头的**
      （`check_baseline` 的口径是「新文件只许多一行表头」），刷新时得 `tail -n +2`，直接 `cp` 会让它把
      表头当数据行、报「第 1 条数据行起不同」。
    - **回滚一处**：`MMD2MAX_NECK_{X,Y,Z}_{LO,HI}` 六键留空（或导出侧认不出颈链）⇒ 脚本退回
      `h × 0.55..0.98` 那只带，颈部 12 行退成 `report`，配置摘要与 `neck 取景：CFG 没给全框` 都写明退回了。

30. **#72「特写被框外的骨糊住」落地（2026-11-04 12:11 第三十九~四十二轮，香菱 `--shot`，退出码 0，
    `香菱.max` 落盘，Max 自行 `quitMax`，无弹窗）：`CHECK` 515 PASS / 0 FAIL / 22 SKIP，
    规格 537 行 / `shot_rb` 65 行 / 19 张截图全落盘 / 填充率 53.2~63.4%**
    - **因由不是「某张图不好看」而是机制**：`viewport.zoomToBounds` 只定视野、**不裁深度**，所以取景框外的
      东西照样进同一个投影。第三十八轮 `香菱_bone_foot_top.png` 是证据：框只到 z=19.7，画面正中那一大坨
      是头骨（z≈130），四周一圈是手指与辫子，两只鞋只剩几十像素 —— 注释里那句「俯视那张已经判不了脚」
      就是把这件事当成了既成事实。用户要的「顶视看外八」从来没拍到过。
    - **做法**：`mmd2maxClipBones lo hi` 在每张特写取景前把**整根都不在框里**的骨临时 `isHidden`，
      `mmd2maxUnclipBones` 拍完按**这一张自己的**名单（`MMD2MAX_SHOTCLIP`）还原；藏没藏上以写回读为准
      （`o.isHidden` 读回 true 才计数），本来就藏着的骨不进名单（还原时才不会把用户自己藏的东西显示出来）。
      判「在不在框里」用 pivot **或** bounds 两条（只看 pivot 会藏掉「关节在框外、骨身伸进框」的骨；
      只看 bounds 不可靠，BoneGeometry 的 `.bounds` 实测可能是零尺寸）。`mmd2maxBoxHit` 六次比较自己写，
      不调 `intersects` —— 本文件对没在 Max 2022 实测过的 API 一律不用。裁与还都住在 `mmd2maxShotOne` 里
      ⇒ 19 个调用点共用同一条，没有「某张忘了裁」这种写法；`mmd2maxRestoreShot` 再兜一次底（中途抛异常
      不把名单带进下一张）。
    - **读数（这才是这一步真正的产出）**：19 行 `shot_rb/clip_hidden`，期望方向**相反** ——
      整场景那 5 张（`front/side/top/bone_front/bone_side`，名单 = `verdicts.SHOT_SCENE_TAGS`，
      与 `.ms` 里 `b.min b.max` 那 5 个调用点由契约对撞）必须是 `max 0`，其余 14 张必须 `min 1`。
      只钉「特写 ≥1」的话，把 `front` 的框改小也能绿；两头的方向一起绿才说明取景框没被谁悄悄改小。
    - **三轮才把「哪一类骨没被认到」查清，靠的是分母那一行**：`clip_bones_total` 原先只报一个数。
      第三十九轮 foot_top 藏掉 **268** = 附属骨总数（321 骨 − 53 映射），53 根 Biped 骨盒一根没动；
      第四十轮补上 `classOf o.controller == Biped_Control`（本文件清探针残留段用过的写法），分母**还是 268**
      —— 那条一个都没认到。于是把它改成 `mmd2maxBoneCensus()`：`objects` 总数 + **逐类计数** + 认定为骨数，
      全塞进 `clip_bones_total` 那一格的读数列（report 行没有 expected，装得下这句话，**不用新开支格表行**）。
      第四十一轮实测：`objects=397 认定为骨=322 Editable_mesh=62 Biped_Object=54 Dummy=13 BoneGeometry=268`
      ⇒ Biped 的骨盒报的就是 `Biped_Object`（本文件自己的探针记录早写着这句，第四十轮却把它当「骨架容器」
      刻意排除在外 —— 那是这一轮真正的 bug）。**322 = 268 + 54 恰好是两类类名之和**，这就是控制器那条
      「一个对象都没多认到」的算术证据 ⇒ 第四十二轮把它删掉，`认定为骨` 仍是 322、19 行 `clip_hidden`
      逐字不变 —— 删除是**实测中性**的，不是「我觉得它没用」。
    - **顺带纠正一条写错的既成事实**：`mmd2maxClipBones` 的注释原说「香菱的身体是一个 Editable_Poly」，
      逐类计数说不是 —— 网格是 **62 个 `Editable_mesh`**（身体 1 + Morpher 目标 61，而且不是 Poly 类）。
      结论没变（每个的包围盒都盖住全身，按对象裁网格等于什么都没裁 ⇒ 带网格那几张仍只能靠取景框本身），
      但注释里的事实换成实测数字了。
    - **读数落点（第四十二轮）**：`clip_hidden` 脚部 6 张 **317**、胸 4 张 **303**、手 2 张 **300**、
      颈 2 张 **269**、整场景 5 张 **0**。`322 − 317 = 5` 与图对上：脚顶视里活下来的正是
      `L/R Foot`、`L/R Toe0` 与 Biped 根（画面正中那个八边形 = `Bip001` Center）。
    - **图证（验收只认图）**：`shots/香菱_bone_foot_top.png` 从「头骨糊在正中」变成**只剩两条脚链 + 两只趾盒 +
      那个 Center 八边形**，辫子/手指/帽子全部出画 —— 用户要的「顶视看外八」第一次真的拍到了。
      `香菱_bone_chest_front.png` 里胸框以下那截骨盆与裙摆骨没了（上一轮那张的下半截就是它）。
      **同时否掉一条把我拦住两轮的猜测**：「藏 `Biped_Object` 会不会连带把整副骨抹掉」——
      `香菱_bone_front.png`（整场景、`clip_hidden=0`）与脚部特写里框内的骨盒都还在 ⇒ Max 的 `isHidden`
      不沿父子传播，这一条以后不用再担心。
    - **静态与测试**：`check_ms [OK]`（非严格债 66 条）、`Ran 611 tests OK (skipped=2)`（606 → +5：
      `ShotClippingContract` 五条 —— 谓词只有一条规则且两条腿都是实测来的、裁剪只认这把尺、
      计数排在写回读之后、还原只用这一张的名单、裁/报/还三件事在 `ShotOne` 里的先后次序、
      Python 的 5 张整场景名单与 `.ms` 的 `b.min b.max` 调用点同源）。谓词那条现在钉的是
      **`assertNotIn("Biped_Control")`**：已被 322=268+54 判死的写法不许再被加回来。
    - **几何中性证明**：`diff_round --allow "^配置" --allow 截图 --allow clip` 对撞 `baselines/round40_*`
      ⇒ 归一后 482 vs 483 行，**无实质差异**（只动时间、耗时与截图那几行）。骨位、权重、转角、骨盒、
      `盒隙`、`链向` 一行都没进差异表 —— 这四轮改的确实只在「.max 落盘之后」。
    - **回滚**：整段裁骨只在 `MMD2MAX_SHOT` 打开时跑，且排在 `.max` 落盘与成功标记之后 ⇒ 坏了不污染产物；
      要退回不裁的行为就是删掉 `mmd2maxShotOne` 里那两行（`mmd2maxClipBones` / `mmd2maxUnclipBones`），
      没有 flag —— 与 #69/#70/#71 同一口径（截图段整体只有一个开关）。

31. **#73「胸部顶视特写」落地（2026-11-04 12:18 第四十三轮，香菱 `--shot`，退出码 0，`香菱.max` 落盘，
    Max 自行 `quitMax`，无弹窗）：`CHECK` 519 PASS / 0 FAIL / 22 SKIP，规格 541 行 / 截图 20 张全落盘**
    - **为什么还要第三张**：用户那句「胸部骨应该指向乳头」里的方向有**两根水平分量** —— 向外（x）与
      向前（−y）。正视图把 y 压成一条线、侧视图把 x 压成一条线，第 33 轮那两张各自只能判一半；
      `骨在水平面内张开成 80 度` 这件事此前**只有数字知道**（`bone_rb dir_deg` 那条 3D 夹角），没有图。
      顶视是唯一两根轴同时可见的视图，而它原先拍不了的原因不是视口坏，是框外的东西没藏掉 ——
      **#72 是它的前置**。
    - **改了多少**：三张名单各加一项（`SHOT_TAGS` 19→20、`SHOT_TIGHT_TAGS` 6→7、`SHOT_H_AXIS`
      加 `"bone_chest_top": "x"`）+ `.ms` 一个调用点。**控制流一行没动**（第 7 条预言的那件事兑现了：
      `#view_top` + `ShotOneTight` + `mmd2maxAspectBox` + `mmd2maxHAxis` + 裁骨全都现成）。
      行数 +4 = fill_pct + clip_hidden + `tight_h_lo/hi`，**通道名一个都不新增**（`len(CHANNELS)` 仍 116）。
    - **读数**：`clip_hidden=303`（胸框里活下来 19 根）、`fill_pct=62.9506`（门槛 45）、
      `tight_h_lo/hi = -19.1061 / 19.1061` 对尺 `±4.915695` ⇒ 收过之后的框照样罩得住胸骨（#70 那半条判据）；
      `tags` 闸的期望从 19 变 20，实报 20。
    - **图证**：`shots/香菱_bone_chest_top.png` 里两根胸骨是**朝屏幕下方（= 世界 −y = 人物前方）**的两个小白锥，
      左右各一、带一点外张，中间是脊柱盒、两侧是两条手臂链；头骨/手指/裙摆全被 #72 裁掉了。
      这是「指向乳头」这句话第一次作为**一张图**存在。
    - **三条测试被这一轮撞出来，全都是「绿着失效」的形状**（不是新缺陷，是旧测试写死了）：
      ① `test_the_aggregate_shot_counter_is_a_two_sided_gate` 拿字面量 `"20"` 去撞 `tags` 闸，
      而名单加一张之后**期望值正好变成 20** —— 那条从此测不到任何东西，改成 `len(SHOT_TAGS) + 1`；
      ② `test_an_unmeasurable_model_still_reports_every_number` 抄死 `len(checks) == 24`，
      改成按 `FRAMING_AXES` 与 `SHOT_TIGHT_TAGS` 算；
      ③ `view_axis = {… "#view_top": "z"}` 是**错的**（顶视屏幕横=世界 X、竖=Y，Z 是视线方向），
      它一直没被撞到，因为 #73 之前没有任何 tight 图走俯视。改成 `"x"` 并在测试里写明这条是
      `bone_chest_top` 第一次走到的分支 —— 这正是「新增一张图」顺带买到的东西：**没被执行过的
      对照表分支，错了也没人知道**。
    - **几何中性证明**：`diff_round --allow "^配置" --allow 截图 --allow clip --allow bone_chest_top`
      对撞 `baselines/round40_*` ⇒ 归一后 482 vs 489 行，**无实质差异**；`check_baseline.py 香菱`
      重新全绿（`meta.tsv` 41 行不变 —— #73 不需要新的导出侧数据，胸框与胸骨的尺是 #70 就交出去的）。
    - **回滚**：删掉那一个调用点 + 三张名单里的那一项即可（行数会回到 537，`test_shape_of_the_priced_spec`
      的三个硬数字要跟着回退 —— 那是设计好的联动）。

**下一轮（换模型 / 继续验收）判读几件事**：
1. 换任何新模型先跑 `tools/run_model.py <pmx>`，看 `limbs.tsv` 脚段第 17/18 列：**高必须 ≤ 段长**（`min(down/0.848, seg)`），且 L/R 差 ≤0.05cm；若宽 > 鞋网格 AABB 的横向跨度，就是第 20 条④ 那道「只许盒内网格投票」的闸被绕开了。
2. `足盒缩后 … 本地 min/max` 的竖直比例必须还是 15.2 : 84.8（`FootHeelBoxHandoffTests` 钉的四个数）；换了 Max 版本若这个比例变了，那四个常量（`FOOT_BOX_*`）要跟着重新回读，不能猜。
3. **`盒隙 <Foot> -> <Toe0> … 端面覆盖=true` 且 `盒隙结论 没接上=0 根` 是这一版脚链的唯一真判据（#41）**。`链向 … 骨轴与子关节夹角` 现在**按构造读 29.9 度、`断开=2 根` 是预期**（压平水平轴的定义），别再拿它当成绩也别拿它当回归 —— 它只在 `FOOT_BOX_FLAT=false`（斜轴回滚位，M1b 前叫 `MMD2MAX_FOOT_FLATTEN`）时才该回到 ≤3 度。两行一起看才知道口径：`画线 … 目标` 的 z 分量 =0 ⇒ 平轴（#41 默认），≠0 ⇒ 斜轴。
4. 老账不动：`脚骨偏航结论 骨=0.0 趾=0.0`、`段校验 Foot -> Toe0 ≤0.05`、`脚趾复摆 摆后 ≤0.01`（注意 `起始偏差` 在**接趾之后**会跳到 2.2cm 量级，那是盒被拉长的必然，复摆能收回去；收不回去才是回归）、`结构向 复核 夹角>3.0度=6`。
5. **M3 之后的两条新读数口径**：`收敛闸门: 出口=ceiling 遍数=3/3 方向超标=N 判据达标=false` 是**诚实的撞上限**，不是新故障 —— 别拿它当回归，也别拿它当达标；要判断有没有变好，看 `converge dir_over / place_max_cm` 那些锁的行。换模型（非香菱）跑时锁退成 `report`，规格里 `pass/ran` 行照旧在 ⇒ 时间轴审计**参与**；如果哪一轮日志里出现「时间轴审计未参与」，说明那张规格表是 M3 之前写的（旧产物），不是配置出错。
6. 仍开着的：#12 视觉验收（脚链三件事各有中线，等用户判这一版）、#27（Spine2 后仰 / 颈前倾 / 手指贴合）、#29（锁骨 0.955 已定价）、#35（用户拖 `Bip001 L Toe0` 的实测）、`meta.tsv` 的 `has_sdef/has_qdef` 恒 0。**#71 已结案**（见第 29 条）：颈部特写的框现在也由导出侧量，`h × 0.55..0.98` 只剩「认不出颈链」时的兜底。**#72 已结案**（见第 30 条）：`foot_top` 被裙摆/头骨糊住那件事不再是既成事实，15 张特写每张都按取景框裁过骨。**#73 已结案**（见第 31 条）：「前向骨在正视图读成一个点」由 `bone_chest_top` 补上了 —— 胸骨的水平张角现在有一张图能看。
7. **两张特写的框都归 `FRAMINGS` 之后，加第三张（手部/脚部）只许改数据不许改控制流**：`obj_run.FRAMINGS` + `export_obj` 的 `_chest_band`/`_neck_band` 同族函数 + `verdicts.SHOT_H_AXIS`（键集必须 == `SHOT_TIGHT_TAGS`）。手部/脚部那几张目前仍按节点算框（`hand 取景` / `foot_top 取景`），它们的尺是**场景里的 Biped 节点**而不是导出的骨，所以 `tight_h_*` 只有胸/颈 6 张参与判定 —— 要把脚部也纳入，得先在导出侧给脚链出一份 `{prefix}_bone_*`，别拿 Max 读回来的框当尺。
8. **#73 之后开着的两张（同一套机制，各只改数据）**：① `chest_top` —— 带网格的顶视那张。#66 定过一条规矩：「骨在胸前」与「骨指着乳头」分不出来 ⇒ 骨架版与网格版必须成对；胸骨现在 front/side 有对、top 只有骨架版，而**乳头点在网格里**，顶视要判「骨尖落没落到点上」就得有网格版。加它 = `SHOT_TAGS` +1、`SHOT_TIGHT_TAGS` +1、`SHOT_H_AXIS` 加 `"chest_top": "x"`、一个 `ShotOneTight` 调用点，行数 +4、通道 +0。② 颈部同理可补 `neck_top`，但颈部没有「指向」这件事，价值低，别顺手加。
9. **换模型冒烟还没跑过**：`test_runs/香菱-海灯节/` 那份导出产物（24921 顶点 / 577 骨 / 60 morph）一直没进过 Max。`tools/obj_run.py "test_runs/香菱-海灯节" 香菱-海灯节 --shot` 一跑就能验三件**只有换模型才撞得出来**的事：`lock` 行退成 `report`（第 5 条）、`_neck_band` / `_chest_band` 认不出骨时那 12+12 行降级（第 29 条）、以及 #72 的 `clip_bones_total` 那句逐类计数在别的模型上是不是也报 `Biped_Object=…`（如果它报出第三个类名，谓词就得按第 30 条的方式重新对账）。

建议顺序：`--no-skin --shot` 冒烟 → 完整 `--shot` → 读 `test_runs/*/shots/*.png` 与 `<模型名>_objrun.log`。

