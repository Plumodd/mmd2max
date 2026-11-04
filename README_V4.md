# mmd2max v4 · 架构契约

v3 的完整缺陷史在 `README_HANDOVER.md`（第七节那条时间轴，条目只增不删，条数别抄在这里 ——
去看文件）。这份文件只写 v4 改了什么结构、为什么，以及**怎么判断这一轮又犯了老毛病**。

回滚开关：v3 目录 `C:\Users\Administrator\Desktop\mmd2max_v3_src_2026-11-01` 仍是完整的源码 +
`.git`（提交 `d4949f6` 已全部推到 `Plumodd/mmd2max`，工作树干净），`C:\Users\Administrator\Desktop\mmd2max_v3.zip`
是同一份成品的压缩包。2026-11-04 清理时它的 `test_runs/` 产物（693MB）与 v1/v2 两棵树、三份 v2 zip
一起进了 `C:\Users\Administrator\Desktop\_旧版本_待删_2026-11-04\`（**移动**，不是删除，随时能搬回来）。
v4 任何一步做砸了，回到 v3 目录跑 `tools/obj_run.py` 即可，不需要在 v4 里修；产物缺失用
`tools/run_model.py` 重生成，一条命令的事。

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
自动判法：`tools/check_ms.py:check_oracle_independence`，两层，因为**只按名字查会被两类
极便宜的规避绕开**（第 99 轮把这条查法改了，原因写在下面）：

1. 名字层：函数名像判据（`Check` / `Verify` / `Audit` / `Score` / `Valid` / `LogStruct` /
   `Dump` + 中文语素，**不锚行首**）而体内出现像求值器的调用（`Want` / `Target` /
   `Desired` / `Expect` / `BoxDir`）⇒ 报错；
2. 数据流层：求值器的返回值 → 局部量 →（跨函数：实参**按位置**污染被调方的形参，返回值
   污染调用方）→ 流进 `mmd2maxReport` 或任何最终会写报告的函数（汇点取一跳调用闭包，
   因为包装层是这条规则最常见的藏身处）⇒ 报错。

第 2 层才管得住第 1 层查不到的那一种：**把求值器的结果当参数传进去**。`mmd2maxLogStructDir`
的名字里没有一个求值器语素、函数体里也没有求值器调用，按名字查它永远干净，而它的第三个
实参就是 `mmd2maxBoxWantDir` 给的方向 —— 这正是 #26/#40/#41 那个恒等式 bug 换了个名字的形状。
命中了就两选一：期望值改成从 spec 文件读（规则 IV 的数值单源），或者把这一行降级成日志
（`verdicts.py` 里声明成 **lock** 的那几行例外 —— 锁的语义本来就是「只求不比上一轮差」）。

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
| M5 | 测试重写：契约测试只钉表头/配置键；加金标准（脚盒 3.714×8.656×9.736 等）、判据回放、**变异测试**（改坏一个 pivot 必须至少一条判据变红）、**蜕变测试**（~~160cm vs 80cm 输出必须精确 0.5 倍~~：**这条规格本身是错的，产品里做不到** —— 导出层有四类按**导出厘米**定价的绝对量，它们的前提是 `auto_scale` 把每个角色钉成 160cm；落地成「PMX 长度 ×2 配 scale ÷2 ⇒ 产物**逐字节**相同」+ x 镜像 + 两次导出逐字节相同，见交接文档第 39 条） | 变异注入全绿 = 验收层是装饰，直接判失败 |

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

### M4d：快照回滚 —— 量出来是一个**净亏损**，所以开关保持关（默认 `ROLLBACK = False`）

`.fig` 载体本身能用（`probes/figfile_m4h.txt`：`biped.saveFigFile` / `loadFigFile` 收
**controller**、必须在 Figure 模式内；一份 `.fig` 连骨盒一起还原，盒差 5.72205e-06、
#scale 差 4.76837e-07）。但真在香菱上开两轮（第 27、28 轮，证据留在
`baselines/round27_MMD2MAX.REPORT`、`baselines/round27_香菱_objrun.log`、
`baselines/round2{7,8}_driver.log`），量到的是三条谁都没预料到的事：

1. **守卫两轮都没触发** —— `rollback/foot_worse_bones` 实测 **0**、`guard_fired=false`：
   接趾闭环自己就收敛，不需要退路。这一批判据行于是只在「写坏了更远」的那一轮才存在
   （`when=if_present`）。
2. **自检三把尺全绿，可它担保不了载体** —— 自检把锁骨沿最长轴撑大 30%（`perturb_cm=3.40543`）
   再 load 回来，位置 / 盒 / #scale 分别回 5.08705e-05 / 1.90735e-06 / 9.53674e-07，
   第四把尺 `angle_deg`（局部轴朝向，为这一轮新加，尺 0.1 度）在 load 那一刻读 **0.0**。
   可是同一轮的收尾读数里，`Bip001 L Clavicle` 的世界长度轴倾角从第 17~26/29 轮一直量的
   **-162.237 度**（`baselines/round26_objrun.log:260`）变成 **-72.7144 度**
   （`baselines/round27_香菱_objrun.log:262`，差 89.5），双臂落位误差因此从
   0.955353 / 1.01505 / 0.143507 涨到 **1.95714 / 1.8437 / 1.71972 厘米**
   （左链；右链 1.94884 / 1.83903 / 1.71614）。
   两轮这八行逐位相同 ⇒ 这是**确定性失效**，而且是**延迟失效**：损伤不在 load 那一刻，
   在 load 之后的某一遍里显形。⇒ 载体自检永远测不出它，真正把它接住的是 placement 的
   `lock` 行（那 8 行手臂链判据当场判红，退出码 7）。
3. 于是结论不是「把第四把尺加上去就安全了」，而是**这条路的收益为 0、代价是 1~1.6cm 的手臂精度**。
   代码、判据行、`ROLLBACK_TOL_CM/DEG` 两把尺与全部证据留下（默认关），下次接趾真把趾骨推过头时
   再拿它做对照实验 —— 那一次的基线就是这两轮。

几何中性已证：第 29 轮拿 `ROLLBACK = False` 重跑，`diff_round.py --allow "^配置" --allow "^收敛闸门"
--allow "^回读进度" --allow "^蒙皮回读" --allow "^骨盒回读" --allow "^回滚"` 对 `baselines/round26_objrun.log`
⇒ **无实质差异**（437 行 vs 437 行，只剩时间/耗时/路径），`CHECK` 213 PASS / 0 FAIL / 22 未跑
（21 行是回滚自己的：19 条 `rollback` + 2 条 `pass/ran`；剩下那行还是 `physics_build`，
本轮没给 physics 文件）；第 30 轮把「Max 从哪个路径启动」改成自动发现之后再跑一遍，
对 `baselines/round29_香菱_objrun.log` 同样**无实质差异**（`baselines/round30_*`）。
行数账：规格 214→**235**（+19 条 `rollback` +2 条 `pass/ran`，其中 4 条 restore 尺与 1 条
`angle_deg` 是这两轮补的第四维），通道 94→**96**。

### M5 ✅：蜕变测试 —— 第一个被推翻的是**我自己写的这条规格**（2026-11-04 第七十四轮背书）

`160cm vs 80cm ⇒ 输出精确 0.5 倍` 在产品里**做不到**。导出层有四类按**导出厘米**定价的绝对量
（颈轴归位 1.5cm 阈值 / 3cm 位移上限、乳头目标带 3cm 窗、脚尖与脚盒截面的切片带、
`_WIDTH_MIN_CM` / `_RADIUS_CAP_CM`），它们成立的共同前提是 `auto_scale(source, target_cm=160)`
把每个角色钉成 160cm —— **厘米才是跨模型稳定的量纲，PMX 单位不是**（香菱 1 单位 = 7.6563cm、
克雷薇 = 8.763cm，两具模型天生差 14%）。拿 scale 当自由输入要求线性，等于要求「160cm 的绝对量随 scale 缩」。
上一轮我就是被这条规格推着，把 `_WIDTH_MIN_CM` 改成 `× scale`，实测把香菱的**手掌算进了手的粗细**：
`Bip001 R Hand 3.6953 → 58.3399`、widths 12 行掉到 10 行。**规格错，红的是产品。**

换成立得住的三条（`tests/test_metamorphic.py`，5 条用例、全套 1.8 秒）：

- **单位协变**：PMX 每一处长度 ×2 配导出 scale ÷2 ⇒ 产物**逐字节相同**（只有 `meta.scale` 按定义跟着变）。
  `FACTOR = 2.0` 不是随便挑：翻倍与减半在 IEEE 里是精确运算（只动指数不动尾数），
  所以 `fl((2a)·(s/2)) == fl(a·s)` 逐位成立，末位才敢要求「逐字节」。挑 3 之类，红的是算术不是缺陷。
- **x 镜像**：源模型每处 x 取反再导出，位置列取反、其余逐字不变、取景框 `x_lo/x_hi` 对调再取反。
  它只保证**协方差**，不保证 L/R 兜底选得对（把兜底整体关掉，输出照样自洽 —— 实测绿）；
  能钉住的是「要么整体翻、要么整体不翻，不许只翻一半」（`_lr` 决策集合恰好一个元素）。
- **两次导出逐字节相同**：`export()` 走进程池，钉的是拼回顺序与调度无关；红了不用猜几何，先查排序。

列分类是**反向**要求的：只有列进每张表 `scale` / `mirror` 的列允许变，其余一律按「逐字不变」咬 ——
将来新增一列若其实随 scale 变，这条当场红，而不是因为没人写它而静默放过。基准 scale 用模型自己的
`auto_scale`，**不许写死**（这里原写 0.08，注释还声称「与香菱实跑一致」，实测 7.6563，差近 100 倍 ——
写死之后我为了凑它改了产品，这就是上面那次事故的来历）。

它抓到的两处，同属「一张表里有的量乘了 scale、有的没乘，单看这份产物完全自洽」这一族
（①由 M5 的**旧表述**「scale 0.08 vs 0.16 两次导出对撞」抓到 —— 性质换成逐字节协变后它一度只活在注释里，
本轮补了一支变异针才把它重新请进尺；②由本轮的逐字节协变抓到）：

1. `physics.tsv` 关节平移限位 `pos_min/pos_max` 漏乘 scale（`mmd2max/export_obj.py` 里
   `pmin/pmax = convert_vec(jt.pos_min/pos_max, scale)` 那两行）：
   位置乘了、限位没乘 ⇒ 「允许移动的范围」相对骨架缩小 1/scale 倍。钉住它的是**常驻**的蜕变契约
   —— `tests/test_metamorphic.py` 把 `joint_pos_min_x/y/z` 与 `joint_pos_max_x/y/z` 一起列进
   `physics#JOINT` 的 `length` 列，所以「PMX 长度 ×2 配 scale ÷2 ⇒ 逐字节相同」当场咬得住。
   变异探针（`test_runs/probe_m5_mutate.py` 的针 F，把 `scale` 改回 `1.0` 重放这个 bug）实测 RED，
   那一轮报的第一处差异在 `克雷薇_physics.tsv` 第 170193 字节、`sample_physics.tsv` 第 1330 字节
   —— **这两个字节数是当轮读数，不是判据**：探针本身在 `test_runs/`（不进交付包），重跑一遍数就变。
2. **零长骨阈值与占位尾写成「若干个 PMX 单位」**（`scale*0.001 / scale*0.1 / scale*0.05`）：
   那是作者画法不是物理量 —— 同一根无名骨在香菱上尾长 0.76563cm、在克雷薇上 0.8763cm，
   而单位协变要求它俩逐字节相同。改成 `_DEGENERATE_EPS_CM = 0.05` / `_STUB_LEN_CM = 0.8`
   （两个常量定义在 `export_obj.py` 的 `_RADIUS_CAP_CM` / `_WIDTH_MIN_CM` 紧下面，用它们的
   站点是 `build_bones` 与 `_build_skeleton_from_kept` 各一处）。换之前先量谱
   （`test_runs/probe_stub_spectrum.py`）：两具模型 × 两个站点在 **0.002~0.3cm 整个窗内
   「判为零长的根数」一动不动**（香菱 56 / 9，克雷薇 48 / 8），最短真骨 0.993587cm
   ⇒ 0.05cm 落在安全窗正中，这次换单位**不翻任何一根骨的分类**。

第 74 轮实跑背书（香菱 `--physics --shot`，退出码 0，无弹窗）：**530 PASS / 0 FAIL / 21 SKIP**（其中 27 条 lock）、
物理 254 根代理 / 330 条关节**零失败**、21 张截图「投不出=0」、最小纵向填充 54.3342%。
数值影响逐行量过（`baselines/gen17` → 本轮）：只有 `香菱_bones.tsv` 变 37 行 —— 35 行是这次换单位
（33 行 `tail_z` +0.034370 / 2 行 +0.017185），另 2 行是 #66 胸部指向乳头（原先由白名单代管，这次冻进基线，
所以 `BONE_TAIL_OVERRIDES` 清空）；其余 5 张（biped_map 53 / limbs 39 / meta 41 / weights 18892 / widths 12）
**逐行相同**。基线换代规程写进 `tools/check_baseline.py` 的注释：旧一代整份留在 `baselines/gen17/`
（`baselines/` 被 gitignore，就地覆盖等于永久丢血缘）。

盲区也说清楚，别把这三条读成「单位问题从此有人管」：阈值那条性质**看不见**（分类不翻 ⇒ 产物无差异，
实测 GREEN）；`_WIDTH_MIN_CM` 乘 scale 也**看不见**（没有半径落进 0.02~0.153cm 那道缝，实测 GREEN）——
这两处改由 widths 的三层闸 + 基线守。变异复测在 `test_runs/probe_m5_mutate.py`（9 支，逐字节还原），
其中「半径上限乘 scale」这支从旧探针记的 GREEN 复测成 **RED**：**「这条判据看不见」是一个会过期的结论**。
静态与测试：套件 666 条 OK（skip 2）、`check_ms [OK]`、空 catch 债 0、`mutate85` 17/17 RED、`check_baseline` 六张「数字不变」。
细节（含脏夹具让金标准假红那条教训）见 [README_HANDOVER.md §7 第 39 条](file:///C:/Users/Administrator/Desktop/mmd2max_v4_src_2026-11-03/README_HANDOVER.md)。

## 5. 顺手从网上捡到的两条（v3 完全没用上）

- **PMX 自带 roll/扭转轴 —— 审计做完了，结论是「这条路在香菱这类模型上没有数据」**（任务 #57 关闭）：
  骨记录里确实有「轴固定化 Fixed Axis(0x0400)」和「局部轴 Local Axis(0x0800，存 X·Z，Y=Z×X)」两组
  作者手填的数据，v4 的解析器也已经读到（`mmd2max/pmx.py` 骨记录里的 `fixed_axis` / `local_x` /
  `local_z` 三个字段，连同 `Bone.has_local_axis`；这三条轴向量与骨位置、末端偏移一起在
  `_finite` / `_all_finite` 那几行校验，含 NaN/Inf 直接 abort）。
  但**读到 ≠ 有**：`tools/axis_audit.py` 实测香菱 356 根骨 ⇒ 局部轴 **0** 条、轴固定化 **4** 条，
  而这 4 条全在 `左腕捩 / 左手捩 / 右腕捩 / 右手捩` 上 —— 扭转骨属 extended 类，压根不进
  `bones.tsv`（321 行里 0 行含「捩」）。所以「拿作者填的轴向当每根 Biped 骨的 roll 独立真值」
  这个设想**不成立**，v3 那句「恒为 0 = 把现成真值丢了」也说过头了：`has_sdef`/`has_qdef` 是
  **顶点**的变形类型（SDEF/QDEF 蒙皮），跟骨的轴向是两回事，香菱本来就是 0。
  - 唯一量到的一条副产品是真的：那 4 根捩骨的作者轴向与我们**从 pivot→tail 反推**的骨向
    分别差 **0.10° / 0.00° / 0.22° / 0.00°**（转轴是线不是射线，比对时取 `min(θ, 180−θ)`）。
    两个来源没有任何共同计算，对上就是真对上 —— 这是「反推骨向」这条链在**手臂**上的一次独立背书。
  - 下次遇到有局部轴的模型（MMD 官方骨架、带 `ローカル軸` 的改模）再把它接进 roll 判据；
    现在接等于给一条永远不执行的分支写判据 —— 正是 M1b 删掉的那个 DQ 分支的死法。
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

## 7. 换一台机器要动的东西：零（两处路径都改成发现了）

用户会问的两件事，都不该以「改源码」作答：

- **Python 版本**：要的是 **3.8+**，不是「只能 3.12」。实测（不是推断）：全仓 `.py`（口径用
  `git ls-files "*.py" | wc -l` 现取，别在这里抄数）逐份过
  `ast.parse(feature_version=(3, 8))`，无 `match`、无 3.9+ API，最新的一条语法是海象 `:=`
  （`tools/diff_round.py` 的 `normalize` 推导式、`mmd2max/semantic.py` 的手指目标匹配 —— 只点名
  不写行号，这两处一直在动，写过一次行号就再没人对上），第三方库为零。`启动GUI.bat`
  按 `py -3` → `python` → 开发机私有路径 的顺序探，每个候选先验 `version_info >= (3, 8)`
  再执行 —— 顺序不能反，Windows 的 `python` 可能是应用商店的占位别名，跑它会弹商店而不是报错；
  三个都不行时打印安装提示并 `pause`，不让窗口一闪而过。**本机实测（第 99 轮复核，改掉了此前
  那句「这台机 `py` / `python` 都不在 PATH」）**：`where py` → `%LOCALAPPDATA%\Programs\Python\Launcher\py.exe`、
  `where python` → `…\Python312\python.exe`，两条都在 PATH 上，所以双击走的是**第一条**（`py -3`，
  实测 `py -3 -V` = 3.12.10）。这一句值得单独改：那条旧说法会让人以为三级探测的后两级在本机
  被验证过 —— 实际验证过的只有第一条，后两级是给「没装 py 启动器」的机器留的通路。
- **3ds Max 装在哪**：`tools/obj_run.py:find_max_exe()` 按 环境变量 `MMD2MAX_MAX_EXE` →
  注册表 `HKLM/HKCU\SOFTWARE\Autodesk\3dsMax\<内部版本>\Installdir`（24.0 = 2022，年份 = 主版本 + 1998）
  → 各盘 `Program Files` / `Program Files (x86)` / `Programs` / `Software` 下的 `Autodesk\3ds Max *`
  的顺序发现，**只收 exe 真在盘上的**那条（这台机注册表里还躺着 23.0=2021，目录早删了，
  照它启动就是拿陈旧记录去撞不存在的文件）。优先 2022，因为全部探针与判据只在这个版本上量过；
  选中别的年份会在日志里打警告而不是默默跑。启动目录（清理旧版自启动脚本用）同样按发现的
  版本年去扫 `<LOCALAPPDATA>\Autodesk\3dsMax\<年> - 64bit\<语言>\scripts\startup`，
  不再假设 `ENU`。找不到时退出码 2，并把三条发现路径念进日志（脚本、CFG、规格表那时都已落盘，
  只差启动）。本机实测：注册表与逐盘 glob 两条路都指向 `C:\Program Files\Autodesk\3ds Max 2022`，
  第 30 轮用发现结果启动，判据与日志相对第 29 轮**无实质差异**。
  契约测试 `tests/test_obj_run_guard.py:MaxDiscoveryTests`（7 条）钉住：源码里不许再出现安装目录
  字面量、两条启动路径只许吃发现结果、环境变量优先但指错不许回退到「猜一台」、非 2022 不许赢、
  启动目录必须跟着版本与语言走。

---

## 8. 测试版打包：`git archive` + 一次独立导出自检

一条命令（**包内容 = HEAD 内容**，口径现取，别抄数：`git ls-files | awk -F/ 'NF>1{print $1"/"}NF==1{print "(root)"}' | sort | uniq -c`
—— 第 99 轮复核时它是 `mmd2max/` 11 + `probes/` 35 + `scripts/` 1 + `tools/` 13 + 根 4 件 = **64 个文件**。
这一行原先写的是 13/36/2/14 + 4 = 69，自己就和它标称的「64」对不上；字节数与文件数都只当轮读数，
真正不变的那条口径在下面第一个分点里（HEAD == 跑起来需要的东西）：

```
git archive --format=zip -o ../mmd2max_v4_test_<日期>.zip HEAD
```

- **为什么不用手工挑文件**：`.gitignore` 已经把 `tests/ test_runs/ baselines/ outputs/` 全排除在外，
  所以「HEAD 里有什么」== 「跑起来需要什么」。手工挑会漏，漏了的那份要到别人机器上才红。
- **`probes/` 留在包里**：它不参与运行，但 `export_obj.py:649`、`targets.py:53`、`verdicts.py:961`、
  `check_ms.py:38` 这些**注释里的证据**全指向它。删掉它，「这条数是谁量的」就变成死链 —— 而本项目
  最贵的错误全是「拿注释当证据」。
- **自检必须是「解压出来的那份代码」跑的，不是项目里这份**：解到临时目录 → 用它
  `tools/run_model.py <某个 .pmx>` 重导一遍 → 与项目里的金标准**整目录**对撞（同名件逐个 `cmp`，
  再加上「只在一边」的两张清单）。第七十六轮实测：两边同名 20 件里 **19 件逐字节相同**，贴图 7/7 相同，
  唯一不同的是 `MMD2MAX.CFG` —— 把根路径归一后只剩 5 行，每行都指得回那一轮的开关
  （`--physics` 决定 `_phys` 后缀与 `physics.tsv` 那格、非交互驱动决定 `MMD2MAX_INTERACTIVE`）。
  只在一边的也各有来历：`mmd2max_run.ms` 只在包侧（夹具目录只留数据件），
  `MMD2MAX.CHECK/REPORT`、`*_objrun.log`、`*.success.json`、无前缀 `physics.tsv` 只在夹具侧
  （它们是 **Max 轮**产物，导出这一半本来不生成）。
  **只看「有几个文件不同」不算自检**：那 5 行 CFG 里只要有一行归不了因，就是包里缺了东西。
- **不上传 git**：v4 仓库没有 remote，提交只在本地。测试版靠 zip 交付。


