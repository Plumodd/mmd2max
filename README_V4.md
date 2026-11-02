# mmd2max v4 · 架构契约

v3 的完整缺陷史在 `README_HANDOVER.md`（23 条编号记录，一条都别丢）。这份文件只写 v4
改了什么结构、为什么，以及**怎么判断这一轮又犯了老毛病**。

回滚开关：v3 目录 `C:\Users\Administrator\Desktop\mmd2max_v3_src_2026-11-01` 原样不动，
`C:\Users\Administrator\Desktop\mmd2max_v3.zip` 与 `Plumodd/mmd2max` 是已交付的第十七轮
成品。v4 任何一步做砸了，回到 v3 目录跑 `tools/obj_run.py` 即可，不需要在 v4 里修。

---

## 1. 病根：验收层和写入层共用一个真值源

第十七轮之前反复出现的三类缺陷是**同一个 bug 穿了三次衣服**：

| 轮次 | 用户看到的 | 判据当时说什么 |
| --- | --- | --- |
| #26 | `Bip001 R Foot` 朝人物中线偏 | 「偏航 0.0 度」 |
| #40 | 脚链骨轴不指向自己的子关节 | 「链向 OK」 |
| #41 | 脚骨盒是牙签，趾块悬空 | 「段校验通过」+ 计数全绿 |

三次都不是判据写错了，是判据**算的就是写进去的那个值**：`mmd2maxBoxWantDir` 既用来决定
骨盒朝哪，又用来核对骨盒朝哪 —— 恒等式，永远绿。`段校验` 拿两个刚被钉到同一对 PMX
点上的 pivot 互相量 —— 也永远绿。日志里 413 行中文判据、几百个"成功"计数，没有一行
有能力说"不"。

所以 v4 不是"再修一根骨头"，是把**判据的独立性**变成结构约束。

## 2. 规则 I~IV（每条都要有工具能自动判，写在注释里不算；规则 V 在 §6）

**规则 I — 判据独立。** 任何检查只能拿「从 Max 回读的值」去比「从 spec 文件读进来的
值」（spec 由 Python 从 PMX 生成）。检查函数**不许**调用写路径用过的那个求值函数。
自动判法：`tools/check_ms.py` 新增一条静态规则 —— 名字匹配 `*Check*` / `*校验*` 的
函数体里出现 `BoxWantDir` / `SegLen` / 任何 `Want*`/`Target*` 求值器 = 编译期报错。

**规则 II — 只报告不修的检查是非法的。** 每条判据要么登记「谁能修它」（某个 pass 的
收敛目标），要么是终局不变量（不达标 ⇒ 退出码非 0）。v3 的 `盒隙 端面覆盖` 只打印不
处理，`链向` 在退出 Figure 模式之后才跑（结构上已经修不了）—— 这两条在 v4 都必须挂到
pass 上。自动判法：注册表里 `gate_check` 为空且不是终局项 ⇒ 启动即报错。

**规则 III — 陈旧判定。** 每个 pass 声明 `writes` / `invalidates` 两组事实名；runner
保证每条判据在**最后一个能作废它的 pass 之后**重算过，否则记 `STALE` 且不计入成功。
自动判法：runner 断言 + `verdicts.tsv` 的 `stale` 列。这一条专门治 #32 那类"把某个 pass
往前挪一格，长度轴悄悄退化成 0，判据反而更绿"。

**规则 IV — 数值单源。** 所有「应该等于多少」的量在 Python 算（PMX + 网格截面），写进
spec 具名列；MAXScript 只写、只回读。v3 里 `widths.tsv`（手掌/手指粗细）已经是这个模式
并且好用，v4 推广到全身：骨盒长宽高、轴向、前端面位置、大腿扭转、踝俯仰。
理由：Python 侧能离线单测、能一次算完两边（左右脚不再"只按左脚测"），MAXScript 侧
不能。

## 3. 探针实测（先量后设计，别再猜 API 名）

`probes/` 里是原始脚本和原始输出，不是文档。

- `probes/skin_readback.txt` / `skinops_members.txt`：**Max 2022 的 skinOps 一共 143 个
  成员**（`-- Unknown property: "GetVertexWeightTotal" in #Struct:skinOps(...)` 这条报错
  会把整个成员表吐出来 —— 这就是我们缺的 `dumpInterface`）。存在的读回函数：
  `GetVertexWeightCount` / `GetVertexWeight` / `GetVertexWeightBoneID` /
  `GetVertexBoneIDsAndWeights` / `GetBoneWeight` / `GetBoneWeights` / `GetNumberVertices`。
  **不存在**：`GetVertexWeightTotal`、`GetBoneIndex`、`GetNumberWeights`、
  `GetMaxWeightsNumber`、`GetWeightValueByIndex`、`forceUpdate`。
  ⇒ 蒙皮回读验收技术上完全可行（规则 I 想要的那种"问过 Skin 自己存了什么"）。
  ⇒ 顺带抓到一个 v3 死调用：`max_import_obj.ms:3982` 的
  `try ( skinOps.forceUpdate skinMod ) catch ()` 是个**不存在**的函数，被空 catch 静默
  吞掉 —— 我们一直以为它 force 了一次更新。这正是规则 II/探针要消灭的东西。
  ⇒ 还抓到 `ReplaceVertexWeights smod 1 #(1,2) #(0.7,0.3)` 在 8 顶点的 Box 上报
  "Exceeded the vertex count"，而同签名的 v5 写入返回 OK —— 写入返回值不可信，
  只有回读可信。
- `probes/biped_taper.txt`：Biped 链接节点 `classof = Biped_Object`，对它
  `getProperty #Taper/#width/#height/#boneType/#linkLength` 一律 Unknown property；
  `setBox/setWidth/setTaper` 在控制器上不存在。骨盒只有 `#scale` 一个把手。
- `probes/biped_gap2.txt`：骨盒远端面 == 子节点沿轴坐标（差 0.0）；`#scale` 沿长度轴
  乘 k，盒远端与子节点同步乘 k，退出 Figure 模式仍在 ⇒ 盒子的沿轴长度可写。
- MAXScript 本身的六个坑（探针 v1→v6 各交一次学费，全部记在 `probes/skin_readback.ms`
  文件头）：未定义的**全局函数**在调用位会直接 abort 整个文件；`openFile mode:"a"` 遇到
  不存在的文件返回 undefined（**不创建**，日志会静默变空）；顶层不许 `local`；
  `getCurrentException()` 必须在 catch 块**内部**读，出来就没了；`addNewModifier` 不存在
  （v3 用的是 `Skin()` + `addModifier`）；字符串 `+` 整数抛
  "Unable to convert: 0 to type: String"（所以 v3 到处 `as string`）。

## 4. 里程碑（每个都要过一轮实跑 + 一张截图）

| | 做什么 | 完成的判据 |
| --- | --- | --- |
| M1 | 线格式 v2：每个 spec 第一行是列名表头，单一 Python writer，MAXScript 建 name→index 后按列名取值；去掉 `bones.tsv` 与 `香菱_bones.tsv` 双份 | 第十七轮的判据数字逐位不变 |
| M1b | 配置单源：25 个 `MMD2MAX_*` 注入项收成一个 cfg 块 + `MMD2MAX.CFG`；缺块即 abort；删死 flag（`MMD2MAX_LENAXIS`）、`mode=="flat"` 死分支、从没走到的 DQ 分支；`foot_pitch` 三重反转改一条直读 | 手跑 .ms 与自动跑几何**不可能**不一致 |
| M2a | 五个容差/开关（`DIRTOL 3.0 / ROLLTOL 1.0 / TOETOL 0.5 / GAPTOL 0.5 / FOOTREACH true`）从 `.ms` 硬赋值搬进 `mmd2max/targets.py`，经 `MMD2MAX.CFG` 下发；新增 `mmd2maxCfgNum`（`"abc" as float` 在 Max 给 0 不报错）；配置汇总在 `InitLog` 之后落日志 | `.ms` 里每个名字只许一行赋值、右边必须是 CFG 读取；实跑 diff_round exit 0 |

**实跑记录**：M1 ✅（对 v3 第十七轮基线）；M1b ✅（第十九轮 `baselines/round19_objrun.log`）；
M2a ✅（第二十轮 `baselines/round20_objrun.log`，与第十九轮 diff **只多一行**「配置: …」= 新可见的汇总）。
判据是 `python tools/diff_round.py --allow "^配置:" <基线> <本轮日志>` ⇒ exit 0：
17→19 与 18→19 都**只剩 M1b 新增的那一行「配置: …MMD2MAX.CFG」**，其余全是时间、耗时与路径。
这个对比器本身是新写的（`tools/diff_round.py` + `tests/test_diff_round.py`），因为手写 sed 裁
时间戳在这台机器上会把整份日志读成「全变了」（时间戳不补零 + 分隔符回显与字节不一致）；
写它时又抓出它自己两个**会造成漏判**的缺陷（吃掉非时间戳行的行头、把 `累计误差=0.224741`
的小数部分当字节数遮掉），已用三处注入变异复核。细节见
[README_HANDOVER.md §7 第 24 条](file:///C:/Users/Administrator/Desktop/mmd2max_v4_src_2026-11-03/README_HANDOVER.md)。
| M2 | 目标值全部搬到 Python + `verdicts.tsv`（phase/check/bone/expected/actual/tol/status/stale，ASCII）+ Python 侧报告校验器（schema、覆盖率、容差） | 任何 FAIL 或缺覆盖 ⇒ 退出码非 0 |
| M3 | pass 注册表：`requires/writes/invalidates/gate_check/max_iters` 数据化，runner 拓扑排序 + 陈旧断言；收敛循环的退出条件从"撞 3 遍上限"改成"判据真达标" | 第十七轮实测：三遍的落位最大值 5.6995 一动不动、方向超标 4→5→5，是靠上限退出的，"尝试=0 ⇒ 方向达标"从来不成立 |
| M4 | 真回读验收：蒙皮用 `skinOps.GetVertexBoneIDsAndWeights`，骨盒用节点世界包围盒，落位/转角用 `biped.getTransform`，Morpher 用通道名；`biped.saveFigFile` 快照 + 超差 `loadFigFile` 回滚 + 记 FAIL | 判据全部来自回读，写入侧计数不再参与验收 |
| M5 | 测试重写：契约测试只钉表头/配置键；加金标准（脚盒 3.714×8.656×9.736 等）、判据回放、**变异测试**（改坏一个 pivot 必须至少一条判据变红）、**蜕变测试**（160cm vs 80cm 输出必须精确 0.5 倍；左右镜像必须镜像） | 变异注入全绿 = 验收层是装饰，直接判失败 |

`test_maxscript_contract.py` 现在 1885 行 / 118 个方法 / 153 处源码切片 / 499 个
assertIn —— 它从不执行 MAXScript，只把中文日志措辞钉死。这不是测试，是**给重构上锁**。
M5 之前不动它（它还能挡住手滑改日志格式），M5 一次性换掉。

## 5. 顺手从网上捡到的两条（v3 完全没用上）

- **PMX 自带 roll/扭转轴**：骨记录里有「轴固定化 Fixed Axis」和「局部轴 Local Axis /
  SDef C·R·W」两组数据，是源文件给的官方骨向。v3 的 `meta.tsv` 里 `has_sdef`/`has_qdef`
  恒为 0（要么没解析要么写死），等于把现成的**独立真值**丢了，只能靠网格射线反推方向 ——
  而反推的函数又被验收层复用，这就是 #26/#40 反复的根。见任务 #57。
- **`3dsmaxbatch.exe` 官方无头模式：本环境实测不可用**（见 `probes/batch_probe_record.txt`）。
  连三行的 `createFile` 平凡脚本都不执行，三次跑分别卡到 300/240/150 秒超时，子进程连窗口
  都不出现；而同一台机器 `3dsmax.exe -U MAXScript` 是能跑完的。⇒ 驱动层继续用
  `obj_run.py` + UIA 点启动弹窗，「无头模式能去掉弹窗白名单」这条路暂时不成立（任务 #58
  记死因与下次排查顺序）。

另外记两条别人的坑：社区报「Max 里 Bone 的缩放会静默失效，删 `%APPDATA%\Autodesk\3dsMax`
配置后正常」—— 这是我们骨盒尺寸偶发不一致的一个候选解释，**先不删用户配置**，
只在做实跑对照实验时验证。

## 6. 规则 V（探针期间新加）：不许有空 catch

`grep -c "catch ()" scripts/max_import_obj.ms` = **64**。空 catch 的语义是「这件事失败了
我也不想知道」，而 v3 恰恰有一条这样的死调用藏在里面：
`max_import_obj.ms:3982` 的 `try ( skinOps.forceUpdate skinMod ) catch ()` —— 探针实测
Max 2022 的 skinOps **143 个成员里没有 forceUpdate**，这句从来没生效过，我们却一直在
「force 更新之后再读 GetNumberBones」。

v4 的写法：`catch` 必须留下痕迹（记一条 verdict，或至少 `say` 一行带 `getCurrentException()`
的日志）。`tools/check_ms.py` 加一条规则：`catch ()` 与 `catch ( )` 直接报错。
这条和规则 I~IV 一样，是工具能自动判的，不靠注释。

