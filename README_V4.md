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
| M2b | 判据拆成两张表：Python 写 `MMD2MAX.VERDICTS`（规格 = phase/check/subject/expected/tol/kind/when），Max 只往 `MMD2MAX.REPORT` **追加读数**（phase/check/subject/actual），Max 侧不出现 PASS/FAIL/verdict 任何字样；三种 kind 分工 —— `gate` 是真要求、`lock` 是已定价的缺陷（PASS 只等于没退步）、`report` 只要求这一行出现；锁只认被定价的那具模型，换模型退化成 `report` | 规格 121 行与 .ms 的 45 个通道**逐一对撞**（少一行=判据是装饰，多一行=读数没人判）；`--gen-only` 就能证明写侧接上；实跑报告 121 行全部拼上 |
| M2c | `tools/check_verdicts.py`：把两张表拼起来判，只打挂掉的行 + 一句汇总（含 lock 警示），全量状态落 `MMD2MAX.CHECK`；`obj_run` 在产物齐之后**再过这道闸**，退出码新增 7（有 FAIL）/ 8（两张表没法用），GUI 从此报不出「产物在但没判过」的完成 | 坏读数必须换成非 0 退出码：缺 always 行、值不是数字、报告比规格旧、报告里有规格不认识的行 ⇒ 全部非 0；19 条契约用例 |
| M3 | pass 注册表 `mmd2max/passes.py`：23 段，每段声明 `requires`（结构前提）/ `writes`（产出哪些 phase）/ `reads`（同一个 phase 有多个作者时，认领具体哪条 check）/ `invalidates`（跑完作废谁的读数；`moves=True` = 场景读数全作废）/ `optional` / `max_iters` / `gate`。Max 侧每段收工补一行 `pass/ran/<id>`（**ran 是收工不是开工**），报告的行序于是就是免费的时间轴；`passes.audit` 拿它判五件事：覆盖 / 恰好一次 / requires 拓扑 / STALE / 归因 + 闸门自相一致，问题以 `timeline` kind 落进 `MMD2MAX.CHECK` 并计入非 0 退出码。收敛循环的退出条件从 `rp[1] == 0`（写入侧自述「这一遍没有骨需要转」）改成 `mmd2maxConvergeGate dirOver == 0`（场景读数），并把出口理由 / 遍数 / 达标位三条读数互相对账；遍数上限从 `.ms` 的字面量 `3` 搬进注册表，经 `MMD2MAX.CFG` 下发（Max 只读不写） | 第十七轮实测：三遍的落位最大值 5.6995 一动不动、方向超标 4→5→5，是靠上限退出的，「尝试=0 ⇒ 方向达标」从来不成立 —— 现在这种出口必须报成 `exit_reason=ceiling` 且 `gate_satisfied=false`；坏时间轴（少一段 / 重复一段 / 读数落在别人的段里 / 作废者之后没人再量 / 报 ceiling 却没跑满遍数）每一条都有注入变异用例判红 |
| M4 | 真回读验收：蒙皮用 `skinOps.GetVertexBoneIDsAndWeights`，骨盒用节点世界包围盒，落位/转角用 `biped.getTransform`，Morpher 用通道名；`biped.saveFigFile` 快照 + 超差 `loadFigFile` 回滚 + 记 FAIL | 判据全部来自回读，写入侧计数不再参与验收 |
| M5 | 测试重写：契约测试只钉表头/配置键；加金标准（脚盒 3.714×8.656×9.736 等）、判据回放、**变异测试**（改坏一个 pivot 必须至少一条判据变红）、**蜕变测试**（160cm vs 80cm 输出必须精确 0.5 倍；左右镜像必须镜像） | 变异注入全绿 = 验收层是装饰，直接判失败 |

**实跑记录**：M1 ✅（对 v3 第十七轮基线）；M1b ✅（第十九轮 `baselines/round19_objrun.log`）；
M2a ✅（第二十轮 `baselines/round20_objrun.log`，与第十九轮 diff **只多一行**「配置: …」= 新可见的汇总）。
判据是 `python tools/diff_round.py --allow "^配置:" <基线> <本轮日志>` ⇒ exit 0：
17→19 与 18→19 都**只剩 M1b 新增的那一行「配置: …MMD2MAX.CFG」**，其余全是时间、耗时与路径。
这个对比器本身是新写的（`tools/diff_round.py` + `tests/test_diff_round.py`），因为手写 sed 裁
时间戳在这台机器上会把整份日志读成「全变了」（时间戳不补零 + 分隔符回显与字节不一致）；
写它时又抓出它自己两个**会造成漏判**的缺陷（吃掉非时间戳行的行头、把 `累计误差=0.224741`
的小数部分当字节数遮掉），已用三处注入变异复核。细节见
[README_HANDOVER.md §7 第 24 条](file:///C:/Users/Administrator/Desktop/mmd2max_v4_src_2026-11-03/README_HANDOVER.md)。

M2b/M2c 的实跑（第二十一、二十二轮）第一次把两张表真的拼起来，当场抓到三条只有实跑才会现形的毛病：

1. **表头写成了函数名**：`format "%\n" (mmd2maxReportHeader)` 少一对括号，落地的是**函数本身**
   （字面量 `mmd2maxReportHeader()`），列名行变 1 列，后面 121 行读数每行都「比列名多 3 列」
   ⇒ `parse_table` 抛。写文件的调用自己成功了，日志里一个异常都没有。
2. **`链向 断开` 的阈值在两边各刻了一遍**：`.ms` 里写死 3.0，Python 侧又拿 `DIRTOL` 定尺
   ⇒ 改成 `MMD2MAX_DIRTOL`，并用契约测试钉住「这一段的门槛不许是字面量」。
3. **骨盆三段有一条永远不可能通过的 gate**：Max 在 `mmd2maxHipAnchor` 把 Pelvis→{L Thigh,
   R Thigh, Spine} 的目标改写成「离两髋中点的距离」（腿根 9.03→7.00、脊柱 6.49→3.51），
   而 Python 的尺还按 PMX 腰→子骨的长度刻 —— 第二十一轮判红 3 行就是这个分叉。
   现在 `verdicts.segment_expectation` 用同一批 limbs 坐标列、同一个 0.05 门槛、同一对中点
   重算这三行，并拿第二十轮日志里的 `目标=` 做实证对照。
   **注意方向**：这条不是「改判据去迁就 Max」—— Max 报的是场景里量到的长度，改的只是
   期望值该按哪个锚点算；几何一个字节没动（diff_round 见下）。

M3 ✅（第二十三轮 `baselines/round23_objrun.log`，判据 `diff_round.py --allow "^配置" --allow "^收敛闸门"
baselines/round22_objrun.log baselines/round23_objrun.log` ⇒ **exit 0**，415→416 行）。
把遮罩去掉只有 **3 行**实质差异：CFG 汇总从「28 项」变「29 项」（多 `收敛上限=3`），以及新增的一行
`收敛闸门: 出口=ceiling 遍数=3/3 方向超标=5 判据达标=false`。⇒ 几何一个字节没动，动的是**它怎么说话**：
第十七轮那个「尝试=0 ⇒ 方向达标」的假绿灯从此自己报「没达标」，因为出口判据改成读场景
（`mmd2maxConvergeGate dirOver == (dirOver == 0)`），不再读写入侧的计数。
行数账：规格 121→**147** 行（+23 行 `pass/ran`、+3 行 converge 闸门），报告 146 行读数，
`CHECK` 146 PASS / 0 FAIL / 1 SKIP —— 那条 SKIP 是 `pass/ran physics_build`（本轮没给 physics 文件，
`when=if_present` 退成未跑，不是判据缺失）。

写 M3 时抓到两条，一条在产品、一条在判据层：

1. **`ran physics_build` 原来是无条件报的**：没给 physics 文件时那段整段不发生，却照样宣称跑过。
   「没跑却报 ran」比「没跑没报」难查得多 —— 它会给陈旧判定一个根本不存在的段。已挪进 `if` 块里。
2. **时间轴审计只按本轮的规格参与**：注册表是**今天**的代码，规格表是**那一轮**的 Python 写的。
   拿今天 23 条 pass 行去判第二十二轮 = 拿新合同判旧产物，会把每一轮冻结基线都判红。
   所以规格里没有 `pass/ran` 行时打一行 SKIP「时间轴审计未参与」；这条逃生通道由
   `FrozenRoundReplayTests`（唯一允许多出的就是这行 SKIP）和 `test_pre_m3_spec_is_not_judged_by_todays_registry`
   （本轮规格有 pass 行 ⇒ 审计必须参与、坏时间轴必须 exit 1）两头夹住。

`tests/test_passes.py`（新，38 条）里最值钱的是一批**注入变异**：重复 ran、注册表不认识的 pass id、
`requires` 顺序倒置、把 `segment/len_cm` 挪到作废它的 pass 之后（同时触发 STALE + 归因错）、
把 `mesh/vertex_count` 挪进别人的段、闸门三数互相打脸（`ceiling` 却遍数不满上限、`gate` 却
`gate_satisfied=false`）。每条都断言**具体那一句**报错文本，不满足于「非空」。全套 438 条 OK。

`test_maxscript_contract.py` 现在 2011 行 / 123 个方法 / 435 个 assertIn + 67 个 assertNotIn —— 它从不执行 MAXScript，只把中文日志措辞钉死。这不是测试，是**给重构上锁**。
M5 之前不动它（它还能挡住手滑改日志格式），M5 一次性换掉。

M4b ✅（第二十四轮 `baselines/round24_objrun.log`，`--shot`，退出码 **0**）：蒙皮第一次被**从场景里读回来**验收。
期望值那一侧全部来自输入文件（`tools/obj_run.py` 解析 `bones.tsv` + `weights.tsv`，
`verdicts.skin_expectation()` 数出「作者要什么」），Max 那一侧只报「Skin 修改器里到底挂着什么」：

| 读数 | 本轮实测 | 尺的来源 |
| --- | --- | --- |
| `skin_rb/links_read` | 26742 | weights.tsv 的权重对总数（Python 另一进程数） |
| `skin_rb/links/` 四根脚骨 | L Foot 363 / L Toe0 74 / R Foot 366 / R Toe0 74 | 按骨号出现次数逐根数 |
| `skin_rb/verts_read` | 18892 | CFG 的顶点数 = 权重表数据行数 |
| `zero_verts` / `dom_mismatch` / `name_mismatch` / `unmodified_verts` | 全 0 | 「写完立刻读」的硬要求 |
| `weight_err_max` / `sum_err_max` | 0.0 / **1.19209e-07** | 期望 0，容差 1e-4 / 1e-3（float32 往返） |
| `repaired` / `zero_verts_pre` / `elapsed_s` | 0 / 0 / 1.318 秒 | 过程量 ⇒ `kind=report`，合法的修权不该判红 |

`sum_err_max` 那个非零的 1.19e-07 正是「这是真量出来的」的证据：抄来的数会是 0。
配套的 `--no-skin` 冒烟轮（第二十五轮 `baselines/round25_noskin_objrun.log`，退出码 0）证明降级方向对：
141 通过 / 0 失败 / **30 未跑** —— 23 条回读 + 4 条写入侧 + `skin_apply`/`skin_verify` 两条收工边界
+ 一条 `physics_build`，全部 `when=if_present` 退成 SKIP 而不是 FAIL，规格表一行没少。
几何中性：`diff_round.py --allow "^配置" --allow "^收敛闸门" --allow "^回读进度" --allow "^蒙皮回读"
round23 round24` ⇒ **exit 0**（416→420 行，多出的四行全是回读自己的进度与汇总）。
规格 147→**171** 行（+23 条回读判据 +1 条 `pass/ran skin_verify`），通道 49→**69**。

顺带扳正一条歪了 23 轮的尺：`skin/bones` 的期望值原先拿 meta 的 `kept_bones`，可它报的是
`skinOps.GetNumberBones` —— 只有「每一根骨都参与蒙皮」的模型才碰巧相等（香菱正好如此，所以一直是绿的）。
换一具有 `skin_flag=0` 的骨就会判红，而错的是尺。现在期望值来自骨骼表的 `skin_flag`，
`kept_bones` 退回去只干一件事：交叉核对「骨骼表行数 == 导出摘要说的骨数」，分叉当场中止（两条新守卫用例）。

**探针 m4~m4f 换来的六条硬事实**（全部落进 `mmd2maxVerifySkin`，别再靠猜 API 行为）：
新建的 Skin 必须先被堆栈求值一次才读得动（`mesh.numVerts` 或 `redrawViews()`，`skinOps` 的**读**操作不算求值）；
每个顶点只有**第一次**写生效，第二次写会把权重永久变成 0.0，唯一补救是 `skinOps.unNormalizeVertex smod v true`
再重写；Skin 总会归一化并保持写入顺序；没写过的顶点读回来是 `<某根骨> @ 1.0` 而不是空；
`IsVertexModified` 返回整数 0/1 而非布尔；权重 ≲1e-8 会被存成 0.0 但**骨号还在** —— 所以链路按骨号出现次数数，
按权重数就会少数（M4f C2）。18892 个顶点全表读一遍 1.3 秒，不是性能瓶颈。

**规则 10 的边界还没测清**（记成待办，别当已解决）：契约测试禁的是 `local x = try (…) catch (…)`，
而 v3 的规则文本把「try 当表达式取值」和「行尾续行」捆在一句里说。探针里那种写法跑通过，所以真正致命的
很可能是**顶层 `local`**（那本身就该 abort 整份文件）。本轮选择改代码绕开、而不是赌一整轮实跑去放宽规则；
下次单开一个探针把「顶层 local」「try 当表达式」「行尾续行」三者**分别**测一遍，再决定这条规则怎么切。

M4c ✅（第二十六轮 `baselines/round26_objrun.log`，`--shot`，退出码 **0**）：骨盒第一次被**从场景里读回来**验收。
新 pass `box_verify`（`requires=struct_dir_check`、`writes=box_rb`、非 optional，`pass/ran` 也进了规格），
Max 侧 `mmd2maxVerifyBoxes` 只报四条数（`width_cm` / `height_cm` / `length_cm` / `len_axis`），
期望值全部由 Python 从**输入文件**算：脚段读 `limbs.tsv` 的第 11~18 列（`toe_len` / `tip_x..z` / `toe_w` /
`toe_h` / `foot_w` / `foot_h`），手读 `widths.tsv`。本轮 42 条 `box_rb` 全绿，`nodes_measured=16 读不到=0`。

**两把尺必须分开，不能一把尺量全身**（这是 M4c 唯一真正的设计决定）：

| 部位 | 闸在哪把尺上 | 为什么 | 本轮实测 |
| --- | --- | --- | --- |
| Foot / Toe0 截面 | 世界包围盒**跨度** | 写入侧 `mmd2maxFitCrossSection` 缩的就是跨度，量错东西等于拿别人的尺判它 | L Foot 宽 3.71425 / 高 8.65601（期望 3.714245 / 8.656011） |
| Hand / Finger 截面 | `#scale` | 手上没有任何「网格截面 → 骨盒」的闭环，只有 scale 是被写下去的量 | 12 根全部差 **0.0** |
| Foot 长度 | 不闸（`report`） | 源文件里没有一根数能对上：接趾把远端拉到平投影 9.73629，最终 extent 是 12.6445 —— 硬刻一个期望值就是第二十一轮那种**永远不可能通过的 gate** | 12.6445 / 12.6419 |
| Toe0 长度 | 跨度，闸 | 这一根**有**输入尺：`toe_len` | L 3.56919（期望 3.569191） |

手那 12 行的 0.0 要说实话：它只回答「写进去的数还在不在」，不回答「骨盒合不合身」。真正有意思的是
`hand_box_cm` —— L Hand 的 scale 截面 3.6286 对盒跨度只有 2.17486（比值 0.60），手指在 0.46~0.50，
差得离谱。所以 `hand_box_cm` 现在**只报不闸**：先让它说话，等知道该等于几再定闸。左右还不对称
（Finger1 盒跨度 L 0.576537 / R 0.585769），这也是「报」才看得见的东西。

`len_axis` 顺手把一条一直靠嘴说的话变成读数：Foot=**2**（Y）、Toe0=**1**（X）—— 脚骨长度在 Y 上、
趾骨在 X 上，正是 `mmd2maxLengthAxisIndex` 注释里那条「不许写死 X」的实证。

**差点刻第二遍的尺**：宽高轴的判据（两根非长度轴里谁贴近世界 Z 谁是「高」）原本只活在
`mmd2maxFitCrossSection` 里，我的回读函数一写就等于抄第二份 —— 抄的那份将来必然漂。
抽成 `mmd2maxWidthHeightAxes node lengthAxis`，写入侧和验收侧同时改成调用它（对写入侧行为完全等价：
读不到轴时仍沿用默认那一对），契约测试从「钉住那句比较」升级成「钉住那句比较**全脚本只出现一次**
+ 两个调用者都必须调它」。回读侧则更严：轴读不到就返回 `undefined` = 没量到，绝不猜一根轴去报数。

容差只在判据侧存在，并且**故意不进 CFG**（29 项一个没多，所以 `diff_round` 的 `--allow` 名单不用改）：
`BOX_TOL_REL = 0.02` 直接抄写入侧自己的收工条件 `abs(cur-target) <= 0.02*target` —— 用作者的收工条件
当验收的门槛，判红时没人能争辩「尺太紧」；`BOX_TOL_MIN_CM = 0.02` 是地板，专门盖住 MAXScript
`as string` 只有 6 位有效数字这件事（`box_tol_cm()` 把结果四舍五入到 6 位小数，线格式里不会出现浮点尾巴）。
实测差值 1e-6 ~ 5e-6 量级，正是「真去量了」而不是「抄了期望值」的证据 —— 抄来的会是 0.0。

两条守卫（各有用例）：`widths.tsv` 不在 ⇒ 只掉手那 24 行，`nodes_measured` 的期望跟着从 16 变 4，
不许留下「规格里有一把尺、报告里永远没人报」；脚段截面列缺失或非正 ⇒ Python 侧 `ValueError`
（`obj_run` 转成 `SystemExit` 并带上目录名），门槛 0.02 严格早于 `.ms` 的 `> 0.001` 过滤，两侧不会
一边收下一边丢掉。

行数账：规格 171→**214**（+42 条 `box_rb` +1 条 `pass/ran box_verify`），通道 69→**77**；
`CHECK` 213 PASS / 0 FAIL / 1 SKIP（那条仍是 `physics_build`）。几何中性：
`diff_round.py --allow "^配置" --allow "^收敛闸门" --allow "^回读进度" --allow "^蒙皮回读" --allow "^骨盒回读"
round24 round26` ⇒ **exit 0**（420→437 行，多出的 17 行全是骨盒回读自己的 16 条读数 + 1 条汇总）。
`POSE_FACTS` 也补了 `box_rb`：一次旋转完全可能把宽高轴的对调判据翻过来，落位/转角之后的读数必须作废。
`tests/test_passes.py` 的 `FROZEN_REPORT` 已从 round23 换到 **round26**，那条「唯一允许多出
`box_verify` 缺 ran」的豁免**删掉了** —— 从现在起注册表里声明的每一段，实跑报告少一行就判红。

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

