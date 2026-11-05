"""MAXScript 端契约（跑不了 Max，只能用文本钉住，改脚本时别把它们改回去）。

骨盆 / COM
1. 摆位名单里没有裸 "Bip001"（COM）：COM 由 createNew + 骨盆闭环负责。
   实测教训：给 "Bip001 Pelvis" 调 setTransform #pos 不生效，骨盆 pivot 只跟 COM 走；
   闭环把 COM 抬到腰以后，如果名单里还留着 "Bip001"，下一轮摆位会把它按
   センター（比腰低 14.5cm）压回去，骨盆低 → 髋低 → 膝只能折叠（用户截图）。
2. createNew 用 comStart（腰），不是 centerPos（センター）。
3. 闭环的测量点是「左右髋（Thigh）中点」而不是 Pelvis pivot：把 Pelvis pivot 对齐
   PMX 腰会让整条腿链平移 6~7cm（实测 L Thigh 偏差 6.99、踝 1.57）。
   同一条约定也管骨盘子段的**长度**：desired 要按髋中点量（mmd2maxHipAnchor）。
   照抄 PMX 腰→腿根 = 9.03 会把两髋对称撑到 ±8.97，而 PMX 要的是相对髋中点
   ±7.00、dy/dz 全 0（实测左右髋各偏 2.2288）；Pelvis->Spine 同样虚高
   （表里 6.49 / 摆位后实测 3.51，段校验误差 2.98）。
   前提是 limbs.tsv 每行都带 child_x child_y child_z + mode —— 普通段曾经只写 5 列，
   childPos 恒为 undefined，改写静默不触发（日志里却全是「成功」）。

截图
4. save 的结果必须写日志，并且用 doesFileExist 实测文件是否真的出现：
   上一版 `try ( save ... ) catch ()` 把异常吞了，日志只有「开始抓取」，
   shots 目录空空如也，白跑一轮没法验收。
5. 成功标记写在截图之前，且截图整段再包一层 try —— 截图里的错误绝不许
   把已经装配好的 .max 判成失败（实测：Type error 直接吃掉标记，退出码 4）。
6. 正/侧/顶 + 只留骨架 + 颈部/脚尖特写各包一层 try（共用一个会把落盘的截图
   连同线索一起带走）；骨架可见性用 viewport.SetShowEdgeFaces + 网格 isHidden
   （节点级 o.wire / o.seeThru 在 Max 2022 不存在，探针实测 Unknown property）；
   缩放用真实包围盒（±1e9 巨盒等于没缩放，实测把头裁掉了）+ zoomToBounds，
   且抓图前必须 completeRedraw()（不重绘时 getViewportDib 拿到的是上一帧）。
7. 非交互跑完自己 quitMax quiet:true 退出：上层兜底的 taskkill /F 会累积 Max 的
   崩溃计数，攒够就每次启动弹 "Startup Failure Detection"，把 -U MAXScript 挡死。

脚趾（#19）
7. Toe0 的三根轴各自闭环到 Python 量好的厘米目标（趾长 / 脚尖处宽 / 脚尖处高），
   单轴乘倍率，长度轴最后缩。旧的「缩到鞋前缘 + 粗细 x0.7」是拍脑袋：
   鞋前缘那列本身量错了（+y 最大 = 量到脚后跟，趾长 12.28 > 整只脚 11.23），
   于是 Bip001 L/R Toe0 被撑成一根大骨头（用户截图反馈）。
8. 宽/高分配按「哪根轴贴近世界 Z」定，不按「哪根现在更粗」定；宽高目标来自
   「脚尖那一段鞋里装得下的中心对称盒子」（用户第二轮反馈：整块骨头呆在鞋子里），
   不是 Foot 末端截面。

表情（Morpher）
13. WM3_NumberOfChannels 返回的是「已分配容量」，不是「已用通道数」：空 Morpher 就报
    100，建 7 个表情报 800、60 个报 6100、61 个报 6200（每次正好 +100）。所以
    ① 通道号必须自己数（built + 1），拿它 +1 当索引会把表情全写到 101/201/… 上，
    1..8 号通道读回来全是 "- empty -/N"（日志实证，built 却照样报 61/61 全成功）；
    ② 拿它当自检判据（容量 < 表情数）恒不成立，等于没有自检。自检改成两级：
    built == 0（两个建通道接口逐个抛异常）才拆修改器；没抛异常但 1..blocks.count
    读不回目标/名字，只打警告、保留现场 —— 读接口本身通不通没探过，不能拿它当拆除依据。
14. Morpher 的通道名跟着「目标节点名」走：#24 修好之后 08:14 那轮 1..8 号通道读回来是
    default001..default008（= 网格对象 default 的副本自动编号名），不是表情名 あ/い/う…；
    WM3_SetChannelName 在本环境静默不生效。所以表情名要在 copy 之后、建通道之前用普通
    属性 target.name 打上去；「有名」的计数也必须逐个和 blocks[k][1] 比 ——
    按「非空」判会被 default001 蒙过去。

脚骨朝向（#26）
15. 全局 facing 必须取**两脚水平方向的角平分线**，不能只按左脚测：MMD 的角色是外八的
    （香菱左右各 6.69°），拿左脚方向当整副骨架的朝向 = 把骨架拧向左脚 —— 左脚正好、
    右脚整体朝人物中线偏 13.4°（用户截图：「你的 Bip001 R Foot 又向人物中间偏了」，
    Front 视图投影 11.4°，与红/蓝箭头张角吻合）。Biped 只有这一个全局偏航旋钮：
    setTransform #pos 摆的是落位，摆不出偏航。
16. 脚骨上「pivot 落位」和「两点距离」是**恒等式**，看不见偏航：Toe0 的 head 就是 Foot
    的 tail，把它摆到目标，距离自然等于目标长度 —— 所以 08:20 那轮 pivot偏差=9.6e-07、
    段校验误差 3e-05 全绿，而脚骨转了 18°。而且 limbs 里 Toe0 被 flatChildren 排除，
    最终位置校验也不覆盖它。
17. 「实际朝向」也不能用两个 pivot 相减（08:59 那轮又踩一次）：Toe0 的 pivot 是脚本
    自己摆到 つま先 目标的，Foothead 是自己摆到 足首 目标的，两者相减再和目标比 = 拿
    自己写的数验自己，实测 L=0.0198° R=0.0° 照样全绿，用户的红箭头照旧指着里面。
    骨头的**可见朝向**只有节点局轴能表示：`mmd2maxBipedAxes` 取 row1/2/3，三根都报
    （脚骨长度在 Y、脚趾骨在 X，写死一根就是猜），并且比**水平偏航差**而不是三维夹角
    （脚骨向下斜 30°，13° 的偏航在三维夹角里被稀释得几乎看不见）。局轴要在**退出 Figure
    模式之后**量：Figure 模式里的 #pos 改的是结构，退出才定下来。
18. `biped.setTransform` 的旋转 token 是 **`#rotation`，不是 `#rot`**（09:37 探针实测
    `#rot`：三参报 `Argument count error: setTransform wanted 4, got 3`，四参报
    `Runtime error: Unknown transform type, unsupplied` 且局轴 0.0 度未动）。Max 自带
    的 `scripts/BulletScripts/BsRetargetTools.ms`、`BsBipedTools.ms` 里全是
    `biped.setTransform node #rotation <quat> <bool>` —— `Straighten` 函数拿它给 Toe 补
    90°，Retarget 拿它把源骨的 `.transform.rotation` 整个写进 Biped 骨，可见**值是全局
    quat**。10:17 再探：`#rotation` 是六个候选里唯一不被拒的 token（喂 point3 报 `Unable to
    convert: [0,0,30] to type: Quaternion` ⇒ 只吃 quat）；但当时那句「喂 quat 之后局轴 0.0 度」
    是**假的** —— 见规则 25：`eulerToQuat [0,0,30]` 自己就抛错，四元数根本没构造出来，
    `#rotation` 到今天还没被真正调用过一次。同一轮里 `.transform` 与 `rotate` 命令读到 30 度，
    10:34 在 Figure 模式内复测变成 `.transform=0.0度 / rotate命令=30.0度且留=30.0度`（规则 26）。
    脚本里仍然不许把任何一条写法猜死：写法由探针在临时骨架上现场选（判据见 22/23），
    探不到就整段跳过转角 pass。
    **11:11 结案**：quat 换成 `eulerToQuat (eulerAngles 0.0 0.0 30.0) order:1` 之后
    `rotation … q=120.0度/留=120.0度`，探针 `采用=bipedRotation`，真骨架
    `转角 pass：接口=bipedRotation 尝试=36 改善=34 未改善=2 抛错=0`，`结构向 复核` 的
    超标从 35 掉到 2（只剩 L/R Clavicle）—— Biped 的单骨转角旋钮确实存在，之前三轮
    「骨盒朝向不可控」全是参数构造失败造成的假否决。
27. **转角会把子骨 pivot 一起甩出去，所以「先转方向、再摆落位」**（11:11 实测）：
    `#rotation` 绕骨自身 pivot 转，父骨转完子骨关节就偏离 PMX 的 head —— 同一轮方向从
    35 个超标降到 2 个，落位却从 1e-5 量级变成 `Neck 4.90352 / Head 4.15381 /
    L Toe0 5.99721 / L Finger32 7.82856`（Spine2 转 17.4° × 段长约 11cm ≈ 3.4cm，量级吻合）。
    而探针里 `q=120.0度/留=120.0度` 那一步正是「重发 `#pos` + `update 0` 之后」读到的
    ⇒ `#pos` 不吃转角，重摆是安全的。`Pelvis 5.6995` 不算这条：那是 `校验` 拿 PMX 腰
    比 髋中点 的既有口径差（见 mmd2maxHipAnchor），`#pos` 对 Pelvis 本来就无效。
19. 「pivot 摆到 head」满足不了**方向**验收（用户 #27：Spine2 没后仰、颈没前倾、手指不贴合）。
    PMX 每根骨自带 head 和 tail，骨的**显示方向**是 tail - head，网格就是照着它做的；
    Biped 只被摆 head，画出来的段是 head -> 下一个 head。离线按 bones.tsv 算过：PMX 手指
    是 head-to-tail 串起来的（左中指１ 的 tail 就是 左中指２ 的 head），所以「盒随 pivot 走」
    时手指链应读 0 度 —— 真读出大夹角 = **盒不随 pivot 转**（#26 的脚骨正是这个机制），
    要修的是转角而不是落位。躯干侧 PMX 上半身2 倾 +17.42°、首 −8.58°。
    所以方向复核的目标必须是 tailPos，
    而且拆成矢状面倾角 `atan2(v.y, v.z)`（+ 后仰 / − 前倾）与水平偏航两个数：
    三维夹角把「后仰 17°」和「转了 17°」混成一个数，用户看不出我在说什么。
20. 位置校验名单写死 = 没打印被当成没问题：那份 校验 只遍历 19 个名字，
    `Bip001 Spine1` 与 30 根手指骨从来没被检查过，日志里也就从来没出现过它们。
    复核要遍历 targetNames 全部行（只排除 COM），并打印「可测=N 夹角>T度=M 短骨不计=S」
    （T 取 MMD2MAX_DIRTOL 并打在文本里，不写死成 5：门控/打分/复核三处共用这个数，
    阈值换了要能一眼看出是数变了还是判据严了），
    量不出来的（dummy 骨 tail 与 head 重合）计入 -1 不算进 N，tail 不足 1cm 的显示 stub
    （香菱 右手首 0.38cm）单独计 S，否则分母自己缩水 / 汇总里混进假红灯。
21. **Biped 的骨盒不随 pivot 走**（09:37 那轮 结构向 表实测，可测=51 / 夹角>5度=35）：
    `setTransform #pos` 把关节摆到 PMX 的 head 上（`pivot偏差` 普遍 1e-5 量级），盒子却
    仍停在 `createNew` 给的方向上 —— 整条脊柱 `实测倾=-0.0456`（竖直，而 PMX 上半身2 该
    +17.42°、首 该 −8.58°）、`Finger1`~`Finger4` 轴1 `实测倾` 全 = 179.988（笔直朝下，
    它们的 `差偏航` 是 +4.17/+4.68/-2.41/-15.64，拇指 `Finger0` 读 -167.678）、
    `Foot 实测倾=-90`（平放，PMX 足首->つま先 向下 30°）、`Head 夹角=48.9`。
    后果不止是「看着不对」：指节链因此沿竖直线往下叠，`pivot偏差` 从 Finger1 的 0 累积到
    Finger02（拇指２）的 2.17585cm。所以**方向问题只能靠转角解决**，摆位与肢长拟合再准都补不上；
    而旋转入口见规则 18（token 是 `#rotation`，具体写法仍由探针现场选）。
28. **转角判据量「三根轴里最顺的那根」会给自己打假绿灯**（14:08 实测）：
    `结构向 Bip001 L Finger3 轴3 夹角=0.789815` 与 `肢段 Bip001 L Finger3 -> Bip001
    L Finger31 … 轴=1` 是互相矛盾的两行 —— 转角把 轴3 对齐到了 PMX 方向，决定子关节
    落在哪的长度轴 轴1 却被转到垂直方向，于是 `L Finger31 pivot偏差=3.61871`、
    `L Finger32=6.39328`、`L Finger41=2.85972`、`L Finger42=4.73953`，而**指根**
    `L Finger3` 自己在 PMX 上（3.8e-06）、`段校验` 也说段长对（误差 0.0278664）。
    指根对 + 段长对 + 指尖差 3.6cm 只剩方向一个解释：余弦定理反解真实夹角
    `cos φ = (2.58958² + 2.56171² − 3.61871²)/(2·2.58958·2.56171)` ⇒ φ = 89.26 度，
    而 89.26 = 90 − 0.74 正好是那一行报的 0.79 的补角；`L Finger4` 链独立算也给 89.26。
    右手同样五根骨却全在 0.07 以内，因为它 `结构向` 报的就是 轴1 == 长度轴。
    所以**门控 / 写入 / 复核三处必须量同一根实测长度轴**：数值来自 `肢段` 拟合那一步
    （跑在任何摆位之前，「缩这根轴能改掉父→子距离」就是长度轴的操作定义），
    不在现场量 —— 摆位之后父→子方向已经被我们写成 PMX 方向，量出来必然等于 PMX，
    是循环论证。且只对「恰好一个子关节」的骨生效：Pelvis 三个子、Hand 五个子，
    它们的「长度」不是一个方向，量错会把 #16 #17 的地盘带跑。
22. **转角必须写在 Figure 模式里**：`mmd2maxFigureMode ctrl false` 之后 `#rotation`
    就是在做动作（插 key），改的不是结构性姿态。所以转角 pass 排在退出 Figure 模式之前、
    `结构向` 复核之前 —— 复核读到的才是转完之后的真值。**探针也必须在同一个模式下测**：
    10:17 那轮探针是在模式外跑的，`#rotation` 读出 0.0 度并不能算否决它（`#scale`/`#pos`
    就是只在模式内生效的先例）。现在先在 `mmd2maxFigureMode probe.controller true` 里试
    全部写法，退出模式后再补一次 `#rotation` 对照，两个数并排进日志。
23. **「写完立刻回读」不等于「写成了」**：MAXScript 可能读到自己的缓存，Biped 求解器下次
    update 能把 TM 按结构重算回去（10:17 的 `.transform=30度 / rotate命令=30度` 就有这个
    嫌疑）。任何一条路都要再过 `mmd2maxRefreshedMoveDeg` —— 对同一根骨重发一次一直好用的
    `#pos`（原值写回，位置不动）+ `update 0`，然后重量局轴；**立刻数与刷新后都 >= 15 度**
    才允许采用。异常文字两路（point3 / quat）都要进日志，只打一条会把「其实抛错了」藏掉。
24. **`getNodeByName` 不支持 `*` 通配符**（10:17 `hand 取景：手指节点=1` 就是这么来的：
    `getNodeByName "Bip001 L Finger*"` 直接返回 undefined，框退回整场景，两张手部特写
    等于没拍）。按名字模式收节点要遍历 `objects` + `matchPattern n.name`；日志要把
    「命中几根」和「取到几个点」分开打 —— 命中 0（遍历/匹配没跑）和只命中 Hand 是两种故障。
    10:34 的第二次失败：`matchPattern n.name "Bip001 L Finger*"`（位置实参）读到 `手指命中=0`，
    而 Max 自带脚本一律写成 `matchPattern <串> pattern:"*.ms"` —— 模式是**带标签的第二个
    参数**；并且那段代码给每根节点套了 `try catch`，异常被吞成 hit=false，日志里什么也看不见。
    现在照抄 `pattern:` 写法、去掉逐根 catch（交给外层），并把 Biped 节点总数一起打出来，
    这样「命中 0」能立刻分辨是模式不对还是 `classOf` 判据不对。
25. **`eulerToQuat` 只吃 `EulerAngles`，不吃 `point3`**（10:34 实测：`eulerToQuat [0,0,30]`
    抛 `Unable to convert: [0,0,30] to type: EulerAngles`）。这条把前面两轮读数整个翻掉：
    09:37/10:17 看到的 `#rotation … q=0.0度` **不是**「Biped 不接受转角」，而是四元数根本没
    构造出来、`setTransform #rotation` 一次都没被真正调用过（旧代码只打 errP，errQ 藏了两轮）。
    正确形式照抄 Max 自带 `BsBipedTools.ms:1491`：`eulerToQuat (eulerAngles x y z) order:1`。
    推论：**任何「接口不通」的结论都要先看它抛的是哪一段** —— 参数构造失败和被调方拒绝
    是两件事，只记一条异常文字就分不清。
26. **`rotate node (angleaxis ang ax)` 是增量写法，`node.transform = m` 在 Figure 模式内被
    Biped 整个忽略**（10:34 同一轮里前者 `30.0度/留=30.0度`、后者 `0.0度/留=0.0度`；10:17
    在模式外读到 30 度是缓存/未被重算的假象）。所以探针采用 `#rotateCmd` 之后，转角 pass
    绝不能继续用它写绝对矩阵 —— 上一版就是这么把 35 次尝试全变成 `改善=0` 的。增量路要给
    的是 轴 + 角度（`cross(当前轴, want)` + `acos(点积)`），而 `angleaxis` 的左右手正方向
    纸面定不下来 ⇒ ±ang 都试，取回读更好的，都不更好就把净角转回 0。

腿扭转（#36）/ 足背截面（#37）/ 脚趾复摆（#38）
29. **髋和踝都钉死之后，膝剩下的唯一自由度就是绕「髋→踝」轴的扭转**（用户 #36：
    「两个小腿的朝向有问题内八了，实际上并没有」）。round-9 实测：髋偏差 0.106、踝偏差
    6.8e-06 / 4.2e-05 全对，膝却各偏 1.36 / 1.25cm 且**偏向人物中线** —— 因为脚本从没有
    一处写过 roll，Biped 自己给的。判据用「髋→踝 轴上，实测膝 与 目标膝 的有符号方位差」
    （符号取 (实测×目标)·轴，左右腿共用同一段代码），收敛门 `MMD2MAX_ROLLTOL` 同时是
    唯一的回滚开关（写 999 = 整段不转）。转完必须立刻重摆踝、复测膝，膝没靠近就把
    **转之前的整块矩阵**写回去 —— 和转角 pass 同一条规矩，绝不留下更差的姿态。
    顺序排在收尾**之前**：转大腿会把踝甩出去、并把整条脚链的局轴带走同样角度，而收尾
    那两道写干的正是「把踝摆回来 + 把 #dir 的方向补回来」。
30. **谁是大腿靠 limbs.tsv 的 mode=leg 认，不靠骨名**（mmd2maxChainPriority 是全脚本
    唯一的骨名分支，再开一处就破了那条契约）。Foot 的宽高同理走数据：第 17/18 列 =
    Python 沿踝→脚掌轴量到的鞋内切盒子（用户 #37：「脚的骨骼并没有包在脚的里面」，
    round-9 骨盒 8.00×6.61cm 而鞋里只有 3.93×1.53cm）。缩截面统一走
    `mmd2maxFitCrossSection`：长度轴一根手指都不碰（归肢段闭环），另两根按「谁贴近
    世界 Z 谁是高」分配 —— Toe0 和 Foot 共用，别再复制一份判据。
31. **链尾的落位归链尾自己管**（#38，#36 带出来的回归）。round-10 实测
    `段校验 Bip001 L Foot -> Toe0 误差=1.49461`（R 1.49644），而 round-9 同一行是 3.2e-05；
    `结构向 Bip001 L Toe0 pivot偏差=4.7362`。Foot 自己的 pivot 与朝向都还是准的 —— 坏的是
    它的孩子：脚趾例程在收敛循环**之前**把 Toe0 摆到 PMX つま先，之后转角 pass、大腿扭转、
    「收尾重摆(全摆)」三道写都带着这根链尾刚体走，而 `收尾转角: 尝试=0` 说明再没人回头管它。
    所以「摆完就完事」的写法在链尾骨上不成立：任何一道动过它父骨之后都要按 PMX 目标复摆一次，
    门控 `MMD2MAX_TOETOL` 同时是唯一的回滚开关（写 999 = 整段跳过）。
    复摆必须把**连带**一起打出来（踝偏差 前->后、摆完的踝→趾实测长度）：这两列决定「摆趾
    到底是把小腿转了还是把足骨抻长了」，是那一轮唯一的新信息。

MAXScript 语法
8. 函数体里绝不引用「定义在它后面」的顶层函数（前向引用）：MAXScript 的裸调是
   解析期绑定的，解析 `f a` 那一刻 `f` 还不是函数值，运行期就拿 undefined 去 apply，
   报 `-- Type error: Call needs function or class, got: undefined`。
   这就是截图段查不出来那个坑的真凶：mmd2maxSaveDib / ShotOne / Screenshot 定义在
   mmd2maxLog 之前，函数体第一行日志直接炸（19:45 香菱日志「开始抓取」之后一行都没有，
   而探针实测 `classof mmd2maxLog = MAXScriptFunction`）。日志三兄弟现在在文件开头，
   别把它们挪回截图函数后面。check_ms.check_forward_reference 负责拦。
   （同一条报错文字还有第二个成因：顶格 fn 被卷进别人的函数体 —— 见规则 11。）
9. 绝不用逗号实参表 `f(a, b)`：MAXScript 根本没有这种语法，实测报
   `-- Syntax error: at ,, expected <factor>`，而且是编译期错误，整份脚本一行都不跑。
   调用只有「空格分隔」形式 `f a b`；`f(x)` 之所以合法，是因为那对括号只是分组，
   永远只带一个实参。（曾经以为「表达式位置的裸多参调用会炸」并据此改成逗号写法 ——
   直跑探针实测证伪：try ( f a b c d ) 正常进函数体。别再改回去。）
10. 绝不用 `local x = try ( ... ) catch ( ... )`（try 当表达式取值 + 行尾续行）。
11. 顶格 `fn` 必须处在括号深度 0（check_ms.check_fn_depth）：括号配平不代表嵌套
    正确，被卷进别的函数体就成了局部函数，顶层调不到。
12. 绝不写裸调 `max a b` / `min a b`：会被当成 MAX 命令解析（Unrecognized MAX
    command），同样整份脚本编译不过。
13. 绝不给 `amax` / `amin` 直接传负数字面量：`amax x -1.0` 里的 `-1.0` 会被解析成
    减法（`amax (x - 1.0)`），钳位静默失效。要钳负边界就写 `amax x (-1.0)`，
    或者干脆收进带显式钳位的辅助函数（见 mmd2maxVecAngleDeg）。
14. **MAXScript 没有 `isNode`**（09:37 实测 `-- Type error: Call needs function or
    class, got: undefined`，而且因为整段包在 `try catch` 里，只留下一行异常日志、
    手指特写静默退化成全身图）。判「取到节点没有」用 `x != undefined`，判通配符
    `getNodeByName` 的返回形状用 `classof x == Array`。
"""
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (PROJECT_ROOT / "scripts" / "max_import_obj.ms").read_text(encoding="utf-8")
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import check_ms                                    # noqa: E402
from mmd2max import passes, verdicts               # noqa: E402


def _ordered_list(text: str) -> str:
    """取摆位名单 ordered = #(...) 的原文（跨行续行符已含在内）。"""
    match = re.search(r"local ordered = #\(.*?\)", text, re.S)
    assert match, "找不到摆位名单 ordered"
    return match.group(0)


def _between(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end)]


def _code(text: str) -> str:
    """剥掉注释，只留代码。

    assertNotIn 钉的是「代码里不许再出现某个不存在的 API」，而注释里必须记录
    「为什么不用它」（这是探针花大力气换来的硬知识）——不剥注释的话，文档本身
    会把测试打脸。
    """
    out = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"--[^\n]*", " ", out)


class PelvisPlacementContract(unittest.TestCase):
    def test_static_health_check_still_passes(self):
        self.assertEqual(check_ms.check_source(SCRIPT), [], "MAXScript 静态体检不过")

    def test_com_is_not_re_placed_by_the_pose_loop(self):
        # 名单里只允许 "Bip001 xxx"，不允许裸 "Bip001"
        self.assertNotIn('"Bip001",', _ordered_list(SCRIPT))
        self.assertIn('if t != "Bip001"', SCRIPT, "rest 名单要排除 COM")

    def test_pelvis_closed_loop_owns_the_com(self):
        self.assertIn("fn mmd2maxFitPelvis", SCRIPT)
        self.assertIn('if t == "Bip001 Pelvis" do pelvisPos = pos', SCRIPT)
        self.assertIn("mmd2maxFitPelvis root lines rowIds targetNames passList "
                      "pelvisPos hipTarget", SCRIPT)

    def test_loop_anchors_on_the_hip_midpoint(self):
        self.assertIn("fn mmd2maxHipMean", SCRIPT)
        self.assertIn('if t == "Bip001 L Thigh" do hipPosL = pos', SCRIPT)
        self.assertIn('if t == "Bip001 R Thigh" do hipPosR = pos', SCRIPT)
        self.assertIn("hipTarget = [ (hipPosL.x + hipPosR.x) / 2.0", SCRIPT)
        body = _between(SCRIPT, "fn mmd2maxFitPelvis", "fn mmd2maxBuildBiped")
        self.assertIn("mmd2maxHipMean root", body)
        self.assertIn("if want == undefined do want = pelvisPos", body,
                      "缺腿根骨时才退回腰")

    def test_biped_is_created_at_the_waist(self):
        self.assertIn("root = biped.createNew height facing comStart", SCRIPT)
        self.assertNotIn("root = biped.createNew height facing centerPos", SCRIPT)

    def test_pelvis_children_are_fitted_from_the_hip_midpoint(self):
        """骨盆子段的 desired 必须按 COM 锚点（髋中点）重算，否则髋宽偏 2.23cm。

        实测（19:45 香菱）：Pelvis->L Thigh 照抄 PMX 腰→腿根 = 9.0278，Biped 把两髋
        对称撑到 ±8.97；PMX 要的是相对髋中点 ±7.0013（dy/dz 全 0）。
        Pelvis->Spine 同理：照抄 6.49，摆位后实测 3.51，段校验误差 2.98。
        """
        self.assertIn("fn mmd2maxHipAnchor limbs", SCRIPT)
        self.assertLess(SCRIPT.index("fn mmd2maxHipAnchor"),
                        SCRIPT.index("fn mmd2maxBuildBiped"),
                        "被 BuildBiped 调用的函数必须排在它前面（裸调在解析期绑定）")
        block = _between(SCRIPT, "local hipAnchor = mmd2maxHipAnchor limbs",
                         "local scaleAxis = 0")
        self.assertIn('if item[1] == "Bip001 Pelvis"', block)
        self.assertIn("local want = distance hipAnchor item[4]", block)
        self.assertIn("item[3] = want", block,
                      "要直接改写表里的 desired：拟合与段校验读同一个值才不会各算各的")
        self.assertIn("骨盆段目标按髋中点改写", block, "改写必须留日志，否则线上看不出锚点生效没有")


class ScreenshotContract(unittest.TestCase):
    def test_save_failures_are_logged_and_verified(self):
        # 注意边界要写 "fn mmd2maxScreenshot ="：光 "fn mmd2maxScreenshot"
        # 会先命中上面的 mmd2maxScreenshotEnabled，切出一段空文本。
        body = _between(SCRIPT, "fn mmd2maxSaveDib", "fn mmd2maxScreenshot =")
        self.assertIn("bmp.filename = shotFile", body)
        self.assertIn("save bmp fileName:shotFile", body)
        self.assertIn("截图 filename 写法失败", body, "两种写法各自 catch，静默失败要能在日志里看见")
        self.assertIn("截图 save 写法失败", body)
        shot = _between(SCRIPT, "fn mmd2maxShotOne", "fn mmd2maxScreenshot =")
        self.assertIn("落盘=", shot)
        self.assertIn("截图开始", shot, "进函数就写一行日志，才能区分「没被调到」和「调到后半段炸了」")
        self.assertIn('mmd2maxShotOne outDir baseName "front" #view_front', SCRIPT)
        self.assertIn('mmd2maxShotOne outDir baseName "top" #view_top', SCRIPT)

    def test_landing_proof_is_mtime_not_file_existence(self):
        """`doesFileExist` 判落盘会被上一轮的同名旧图蒙过去（22:12 实跑踩过）。

        那一轮 front 报 落盘=true，文件时间戳却停在 19:45：第一个分支只
        `bmp.filename = shotFile`（改属性、不写盘），紧接着的存在性检查因为
        旧文件还在就直接为真，于是真正写盘的 save 被整段跳过。
        判据必须是「修改时间变了没有」—— 而 Max 2022 里唯一能取到修改时间的是
        .NET：getOsProperty / fileLength / getSecurityAttribute 三个「按理该有」的
        全局函数实测都不存在（23:49 那轮 front+top 两张截图因此全废，日志
        `Type error: Call needs function or class, got: undefined`）。
        """
        self.assertLess(SCRIPT.index("fn mmd2maxFileTicks"),
                        SCRIPT.index("fn mmd2maxSaveDib"),
                        "裸调是解析期绑定的：取时间戳的函数必须排在调用者之前")
        body = _between(SCRIPT, "fn mmd2maxFileTicks", "fn mmd2maxShotOne")
        self.assertIn('(dotNetClass "System.IO.File").GetLastWriteTime', body)
        self.assertIn("before = mmd2maxFileTicks shotFile", body)
        self.assertIn("!= before", body)
        self.assertNotIn("(getOsProperty", body, "Max 2022 没有这个函数，调它=整段截图炸")
        self.assertNotIn("if doesFileExist shotFile do done = true", body,
                         "存在性判落盘 = 旧同名文件会让它永远为真")

    def test_each_view_is_guarded_separately(self):
        """整段共用一个 try 会把已经落盘的截图连同日志一起带走（实测踩过）。"""
        body = _between(SCRIPT, "fn mmd2maxScreenshot =", "fn mmd2maxPrepareScene")
        self.assertIn('try ( mmd2maxShotOne outDir baseName "front"', body)
        self.assertIn('try ( mmd2maxShotOne outDir baseName "top"', body)
        self.assertIn("front 截图异常：", body)
        self.assertIn("top 截图异常：", body)

    def test_bone_only_shot_hides_meshes_via_viewport_api(self):
        """节点级 `o.wire` / `o.seeThru` 在 Max 2022 里根本不存在（探针实测
        Unknown property: "wire" in $Box:VBody），所以那版截图永远是纯实体、
        骨架看不见，验收判不了。可用的两招：viewport.SetShowEdgeFaces（实体上
        叠三角边）+ 网格 isHidden（只留骨架），两种各来一张。"""
        prep = _code(_between(SCRIPT, "fn mmd2maxPrepareShot", "fn mmd2maxRestoreShot"))
        self.assertIn("viewport.SetShowEdgeFaces true", prep)
        self.assertIn("MMD2MAX_SHOTMESH = mmd2maxShotMeshes()", prep,
                      "先缓存网格清单，还原时才只动我们自己动过的那些")
        self.assertIn("select #()", prep, "取消选择，否则截图里一圈选中高亮")
        self.assertIn("hide (getNodeByName \"Grid\")", prep)
        self.assertIn("completeRedraw()", prep,
                      "不重绘时 gw.getViewportDib() 拿到的是上一帧（探针 v4 两张图字节级相同）")
        self.assertNotIn("o.wire", prep, "Max 2022 没有这个属性，写了等于没写")
        self.assertNotIn("sceneMaterials", prep)
        restore = _between(SCRIPT, "fn mmd2maxRestoreShot", "fn mmd2maxFileTicks")
        self.assertIn("for o in MMD2MAX_SHOTMESH do", restore,
                      "还原必须只针对缓存的网格：对 objects 全体 isHidden=false 会把"
                      "用户本来就藏着的对象显示出来")
        self.assertIn("o.isHidden = false", restore)
        self.assertIn("viewport.SetShowEdgeFaces false", restore)
        self.assertIn("MMD2MAX_SHOTMESH = #()", restore)

    def test_mesh_hiding_is_scoped_to_meshes_only(self):
        """藏网格只能藏 Editable_Poly/Editable_Mesh —— 骨架必须留下，否则
        「只留骨架」那张就没有对照对象了。"""
        body = _between(SCRIPT, "fn mmd2maxShotMeshes", "fn mmd2maxPrepareShot")
        self.assertIn("classof o", body)
        self.assertIn("Editable_Poly", body)
        self.assertIn("Editable_Mesh", body)
        self.assertIn("append arr o", body)

    def test_zoom_uses_the_viewport_api_that_exists(self):
        """Max 2022 的 viewport 结构体没有 getViewSize/setViewSize/setZoomRegion，
        MAX 命令 `max zoomextents all` 在脚本里压根不编译（探针逐条实测）。
        能用的是 zoomToBounds <bool> <Point3> <Point3>，且它不留边距，要再 zoom 一次。"""
        fit = _between(SCRIPT, "fn mmd2maxZoomFit", "global MMD2MAX_SHOTMESH")
        code = _code(fit)
        self.assertIn("viewport.zoomToBounds off", code)
        self.assertIn("viewport.zoom 1.35", code,
                      "zoomToBounds 是贴边取景，不加裕量会裁掉头顶/脚尖")
        self.assertIn("viewport.activeViewport = v", code,
                      "逐个视口切活动视口，否则只作用在一个窗口")
        self.assertIn("completeRedraw()", code)
        self.assertNotIn("max zoomext", code)
        self.assertNotIn("setZoomRegion", code)
        self.assertNotIn("getViewSize", code)
        one = _between(SCRIPT, "fn mmd2maxFitView", "fn mmd2maxShotOne")
        self.assertIn("viewport.setType viewKind", one, "特写要先切正/侧视，继承当前视图角度看不出前后向偏差")
        self.assertIn("viewport.zoomToBounds off lo hi", one)

    def test_closeups_cover_neck_and_toes(self):
        """#17 颈部落位、#19 Toe0 尺寸都是细节，整场景一张图判不动；
        挺胸只有侧视看得出来，所以 side 是硬性要求；
        #26 脚骨偏航只有**俯视**看得出来（正视图把左右分量压成十几像素）；
        #27 躯干倾角要连腰一起框进来、手指不贴合要看手部近景；
        #66 胸部骨指着乳头：4.55cm 的骨在整场景那张里只有二十几个像素，必须有胸部近景。"""
        body = _between(SCRIPT, "fn mmd2maxScreenshot =", "fn mmd2maxPrepareScene")
        for tag in ("front", "side", "top", "bone_front", "bone_side",
                    "neck_side", "neck_front", "foot_side", "foot_front",
                    "foot_top", "hand_front", "hand_side",
                    "bone_foot_side", "bone_foot_front", "bone_foot_top",
                    "chest_front", "chest_side", "chest_top",
                    "bone_chest_front", "bone_chest_side", "bone_chest_top"):
            self.assertIn(f'"{tag}"', body, f"少了 {tag} 这张：验收要看的就是它")
        self.assertIn("#view_left", body)
        self.assertIn("b.max.z - b.min.z", body, "特写区域按包围盒高度取比例，换角色不用改数")
        self.assertIn("0.55", body, "颈部特写的下沿要低到腰：0.72 起框把 Spine1 裁掉了")
        self.assertIn("0.14", body)
        # 俯视特写必须按两只脚的实际落位取景，不能沿用整场景宽度（脚只占几个像素）
        self.assertIn('"Bip001 L Toe0", "Bip001 R Toe0"', body)
        self.assertIn("if footPts.count >= 2 do", body)
        self.assertIn('mmd2maxShotOne outDir baseName "foot_top" #view_top topLo topHi', body)
        self.assertIn('"Bip001 L Finger*"', body, "手部特写按手指节点取景")
        self.assertIn('mmd2maxShotOne outDir baseName "hand_side" #view_left handLo handHi', body)
        # #66：不许按骨名去找那两根骨 —— PMX 的胸骨名从「右胸」到 bustR 都有，写死名字
        # 就是给下一具模型埋雷（脚/手那两组按 Biped 名是因为 Biped 节点名由我们自己生成，
        # PMX 骨名不是）。
        # #70 把框的**数值**也交出脚本：那两只比例现在只是「CFG 没给全框」时的退路，
        # 正常路径读 MMD2MAX_CHEST_{X,Y,Z}_{LO,HI}（导出侧按量出来的胸骨三轴极值算的框），
        # 并把真去 zoom 的那只框的三个角点报回 shot_rb —— 否则框里没骨还是全绿（第 35 轮
        # 坏在 z、第 36 轮坏在 y，两轮都只报了一根轴的话另一根永远没人看）。
        # #71 把同一套机制接到颈部：neckLo/neckHi 上面那对「h × 0.55~0.98」只剩兜底，
        # 正常路径改读 MMD2MAX_NECK_*。两套写法逐字对齐（同一套 helper、同一组 report
        # 形状），所以这里按前缀生成 —— 哪天加第三张特写，缺哪一句就红在哪一句上。
        for needle in ("h * 0.58", "h * 0.72"):
            self.assertIn(needle, body, f"胸部兜底带少了 {needle}")
        for prefix in ("chest", "neck"):
            for needle in (f"if (mmd2maxNumsGiven {prefix}Vals) then (",
                           f"{prefix}Lo = [{prefix}Vals[1], {prefix}Vals[2], "
                           f"{prefix}Vals[3]]",
                           f"{prefix}Hi = [{prefix}Vals[4], {prefix}Vals[5], "
                           f"{prefix}Vals[6]]",
                           f"{prefix} 取景：CFG 没给全框"):
                self.assertIn(needle, body, f"{prefix} 特写少了 {needle}")
            for edge in ("lo", "hi"):
                corner = prefix + ("Lo" if edge == "lo" else "Hi")
                for axis in ("x", "y", "z"):
                    needle = (f'mmd2maxReport "shot_rb" "{prefix}_box_{edge}" '
                              f'"{axis}" {corner}.{axis}')
                    self.assertIn(needle, body, f"{prefix} 特写少了 {needle}")
            for view, kind in (("front", "#view_front"), ("side", "#view_left")):
                self.assertIn(f'mmd2maxShotOneTight outDir baseName "{prefix}_{view}" '
                              f'{kind} {prefix}Lo {prefix}Hi', body,
                              f"{prefix}_{view} 没用量的框：框是导出的，图却拍猜的带")
        self.assertIn('mmd2maxShotOneTight outDir baseName "bone_chest_side" '
                      '#view_left chestLo chestHi', body)
        # #73：胸骨那 80 度张角是 **x 与 y 两根分量**，正视图把 y 压成一条线、侧视图把 x
        # 压成一条线 —— 两张各判一半。删掉俯视那张就等于「指向乳头」的 x 分量从此没有
        # 任何图能判（数字判据还在，但用户那句「应该表现胸部的指向方向」是眼睛的活）。
        self.assertIn('mmd2maxShotOneTight outDir baseName "bone_chest_top" '
                      '#view_top chestLo chestHi', body,
                      "胸部顶视那张没了：骨在 x 上张开这件事重新变成只有数字知道")
        # #74：bone_chest_top 只说得出「两根骨朝外朝前张开」，张开的**终点**是不是乳头点
        # 得有网格那张才能判（乳头在网格里）。不带 _bone 的 chest_top 与它同框同视口，
        # 差的只有显不显网格 —— 少一张就退回到「只有鼓包、看不出落到哪」。
        self.assertIn('mmd2maxShotOneTight outDir baseName "chest_top" '
                      '#view_top chestLo chestHi', body,
                      "胸部顶视的带网格版没了：骨尖落没落到乳头上重新变成看不出来")

    def test_the_tight_span_is_reported_on_the_axis_the_script_shrinks(self):
        """#70 的另一半：.ms 收的是哪根轴，规格表就得按哪根轴判 —— 两份名单对撞。

        `mmd2maxAspectBox` 只砍「视口的水平方向」（#view_front 是 x、#view_left 是 y），
        `mmd2maxShotOneTight` 报的 tight_h_lo/hi 就是这根轴上收框**之后**的两个端点。
        Python 侧的 `verdicts.SHOT_H_AXIS` 是同一件事的另一处写法：它按每张 tight 图的
        视图种类给出轴。哪天脚本新增一张 tight 图、或改了分量，这条会先红 —— 而不是
        留下一条拿 y 的尺去判 x 的读数、永远绿也永远看不见偏航的判据。
        """
        # 脚本里的轴选择只有两种写法：默认 1（x），#view_left 换成 2（y）。
        haxis = _between(SCRIPT, "fn mmd2maxHAxis viewKind", "fn mmd2maxComp")
        self.assertIn("#view_left do a = 2", _code(haxis),
                      "左视的水平轴是 y：这条改了，tight_h_* 的读数就串轴了")
        self.assertIn("local a = 1", _code(haxis))
        # 每张 tight 图在脚本里用的视图种类，必须和 Python 表里的轴一致。
        # 俯视写 x 不是笔误：Max 的顶视里屏幕横=世界 X、屏幕竖=世界 Y（Z 是视线方向），
        # 与 `mmd2maxHAxis`（默认 1，只有 #view_left 换 2）和 `mmd2maxShotFill`
        # （俯视的纵向取 y）同一口径。这一格在 #73 之前从来没被走到过 —— 那时没有任何
        # tight 图用俯视，写错的 z 也就没人撞；`bone_chest_top` 是第一条撞它的读数。
        view_axis = {"#view_front": "x", "#view_left": "y", "#view_top": "x"}
        for tag in verdicts.SHOT_TIGHT_TAGS:
            got = re.search(r'mmd2maxShotOneTight outDir baseName "%s" (#view_\w+)' % tag,
                            SCRIPT)
            self.assertTrue(got, f"{tag} 不是走 mmd2maxShotOneTight 拍的：那两行读数没人报")
            self.assertEqual(verdicts.SHOT_H_AXIS[tag], view_axis[got.group(1)],
                             f"{tag} 用的是 {got.group(1)}，规格表却按别的轴卡数")

    def test_non_interactive_run_exits_by_itself(self):
        """上层 taskkill /F 会被 Max 记成「上次会话异常终止」，计数一路累积
        （实测 SessionStartCount=167 / SessionCleanCloseCount=50），之后每次启动
        都弹 "Startup Failure Detection" Qt 模态框 —— 它挡住 -U MAXScript 的自动
        执行，用户不在电脑前就是死锁。所以非交互跑完必须自己 quitMax。"""
        tail = SCRIPT[SCRIPT.index("if not mmd2maxIsInteractive() do"):]
        self.assertIn("quitMax quiet:true", tail,
                      "quiet:true 不弹「保存更改？」；截图改过 isHidden/EdgeFaces，场景是脏的")
        self.assertIn("try ( quitMax )", tail, "quiet 写法万一不支持要有兜底")
        self.assertNotIn("if not mmd2maxIsInteractive() do quitMax", tail,
                         "裸 quitMax（不带括号）在 -U 模式下实测退不出去")


    def test_zoom_fit_uses_the_real_scene_bounds(self):
        """±1e9 巨盒兜底等于没兜：上一轮正视图的头就是被这么裁掉的。"""
        fit = _between(SCRIPT, "fn mmd2maxZoomFit", "fn mmd2maxPrepareShot")
        self.assertIn("mmd2maxSceneBox()", fit)
        self.assertNotIn("box3 [-1e9", fit)
        box = _code(_between(SCRIPT, "fn mmd2maxSceneBox", "fn mmd2maxZoomFit"))
        self.assertIn("o.bounds", box)
        self.assertIn("o.transform.translationpart", box,
                      "pivot 至少要参与包围盒，否则纯骨架那张图会把骨架框掉")
        self.assertNotIn("o.pos", box,
                         "Biped_Object 没有 .pos：01:10 香菱就在取景这一步报 "
                         'Unknown property: "pos" in $Biped_Object:Bip001，整轮判失败')

    def test_failed_non_interactive_run_exits_too(self):
        """失败路径也一样不许留给上层 taskkill：错误全文已经在 error.txt 里，
        非交互模式没人看屏幕，等满超时再强杀只是白攒一次崩溃计数。"""
        # 锚在文件最末的那个 mmd2maxIsInteractive 分支 = 外层 catch（成功分支的
        # messageBox 在它前面），从它到文件尾就是失败收尾段。
        tail = _code(SCRIPT[SCRIPT.rindex("if mmd2maxIsInteractive() do"):])
        self.assertIn("if not mmd2maxIsInteractive() do", tail)
        self.assertIn("quitMax quiet:true", tail)



    def test_marker_is_written_before_the_screenshot(self):
        main = _between(SCRIPT, "fn mmd2maxMain", "try\n(\n    mmd2maxMain()")
        self.assertLess(main.index("close marker"), main.index("mmd2maxScreenshot()"),
                        "截图排在标记之前 = 一个截图错误就毁掉整轮产物判定")
        self.assertIn("mmd2maxScreenshot()",
                      _between(main, "if mmd2maxScreenshotEnabled()", "messageBox"))
        wrapped = _between(main, "if mmd2maxScreenshotEnabled()", "messageBox")
        self.assertIn("截图步骤异常（不影响产物）", wrapped)


class ToeBoxContract(unittest.TestCase):
    """#19：Toe0 必须「接在 Foot 末端、整块呆在鞋里、长度只占脚尖一小段」。"""

    @staticmethod
    def _foot_block():
        return _between(SCRIPT, "===== 脚趾两段",
                        "mmd2maxFigureMode root.controller false")

    def test_toe_fits_three_axes_from_the_measured_targets(self):
        block = self._foot_block()
        helper = _between(SCRIPT, "fn mmd2maxFitCrossSection", "fn mmd2maxLengthAxisIndex")
        reader = _between(SCRIPT, "fn mmd2maxReadLimbs", "fn mmd2maxReadWidths")
        for col, note in (("toe_w", "脚尖处宽"), ("toe_h", "脚尖处高"),
                          ("foot_w", "足背处宽（#37）"), ("foot_h", "足背处高（#37）")):
            self.assertIn(f'mmd2maxColPos tbl "{col}"', reader,
                          f"limbs.tsv 的{note}要按列名读进来（写死下标 = 列序一变就读到隔壁列）")
        self.assertNotIn("parts[15] as float", reader,
                         "limbs 读数还在全局下标：M1 已改成按列名取")
        self.assertIn("local toeW = item[9]", block)
        self.assertIn("local toeH = item[10]", block)
        self.assertIn("mmd2maxFitCrossSection cnode dir toeW toeH", block,
                      "宽/高交给共用助手：Foot 段用的是同一套判据，不许各写一份")
        self.assertIn("mmd2maxFitBoneExtent cnode ai toeLen", block,
                      "长度轴仍由脚趾段自己缩（助手一根手指都不碰长度轴）")
        for call in ("mmd2maxFitBoneExtent node wIdx targetW",
                     "mmd2maxFitBoneExtent node hIdx targetH"):
            self.assertIn(call, _code(helper), f"助手缺逐轴闭环：{call}")
        self.assertIn("mmd2maxBipedAxis node dir", _code(helper),
                      "长度轴必须按参考方向实测，写死一根就是猜")
        # 长度最后缩：前面缩完宽/高才不会把刚拟合好的趾长又带跑
        self.assertLess(block.index("mmd2maxFitCrossSection cnode dir toeW toeH"),
                        block.index("mmd2maxFitBoneExtent cnode ai toeLen"))

    def test_width_and_height_axes_are_picked_by_world_direction(self):
        """哪根是「高」看谁贴近世界 Z，不看谁现在更粗：新口径下宽 5.3 / 高 1.7 差三倍，
        旧的「粗的那根配大值」一旦对调，骨头当场从鞋面上戳出去。

        M4c 起这条口径只有 `mmd2maxWidthHeightAxes` 一份：写入（FitCrossSection）与
        回读（BoxDims）都调它。两份副本一旦分叉，回读报的「宽」量的其实是「高」，
        gate 会红在尺子上而不是几何上 —— 所以这里既验规则在，也验它只被刻了一遍。
        """
        picker = _code(_between(SCRIPT, "fn mmd2maxWidthHeightAxes",
                                "fn mmd2maxFitCrossSection"))
        self.assertIn("mmd2maxBipedAxes node", picker, "要靠世界轴向判断宽高，得先取三轴")
        self.assertIn("(abs (axes[other[1]].z)) > (abs (axes[other[2]].z))", picker)
        self.assertEqual(_code(SCRIPT).count("(abs (axes[other[1]].z)) > "
                                             "(abs (axes[other[2]].z))"), 1,
                         "宽高轴的判据又被刻了一遍：写侧与读侧会各按一份口径挑轴")
        for name, last in (("fn mmd2maxFitCrossSection", "fn mmd2maxLengthAxisIndex"),
                           ("fn mmd2maxBoxDims", "fn mmd2maxReportBoxDims")):
            self.assertIn("mmd2maxWidthHeightAxes node ai", _code(_between(SCRIPT, name, last)),
                          f"{name} 没走共用助手，它挑的轴和写入那一份可能不同")
        self.assertNotIn("eB > eA do tA", SCRIPT, "按粗细猜宽高轴的旧写法已废弃")

    def test_foot_box_is_a_sibling_step_not_a_rename(self):
        """#37：Foot 段的截面是脚趾段旁边的**第二步**，两段的参考方向来源不同。

        Toe0 用 PMX 的「ball→tip」名义方向（趾骨本来就短，pivot 又被脚本摆到目标上）；
        Foot 用**实测踝 pivot→脚掌**，因为要缩的是这根骨盒此刻真正指向的那条轴。
        写成一份就变成「拿趾骨的目标量足骨」，round-9 的 8.00×6.61 会照旧留在鞋里。
        """
        block = self._foot_block()
        self.assertIn("mmd2maxFitCrossSection fnode fdir footW footH", block)
        self.assertIn("local ankleP = fnode.transform.translationpart", block,
                      "Foot 的参考方向取实测踝，不取 PMX 名义方向")
        self.assertIn("local fdir = (ballPos - ankleP) / fl", block)
        self.assertIn("if item.count >= 12 do ( footW = item[11]; footH = item[12] )", block,
                      "第 17/18 列要按列数取，老 lim.ts 只有 16 列时不许越界报错")
        self.assertIn("足背 ", block, "每一段都要单独留一行读数，混在「脚趾」里就分不清谁缩了")

    def test_old_guesstimates_are_gone(self):
        """「粗细 x0.7」「缩到鞋前缘」「与 Foot 末端同高」都是拍脑袋，
        现在一律照 Python 从脚尖网格量的厘米值。"""
        block = self._foot_block()
        self.assertNotIn("0.70", block)
        self.assertNotIn("粗细x0.7", SCRIPT)
        self.assertNotIn("占满鞋前缘", SCRIPT)
        self.assertIn("pivot 落在 Foot link 末端", block)
        self.assertIn("我要整块骨头呆在鞋子里", SCRIPT, "用户的第二轮反馈要留在脚本里")

    def test_axis_scaling_is_per_axis(self):
        """整体乘三根轴会把刚缩好的宽/高带跑，必须单轴乘倍率。"""
        helper = _between(SCRIPT, "fn mmd2maxScaleBoneAxis", "fn mmd2maxFitBoneExtent")
        self.assertIn("if axisIdx == 1 do v.x = v.x * factor", helper)
        self.assertIn("if axisIdx == 2 do v.y = v.y * factor", helper)
        self.assertIn("if axisIdx == 3 do v.z = v.z * factor", helper)
        self.assertNotIn("sc.x * f", SCRIPT, "旧的三轴同乘写法已废弃")

    def test_the_toe_direction_is_one_convention_across_write_and_read(self):
        """#78：趾盒的「参考方向」只有一个口径 —— `mmd2maxBoxWantDir`。

        第 48 轮实测海灯节：鞋尖比脚掌关节**低 1.23cm、水平只走 0.17cm**，现场那条三维
        的 鞋尖−脚掌 几乎是竖直向量。按它挑「长度轴」挑中的是骨自己的竖轴，于是 0.5722
        的趾长被写在竖直轴上、3.4674 那根边顺着趾方向戳出鞋尖 3.47cm。而导出侧量脚尖
        截面（`FOOT_BOX_FLAT` 压平口径）用的就是压平后的同一条方向 —— 写侧自己都得先
        和读侧对上，否则「盒子对了、方向是竖直的」这种形状永远过得了 box_rb。
        现场三维向量只许当兜底（want-dir 返回 undefined 时），且必须紧跟在调用之后。
        """
        early = _code(_between(SCRIPT, "===== 脚趾两段", "local toeBoxFinal = 0"))
        final = _code(_between(SCRIPT, "local toeBoxFinal = 0",
                               'mmd2maxReport "pass" "ran" "toe_box_final" 1'))
        reader = _code(_between(SCRIPT, "fn mmd2maxVerifyBoxes root limbs widths =",
                                "fn mmd2maxFigPath"))
        for region, note in ((early, "早帧写入"), (final, "最终帧写入"), (reader, "骨盒回读")):
            self.assertIn("mmd2maxBoxWantDir", region, f"{note}没走共享的脚链方向口径")
        self.assertEqual(_code(SCRIPT).count("= normalize (tipPos - ballPos)"), 2,
                         "现场三维趾方向又长出一份：它是兜底，不是默认口径")
        for region in (early, final):
            self.assertLess(region.index("mmd2maxBoxWantDir cnode.name ballPos ballPos tipPos"),
                            region.index("= normalize (tipPos - ballPos)"),
                            "兜底排在了共享口径前面，等于退回 #78 之前的写法")
        self.assertLess(reader.index("mmd2maxBoxWantDir cName ballPos ballPos tipPos"),
                        reader.index("wantC = tv / tl"),
                        "回读先用现场向量、后想起共享口径：尺子和写入不是同一条")
        # 足背那根骨的尺子仍是「实测踝 pivot → PMX 脚掌」（#37/#40）：它要缩的是骨盒
        # 此刻真正指向的轴，换成 PMX 名义方向就会把 8.00×6.61 那种旧形状放回鞋里。
        self.assertIn("local fdir = (ballPos - ankleP) / fl", early)
        self.assertIn("mmd2maxBoxWantDir fName ankleTgt toeTgt tipPos", _code(SCRIPT),
                      "画线/链向换了口径：它和趾盒写入就不再比同一条方向")

    def test_the_toe_box_is_refitted_after_the_last_pass_that_moves_the_toe(self):
        """#78：会改骨盒的写必须排在「最后挪动这根趾骨的 pass」之后，否则写帧≠验收帧。

        大腿扭转 + 两次复摆能把这根本来短得可以忽略的趾骨转过约 90 度：早帧按当时那一帧
        达成的「截面」到最终帧已经落在别的轴上。这条 pass 的存在本身不算证据 —— 要的是
        它在时间轴上的**位置**，所以三头都钉住：注册表里排在 toe_snap_reach 之后、
        figure_exit 之前，脚本里那一段真的写在两个 ran 行之间。
        """
        ids = list(passes.pass_ids())
        self.assertLess(ids.index("toe_snap_reach"), ids.index("toe_box_final"),
                        "收尾重收排在了复摆之前，量的还是旧帧")
        self.assertLess(ids.index("toe_box_final"), ids.index("figure_exit"),
                        "退出 Figure 模式之后再缩骨盒就写不进场景了")
        toe_final = next(p for p in passes.PASSES if p.id == "toe_box_final")
        # 只缩一根**叶子**骨：它没有子节点，父→子距离、落位、复摆残差都不受影响，
        # 所以不作废任何场景读数（声明成 moves=True 会让后面没人重测的读数直接判红）。
        self.assertEqual(toe_final.invalidates, (),
                         "趾盒收尾若开始挪动别的骨，作废名单要重议而不是顺手加全量")
        self.assertEqual(toe_final.requires, ("toe_snap_reach",))
        script = _code(SCRIPT)
        self.assertLess(script.index("local toeBoxFinal = 0"),
                        script.index('mmd2maxReport "pass" "ran" "figure_exit" 1'))
        self.assertLess(script.index('mmd2maxReport "pass" "ran" "toe_snap_reach" 1'),
                        script.index("local toeBoxFinal = 0"))
        # 收尾用的是同一个写入助手，不是另抄一份缩盒逻辑
        self.assertEqual(script.count("mmd2maxFitCrossSection cnode dir toeW toeH"), 2,
                         "趾盒截面的写入手抄成了两份：早帧与终帧会各自漂移")


class ToeSkinProbeContract(unittest.TestCase):
    """#35 的 Max 端回读：脚尖改绑不能只在 Python 侧自证，Skin 里真挂了顶点才算。

    为什么要单独一组：weights.tsv 是「导出对了」的证据，不是「Max 里扳得动」的证据。
    用户报「Toe0 控制不了脚尖」时，两边都得有读数才能分清是源数据没绑、改绑没生效，
    还是权重写进 Skin 时被别的骨抢走。判据用离线算好的 74/363，不写「大概几十」。
    """

    def setUp(self):
        self.global_block = _between(SCRIPT, "local skinNodeNames = #()",
                                     "local weightStart = timeStamp()")
        self.write_block = _between(SCRIPT, "local boneVertHits = #()",
                                    "顶点变形（表情）→ Morpher")

    def test_skin_local_names_are_recorded_in_add_order(self):
        """骨名要和 AddBone 的顺序同源，否则「局部序号 -> 名字」对不上，读数就是假的。"""
        self.assertIn("append skinNodeNames boneNodes[i].name", self.global_block)
        self.assertIn("globalToSkin[i] = skinId", self.global_block)
        self.assertLess(self.global_block.index("globalToSkin[i] = skinId"),
                        self.global_block.index("append skinNodeNames boneNodes[i].name"),
                        "名字必须在 skinId 自增之后追加，两者顺序必须一致")

    def test_hit_counter_uses_the_skin_local_index(self):
        """计数必须落在 Skin 局部序号上（globalToSkin 换过），不是全局骨序号。"""
        self.assertIn("local sid = globalToSkin[g + 1]", self.write_block)
        self.assertIn("boneVertHits[sid] = boneVertHits[sid] + 1", self.write_block)
        self.assertNotIn("] +=", self.write_block,
                         "数组下标做左值再叠复合赋值不拿去赌实跑（标量 += 是验证过好用的）")

    def test_coverage_is_logged_for_toe_and_foot(self):
        """读数只打 Toe0/Foot 两组（总量守恒：一边涨一边落），并且不许被删掉。"""
        self.assertIn('matchPattern nm pattern:"* Toe0"', self.write_block)
        self.assertIn('matchPattern nm pattern:"* Foot"', self.write_block)
        self.assertIn('"  蒙皮覆盖 " + nm + " 顶点="', self.write_block)


class FootYawContract(unittest.TestCase):
    """#26：Bip001 R Foot 朝人物中线偏 —— 全局朝向要两脚平分，偏航要有读数。"""

    @staticmethod
    def _facing_block():
        return _between(SCRIPT, "实测朝向：脚尖", "local probeAngle = 0.0")

    @staticmethod
    def _yaw_block():
        return _between(SCRIPT, "===== 脚骨「画出来的朝向」复核", "/* 肢段长度复核")

    @staticmethod
    def _probe_block():
        return _between(SCRIPT, "旋转接口探针（#27）", "delete probe")

    def test_facing_bisects_both_feet(self):
        """只按左脚测朝向 = 把整副骨架拧向左脚，右脚偏 13.4 度（用户截图）。"""
        block = self._facing_block()
        for name in ("Bip001 L Foot", "Bip001 L Toe0", "Bip001 R Foot", "Bip001 R Toe0"):
            self.assertIn(f'targetNames[k] == "{name}"', block, f"朝向测量漏了 {name}")
        self.assertIn("local bis = uL + uR", block, "两脚方向必须取角平分线")
        self.assertIn("fwdX = bis.x / bisLen", block)
        # 单脚退回只在缺另一只时兜底，不许拿它当主路径
        self.assertIn("if not gotFwd and uL != undefined", block)
        self.assertIn("两脚外八", block, "左右各偏多少度要能被读出来（下一轮的判据）")

    def test_direction_check_is_not_a_pivot_identity(self):
        """08:59 那轮的教训：比 (Toe0head - Foothead) 与 PMX 目标 = 恒等式，
        两个点都是自己摆到位的，夹角必然 ≈0（实测 L=0.0198° R=0.0°）却照样全绿。
        脚骨的可见朝向只能量节点局轴。"""
        block = _code(self._yaw_block())
        self.assertNotIn("translationpart - fnode.transform.translationpart", block,
                         "又写回 pivot 减 pivot 的恒等式了，这条校验量不到偏航")
        self.assertIn("mmd2maxBipedAxes fnode", block)
        self.assertIn("mmd2maxBipedAxes cnode", block)

    def test_yaw_is_read_after_leaving_figure_mode(self):
        """Figure 模式里的 #pos 改的是结构，退出之后局轴才定下来 —— 只能退出后量。"""
        self.assertGreater(SCRIPT.index("===== 脚骨「画出来的朝向」复核"),
                           SCRIPT.index("mmd2maxFigureMode root.controller false"))
        block = self._yaw_block()
        # 目标朝向来自 PMX 的两个落位（足首→つま先、つま先→鞋尖），不是当场量出来的两点，
        # 而且两根都必须取自同一个 mmd2maxBoxWantDir：压不压平只由 MMD2MAX_FOOTBOXFLAT 决定，
        # 翻回滚开关时「写」和「读」必须一起变（第十三轮的判据也全靠这两行同源才敢认）
        self.assertIn("(mmd2maxBoxWantDir fName ankleTgt toeTgt tipPos)", block,
                      "画线复核必须和转角 pass 同一判据，否则刚修好的脚又读成超标")
        self.assertIn("(mmd2maxBoxWantDir cName toeTgt toeTgt tipPos)", block,
                      "趾骨也必须走 mmd2maxBoxWantDir：自己减两点等于绕开斜度上限和回滚开关")
        self.assertNotIn("mmd2maxFlatDir", block, "压平只许留在 mmd2maxBoxWantDir 内部")
        self.assertIn("if row.count >= 4 and row[2] == fName do ankleTgt = row[4]", block)
        self.assertIn("脚骨偏航结论", block, "结论行得打出来，否则四行轴数没人对着读")

    def test_rot_probe_only_touches_the_disposable_biped(self):
        """09:37 实测：`#rot` 报 "Unknown transform type" ⇒ 不是「Biped 没有转角」，是 token
        猜错了。Max 自带的 scripts/BulletScripts/BsRetargetTools.ms、BsBipedTools.ms 里全是
        `biped.setTransform node #rotation <quat> <bool>`（Straighten 用它给 Toe 补 90°），
        所以 #rotation 排第一。判据仍然是**局轴有没有动**，不是「调用没抛错」——#pos 就是
        接受参数而根本不转骨头的先例。探针选出的写法写进 rotWay，真骨架一行都不许提前动。"""
        block = _code(self._probe_block())
        self.assertIn("#(#rotation, #rot, #rotate, #move, #worldRot, #rotEuler)", block,
                      "Max 自带脚本用的 #rotation 必须排在第一个，候选 token 少试一个就得再开一轮")
        self.assertIn("eulerToQuat (eulerAngles 0.0 0.0 30.0) order:1", block,
                      "10:34 实测 `eulerToQuat [0,0,30]` 自己就抛 Unable to convert…EulerAngles："
                      "两轮日志里的 `#rotation q=0.0度` 其实是四元数压根没构造出来，"
                      "照抄 Max 自带 BsBipedTools.ms:1491 的 (eulerAngles …) order:1 形式")
        self.assertIn("biped.setTransform plf tok q30 false", block,
                      "构造好的 quat 必须真的喂进 setTransform，否则 #rotation 仍然是没测过的")
        self.assertIn("mmd2maxAxisMoveDeg ax0 ax1", block,
                      "每个 token 试完立刻回读局轴：只有轴动了才算接口通")
        self.assertIn("rotWay = #bipedRotation", block,
                      "通过的写法要交给转角 pass，否则探到了也用不上")
        self.assertIn("plf.transform = rotateZMatrix 30.0 * plf.transform", block,
                      "biped.setTransform 之外还得试直接写 TM 这条路")
        self.assertIn("rotate plf (angleaxis 30.0 [0.0, 0.0, 1.0])", block,
                      "第三条路：通用 rotate 命令（10:34 实测 Figure 模式内唯一有效的一条）")
        self.assertIn("mmd2maxLog rotMsg", block, "探针结果不落日志等于没探")
        self.assertNotIn("root.name", block, "探针阶段还没有 root，动真骨架就是越界")
        self.assertLess(SCRIPT.index("旋转接口探针（#27）"), SCRIPT.index("已创建 Biped"))

    def test_rot_probe_requires_the_rotation_to_survive_a_refresh(self):
        """10:17 探针：`.transform=30.0度 rotate命令=30.0度` —— 但那是**写完立刻读**，
        可能只是 MAXScript 自己的缓存，Biped 求解器下一次 update 能把 TM 按结构算回去。
        所以采用任何一条路之前必须再过一次 mmd2maxRefreshedMoveDeg（原值重发 #pos +
        update 0 之后重量局轴）；只认「立刻数」的探针会把假接口交给真骨架。"""
        block = _code(self._probe_block())
        self.assertIn("mmd2maxRefreshedMoveDeg plf ax1", block)
        self.assertIn("mmd2maxRefreshedMoveDeg plf axA", block)
        self.assertIn("mmd2maxRefreshedMoveDeg plf axC", block)
        self.assertIn("if rotWay == undefined and mvQ >= 15.0 and keepQ >= 15.0 do", block,
                      "采用判据必须同时卡「立刻转了」和「刷新后还在」")
        self.assertIn("if rotWay == undefined and keepTm >= 15.0 do rotWay = #tmAssign", block)
        self.assertIn("if rotWay == undefined and keepRot >= 15.0 do rotWay = #rotateCmd", block)
        self.assertIn('q抛错=', block, "10:17 只打了 errP，quat 那条调用抛没抛错日志里看不出来")

    def test_rot_probe_measures_inside_and_outside_figure_mode(self):
        """真骨架的转角 pass 跑在 `mmd2maxFigureMode ... true` 与 `false` 之间，
        而 #scale/#pos 这些结构写入本来就只在 Figure 模式里生效 —— 10:17 的探针是在
        模式**外**测的，`#rotation` 读出 0.0 度不能算否决。所以先在模式内测、
        退出后再对照一次，两个数都进日志。"""
        block = _code(self._probe_block())
        self.assertIn("mmd2maxFigureMode probe.controller true", block)
        self.assertIn("mmd2maxFigureMode probe.controller false", block)
        self.assertLess(block.index("mmd2maxFigureMode probe.controller true"),
                        block.index("mmd2maxFigureMode probe.controller false"),
                        "先模式内测 token，退出后只做对照")
        self.assertLess(block.index("mmd2maxFigureMode probe.controller true"),
                        block.index("rotWay = #bipedRotation"),
                        "采用判据必须是在 Figure 模式里量出来的")
        self.assertIn("退出Figure(#rotation)=", block)

    def test_rotation_pass_is_gated_on_the_probe(self):
        """骨盒不随 pivot 走（09:37：可测=51 / 夹角>5度=35，而 pivot偏差 1e-5），
        唯一能改方向的是转角；但转角接口必须先在临时骨架上探通才允许写真骨架 ——
        探针没过就整段跳过，这一轮退回成纯测量，绝不能把场景转坏。
        12:04 之后这段从主流程里提成 mmd2maxRotatePass（收敛循环要调它），
        所以切片按函数体切，不再按「pass 在退出 Figure 模式之前」那条老标记切。"""
        helper = _between(SCRIPT, "fn mmd2maxRotatePass", "fn mmd2maxLocalExtent")
        code = _code(helper)
        self.assertLess(SCRIPT.index("fn mmd2maxRotatePass"),
                        SCRIPT.index("mmd2maxFigureMode root.controller false"),
                        "退出 Figure 模式之后再转就是做动作、插 key，不是改结构性姿态")
        self.assertIn("if rotWay == undefined do", code)
        self.assertIn("转角 pass：探针未跑通", code, "跳过必须留一行日志，不然分不清没跑和没转")
        self.assertIn("return #(0, 0, 0, 0)", code, "探针没过就一根都不许碰，直接交回零计数")
        self.assertIn("mmd2maxRotateBoneToDir node want rotWay", code)
        self.assertIn('if t != "Bip001" do', code, "COM 由骨盆闭环负责，不参与转角")
        self.assertIn("(length want) >= 1.0", code, "短骨是模型自己的显示 stub，方向没意义")
        self.assertIn('if b > MMD2MAX_DIRTOL do', code, "已经对上的骨头不要再去动它")
        self.assertIn("夹角=", code)
        self.assertIn("抛错=", code, "setTransform 抛错必须进日志，不能包在裸 try catch 里")

    def test_rotation_pass_replaces_pivots_afterwards(self):
        """转角是绕自身 pivot 转，父骨一转就把子骨关节甩出 PMX 的 head：11:11 那轮
        `结构向` 超标从 35 掉到 2，落位却从 1e-5 变成 Neck 4.90 / Head 4.15 /
        L Finger32 7.83。探针里 `q=120.0度/留=120.0度` 正是「重发 #pos + update 之后」
        读到的 ⇒ #pos 不吃转角，所以顺序必须是先转方向、再摆落位。
        12:04 之后这段换成收敛循环；13:11 之后收尾改成「先全摆、再只转 #dir」——
        旧收尾跳过 #dir，于是 `转角1:Bip001 L Hand 夹角=43.1` 甩出去的整条指链
        再没人摆回来（`L Finger32 pivot偏差=10.6619`，而那一遍没转 Hand 的右手全在 0.15 内）。"""
        block = _between(SCRIPT, "摆位 <-> 转角 收敛循环（机制层）",
                         "mmd2maxFigureMode root.controller false")
        code = _code(block)
        self.assertIn("重摆(方向优先的骨跳过): 成功=", code,
                      "循环内的重摆必须留读数，否则分不清没摆和摆不动")
        self.assertIn("mmd2maxPlaceBipedNodes root lines rowIds targetNames passList true",
                      code, "重摆要复用同一个先父后子的 passList，自己排顺序就是猜")
        self.assertIn("if (lastAttempt > 0 or legRolled > 0) do", code,
                      "最后一遍没转任何东西、大腿也没扭转成功就不许白摆一遍；"
                      "转腿会把踝甩出去，收尾必须跟着 legRolled 走（#36）")
        self.assertIn("收尾重摆(全摆，含方向优先的骨): 成功=", code,
                      "收尾必须带读数")
        self.assertIn("mmd2maxPlaceBipedNodes root lines rowIds targetNames passList false",
                      code, "收尾是全摆：跳过 #dir 就等于承认指链可以留在 10cm 外")
        self.assertGreaterEqual(code.count("passList false"), 1,
                                "收尾那一次全摆不许退化成跳过")
        self.assertIn("收尾转角(只转方向优先的骨): 尝试=", code,
                      "全摆之后必须把 #dir 的方向补回来，否则收尾等于把 12:04 的指链问题换个方向重演")

    def test_rotate_bone_helper_never_leaves_worse_pose(self):
        """骨盒转歪了比不转更糟，所以写完必须立刻回读：不比原样好 0.5 度以上就把**原矩阵
        整块**写回去。转角用「钉住结构轴 + 施密特正交化 + 叉乘补第三根」构造，
        完全绕开 MAXScript 四元数乘法的左右手约定（约定猜反时判据只会当「没改善」）。
        10:17 之后删掉的「相对父骨再试一次」：Max 自带 BsBipedTools.ms 的 Straighten 写的是
        `offsetRotation * bip.parent.transform.rotation` —— 世界四元数语义已经被证清，
        不需要拿真骨架当试错场。"""
        helper = _between(SCRIPT, "fn mmd2maxRotateBoneToDir", "fn mmd2maxLocalExtent")
        code = _code(helper)
        self.assertIn("mmd2maxSetBoneRot node way mTarget", code)
        self.assertIn("mmd2maxSetBoneRot node way m0", code, "没改善必须写回原矩阵")
        self.assertNotIn("node.parent.transform.rotation", code,
                         "父骨相对写法已被 Max 自带脚本否掉，留着就是拿真骨架试错")
        self.assertIn("if drop == 1 do rows[1] = cross rows[2] rows[3]", code,
                      "第三根轴必须叉乘补出来，行列式才保持 +1")
        self.assertIn("local mTarget = matrix3 rows[1] rows[2] rows[3] m0.row4", code,
                      "平移必须按原 row4 写回，否则转角一写 pivot 就飞了")
        for k in ("1", "2", "3"):
            self.assertIn("mmd2maxScaleRow rows[%s]" % k, code,
                          "行必须乘回原长度：写单位向量等于顺手把骨盒缩放抹成 1")
        self.assertIn('if (length v) < 0.001 do return #(before, before, "其余轴与结构方向共线")',
                      code)

    def test_rotate_cmd_route_is_incremental_not_a_matrix(self):
        """10:34 的失败方式：探针采用 `#rotateCmd`，而 `mmd2maxSetBoneRot` 把 #rotateCmd 和
        #tmAssign 当成同一条（都写 `node.transform = m`）⇒ `转角 pass：接口=rotateCmd
        尝试=35 改善=0 未改善=35`，用的正是那一轮刚被实测否掉的直写路。
        `rotate node (angleaxis ang ax)` 是**增量**写法，必须换算成 轴 + 角度：
        轴 = cross(当前最接近结构方向的轴, want)，角度 = 两者夹角；
        而 angleaxis 的正方向纸面定不下来，所以 ±ang 都要试，都不更好就把净角转回 0。"""
        helper = _between(SCRIPT, "fn mmd2maxRotateBoneToDir", "fn mmd2maxLocalExtent")
        code = _code(helper)
        self.assertIn("if way == #rotateCmd then", code, "增量路与绝对路必须真的分叉")
        self.assertIn("local axU = cross axes[bestAx] want", code)
        self.assertIn("if (length axU) < 0.001 do axU = axes[keep]", code,
                      "反平行时叉乘退化，得换一根已知轴转 180")
        self.assertIn("local ang = mmd2maxVecAngleDeg axes[bestAx] want", code)
        self.assertIn("for cand in #(ang, (-ang)) do", code, "正负方向都要试，不能猜左右手")
        self.assertIn("mmd2maxRotateBoneAround node step axU", code)
        self.assertIn("if bestNet != net do", code, "两个方向都没改善要能回到原姿态")
        self.assertIn("mmd2maxRotateBoneAround node (bestNet - net) axU", code)

    def test_rotate_around_helper_reports_errors(self):
        helper = _between(SCRIPT, "fn mmd2maxRotateBoneAround", "fn mmd2maxRefreshedMoveDeg")
        code = _code(helper)
        self.assertIn("rotate node (angleaxis ang ax)", code)
        self.assertIn('if (length ax) < 0.0001 do return "旋转轴退化"', code)
        self.assertIn("getCurrentException() as string", code)

    def test_set_bone_rot_rejects_routes_it_cannot_write(self):
        """转角写法由探针现场给出，token 与实参形状都不写死；但异常文字必须返回给调用方
        —— 09:37 那条 isNode 的教训就是「包在 try catch 里 = 这件事从来没发生过」。
        10:34 之后 #rotateCmd 不再由这里实现（它是增量的），未知写法必须显式报错，
        绝不能悄悄退化成 `node.transform = m`。"""
        helper = _between(SCRIPT, "fn mmd2maxSetBoneRot", "fn mmd2maxRotateBoneAround")
        code = _code(helper)
        self.assertIn("if way == #bipedRotation then", code)
        self.assertIn("biped.setTransform node #rotation q false", code,
                      "四参形状与 Max 自带脚本一致；第 4 个参数 false = 不插 key")
        self.assertIn("local q = m.rotation", code,
                      "四元数要从目标矩阵取出来再喂给 biped（10:17：喂 point3 报 Unable to convert）")
        self.assertIn("else if way == #tmAssign then", code)
        self.assertIn("node.transform = m", code, "#tmAssign 才走整块矩阵直写")
        self.assertIn('err = "未知转角写法 "', code, "落到 else 就报错，不许默默用被否掉的路")
        self.assertIn("getCurrentException() as string", code)
        self.assertIn('local err = ""', code)

    def test_refresh_helper_reissues_pos_and_gives_up_honestly(self):
        """「留没留」的测法本身不能是猜的：对同一根骨重发一次一直好用的 #pos（原值，
        位置不动）逼 Biped 刷新，再走刷新探针，然后重量局轴。取不到时要给 -1.0，
        不能给 0 度 —— 0 度看起来像「没转」，-1 才是「这条没测成」。

        以前这条断言的是 `"update 0" in code`，也就是**把一句死调用钉成了契约**：
        第 62 轮的日志证明 Max 2022 没有全局 `update`（`update 0` 被读成「给整数 0 发
        update 消息」，抛 `No "update" function for 0`）。所以现在的契约是「刷新这件事
        交给 mmd2maxBipedRefresh()，并且重发 #pos 没走通时绝不留下一个看起来像验证过的
        读数」，同时把那句死调用反向钉住（不许回来）。"""
        helper = _between(SCRIPT, "fn mmd2maxRefreshedMoveDeg", "fn mmd2maxStructErr")
        code = _code(helper)
        self.assertIn("p = node.transform.translationpart", code)
        self.assertIn("biped.setTransform node #pos p false", code)
        self.assertIn("mmd2maxBipedRefresh()", code)
        self.assertIn("if not refreshed do return -1.0", code,
                      "重发 #pos 失败之后量到的局轴没被 Biped 重算过，报 -1.0 而不是继续报数")
        self.assertIn("return -1.0", code)
        self.assertIn("mmd2maxAxisMoveDeg axRef ax", code)
        self.assertNotIn("update 0", code,
                         "第 62 轮实测的死调用：Max 2022 没有全局 update，它只是看着像一句刷新")

    def test_refresh_probe_tries_more_than_it_assumes(self):
        """刷新调用必须是**探出来的**，不是写死的一句：候选至少两句、每句的失败文字都要
        记下来、赢家缓存进全局、赢家与被试掉的那句一起打一行读数。
        只报赢家不够 —— 第 63 轮就是 `redrawViews()` 赢了，但没人知道 `updateSceneTime`
        是怎么死的，而「哪一句真的会重算控制器」只能靠那几个报错文字判。"""
        helper = _between(SCRIPT, "fn mmd2maxBipedRefresh", "fn mmd2maxRefreshedMoveDeg")
        code = _code(helper)
        self.assertIn("try ( evalTime 0; used =", code)
        self.assertIn("try ( redrawViews(); used =", code)
        self.assertEqual(code.count("getCurrentException() as string"), 2,
                         "每一个候选的失败文字都得存下来：静默地少一个候选就等于悄悄换掉了尺")
        self.assertIn("if MMD2MAX_REFRESHCALL == undefined do", code,
                      "结果要缓存：一轮里几百次调用只许打一行读数")
        self.assertIn("MMD2MAX_REFRESHCALL = used", code)
        self.assertIn('没有一种跑得通', code, "全失败也是一种读数，不许打空")
        self.assertIn("（试掉：", code)
        self.assertIn('只重画视口，不重算控制器', code,
                      "赢家必须带口径：第 63~65 轮赢的都是 redrawViews()，"
                      "光一个名字会被读成「控制器重算过了」")
        self.assertIn('used + "（" + usedKind + "）"', code)
        self.assertNotIn("update 0", code, "死调用不许回到候选里")
        self.assertNotIn("updateSceneTime", code,
                         "第 64 轮实测这个名字在 Max 2022 里不存在（Call needs function or class, "
                         "got: undefined）；留作候选只是每轮多一行噪声")

    def test_scale_row_helper_keeps_box_size(self):
        helper = _between(SCRIPT, "fn mmd2maxScaleRow", "fn mmd2maxSetBoneRot")
        code = _code(helper)
        self.assertIn("if l < 0.0001 do return v", code, "零长度行不能除")
        self.assertIn("v * (s / l)", code)

    def test_bone_dir_helper_reports_all_three_axes(self):
        """脚骨长度在局部 Y、脚趾骨在 X —— 写死一根就是猜，猜错时读数假性变大。"""
        helper = _between(SCRIPT, "fn mmd2maxLogBoneDir", "fn mmd2maxBipedAxes")
        self.assertIn("for k = 1 to 3 do", helper)
        self.assertIn("mmd2maxVecAngleDeg axes[k] wantDir", helper)
        self.assertIn("mmd2maxYawDiffDeg axes[k] wantDir", helper)
        self.assertIn("最近轴", helper)
        self.assertIn("if axes == undefined do return -1.0", helper)
        self.assertIn("if wantDir == undefined do return -1.0", helper)

    def test_yaw_diff_is_horizontal_and_flags_degenerate_projection(self):
        helper = _between(SCRIPT, "fn mmd2maxYawDiffDeg", "fn mmd2maxLogBoneDir")
        self.assertIn("sqrt (a.x * a.x + a.y * a.y)", helper)
        self.assertIn("return -999.0", helper, "水平投影退化时必须显式标记，不能混进正常读数")
        self.assertIn("atan2 a.y a.x", helper)
        self.assertIn("d = d - 360.0", helper)
        self.assertIn("d = d + 360.0", helper)

    def test_foot_top_frame_is_tight_and_logged(self):
        """取景框扩 15cm 时视口按 1.6 宽高比自动加宽，整条手臂被拉回画面、
        脚只剩几个像素（08:59 实测）；而且分不清是节点没查到还是框被撑开。"""
        block = _between(SCRIPT, "俯视特写的取景框", "mmd2maxPrepareShot()")
        self.assertIn("lo.x - 6.0", block)
        self.assertNotIn("15.0", block, "15cm 的边距会把 ±38cm 的手臂框进来")
        self.assertIn("foot_top 取景：脚骨节点=", block)

    def test_no_isnode_anywhere(self):
        """MAXScript 根本没有 isNode（09:37 实测 `Type error: Call needs function or
        class, got: undefined`）。它一旦被包在 try catch 里，就只留一行异常日志、
        功能静默退化 —— 手部取景就是这么变成全身图的。"""
        self.assertNotIn("isNode", _code(SCRIPT),
                         "判取到节点用 != undefined；通配符返回形状用 classof == Array")

    def test_hand_frame_is_tight_and_logged(self):
        """10:17 那轮 `hand 取景：手指节点=1` 暴露的 bug：**getNodeByName 不支持 `*` 通配符**
        （返回 undefined），所以框退化成整场景、两张手部特写等于没拍。按名字模式收节点
        只能遍历 objects + matchPattern；而且要把「命中几根」和「取到几个点」分开打，
        命中 0 与只命中 Hand 是两种不同的故障。"""
        block = _between(SCRIPT, "local handPts = #()", "mmd2maxPrepareShot()")
        self.assertIn('"Bip001 L Finger*"', block, "手指特写要按指节点实际落位取景")
        code = _code(block)
        self.assertNotIn('getNodeByName "Bip001 L Finger*"', code,
                         "通配符交给 getNodeByName 只会拿到 undefined（框静默退回整场景）")
        self.assertIn('for n in objects do', code)
        self.assertIn("(classOf n) == Biped_Object", code)
        self.assertIn('matchPattern n.name pattern:"Bip001 L Finger*"', code,
                      "10:34 位置实参写法命中 0 根；Max 自带脚本一律写成 pattern: 标签")
        self.assertNotIn("try ( hit =", code,
                         "逐根 try catch 会把 matchPattern 的真实报错吞成 命中=0")
        self.assertIn("getNodeByName \"Bip001 L Hand\" exact:true", code)
        self.assertIn("hand 取景：Biped节点=", block, "命中 0 到底是没有 Biped 对象还是模式不对")
        self.assertIn("手指命中=", block, "遍历到 0 根 = 匹配没跑起来")
        self.assertIn("取景点数=", block, "点数只有 1 = 只找到 Hand，框会退回整场景")
        self.assertIn("if handPts.count >= 2 do", code, "点不够就不能改框，宁可退回整场景")

    def test_angle_helper_clamps_acos_and_rejects_zero_vectors(self):
        helper = _between(SCRIPT, "fn mmd2maxVecAngleDeg", "fn mmd2maxYawDiffDeg")
        self.assertIn("return -1.0", helper, "零向量要当「没测到」，不能给 0 度假绿")
        self.assertIn("if c > 1.0 do c = 1.0", helper)
        self.assertIn("if c < -1.0 do c = -1.0", helper)

    def test_no_negative_literal_after_amax_or_amin(self):
        """`amax x -1.0` 会被解析成减法，钳位静默失效（规则 15）。"""
        self.assertNotRegex(_code(SCRIPT), r"\b(amax|amin) [^\n]*\s-\d")


class StructDirectionContract(unittest.TestCase):
    """#27：Spine2 没后仰 / 颈没前倾 / 手指不贴合 —— 位置校验答不了「方向」，
    而且那份校验名单是写死的，Spine1 和 30 根手指骨根本不在里面。"""

    @staticmethod
    def _block():
        return _between(SCRIPT, "===== 结构方向复核", "把 Biped 节点写回")

    def test_struct_check_covers_every_mapped_node(self):
        block = self._block()
        self.assertIn("for k = 1 to targetNames.count do", block,
                      "必须遍历全部映射节点：写死名单 = 没打印被当成没问题")
        self.assertIn('if t != "Bip001" do', block, "只有 COM 排除（它由骨盆闭环负责）")
        self.assertIn("[(p[cTailX] as float), (p[cTailY] as float), (p[cTailZ] as float)]",
                      block, "tail 必须按 bones.tsv 的 tail_x/y/z 三列取，"
                             "取错列就是在比 head->head")
        self.assertNotIn("p[9] as float", block,
                         "还在按第 9 列硬取：M1 之后列号一律由表头名解析，"
                         "Python 侧一改列序，写死下标就静默读到隔壁列还照样报「方向正常」")
        self.assertIn('mmd2maxColPos lines "tail_z"', SCRIPT,
                      "tail_z 这一列必须由 mmd2maxReadTable 的表头解析出列号")
        self.assertIn("if a >= 0.0 do", block,
                      "量不出来的（dummy 骨 tail 与 head 重合）计入「可测」会让超标率假低")
        self.assertIn("local stubBone = (distance headPos tailPos) < MMD2MAX_STUBCM", block,
                      "tail 只差 0.38cm 的显示骨（香菱 右手首）方向没意义，"
                      "混进超标数就是一条假红灯；门限读 CFG，不在脚本里留字面量")
        self.assertIn("短骨不计=", block, "分桶要在汇总行里露出来，不然读者以为被漏检了")
        self.assertIn("结构向 复核：可测=", block, "要有总数与超标数，否则读的人得自己数 50 行")
        self.assertNotIn("Bip001 L Clavicle", block, "又写死白名单了")

    def test_struct_direction_target_is_caller_supplied(self):
        """网格是照着 PMX 的 head->tail 做的；我们只把 pivot 摆到 head，
        画出来的段是 head->下一个 head。手指链两者离线算过是同一个方向（PMX head-to-tail），
        所以这张表的意义是「盒到底跟不跟 pivot」——不跟就整片超标，跟就只有
        Hand/掌心/Spine 三类已知不可比的段冒出来。
        但「盒该指哪」不是所有骨都等于 PMX tail-head（脚链另有约定，见
        test_foot_chain_box_direction_is_horizontal），所以方向由调用方交进来。"""
        helper = _between(SCRIPT, "fn mmd2maxLogStructDir", "fn mmd2maxLogBoneDir")
        self.assertIn("fn mmd2maxLogStructDir label axes headPos want gotPos nodeNm =", helper,
                      "want 必须从外面传：写死 tail-head 就否掉了脚链的水平规定；"
                      "nodeNm 也要传进来，复核得查这根骨的长度轴（规则 28）")
        self.assertIn("local useAx = mmd2maxLenAxisOf nodeNm", helper,
                      "复核量的轴必须与 mmd2maxStructErr 同一规则，否则门控绿而复核红"
                      "（或像第七轮那样反过来：门控绿而指尖在 6.39cm 外）")
        self.assertIn("按长度轴", helper, "按长度轴量的行要自己说明，读日志的人才知道 轴N 是谁")
        self.assertIn('axTag = axTag + " 最近轴=" + (bestAx as string)', helper,
                      "两种规则给出不同轴时把另一个也打出来：这一行就是"
                      "「新规则在哪几根骨上真的改了行为」的清单")
        self.assertIn("if headPos == undefined or want == undefined do return -1.0", helper)
        self.assertIn("if (length want) < 0.01 do return -1.0", helper,
                      "tail 与 head 重合（dummy 骨）= 量不出来，不能给 0 度假绿")
        self.assertNotIn("local want = tailPos - headPos", helper,
                         "自己重算方向 = 悄悄绕开 mmd2maxBoxWantDir")
        self.assertIn("for k = 1 to 3 do", helper)
        self.assertIn("差倾=", helper)
        self.assertIn("差偏航=", helper)
        self.assertIn("pivot偏差=", helper)
        self.assertIn('" 短骨"', helper, "短骨要在行里自己说明，否则读日志的人以为漏了一行")
        self.assertIn("\n    bestAng\n)", helper,
                      "调用方要拿这个数统计超标，返回别的东西就得回头解析日志")

    def test_foot_chain_box_direction_pins_which_two_points(self):
        """脚链的骨盒方向在这一个函数里定死「取哪两个点」，俯仰交给注入头（#37 引入、#39 改口径）。

        历史三笔都在这儿：
        11:11 拿 PMX tail-head 一刀切转角，足首 的 tail 落在**踩地点**（比踝低 5.59cm），
        脚被转成下扎 30 度插进地里；つま先 的 tail 从踩地点竖直向上 7.66cm，趾骨被转得
        竖直朝天（用户：「两边的脚也没有落在地面上，Y没有90度」）—— 那是**子关节被转角
        拖走**的年代，于是 #28 把脚链一刀压平。
        第十二轮藏网格脚部特写拍到一根卡在脚背上的水平板、前端离趾骨盒还差 5.6cm（用户：
        「Bip001 Foot的底部没有够到脚的下面」），#37 据此把俯仰还给了三维结构方向。第十四
        轮回读才找出真因：Biped 脚盒**不绕长度轴对称**（84.8% 在轴下方），而量截口的
        `_foot_link_box` 按上下各一半算，盒子被压成 1.53cm 的牙签板 —— 差 5.6cm 是量错高度，
        不是压平带来的。#39 按真实摆位重新量出高 8.7，斜轴容不下、平轴才容得下，俯仰又翻回
        压平；两个方向的取点仍然只有这一处能说事，所以这条测试守的是**取点与地面保护**，
        俯仰由 `MMD2MAX_FOOTBOXFLAT` 那条回滚分支管。
        地面保护是**斜度上限**：踝->球 不可能陡过 45 度，陡过就判「这根骨的 tail 是
        关节/踩地点约定」，退路仍是 #28 那条「宁可不转」。偏航一个字没动（两条分支的水平
        分量都是同一个向量），所以 #26 修好的外八不会被口径改动带跑。"""
        helper = _code(_between(SCRIPT, "fn mmd2maxBoxWantDir", "fn mmd2maxRotateBoneToDir"))
        self.assertIn('matchPattern name pattern:"* Foot"', helper)
        self.assertIn('matchPattern name pattern:"* Toe0"', helper)
        self.assertIn("if (not isFoot) and (not isToe) do return tailPos - headPos", helper,
                      "只有脚链特殊，其余骨仍是 PMX 结构方向")
        self.assertIn("if isToe and shoeTip != undefined do raw = shoeTip - headPos", helper,
                      "趾骨的来向是网格实测鞋尖，PMX 那根是竖直 stub，没有方向信息")
        self.assertIn("if horiz < 0.001 do return undefined", helper,
                      "水平分量读不出偏航 = 这根骨的方向不可信，宁可不转")
        self.assertIn("if (abs raw.z) > horiz do return undefined", helper,
                      "斜过 45 度判成踩地点约定，这条就是新的地面保护")
        self.assertIn("\n    raw\n)", helper,
                      "默认分支必须原样返回三维向量：缩放或换向量就会改掉偏航")
        self.assertNotIn("mmd2maxFlatDir", SCRIPT,
                         "压平只许留在 mmd2maxBoxWantDir 里当回滚分支，不许再养一个 helper")

    def test_foot_box_flat_comes_straight_from_the_cfg(self):
        """「翻一个词」落在导出侧 `FOOT_BOX_FLAT`（#40 之后轴俯仰不再跟着
        `FOOT_HEELBOX` 走），Max 这一侧只是接收端：值经 meta 的 `foot_box_flat`
        → MMD2MAX.CFG 的 `MMD2MAX_FOOTBOXFLAT` 原样到这里，链上**零次取反**，
        也没有「没注入头才取默认」那条分叉 —— M1b 起 CFG 读不到就直接中止。
        以前这里是 `if MMD2MAX_FOOTPITCH == undefined do … = true` + `if not …`，
        三条链连反三次，翻开关的人说不清最终摆哪根轴；单独 fileIn 这份 .ms 还会
        走「默认斜轴」而自动跑走「注入压平」，两边日志都绿。
        分支里必须**保住原长度**——复核表靠 `length want` 判短骨 stub，单位化会把脚
        误判成 stub（#28 当年就踩过）；而这个开关必须同时管住「写」和「读」，
        否则翻回去以后转角不转了、复核表却还按三维方向报超标。"""
        decl = _between(SCRIPT, "脚链骨盒的俯仰", "fn mmd2maxBoxWantDir")
        self.assertIn("MMD2MAX_FOOTBOXFLAT", decl,
                      "#40/#41 那段历史要写明现在的键名，别留在旧名 FOOTPITCH 上")
        self.assertNotIn("global MMD2MAX_FOOTBOXFLAT = true", SCRIPT,
                         "无条件赋值 = CFG 的值被踩掉，这个词就翻不动了")
        self.assertNotIn("MMD2MAX_FOOTBOXFLAT == undefined", SCRIPT,
                         "缺配置不许退回默认，必须中止（见 mmd2maxLoadCfg）")
        self.assertNotIn("MMD2MAX_FOOTPITCH", _code(SCRIPT),
                         "旧的三重取反链不许回来（注释里留名只作历史，代码里一处都不许有）")
        helper = _code(_between(SCRIPT, "fn mmd2maxBoxWantDir", "fn mmd2maxRotateBoneToDir"))
        self.assertIn(
            "if MMD2MAX_FOOTBOXFLAT do return [raw.x, raw.y, 0.0] * ((length raw) / horiz)",
            helper, "压平时要**且**保原长，这两件事是同一个表达式；极性直读，不许再取反")

    def test_foot_chain_continuity_readback_targets_the_real_child(self):
        """#40 的护栏：复核必须拿**回读到的子节点位置**当目标，而不是 `mmd2maxBoxWantDir`。

        第十五轮那一版全绿（`脚骨偏航结论 骨=0.0 趾=0.0`、段校验 4.8e-06、结构向 6 根
        里没有脚链），用户却一眼看见「Bip001 Toe0 的起始轴没接上 Bip001 Foot 的末端」：
        「画线/偏航」比的是写进去的那个方向本身 = 恒等式，压平之后它当然读 0 度。
        真正没被覆盖的量是**骨轴 vs 自己的子关节**（子关节被 段校验 钉在 PMX 球部，
        压不压平都不动）：平轴 29.93 度、斜轴 ~0 度。这是项目里那条老账
        「恒等式校验看不见偏航」的第二次踩坑，所以这条判据单独钉成用例。
        """
        decl = SCRIPT.index("local chainBad = 0")
        start = SCRIPT.index("local fAxes = mmd2maxBipedAxes fnode")
        block = _code(SCRIPT[start:SCRIPT.index("肢段长度复核")])
        self.assertIn("cnode.transform.translationpart", block,
                      "目标必须是回读到的子节点，不是 limbs.tsv 里我们自己写下去的落位")
        self.assertNotIn("mmd2maxBoxWantDir", block,
                         "再比一次同一个目标就又是恒等式，这条护栏就白加")
        self.assertIn("if a >= 0.0 and a < minAng do minAng = a", block,
                      "三根局轴取最接近的一根 = 最乐观读数，还敢报 >3 度才是真断了")
        self.assertIn("if minAng > MMD2MAX_DIRTOL do", block,
                      "阈值读 CFG 那一个数：留字面量 3.0 就成了第二把尺，"
                      "把 DIRTOL 扳到 999 回滚时 broken_roots 还停在旧读数上")
        self.assertNotIn("minAng > 3.0", block, "同上：这条判据的尺只许有一份")
        self.assertIn("chainBad = chainBad + 1", block)
        self.assertLess(decl, start, "计数器必须声明在循环外，否则每只脚都归零")
        self.assertIn("链向结论 断开=", block, "必须有一行总结，验收不用自己数脚")


    def test_foot_direction_falls_back_to_no_rotation(self):
        """鞋尖缺失 / 来向竖直 / 斜过 45 度时判据是 undefined：转角段整根跳过并留下一行
        「转角不做」，不能拿 undefined 去 length（会抛），也不能退化成没上限的三维方向。"""
        block = _code(_between(SCRIPT, "骨盒转角 pass（#27 的修法）", "转角 pass：接口="))
        self.assertIn("local want = mmd2maxBoxWantDir t rHead rTail (mmd2maxShoeTip limbs t)",
                      block)
        self.assertIn("转角不做 ", block, "跳过必须留一行，否则读日志的人以为漏了一根骨")
        self.assertIn("脚链方向读不出（鞋尖缺失、来向竖直或斜过 45 度）", block)
        self.assertNotIn("local want = rTail - rHead", block,
                         "转角段自己重算三维方向 = 绕开斜度上限")
        self.assertNotIn("if (length undefined", block)

    def test_all_three_foot_checks_use_the_same_direction(self):
        """转角、结构向复核、画线复核必须共用 mmd2maxBoxWantDir：判据不一致就会出现
        「脚修平了，复核表却报它超标」，下一轮就会照着那个数把地又踩穿。"""
        for start, end, needle in (
            ("骨盒转角 pass（#27 的修法）", "转角 pass：接口=", "mmd2maxBoxWantDir t"),
            ("===== 结构方向复核", "结构向 复核：可测=", "mmd2maxBoxWantDir t"),
            # 终止标记取段校验那行**代码**：注释里也会引用日志原文（#38 的函数文档
            # 就抄了一行 `段校验 ...`），拿 "段校验 " 当标记会切到文件前半段去。
            ("脚骨「画出来的朝向」复核", 'local smsg = "  段校验 "', "mmd2maxBoxWantDir fName"),
        ):
            self.assertIn(needle, _code(_between(SCRIPT, start, end)), start)
        struct = _code(_between(SCRIPT, "===== 结构方向复核", "结构向 复核：可测="))
        self.assertIn("local stubBone = (distance headPos tailPos) < MMD2MAX_STUBCM", struct,
                      "短骨判定仍按 PMX 真实段长，不能被压平后的长度顶替")

    def test_lean_and_wrap_helpers(self):
        lean = _between(SCRIPT, "fn mmd2maxLeanDeg", "fn mmd2maxWrap180")
        self.assertIn("atan2 v.y v.z", lean, "矢状面倾角 = atan2(Y, Z)：+ 是后仰")
        self.assertIn("sqrt (v.y * v.y + v.z * v.z)", lean)
        self.assertIn("return -999.0", lean)
        wrap = _between(SCRIPT, "fn mmd2maxWrap180", "fn mmd2maxLogStructDir")
        self.assertIn("if r > 180.0 do r = r - 360.0", wrap)
        self.assertIn("if r <= -180.0 do r = r + 360.0", wrap)

    def test_struct_check_runs_after_leaving_figure_mode(self):
        self.assertGreater(SCRIPT.index("===== 结构方向复核"),
                           SCRIPT.index("mmd2maxFigureMode root.controller false"))


class LengthAxisContract(unittest.TestCase):
    """规则 28：转角「量哪根轴」必须是实测长度轴，不能现场挑最顺的那根。

    第七轮的账：`转角 pass：接口=bipedRotation 累计尝试=45 改善=45` 全绿，
    而左手四根指节在 2.86~6.39cm 之外 —— 因为门控、写入、复核比的是同一根
    「目前最顺的轴」，那个数被对齐到 0.79 度时，长度轴刚好被转到 89.26 度。
    三处必须一起改，改一处就等于让门控和复核互相说谎。"""

    def test_gate_measures_length_axis_when_recorded(self):
        gate = _code(_between(SCRIPT, "fn mmd2maxStructErr", "fn mmd2maxBoxWantDir"))
        self.assertIn("local la = mmd2maxLenAxisOf node.name", gate)
        self.assertIn("if la >= 1 and la <= 3 do", gate)
        self.assertIn("return (acos dl)", gate,
                      "有记账就只量那一根，别再把三根里的最小值当方向达标")
        self.assertIn("local best = 180.0", gate,
                      "没记账的骨（Pelvis 三子 / Hand 五子 / 叶子）必须退回老规则，"
                      "否则躯干与骨盆被一根没测准的轴带走")

    def test_write_targets_the_same_axis_as_the_gate(self):
        rot = _code(_between(SCRIPT, "fn mmd2maxRotateBoneToDir", "fn mmd2maxDumpBipedApi"))
        self.assertIn("local la = mmd2maxLenAxisOf node.name", rot)
        self.assertIn("bestAx = la", rot,
                      "写入挑的轴若与门控不同根，就会出现「判据说不达标、写下去却转的是另一根」")
        self.assertIn("bestDot = dot axes[la] want", rot)

    def test_length_axis_is_recorded_from_the_pre_placement_segment_fit(self):
        fit = _code(_between(SCRIPT, "fn mmd2maxFitSegments", "fn mmd2maxPlaceBipedNodes"))
        self.assertIn("MMD2MAX_LENAXIS_MAP = #()", fit,
                      "同一个 Max 会话里重跑必须清账，上一轮的骨名会冒充这一轮的")
        self.assertIn("append MMD2MAX_LENAXIS_MAP #(pnode.name, usedAxis)", fit,
                      "键是 Max 节点名：场景里已有同名 Biped 时节点会被改名，"
                      "拿 PMX 骨名当键永远查不中")
        self.assertIn("if err <= 0.05 do", fit, "没收敛的 usedAxis 是试出来的假轴")
        self.assertIn("if usedAxis >= 1 and usedAxis <= 3 do", fit,
                      "轴=4 是三轴等比例，它没指出长度在哪根轴上")
        self.assertIn("if kids.count == 1 and kids[1] == cnode do", fit,
                      "只给「恰好一个子关节」的骨记账：Pelvis 三子 / Hand 五子")
        self.assertIn("try ( kids = pnode.children ) catch ( kids = #() )", fit)

    def test_the_length_axis_comes_from_a_coupling_probe(self):
        """#77：长度轴 = 缩 5% 之后把子关节推得最远的那根轴，不是「试到收敛」的那根。

        第 44 轮海灯节那行 `肢段 Bip001 L Foot -> Bip001 L Toe0 … 轴=1 误差=0.0475168`：
        scale.x 8.86744->9.82177（+10.8%）只换来父→子距离 +4.2% —— 一根**宽轴**因为
        初始长度本来就离目标只差 4.5%，被 residual 门槛（0.05cm）当场收下。香菱同一段
        是 `轴=2`（默认轴 1 不收敛、重试才找到 2），所以这句话在两具模型上给出两个答案
        的那一刻，「收敛」就不再是认轴的判据。#75 的盒账又把这笔错账传给 足背 / 接趾 / 回读，
        于是脚盒带着 23:77 脚跟不对称的那根长边朝小腿上方长（端面覆盖=false 那两行）。
        """
        self.assertLess(SCRIPT.index("fn mmd2maxCoupledAxis"),
                        SCRIPT.index("fn mmd2maxFitSegments"),
                        "前向引用陷阱：探针必须在 肢段 之前定义")
        probe = _code(_between(SCRIPT, "fn mmd2maxCoupledAxis", "fn mmd2maxFitSegments"))
        self.assertIn("mmd2maxApplyScale pnode ax 1.05 method", probe,
                      "三根轴都要被同一个比例碰一次，只比谁收敛就是回到旧判据")
        self.assertIn("abs ((mmd2maxSegLen pnode cnode) - base) / base", probe,
                      "增益必须按原距离归一，否则长骨天然比短骨「耦合强」")
        self.assertIn("mmd2maxSetScale pnode s0 method", probe,
                      "探完不复原就等于用探针改了几何")
        self.assertIn("if base < 0.0001 do return #(0, 0.0, 0.0, 0.0, 0.0)", probe,
                      "读不到基准长度时轴号必须是 0，让调用方退回默认轴")
        fit = _code(_between(SCRIPT, "fn mmd2maxFitSegments", "fn mmd2maxPlaceBipedNodes"))
        self.assertIn("local probe = mmd2maxCoupledAxis pnode cnode method", fit)
        self.assertIn("if probe[1] > 0 do usedAxis = probe[1]", fit,
                      "探针读到轴就必须拿它做首试轴，只记日志不改写入等于没修")
        self.assertIn("mmd2maxFitOnePair pnode cnode desired usedAxis method", fit)
        self.assertIn("if best[1] > 0.05 and ax != usedAxis do", fit,
                      "逐轴重试要跳过探针刚试过的那根，否则同一根轴缩两遍")
        self.assertIn('探针=" + (probe[1] as string)', fit,
                      "探针轴要落进日志：轴=1 到底是长度轴还是被蒙过去的宽轴，现场必须能看出来")
        self.assertIn("增益=", fit)
        self.assertIn("0.0475", _between(SCRIPT, "fn mmd2maxCoupledAxis",
                                         "fn mmd2maxFitSegments"),
                      "把海灯节那个压线过关的误差留在原地，否则下一个人会把门槛当成判据")

    def test_lookup_helper_is_defined_before_its_callers(self):
        """#20 那条前向引用陷阱的回归位：这条规则一落地就把 helper 挪到了 mmd2maxBipedAxes
        后面，结果 check_ms 立刻报 mmd2maxLogStructDir 引用了 30 行之后才定义的它。"""
        self.assertLess(SCRIPT.index("fn mmd2maxLenAxisOf"),
                        SCRIPT.index("fn mmd2maxLogStructDir"))
        self.assertLess(SCRIPT.index("fn mmd2maxLenAxisOf"),
                        SCRIPT.index("fn mmd2maxStructErr"))

    def test_rule_has_no_dead_switch_and_exactly_three_readers(self):
        """M1b：`MMD2MAX_LENAXIS` 那个开关删了 —— 没有任何一条路径把它写成 false，
        留着它就等于养着一条从没跑过、也就从没验过的分支（三处消费点各判一次「开关关」
        还要各写一份退路）。现在规则本身是无条件的，只剩「读表的地方只许一个」这条账。"""
        self.assertNotIn("global MMD2MAX_LENAXIS = true", SCRIPT)
        helper = _code(_between(SCRIPT, "fn mmd2maxLenAxisOf", "fn mmd2maxLogStructDir"))
        self.assertNotIn("if not MMD2MAX_LENAXIS do return 0", helper,
                         "死分支不许回来：要关掉这条规则就改判据本身，别留一条没人走的旁路")
        self.assertIn("M1b 删掉了 MMD2MAX_LENAXIS",
                      _between(SCRIPT, "fn mmd2maxLenAxisOf", "fn mmd2maxLogStructDir"),
                      "删除的理由要写在原地，否则下一个人会把它当漏配的开关补回来")
        self.assertEqual(SCRIPT.count("mmd2maxLenAxisOf node"), 4,
                         "门控 / 写入 / 复核 / 盒账写入四处共用一个查表，别在第五处再判一次名字")

    def test_the_box_axis_is_written_once_and_read_from_the_record(self):
        """#75：盒子「沿哪根轴缩的」是一次事实，不是每次现场重挑。

        第 44 轮（香菱-海灯节）15 行 FAIL 里 10 行的根因：肢段给 Foot 记的长度轴是 1，
        而足背收截面按现场方向判成 2 ⇒ 「高=8.7534」写在了真正的长度轴上，14.82 那根
        边被当成长度留在原地没碰；最后的骨盒回读又按现场方向判一次，报出来三根边
        **数字全对、轴名全错**，左右脚还各错一套（L 长=3.4674 / R 长=0.5722）。
        香菱不显形纯属巧合：那具模型的脚骨在摆位前后长度轴恰好都是 2。
        """
        fit = _code(_between(SCRIPT, "fn mmd2maxFitCrossSection", "fn mmd2maxLengthAxisIndex"))
        dims = _code(_between(SCRIPT, "fn mmd2maxBoxDims", "fn mmd2maxReportBoxDims"))
        self.assertIn("local want = mmd2maxLenAxisOf node.name", fit,
                      "写入侧不认肢段那笔操作账，就会把截面缩在长度轴上")
        self.assertIn("if want > 0 do ai = want", fit, "没账的骨（叶子骨）仍要能退回现场方向")
        self.assertIn("mmd2maxRememberBoxAxis node.name ai", fit,
                      "写完不记账 = 回读只能自己再判一次，判错就是「数字对、标签错」")
        self.assertIn("local pinned = mmd2maxBoxAxisOf node.name", dims,
                      "回读侧必须先认盒账，它验的是「我们写进去的那只盒子」")
        self.assertIn("if pinned > 0 do ai = pinned", dims)
        # 两份账不许合成一份：MMD2MAX_LENAXIS_MAP 只记「恰好一个子关节」的骨（Toe0 是
        # 叶子骨进不去），而「盒子写在哪儿」与「转角该转哪根轴」是两个问题。
        self.assertNotIn("MMD2MAX_LENAXIS_MAP", dims,
                         "回读拿转角的账当盒账：叶子骨永远查不中，还悄悄改了 #28 的口径")
        self.assertLess(SCRIPT.index("global MMD2MAX_BOXAXIS_MAP = #()"),
                        SCRIPT.index("fn mmd2maxFitCrossSection"),
                        "#20 的前向引用陷阱：global 与两个 helper 必须排在读写两侧之前")
        self.assertLess(SCRIPT.index("fn mmd2maxBoxAxisOf"),
                        SCRIPT.index("fn mmd2maxBoxDims"))

    def test_the_span_ruler_does_not_read_the_axis_record(self):
        """#79：`span_want_cm` 必须是 box_rb 里**唯一不碰轴记录**的那把尺。

        上面那条 #75 的规矩把「长/宽/高」三行钉在盒账上，代价是这套读数从此自指：
        盒账说哪根是长度轴，`length_cm` 就报那根边的长度，于是「薄边有没有朝着骨指的
        方向」这句话在里面**问不出来**（第 48 轮的趾板：长=0.5722 报在竖直轴上，
        box_rb 全绿，用户看得见的那只鞋尖戳出去 3.47cm）。唯一的解药是再加一把不看记录
        的尺 —— 所以这里钉的不是「有没有这行」，而是「这行到底有没有偷看记录」：
        一旦它调了 `mmd2maxBoxAxisOf` / `mmd2maxWidthHeightAxes` 里的任何一个，
        它就退化成第四行自指读数，而这组测试照样全绿。
        """
        self.assertLess(SCRIPT.index("fn mmd2maxWorldSpanAlong"),
                        SCRIPT.index("fn mmd2maxBoxDims"),
                        "前向引用陷阱：跨度尺必须排在用它的那行之前")
        span = _code(_between(SCRIPT, "fn mmd2maxWorldSpanAlong", "fn mmd2maxBoxDims"))
        for forbidden in ("mmd2maxBoxAxisOf", "mmd2maxLenAxisOf",
                          "mmd2maxWidthHeightAxes", "mmd2maxBipedAxis"):
            self.assertNotIn(forbidden, span,
                             f"跨度尺读了 {forbidden}：它又变成一把按记录挑轴的尺，自指回来了")
        self.assertIn("nodeGetBoundingBox node node.transform", span,
                      "角点必须来自包围盒本身，不是某个轴号下的本地跨度")
        # 本地角点搬到世界只许乘旋转：这个包围盒**已经带着一份 #scale**（脚骨的
        # #scale 本身就是厘米数，[8.87, 14.27, 7.33]），整块乘 node.transform 等于缩两遍，
        # 量出来的跨度会大一个数量级而不是差一点 —— 差一点的错能被闸拦住，大一个数量级
        # 的错如果忘了夹住就只会让这一行永远红，然后被人当噪声删掉。
        self.assertIn("mmd2maxBipedAxes node", span,
                      "不转世界就量不到「骨朝哪儿」：这根骨转了多少度必须体现在跨度里")
        self.assertNotIn("node.transform *", span,
                         "整块乘 transform 会把 #scale 乘第二遍（包围盒已经是缩过的）")
        self.assertIn("local len = (length dir)", span)
        self.assertIn("if len < 0.0001 do return undefined", span,
                      "dir 读不到长度时必须报「没量到」，不能拿零向量投出 0")
        self.assertIn("local u = dir / len", span,
                      "调用方给的 mmd2maxBoxWantDir 三条返回路径全都不是单位向量，"
                      "不自己归一就量成「跨度 × |dir|」")
        self.assertLess(span.index("local u = dir / len"), span.index("local a1 ="),
                        "归一必须在投影之前，投完再归一等于用错方向的数")
        # 闭式：三根棱长各乘自己与 dir 的 |cos|。括号是判据的一部分 —— MAXScript 在
        # 表达式里会把 `abs a1 * e1 + …` 整段吞成 abs 的参数，那样符号能互相抵掉。
        self.assertEqual(span.count("(abs a1) * (mx.x - mn.x)"
                                   " + (abs a2) * (mx.y - mn.y)"
                                   " + (abs a3) * (mx.z - mn.z)"), 1,
                         "投影只许按参考方向做一次、三根轴全进；漏一根就不是跨度，"
                         "多算一次方向就又引入了一条自选口径")
        self.assertNotIn("for sx in", span,
                         "又展开成角点循环就等于把同一个闭式写两遍，两处会分叉")
        report = _code(_between(SCRIPT, "fn mmd2maxReportBoxDims", "fn mmd2maxVerifyBoxes"))
        self.assertIn('mmd2maxReport "box_rb" "span_want_cm" subject span', report)
        self.assertEqual(SCRIPT.count('mmd2maxReport "box_rb" "span_want_cm"'), 1,
                         "同一行读数有两个作者：拼表时后写的那条会盖掉前一条")


class MorphChannelContract(unittest.TestCase):
    """#24/#25：通道号要连号、通道名要真表情名、自检要看硬证据而不是容量。"""

    @staticmethod
    def _morph_block():
        return _between(SCRIPT, "===== 顶点变形（表情）", "===== 物理刚体")

    def test_channel_index_is_a_running_counter(self):
        """WM3_NumberOfChannels 是容量（实测 61 个表情报 6200），
        拿它 +1 当索引会把表情写到 101/201/… 上，1..8 号通道全空。"""
        block = self._morph_block()
        self.assertIn("local chIdx = built + 1", block)
        self.assertNotIn("WM3_NumberOfChannels morpherMod) + 1", _code(block),
                         "旧写法：用容量推通道号，表情全落在空索引上")

    def test_destructive_cleanup_needs_hard_evidence(self):
        """拆修改器只看 built == 0（两个接口逐个抛异常）；读不回通道只警告 ——
        读接口本身通不通没探过，拿它当判据会把能用的 Morpher 拆掉。"""
        block = self._morph_block()
        self.assertIn("if built == 0 do", block)
        self.assertNotIn("if chUsed == 0 and chNamed == 0 do\n    (", block)
        self.assertIn("Morpher 警告", block)

    def test_capacity_is_no_longer_the_verdict(self):
        block = self._morph_block()
        code = _code(block)
        self.assertNotIn("if chTotal < blocks.count", code, "容量判据恒不成立 = 没有自检")
        self.assertIn("有目标=", block, "日志要把 chUsed/chNamed 打出来，否则又是假成功")
        self.assertIn("通道容量=", block)

    def test_channel_name_comes_from_the_renamed_target(self):
        """#25：BuildFromNode 拿目标节点名当通道名（实测读回 default001..061），
        而 WM3_SetChannelName 静默不生效 —— 所以改名要在建通道之前，用普通属性。"""
        block = self._morph_block()
        self.assertIn("try ( target.name = name )", block)
        self.assertLess(block.index("target.name = name"),
                        block.index("WM3_MC_BuildFromNode morpherMod chIdx target"),
                        "副本必须在建通道之前就带上表情名")
        self.assertNotIn("WM3_SetChannelName", _code(block),
                         "实测 61/61 抛异常（08:20 轮 改名接口抛异常=61），是死调用，别再加回来")

    def test_named_counter_requires_the_exact_morph_name(self):
        """「有名」按非空判会被 default001 蒙过去 —— 必须逐个和 blocks[k][1] 比。"""
        block = self._morph_block()
        self.assertIn("if nm == want do chNamed += 1", block)
        self.assertNotIn('nm != "- empty -" do chNamed', _code(block))
        self.assertIn("Morpher 通道名对不上", block)


class MaxScriptSyntaxContract(unittest.TestCase):
    def test_try_is_never_used_as_a_value(self):
        """`local x = try (...) catch (...)` 在 Max 2022 里会把整轮装配打死。

        只看代码：这条规则 itself 记在 .ms 的注释里（「为什么改成语句形式」），
        拿全文去撞就成了「注释写得越清楚越判红」——那会逼人删掉最值钱的那几行。
        """
        hits = re.findall(r"local\s+\w+\s*=\s*try\s*\(", _code(SCRIPT))
        self.assertEqual(hits, [], f"改用语句形式的 try/catch + 标志位：{hits}")

    def test_comma_call_form_is_never_used(self):
        """MAXScript 没有 `f(a, b)`：实测 `-- Syntax error: at ,, expected <factor>`，
        编译期就死，整份脚本一行都不跑。截图路径必须保持空格分隔的裸参表。"""
        self.assertEqual(check_ms.check_comma_call(SCRIPT), [],
                         "模板里出现了逗号实参表")
        shot = _between(SCRIPT, "fn mmd2maxScreenshot =", "fn mmd2maxPrepareScene")
        self.assertIn('mmd2maxShotOne outDir baseName "front" #view_front', shot)
        self.assertIn('mmd2maxShotOne outDir baseName "top" #view_top', shot)
        self.assertIn("mmd2maxSaveDib bmp shotFile",
                      _between(SCRIPT, "fn mmd2maxShotOne", "fn mmd2maxScreenshot ="))

    def test_comma_call_rule_rejects_the_bad_form(self):
        broken = 'try ( mmd2maxShotOne(outDir, baseName, "front") )\n'
        self.assertTrue(check_ms.check_comma_call(broken),
                        "静态体检要拦住逗号实参表")
        # 单参数的 f(x) 只是分组括号，合法；字符串里的逗号也不算实参分隔符
        self.assertEqual(check_ms.check_comma_call('mmd2maxLog ("a, b")\n'), [])

    def test_optional_param_syntax_is_never_used(self):
        """13:06 那轮 `[skipDir:false]` 让 2900 行脚本一行没跑：`-- Syntax error: at [,
        expected =`（解析器把 false 当类型名）。这类错误弹窗只给标题、日志一个字不写，
        所以必须在静态体检里拦，而不是等下一轮实跑。
        第 34 轮又烧了一次同一个坑的**另一个拼法**：`fn mmd2maxShotOne ... /tight:false =`
        →「Syntax error: at /, expected =」（解析探针 probe_parse.txt 拿到的原文），
        同样整份脚本不跑、日志与 error.txt 全空。两种拼法都要拦。
        """
        self.assertEqual(check_ms.check_optional_params(SCRIPT), [],
                         "模板里又用了 MAXScript 可选参数")
        broken = "fn placeBiped nodes passList [skipDir:false] =\n(\n    0\n)\n"
        self.assertTrue(check_ms.check_optional_params(broken),
                        "静态体检要拦住 fn 参数表里的 [name:value]")
        # 位置参数写法不许误伤；数组字面量出现在函数体里也不算
        self.assertEqual(check_ms.check_optional_params(
            "fn placeBiped nodes passList skipDir =\n(\n    local v = [0, 0, 1]\n    v\n)\n"), [])
        # 斜杠拼法：参数表里出现 `/` 就是这条死路
        slashed = "fn shotOne outDir tag lo hi /tight:false =\n(\n    0\n)\n"
        hits = check_ms.check_optional_params(slashed)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("at /", hits[0])
        # 调用点的关键字实参（fileName:x）没有斜杠，是本仓库实测跑得通过的写法，不许误伤；
        # 函数体里的除法也不算参数表。
        self.assertEqual(check_ms.check_optional_params(
            'fn saveDib bmp shotFile =\n(\n    save bmp fileName:shotFile\n'
            '    local half = shotFile.count / 2\n    half\n)\n'), [])

    def test_depth_rule_catches_a_swallowed_fn(self):
        """括号总数配平 ≠ 嵌套正确：少一个 `)` 会把后面的 fn 卷进上一个函数体，
        顶层调用就报「Call needs function or class, got: undefined」。"""
        broken = "fn outer =\n(\n    local a = 1\nfn inner x =\n(\n    x\n)\n)\n"
        self.assertTrue([h for h in check_ms.check_fn_depth(broken) if "inner" in h],
                        "静态体检要能拦住被吞掉的顶层函数")
        self.assertEqual(check_ms.check_fn_depth(SCRIPT), [],
                         "模板里每个顶格 fn 都必须在深度 0")

    def test_no_forward_reference_in_the_template(self):
        """裸调在**解析期**绑定：调用者排在被调者前面，运行期就拿 undefined 去 apply。"""
        self.assertEqual(check_ms.check_forward_reference(SCRIPT), [],
                         "函数体引用了定义在后面的顶层函数")
        broken = ('fn mmd2maxShotOne outDir =\n(\n    mmd2maxLog outDir\n)\n\n'
                  'fn mmd2maxLog message =\n(\n    format "%" message\n)\n')
        self.assertTrue(check_ms.check_forward_reference(broken),
                        "静态体检要拦住前向引用")
        # 注释/字符串里的提及不算引用（历史上「注释里提到 mmd2maxMain」曾被误报）
        quiet = ('fn a x =\n(\n    /* 另有 mmd2maxFitPelvis 闭环 */\n'
                 '    local s = "mmd2maxFitPelvis"\n    x\n)\n\n'
                 'fn mmd2maxFitPelvis =\n(\n    true\n)\n')
        self.assertEqual(check_ms.check_forward_reference(quiet), [],
                         "注释与字符串里的函数名不该报警")

    def test_log_helpers_come_before_the_screenshot_functions(self):
        """截图段「Call needs function or class, got: undefined」的真凶就是顺序。"""
        log_at = SCRIPT.index("fn mmd2maxLog message =")
        for caller in ("fn mmd2maxSaveDib", "fn mmd2maxShotOne", "fn mmd2maxScreenshot ="):
            self.assertLess(log_at, SCRIPT.index(caller),
                            f"{caller} 排在 mmd2maxLog 之前的话，函数体第一行日志就炸")
        for helper in ("fn mmd2maxJsonStr", "fn mmd2maxInitLog"):
            self.assertLess(SCRIPT.index(helper), SCRIPT.index("fn mmd2maxSceneBox"),
                            f"{helper} 也必须排在所有调用者之前")


class FitPriorityLoopContract(unittest.TestCase):
    """机制层：方向与落位互相打架时，取舍写在一张表里，摆位/转角变成收敛循环。

    为什么要有这一组测试：#26/#27/#28/#29 每一轮的修法都是「加一个特例分支」
    （脚链单独判据、指链单独门控、锁骨单独回滚），分支之间还会互相吃。这张表
    （mmd2maxChainPriority）是那次汇总之后换来的结构：新冲突改数据，不改控制流。
    测试钉的是「控制流里不许再长出按骨名的特例」，不是钉某个具体返回值 ——
    返回值本来就允许随实测翻面（指链从 #both 改成 #dir 就是 12:04 的实测推的）。
    """

    def setUp(self):
        self.prio = _code(_between(SCRIPT, "fn mmd2maxChainPriority", "fn mmd2maxRayFromParent"))
        self.place = _code(_between(SCRIPT, "fn mmd2maxPlaceBipedNodes",
                                    "fn mmd2maxHipMean root ="))
        self.rot = _code(_between(SCRIPT, "fn mmd2maxRotatePass", "fn mmd2maxLocalExtent"))
        self.loop = _code(_between(SCRIPT, "摆位 <-> 转角 收敛循环（机制层）",
                                   "mmd2maxFigureMode root.controller false"))

    def test_priority_table_is_the_only_per_bone_branch(self):
        """骨名判断只许出现在取舍表里；摆位函数与转角 pass 只问「这根骨保什么」。"""
        self.assertIn('if matchPattern name pattern:"* Finger*" do return #dir', self.prio)
        self.assertIn('if matchPattern name pattern:"* Foot" do return #dir', self.prio)
        self.assertIn('if matchPattern name pattern:"* Toe0" do return #dir', self.prio)
        self.assertIn('if matchPattern name pattern:"* Clavicle" do return #pos', self.prio)
        # #33 #34：PMX 的 手首/頭 尾巴是显示用 stub（0.38~3.26cm），按它转就把好端端的
        # 腕/头甩歪 —— 判据与锁骨同一条「转角写不进去/转了就错」，所以进同一组。
        self.assertIn('if matchPattern name pattern:"* Hand" do return #pos', self.prio)
        self.assertIn('if matchPattern name pattern:"* Head" do return #pos', self.prio)
        self.assertEqual(self.prio.count("return #pos"), 3,
                         "保落位的骨只有锁骨/腕/头三组，多出来的一律先补实测再进表")
        self.assertIn("    #both\n)", self.prio, "默认值必须是「两个都要」，漏网的骨不许被跳过")
        for block, who in ((self.place, "摆位"), (self.rot, "转角")):
            self.assertEqual(block.count("matchPattern"), 0,
                             f"{who} 里又长出按骨名的特例分支了，判据应当只在取舍表里")
        self.assertIn("mmd2maxChainPriority targetName", self.place)
        self.assertIn("mmd2maxChainPriority t", self.rot)

    def test_direction_priority_bones_survive_the_replacement(self):
        """#dir 的骨在循环内的重摆那一遍整根跳过（12:04：落位只换回 1.4cm，方向却从
        3.5 变 5.63），但收尾必须摆它 —— 摆完再单独转一遍把方向补回来。
        「跳过」只在中间遍成立，当成最终状态就是 13:11 的 L Finger32 pivot偏差=10.66。"""
        self.assertIn("if skipDir do", self.place)
        self.assertIn("== #dir do continue", self.place)
        self.assertIn("passList true", self.loop, "循环里的重摆要带这个开关")
        whole = _code(SCRIPT)
        self.assertEqual(whole.count("mmd2maxPlaceBipedNodes root lines rowIds targetNames passList"),
                         5, "1 个定义 + 4 个调用点，每个调用点都必须显式传开关"
                         "（skipDir 是位置参数，漏传 = 运行期 Type error）")
        self.assertEqual(whole.count("passList false"), 3,
                         "全摆的三处：首摆、骨盆闭环后的重摆、收尾")
        self.assertEqual(whole.count("passList true"), 1,
                         "跳过 #dir 只许留在循环内部那一遍；收尾再跳过就等于回到 10cm 指链")

    def test_position_priority_bone_is_never_rotated(self):
        """b 方案：锁骨两轮实测转角写不进去（6.60331->6.60331 后回滚），继续追方向就是
        拿肩点跟 Biped 抢 —— 用户看到的「手臂错位」。跳过要留一行日志，
        否则分不清「没试」和「试了没动」。"""
        self.assertIn("local prio = mmd2maxChainPriority t", self.rot)
        self.assertIn("if prio != #pos do", self.rot)
        self.assertIn("转角不做 ", self.rot)
        self.assertIn("落位优先（Biped 约束骨，转角写不进去）", self.rot)

    def test_final_rotation_pass_touches_only_direction_bones(self):
        """收尾转角是全摆之后唯一的「最后一道写」，只许动 #dir 的骨：#both/#pos 就此停在
        落位那一侧（2026-11-02 用户定：Forearm/Calf 保持 #both，认下骨盒方向那 3.4 度，
        不要再把手腕甩开 1cm）。开关是位置参数，两个调用点都得显式传。"""
        self.assertIn("fn mmd2maxRotatePass root lines rowIds targetNames limbs rotWay passNo onlyDir =",
                      self.rot)
        self.assertIn("if (not onlyDir or prio == #dir) do", self.rot)
        whole = _code(SCRIPT)
        self.assertEqual(whole.count("mmd2maxRotatePass root lines rowIds targetNames limbs rotWay"), 3,
                         "1 个定义 + 2 个调用点（循环内一遍、收尾一遍），漏传 onlyDir = 运行期 Type error")
        self.assertEqual(whole.count("rotWay passNo false"), 1)
        self.assertIn("(lastPass + 1) true", self.loop,
                      "收尾那遍必须只转 #dir；写成 false 就等于把 #both 的骨再甩一次")

    def test_upperarm_lands_on_the_clavicle_ray(self):
        """肩点的可达集合是「锁骨 pivot + 骨盒长度轴 × 段长」，硬按 PMX 摆会被拽回来，
        这个拉扯才是 Forearm 1.48 / Hand 1.01 跟着偏的原因。认了射线，肘/腕才摆得准。"""
        self.assertIn('matchPattern name pattern:"* UpperArm"',
                      _code(_between(SCRIPT, "fn mmd2maxRayFromParent",
                                     "fn mmd2maxShoulderRayTarget")))
        self.assertIn('local clavName = substituteString actual "UpperArm" "Clavicle"', self.place)
        self.assertIn("local rayTgt = mmd2maxShoulderRayTarget clavNode pos", self.place)
        self.assertIn("if rayTgt != undefined do", self.place)
        self.assertIn("摆位按骨盒射线 ", self.place, "换目标必须留痕：PMX肩点/射线肩点/差 三个数并排")
        self.assertIn("轴夹角=", self.place,
                      "只有「差」分不清 0.477→0.955 这一路是轴在转还是段长在变（差 ≈ 2·段长·sin(半角)）")
        self.assertIn("pos = rayTgt", self.place)

    def test_ray_target_keeps_pmx_length_and_refuses_to_guess(self):
        """射线目标 = 沿骨盒长度轴走「PMX 肩点到锁骨 pivot 的原长」（骨长由
        mmd2maxFitSegments 负责，这里不顺手改）。读不到节点/轴、或目标与 pivot 重合时
        返回 undefined = 调用方用回 PMX 原目标，绝不返回一个猜出来的点。"""
        helper = _code(_between(SCRIPT, "fn mmd2maxShoulderRayTarget", "fn mmd2maxFitScore"))
        self.assertIn("if clavNode == undefined do return undefined", helper)
        self.assertIn("if seg < 0.001 do return undefined", helper)
        self.assertIn("if ax == undefined do return undefined", helper)
        self.assertIn("base + ax[2] * seg", helper)
        self.assertIn("local ax = mmd2maxBipedAxis clavNode toTarget", helper,
                      "长度轴必须按参考方向挑（脚骨的长轴在 Y 上，写死 X 会原地转 90 度）")

    def test_loop_is_bounded_and_reports_every_round(self):
        """停止条件 = **场景读数**达标（方向超标为 0），不是「这一遍没有骨需要转」。

        遍数上限来自 CFG（`MMD2MAX_CONVERGE_MAX`，数值住在 mmd2max/passes.py 的注册表），
        防 A 的转角甩开 B、B 的重摆又甩开 A 的来回。每遍三个数必须打出来，循环停下来
        之后还必须把「跑了几遍 / 为什么停 / 判据到底达没达标」报进判据通道 —— 否则
        下一轮读日志分不清「收敛了」「撞上限了」「压根没跑」：第十七轮就是撞上限退出，
        而日志那句 `收敛 第3遍: …` 和真达标完全同形。
        """
        self.assertIn("for passNo = 1 to MMD2MAX_CONVERGE_MAX do", self.loop)
        self.assertIn("local rp = mmd2maxRotatePass root lines rowIds targetNames limbs rotWay passNo",
                      self.loop)
        self.assertNotIn("if rp[1] == 0 do break", self.loop,
                         "出口又回到写入侧的自述了：尝试数可以为 0 而场景根本没达标")
        self.assertIn("if (mmd2maxConvergeGate sc[1]) do", self.loop)
        self.assertIn('convergeExit = "gate"', self.loop)
        self.assertIn('local convergeExit = "ceiling"', self.loop,
                      "默认出口必须是「撞上限」：忘了改写出口就等于宣称收敛了")
        for name in ("iters", "exit_reason", "gate_satisfied"):
            self.assertIn(f'mmd2maxReport "converge" "{name}" "-"', self.loop,
                          f"少了 {name} 这一行，Python 侧的 audit_gate 就没法和遍数/出口对账")
        self.assertIn("收敛 第", self.loop)
        self.assertIn("方向超标=", self.loop)
        self.assertIn("落位偏差和=", self.loop)
        self.assertIn("落位最大=", self.loop)
        self.assertIn("转角 pass：接口=", self.loop, "累计汇总还得在，跨轮对比靠它")

    def test_loop_score_shares_the_direction_judgement(self):
        """打分与转角 pass 必须同源（mmd2maxBoxWantDir）：两处判据不一致时，循环会围着一个
        复核表不认的目标收敛。它也不许打逐骨日志 —— 逐骨读数是复核表的活。"""
        helper = _code(_between(SCRIPT, "fn mmd2maxFitScore", "fn mmd2maxRotatePass"))
        self.assertIn("local sWant = mmd2maxBoxWantDir t sHead sTail (mmd2maxShoeTip limbs t)", helper)
        self.assertIn("if (mmd2maxStructErr sNode (normalize sWant)) > MMD2MAX_DIRTOL do badDir += 1", helper)
        self.assertIn("if (length sWant) >= 1.0 do", helper, "短骨 stub 不许参与方向打分")
        self.assertEqual(helper.count("mmd2maxLog"), 0, "循环内打分只出数，别把逐骨日志刷两遍")
        self.assertIn("#(badDir, sumPos, maxPos)", helper)

    def test_score_is_declared_relative_not_authoritative(self):
        """Figure 模式内的读数和退出后的复核表对不上是实测过的（锁骨 6.60 / 13.64）。
        这句话必须写在注释里，否则下一个人拿循环分数去对复核表，会当成回归。"""
        raw = _between(SCRIPT, "fn mmd2maxFitScore", "fn mmd2maxRotatePass")
        self.assertIn("退出 Figure 模式之后", raw)
        self.assertIn("只在轮与轮之间可比", raw)

    def test_direction_tolerance_is_one_number_shared_by_three_sites(self):
        """方向容差同时决定「要不要转这根骨」「循环算不算收敛」「复核表报几条超标」。
        三处各写一个字面量时，改一处就悄悄分家：收敛判据（尝试=0 ⇒ 方向全部达标）不再
        成立，复核表的条数也不能跟循环里的分数对照 —— 所以只许有一个 global，方向判据
        里不许再出现写死的度数。（骨长倍率限幅那个 `f > 5.0` 与方向无关，别误伤。）"""
        code = _code(SCRIPT)
        # M2：数值不再住在脚本里（住在 mmd2max/targets.py，经 CFG 进来），所以钉的是
        # 「赋值只许一次，且那一次必须是读 CFG」，而不是钉某个字面量。
        self.assertEqual(len(re.findall(r"^\s*MMD2MAX_DIRTOL\s*=", code, re.M)), 1,
                         "容差只许赋值一次")
        self.assertIn('MMD2MAX_DIRTOL = mmd2maxCfgNum "MMD2MAX_DIRTOL"', code,
                      "唯一的赋值必须来自 CFG；写回 `global MMD2MAX_DIRTOL = 3.0` 就是又长出第二个来源")
        self.assertIn("if b > MMD2MAX_DIRTOL do", self.rot, "转角 pass 的尝试门控")
        score = _code(_between(SCRIPT, "fn mmd2maxFitScore", "fn mmd2maxRotatePass"))
        self.assertIn("> MMD2MAX_DIRTOL do badDir += 1", score, "循环打分必须跟门控同数")
        review = _code(_between(SCRIPT, "===== 结构方向复核", "把 Biped 节点写回"))
        self.assertIn("if a > MMD2MAX_DIRTOL do structBad += 1", review)
        self.assertNotRegex(code, r"mmd2maxStructErr[^\n]*>\s*[\d.]+\s+do",
                            "方向判据又写死度数了，改用 MMD2MAX_DIRTOL")
        self.assertIn('夹角>" + (MMD2MAX_DIRTOL as string) + "度=', code,
                      "阈值要打在汇总行里，否则下一轮分不清是数变了还是判据严了")

    def test_check_table_prints_the_ray_next_to_the_pmx_offset(self):
        """b 方案让 UpperArm 的 校验 偏差从 1e-5 变成 1.9 —— 那是「认了射线」的样子，
        不是回归。这一行必须并排给出对射线偏差，否则下一轮读日志会把它当成没修，
        然后有人再加一个特例分支去「修」它。判据必须复用同一个 helper：两处各自
        反推射线的话，摆位用的是一套、校验量的是另一套，1.9 到底是哪种读不出来。"""
        check = _code(_between(SCRIPT, 'vmsg = "  校验 " + targetName',
                               "===== 结构方向复核"))
        self.assertIn("if mmd2maxRayFromParent targetName do", check)
        self.assertIn("mmd2maxShoulderRayTarget clavNode want", check)
        self.assertIn("射线目标=", check)
        self.assertIn("对射线偏差=", check)
        self.assertIn("（b 方案认这个）", check)
        self.assertIn('vmsg + " 偏差=" + (dd as string)', check,
                      "对 PMX 的原偏差不能删：射线判据错了的时候要能看出来")


class BipedApiProbeContract(unittest.TestCase):
    """探针层：先把 Biped 的接口整份反射出来，再谈「橡皮筋能不能关」。

    #rotation 那条线连着三轮把「Biped 转不动」当结论，实际是 eulerToQuat 参数没构造
    出来（§6）。一轮实跑很贵（要用户批准开 Max），所以这里改成：一次反射拿到全部
    方法名 + 参数表并落到磁盘，以后「Biped 有没有 X 开关」不用开 Max 就能回答。
    """

    def setUp(self):
        self.dump = _code(_between(SCRIPT, "fn mmd2maxDumpBipedApi", "fn mmd2maxChainPriority"))
        raw_probe = _between(SCRIPT, "橡皮筋基准：搬一个关节", "清理探针残留")
        self.behaviour = _code(raw_probe)

    def test_api_is_reflected_not_guessed(self):
        self.assertIn("getMethodCount def", self.dump)
        self.assertIn("getMethod def i", self.dump)
        self.assertIn("classOf biped", self.dump,
                      "biped 是 struct 的实例，方法表在 (classOf biped) 上")
        self.assertIn("if n == 0 do return", self.dump, "反射拿不到就降级成一句话，不许抛")
        for unproven in ("biped.setRubberBandMode", "biped.setTieDowns", "biped.setBoneLength"):
            self.assertNotIn(unproven, _code(SCRIPT),
                             f"又直接调未证实的接口了：{unproven} —— 先拿反射表核名字")

    def test_interface_value_is_dumped_not_counted(self):
        """13:11 的 `getMethodCount 两次都拿到 0` 是用错了工具：getMethodCount 面向 struct
        实例，`biped` 是 interface 值，枚举它要用 dumpInterface。两条路都要在，
        而且**两边的异常文字都得进日志** —— 只有「拿到 0」这一种读数的话，
        下一轮还是分不清「没有这个开关」和「我用错了办法」。"""
        self.assertIn("dumpVal = dumpInterface biped", self.dump)
        self.assertIn("try ( dumpVal = dumpInterface biped ) catch ( dumpErr = getCurrentException() )",
                      self.dump)
        self.assertIn('if (classOf dumpVal) == String do', self.dump,
                      "dumpInterface 返回 undefined 时不能拿去 split（text.count 会抛）")
        self.assertIn("反射失败：dumpInterface=", self.dump,
                      "失败那一行必须把两条路各自的异常文字都打出来")
        self.assertIn('" 来源=" + how', self.dump,
                      "行数是从哪条路拿的必须写在读数里，否则 dumpInterface 退化成 getMethodCount 看不出来")

    def test_api_dump_lands_on_disk_for_offline_reading(self):
        """全表写文件而不是只进日志：日志一行放不下 200 个方法，而这份表以后每轮都要查。"""
        self.assertIn("biped_api.txt", self.dump)
        self.assertIn("createFile apiPath", self.dump)
        self.assertIn("close fh", self.dump)
        self.assertIn("getFilenamePath MMD2MAX_LOG", self.dump,
                      "写在日志同目录，obj_run 不用认识这个新文件")
        self.assertIn("matchPattern s pattern:", self.dump, "命中关键词的行才进日志")

    def test_rubber_band_probe_measures_child_following(self):
        """判据是「子关节被带着走了几厘米」，不是「调用没抛错」—— #pos 就是接受参数
        而根本不转骨头的先例。父关节沿世界 Z 搬 5cm，父动/子跟两个数都打出来，
        父动≈0 就说明这一遍连搬都没搬动，读数以子跟为准没意义。"""
        self.assertIn('append rbPairs #("L Clavicle", "L UpperArm")', self.behaviour)
        self.assertIn('append rbPairs #("L Finger3", "L Finger31")', self.behaviour)
        self.assertIn("biped.setTransform pNode #pos (p0 + [0.0, 0.0, 5.0]) false", self.behaviour)
        self.assertIn("父动=", self.behaviour)
        self.assertIn("子跟=", self.behaviour)
        self.assertIn("((distance c0 c1) as string)", self.behaviour)
        self.assertIn("biped.setTransform pNode #pos p0 false", self.behaviour,
                      "不复位的话下一对的读数被上一对污染")

    def test_behaviour_probe_runs_inside_figure_mode_and_on_the_disposable_biped(self):
        """真骨架的摆位全跑在 Figure 模式内，模式外量出来的跟随量对不上；
        且这一段只许碰必删的临时骨架（root.name 一个节点都不动）。"""
        self.assertIn("mmd2maxFigureMode probe.controller true", self.behaviour)
        self.assertLess(self.behaviour.index("mmd2maxFigureMode probe.controller true"),
                        self.behaviour.index("父动="),
                        "先回 Figure 模式再量：上面那段对照测试刚退出过模式")
        self.assertIn("probe.name + \" \"", self.behaviour)
        self.assertNotIn("root.name", self.behaviour, "橡皮筋基准动了真骨架")

    def test_clavicle_ray_premise_is_self_checked(self):
        """b 方案的整个前提是「肩点在锁骨骨盒射线上」。这条不成立的话那一遍白做，
        所以在弃用骨架上先反推一次量差；读不到轴也要留一行，别静默退回 PMX 目标。"""
        self.assertIn("锁骨射线自检", self.behaviour)
        self.assertIn("local rayT = mmd2maxShoulderRayTarget kNode sp", self.behaviour)
        self.assertIn("射线反推肩点 与 真实肩点差=", self.behaviour)
        self.assertIn("读不到骨盒长度轴", self.behaviour)


class LegRollContract(unittest.TestCase):
    """#36 大腿扭转闭环：髋踝都钉死之后，膝唯一的自由度就是绕「髋→踝」轴拧几度。

    用户：「两个小腿的朝向有问题内八了，实际上并没有。」round-9 回读把责任钉死：
    髋偏差 0.106、L/R Foot 偏差 6.8e-06 / 4.2e-05（落位全对），可两膝各自被拽向中线
    —— 校验 L Calf X 6.224→4.870、R Calf X -6.224→-5.048，偏差 1.36 / 1.25cm。
    而全脚本没有任何一处写过 roll：转角 pass 转的是「骨盒画出来指哪」（mmd2maxSetBoneRot
    + 结构轴），改不了「这条腿拧了几度」—— 腿拧了但膝盖骨盒照样朝着前方，所以方向
    复核全绿、画面上却内八。**这一组测试钉的是「拧多少」这个数从哪来、写完怎么验。**
    """

    def setUp(self):
        self.fn = _between(SCRIPT, "fn mmd2maxLegRoll", "fn mmd2maxToeSnapBack")
        self.code = _code(self.fn)
        self.loop = _code(_between(SCRIPT, "摆位 <-> 转角 收敛循环（机制层）",
                                   "mmd2maxFigureMode root.controller false"))

    def test_driven_by_the_mode_column_not_bone_names(self):
        """哪条腿要拧靠 limbs.tsv 的 mode=leg，函数体里一个骨名都不许出现。

        mmd2maxChainPriority 是全脚本唯一的骨名分支（另一条契约）；再来一处「按名字
        找大腿」，取舍表和扭转闭环就会互相以为对方在管这条腿。
        """
        self.assertIn('if item.count >= 5 and item[5] == "leg" do', self.code)
        self.assertNotIn('"Bip001', self.code, "又长出按骨名硬挑的分支")
        self.assertNotIn("matchPattern", self.code)
        # 三个目标全部来自肢段表：髋=父段行的 childPos，踝=子段行的 childPos
        self.assertIn("if row[2] == tName do hipTgt = row[4]", self.code)
        self.assertIn("if row[1] == kName do ( ankleTgt = row[4]; ankleName = row[2] )",
                      self.code)
        self.assertIn("local kneeTgt = item[4]", self.code)

    def test_the_number_is_a_signed_azimuth_about_the_hip_ankle_axis(self):
        """判据只有一个数：把实测膝与目标膝各自削掉沿轴分量，两个垂向向量的有符号夹角。

        符号取 (实测 × 目标)·轴 ⇒ 左右腿同一段代码（R 腿的轴自己翻向，角也自己翻号）。
        三维夹角、两点距离这类「恒等式」读数在这条缺陷上是全绿的，别再拿它们当判据。
        """
        self.assertIn("local axis = ankleP - hipP", self.code, "轴必须实测，不取 PMX 名义方向")
        self.assertIn("vc = vc - axis * (dot vc axis)", self.code)
        self.assertIn("vt = vt - axis * (dot vt axis)", self.code)
        self.assertIn("local ang = atan2 (dot (cross vc vt) axis) (dot vc vt)", self.code,
                      "atan2(y=沿轴的分量, x=点积)：符号与 ±180 都归它管")
        self.assertIn("if lc < 0.5 or lt < 0.5 then", self.code,
                      "膝几乎落在轴上时方位读不出，必须停而不是猜一个角度")

    def test_the_tolerance_knob_is_the_single_rollback_switch(self):
        """MMD2MAX_ROLLTOL 独立于 MMD2MAX_DIRTOL（那是「骨盒画出来的朝向」），
        并且只有一个消费者：把它写成 999 就能整段不动，一轮实验只扳一个开关。"""
        self.assertIn('MMD2MAX_ROLLTOL = mmd2maxCfgNum "MMD2MAX_ROLLTOL"', SCRIPT)
        self.assertIn("if abs ang <= MMD2MAX_ROLLTOL then", self.code)
        code = _code(SCRIPT)
        self.assertEqual(len(re.findall(r"^\s*MMD2MAX_ROLLTOL\s*=", code, re.M)), 1,
                         "赋值只许在 mmd2maxLoadCfg 里出现一次")
        self.assertEqual(code.count("MMD2MAX_ROLLTOL then"), 1,
                         "行为上的消费者只许一处（targets.py 那句「写成 999 就整段不动」靠的就是这个门控）")

    def test_it_rotates_the_three_local_rows_not_a_quat_guess(self):
        """写法：把大腿三根局轴各自绕「髋→踝」轴转 Rodrigues 一步，行长度按原矩阵乘回去，
        再交给已经验证过的 mmd2maxSetBoneRot 落笔。

        quat × Point3 的乘法次序在本项目离线证不了（Biped 反射接口是死的），
        而 cross/dot/cos/sin 是一直在用、日志里验过的原语 —— 别再改成 angleaxis 猜方向。
        """
        helper = _code(_between(SCRIPT, "fn mmd2maxRotVecAbout", "fn mmd2maxLegRoll"))
        self.assertIn("v * c + (cross axis v) * s + axis * ((dot axis v) * (1.0 - c))", helper)
        for k in ("1", "2", "3"):
            self.assertIn("mmd2maxRotVecAbout axes[%s] axis cosv sinv" % k, self.code)
        self.assertEqual(self.code.count("mmd2maxScaleRow"), 6,
                         "三根轴 × 正写/回滚两份快照 = 6 次；行长度必须乘回去，"
                         "写单位向量等于把骨盒缩放抹成 1")
        self.assertNotIn("angleaxis", self.code)
        self.assertIn("local fwd = matrix3 n1 n2 n3 m0.row4", self.code,
                      "平移按原 row4 写回，否则转完 pivot 就飞了")
        self.assertIn("mmd2maxSetBoneRot tnode rotWay fwd", self.code,
                      "落笔只用探针选出来的写法，不许直写 node.transform")

    def test_it_never_leaves_a_worse_knee_and_always_replaces_the_ankle(self):
        """写完立刻复测：膝没靠近 0.01cm 就把**转之前的整块矩阵**写回去；
        转成功则把踝按 PMX 目标重摆（扭转必然把踝甩出去），偏差一起打日志。

        node.transform 在 MAXScript 里是同一个 Matrix3 引用，所以回滚快照必须在写之前
        另建一份 matrix3 —— 拿 m0 当快照回滚等于把转过的那份再写一遍。
        """
        self.assertLess(self.code.index("local back = matrix3"),
                        self.code.index("mmd2maxSetBoneRot tnode rotWay fwd"),
                        "回滚快照必须在写之前成型")
        self.assertIn("if dev1 < devPrev - 0.01 then", self.code)
        self.assertIn("mmd2maxSetBoneRot tnode rotWay back", self.code)
        self.assertIn("biped.setTransform fnode #pos ankleTgt false", self.code,
                      "转完不重摆踝，下一遍量的就是被甩出去的踝，判据自己骗自己")
        self.assertIn("while step < 2 and not settled do", self.code,
                      "最多两遍：再多就是拿真骨架当试错场")
        self.assertGreaterEqual(self.code.count("mmd2maxSetBoneRot"), 2,
                                "写完的路和回滚的路必须是同一个函数")

    def test_every_leg_leaves_one_measured_line(self):
        """日志要能分清「没转」「转了没改善」「转到位」——只有计数会看不出区别（老教训）。"""
        for token in ("大腿扭转 ", "起始膝偏差=", "残差=", "已达容差", "膝偏差",
                      "踝偏差=", "没改善->回滚", "迭代=", "两遍未收敛"):
            self.assertIn(token, self.fn, f"少了一处读数：{token}")
        self.assertRegex(self.code, r"\n\s*rolled\s*\n\)", "返回值必须是「真的转过且最终有改善的腿数」")

    def test_the_roll_runs_before_the_cleanup_pair(self):
        """顺序：扭转在**收尾之前**。转大腿会把踝甩出去、并把整条脚链的局轴带走同样的
        角度，而收尾那两道写干的正是「把踝摆回来 + 把 #dir 的方向补回来」。"""
        self.assertIn("local legRolled = mmd2maxLegRoll root limbs rotWay", self.loop)
        self.assertLess(self.loop.index("mmd2maxLegRoll root limbs rotWay"),
                        self.loop.index("收尾重摆(全摆"))
        self.assertLess(self.loop.index("mmd2maxLegRoll root limbs rotWay"),
                        self.loop.index("收尾转角(只转方向优先的骨"))


class ToeSnapBackContract(unittest.TestCase):
    """#38 脚趾复摆：#36 转完大腿之后，链尾那根骨没人再管过（round-10 实测被拽走 4.74cm）。

    用户报的是「脚穿模」，而 round-10 的骨盒截面确实按鞋面收紧到了 3.93×1.53 —— 可侧视里
    仍然有一只盒子飘在脚尖上方，回读给出原因：`段校验 Bip001 L Foot -> Toe0 误差=1.49461`
    （round-9 同一行 3.2e-05）、`结构向 Bip001 L Toe0 pivot偏差=4.7362`，而 Foot 自己的
    pivot（6.5e-06）与朝向（夹角 0.0198）都是准的。**这一组测试钉的是「链尾的落位归谁管」。**
    """

    def setUp(self):
        self.fn = _between(SCRIPT, "fn mmd2maxToeSnapBack", "fn mmd2maxChainPriority")
        self.code = _code(self.fn)
        self.loop = _code(_between(SCRIPT, "摆位 <-> 转角 收敛循环（机制层）",
                                   "mmd2maxFigureMode root.controller false"))

    def test_driven_by_the_mode_column_not_bone_names(self):
        """哪根是脚趾靠 limbs.tsv 的 mode=foot（第 2 列 = 孩子），骨名一个不许出现；
        踝目标按「子节点是这只脚 Foot 的那一行」现查，与 #26 复核段同一写法。"""
        self.assertIn('if item.count >= 10 and item[5] == "foot" do', self.code)
        self.assertIn("local ballPos = item[4]", self.code)
        self.assertIn("if row.count >= 4 and row[2] == fName do ( ankleTgt = row[4] )",
                      self.code)
        self.assertNotIn('"Bip001', self.code)
        self.assertNotIn("matchPattern", self.code)

    def test_the_tolerance_knob_is_the_single_rollback_switch(self):
        """MMD2MAX_TOETOL 与 ROLLTOL（扭转角）、DIRTOL（骨盒画出来的朝向）是三件事，
        各只有一个消费者：把它写成 999 就等于整段跳过，一轮实验只扳一个开关。"""
        self.assertIn('MMD2MAX_TOETOL = mmd2maxCfgNum "MMD2MAX_TOETOL"', SCRIPT)
        self.assertIn("if dev0 <= MMD2MAX_TOETOL then", self.code)
        self.assertEqual(len(re.findall(r"^\s*MMD2MAX_TOETOL\s*=", _code(SCRIPT), re.M)), 1,
                         "赋值只许在 mmd2maxLoadCfg 里出现一次")
        self.assertEqual(_code(SCRIPT).count("MMD2MAX_TOETOL then"), 1,
                         "行为上的消费者只许一处")
        self.assertNotIn("MMD2MAX_DIRTOL", self.code, "把朝向容差当落位容差用")
        self.assertNotIn("MMD2MAX_ROLLTOL", self.code)

    def test_it_only_moves_the_joint_the_way_the_toe_routine_already_does(self):
        """落笔只用脚趾例程已经验过的那道写：biped.setTransform node #pos <PMX 目标> false。
        复摆只管链尾的**位置**，转骨盒是扭转闭环和转角 pass 的事，这里不掺。"""
        self.assertIn("biped.setTransform cnode #pos ballPos false", self.code)
        self.assertNotIn("#rotation", self.code)
        self.assertNotIn("mmd2maxSetBoneRot", self.code)
        self.assertNotIn("angleaxis", self.code)

    def test_the_collateral_is_measured_before_and_written_after(self):
        """摆一根链尾会怎么弄坏别的骨，纸面定不下来（Biped 反射接口是死的）：
        它可能把小腿转一下、也可能把足骨抻长。所以踝的偏差必须在写之前就先量好，
        摆完把「踝偏差 前->后」和「足长」一起打出来 —— 这两列就是那一轮的结论本身。"""
        write = self.code.index("biped.setTransform cnode #pos ballPos false")
        self.assertLess(self.code.index("local aDev0 ="), write,
                        "踝的基线必须在写之前取，否则「连带」读成 0 是假绿")
        self.assertGreater(self.code.index("local aDev1 ="), write)
        self.assertIn("踝偏差", self.fn)
        self.assertIn("足长=", self.fn)

    def test_it_never_leaves_a_worse_toe(self):
        """写完复测：没靠近 0.01cm 就把摆之前那个点写回去（Point3 是值类型，
        直接存就是快照；矩阵才需要另建 matrix3），并把回滚后的偏差一起读回来。"""
        write = self.code.index("biped.setTransform cnode #pos ballPos false")
        self.assertLess(self.code.index("local before = cnode.transform.translationpart"), write)
        self.assertIn("if posed and dev1 < dev0 - 0.01 then", self.code)
        self.assertIn("biped.setTransform cnode #pos before false", self.code)
        self.assertIn("回读=", self.fn)
        self.assertIn("踝回读=", self.fn)

    def test_every_foot_leaves_one_measured_line(self):
        """日志要分清「已在容差内不动」「摆回来了」「摆不动所以回滚」「节点/踝目标缺失」——
        只有计数看不出区别（老教训）。"""
        for token in ("脚趾复摆 ", "起始偏差=", "摆后=", "已在容差", "没改善->回滚",
                      "净改善", "摆位抛错", "跳过：节点或踝目标缺失"):
            self.assertIn(token, self.fn, f"少了一处读数：{token}")
        self.assertRegex(self.code, r"\n\s*snapped\s*\n\)",
                         "返回值必须是「真的跟回落位的根数」")

    def test_it_runs_after_the_cleanup_pair_while_figure_mode_is_on(self):
        """顺序：复摆在收尾那两道写**之后**（收尾是最后动过链的地方，谁被拽走归谁管），
        并且在退出 figure 模式之前（`#pos` 那道写只在 figure 模式下认）。"""
        self.assertIn("local toeSnapped = mmd2maxToeSnapBack root limbs", self.loop)
        self.assertLess(self.loop.index("收尾转角(只转方向优先的骨"),
                        self.loop.index("mmd2maxToeSnapBack root limbs"))
        self.assertLess(self.loop.index("收尾重摆(全摆"),
                        self.loop.index("mmd2maxToeSnapBack root limbs"))


class FootCloseupFramingContract(unittest.TestCase):
    """脚部正/侧特写的取景（#37 的验收工具）：原先按**全身** X/Y 边界取景，
    整只鞋在 984px 的画面里只占 100px（≈7.4px/cm），4.7cm 的骨盒错位画面上只剩十几像素
    —— round-10 的「脚还在穿模」在图上判不动，只能靠回读数字。"""

    def setUp(self):
        self.shots = _code(_between(SCRIPT, "fn mmd2maxScreenshot =", "fn mmd2maxPrepareScene"))

    def test_the_foot_closeups_share_the_box_built_from_foot_nodes(self):
        """与 #26 的俯视特写共用同一只按四只脚骨节点算出来的框（±6cm），
        高度方向仍留到 h*0.14 把靴筒带进画面；查不到节点才退回全身框。"""
        self.assertIn("footLo = [lo.x - 6.0, lo.y - 6.0, b.min.z - 2.0]", self.shots)
        self.assertIn("footHi = [hi.x + 6.0, hi.y + 6.0, b.min.z + h * 0.14]", self.shots)
        self.assertIn('for nm in #("Bip001 L Foot", "Bip001 R Foot", '
                      '"Bip001 L Toe0", "Bip001 R Toe0") do', self.shots)
        self.assertLess(self.shots.index("footPts.count >= 2"),
                        self.shots.index('mmd2maxShotOne outDir baseName "foot_side"'),
                        "框必须在拍 foot_side 之前算好")

    def test_the_frame_itself_is_logged(self):
        """08:59 那张 foot_top 把整条手臂都框进来了，分不清是「节点没查到」还是
        「框算对了但被视口宽高比撑开」——所以框要写进日志，现在两张特写都写。"""
        self.assertIn("foot_top 取景：脚骨节点=", self.shots)
        self.assertIn("侧/正特写框=", self.shots)

    def test_bone_only_foot_closeups_exist_and_reuse_the_same_box(self):
        """#37 的图证：foot_side / foot_front 里骨盒被鞋面挡住，只能靠「两个盒子的
        相对距离 + 从图上反推比例尺」判方向，估错一次就是一轮。藏网格的那一套必须
        也拍脚部特写，而且**沿用同一只框** —— 另算一只框就会和带网格那张对不上。"""
        for call in (
            'mmd2maxShotOne outDir baseName "bone_foot_side" #view_left footLo footHi',
            'mmd2maxShotOne outDir baseName "bone_foot_front" #view_front footLo footHi',
            'mmd2maxShotOne outDir baseName "bone_foot_top" #view_top topLo topHi',
        ):
            self.assertIn(call, self.shots, call)

    def test_bone_only_foot_shots_sit_inside_the_hidden_window(self):
        """三张藏网格的脚部特写必须夹在 `SetMeshHidden true` 与 `SetMeshHidden false`
        之间：排在还原之后就是带网格的重复图，排在隐藏之前同理 —— 而这两种都只会
        在真打开 Max 之后才看得出来，正好是要省掉的那一轮。"""
        hide = self.shots.index("mmd2maxSetMeshHidden true")
        show = self.shots.index("mmd2maxSetMeshHidden false")
        for tag in ("bone_foot_side", "bone_foot_front", "bone_foot_top"):
            at = self.shots.index(f'baseName "{tag}"')
            self.assertTrue(hide < at < show, f"{tag} 落在了藏网格窗口之外")


class FootBoneBoxReadbackDeadEnd(unittest.TestCase):
    """第十二轮实跑的结论，别再试第二次：Biped_Object 的 node.min / node.max 退化成一个点
    （四只脚骨全部读出 `尺寸=0.0x0.0x0.0`），量不到**显示盒**。
    「骨盒在不在鞋里 / 底部够不够到脚的下面」认两样东西：藏网格的脚部特写截图，
    以及 画线 / 结构向 那两行的三维夹角（局轴由 mmd2maxBipedAxes 量，已证明可用）。
    ⚠️ 这条只作废到「node.min/node.max 不能用」为止：第十四轮起改用
    nodeGetBoundingBox 的两个角（见 FootBoxOffsetProbe）—— 截面闭环能收敛，就说明
    这对手柄一直在响应 #scale，只是从来没人打印过它的 min/max。"""

    def test_the_degenerate_probe_is_gone(self):
        self.assertNotIn("骨盒框", SCRIPT, "已证 n.min/n.max 在 Biped 上是点，这段死探针留着只会误导下一轮")
        self.assertNotIn("local bx = n.min", SCRIPT)

    def test_the_foot_box_is_still_judged_by_angles_and_closeups(self):
        """撤掉探针不等于退回观感：判方向的读数和那三张藏网格特写必须都还在。"""
        yaw = _code(_between(SCRIPT, "脚骨「画出来的朝向」复核", 'local smsg = "  段校验 "'))
        self.assertIn("mmd2maxBipedAxes fnode", yaw)
        self.assertIn("脚骨偏航结论", yaw)
        for tag in ("bone_foot_side", "bone_foot_front", "bone_foot_top"):
            self.assertIn(f'baseName "{tag}"', SCRIPT, f"{tag} 没了就只能隔着鞋面猜盒子")


class FootBoxOffsetProbe(unittest.TestCase):
    """#19/#37 那句「骨盒以**长度轴**为中心对称」是整条「骨头待在鞋里」判据的地基
    （Python 侧 _foot_link_box / _toe_sections 都是它的实现），但从没有人看过包围盒本身。
    第十四轮用户改口要「像一个框一样框住脚的脚跟」—— 盒子到底是绕轴对称、还是挂在轴的一侧，
    决定这件事是「把高按踝→鞋底给」就能做，还是几何上不可能（踝在脚的最高最靠后处，
    从它往前伸的对称盒子再怎么放大也到不了踝的后面）。所以先量，再改判据。"""

    def test_helper_reads_the_same_handle_the_fit_already_trusts(self):
        fn = _between(SCRIPT, "fn mmd2maxLogLocalBox", "fn mmd2maxScaleBoneAxis")
        self.assertIn("nodeGetBoundingBox node node.transform", fn,
                      "必须用 mmd2maxLocalExtent 同一对手柄：换口径读到的对称性不算数")
        self.assertIn("本地 min=", fn)
        self.assertIn("本地包围盒读不到", fn, "读不到要留一行，静默的探针等于没打")

    def test_foot_box_prints_on_both_sides_of_the_shrink(self):
        """缩之前 = 默认骨盒挂在轴的哪一侧，缩之后 = 我们写出来的盒子还不在同一侧。
        只打一边就分不开「Biped 本来就这样」和「是 #37 缩出来的」。"""
        before = SCRIPT.index('mmd2maxLogLocalBox ("足盒框 " + fName) fnode')
        fit = SCRIPT.index("local fc = mmd2maxFitCrossSection fnode fdir footW footH")
        after = SCRIPT.index('mmd2maxLogLocalBox ("足盒缩后 " + fName) fnode')
        self.assertTrue(before < fit < after, "骨盒框没夹在收紧两边，读不到「缩之前」的样子")

    def test_toe_box_prints_too(self):
        """#19 当年是按同一条对称判据收的 Toe0，用户已经验收过 —— 同一只手柄在趾骨上
        读到什么，是这条判据对错的第二个样本，不能只测 Foot。"""
        at = SCRIPT.index('mmd2maxLogLocalBox ("趾盒框 " + cName) cnode')
        fit = SCRIPT.index("local cs = mmd2maxFitCrossSection cnode dir toeW toeH")
        self.assertTrue(at < fit)


class FootReachContract(unittest.TestCase):
    """#41：用户两张对照图 —— 左「一根斜牙签 + 前方悬空一块趾骨盒」，右「一块落地的厚
    盒子，趾方块贴在它正面」。#40 那一轮 链向/偏航/段校验 全绿，这块空气却在图里，
    因为**没有任何一条回读量过两块盒子之间的距离**（项目老账：恒等式校验看不见偏航，
    这是第三次）。所以这一轮同时补「写」和「读」：

    写 = 足盒接趾（mmd2maxFitBoneReach）：沿长度轴把骨盒**远端面**拉到 PMX 球部。
        可行性由探针 biped_gap2.ms 定死（stock Biped，脚本没碰过）：
          · 默认骨盒远端 == 子节点沿轴坐标，差 = 0.0 —— 断链不是 Biped 的画法，
            是我们把长度拟成了两 pivot 的三维间距（肢段拟合在摆位之前，之后没人补）；
          · 长度轴 #scale 乘 k ⇒ 盒远端与子节点同步乘 k（3.42361 == 3.42361），
            退出 figure 模式仍在（B2 after-exit 与 B1 in-figure 逐字相同）。
        Max 2022 没有盒体积 API（反射 3400 行里 box/bone/width/height/taper 一个没有，
        setBox/setWidth/setTaper 全是 Unknown property），所以 #scale 是唯一的手柄。
    读 = 盒隙：父盒前端面到子关节沿轴的距离 + 子关节须落在端面矩形内。
    """

    def test_no_box_volume_api_is_called(self):
        """探针实测 Max 2022 的 biped 接口没有盒体积参数；哪天有人「顺手」加一句
        biped.setBox，它会静默抛错并被 try 吞掉，盒子看着没变、日志却全绿。"""
        code = _code(SCRIPT)
        for api in ("biped.setBox", "biped.getBox", "biped.setBoneParams",
                    "biped.setWidth", "biped.setHeight", "biped.setTaper"):
            self.assertNotIn(api, code, f"{api} 在 Max 2022 不存在，只有 #scale 能改盒子")

    def test_reach_helper_fits_the_far_face_to_the_pmx_target(self):
        fn = _code(_between(SCRIPT, "fn mmd2maxFitBoneReach", "fn mmd2maxFitBoneExtent"))
        self.assertIn("targetPos - pivot", fn,
                      "目标必须是传进来的 PMX 落位；回读到的子节点会被这次缩放自己推走")
        self.assertIn("mmd2maxBoxFarEnd node axisIdx cv", fn)
        self.assertIn("mmd2maxScaleBoneAxis node axisIdx f", fn,
                      "只许缩长度轴那一根，宽/高是 #37/#39 刚收好的")
        self.assertIn("if abs gap <= 0.02 * along do exit", fn, "收敛判据按相对量，不按绝对 0.02cm")
        self.assertIn("stalled = true", fn, "读数对缩放没反应就必须停，否则同一个倍率被连乘四次")
        # 第六个返回值 gap0 是 M4d 的守卫用的：退回不退回判的是「这一刀写之前后之后差了多少」，
        # 而 gap 会被后续复摆改掉，所以「写之前的差」必须在同一次调用里带出来。
        self.assertIn("#(gap, iter, stalled, along, far, gap0)", fn,
                      "差值不返回给调用方就没人能在日志里看见它")
        self.assertIn("local gap0 = 1e9", fn,
                      "gap0 必须有初值：循环一次都没跑的骨（已经达标）不能带出 undefined")

    def test_far_end_reader_uses_the_same_handle_the_fit_trusts(self):
        fn = _code(_between(SCRIPT, "fn mmd2maxBoxFarEnd", "fn mmd2maxScaleBoneAxis"))
        self.assertIn("nodeGetBoundingBox node node.transform", fn,
                      "必须和 mmd2maxLocalExtent/mmd2maxLogLocalBox 同一对手柄，换口径不算数")
        self.assertIn("(dot axes[axisIdx] toward) >= 0.0", fn,
                      "朝哪一端自己判符号，写死 max 会在朝后的那只脚上取反")

    def test_reach_pass_runs_after_every_chain_write(self):
        """顺序是这条契约的全部内容：肢段拟合在摆位之前（#41 的成因），所以补长度这一刀
        必须排在 收尾重摆 + 脚趾复摆 之后 —— 否则它拟合的是「一个已经不在原地的踝 pivot
        加一根还没转过去的轴」（第十六轮实测 校验 Bip001 L Foot 偏差=1.44357，
        转角 pass 又把长度轴从投影 9.736 转到全长 11.228）。
        还在 figure 模式内写：探针 B 的 #scale 写入就是在模式内做的。"""
        at = SCRIPT.index("===== 足盒接趾（#41）")
        self.assertGreater(at, SCRIPT.index('mmd2maxLog ("收尾重摆(全摆'),
                           "排在收尾之前 = 给一根马上要被搬走的踝配长度")
        self.assertGreater(at, SCRIPT.index("脚趾复摆：净改善"),
                           "复摆之后链的落位才是最终值")
        self.assertLess(at, SCRIPT.index("mmd2maxFigureMode root.controller false"),
                        "#scale 与转角都在 figure 模式内，模式外没验过")
        block = _code(SCRIPT[at:SCRIPT.index("mmd2maxFigureMode root.controller false")])
        self.assertIn("if MMD2MAX_FOOTREACH do", block, "整段必须受回滚开关门控")
        self.assertIn("mmd2maxFitBoneReach frNode frAx[1] frPivot frBall", block)
        self.assertIn("local frBall = item[4]", block,
                      "目标是 limbs.tsv 里 PMX 的 つま先，不是回读到的 Toe0")
        self.assertIn("mmd2maxBipedAxis frNode", block,
                      "长度轴要在**摆完之后**重新探测，脚趾段那次（转角前）已经过期")
        self.assertIn("足盒接趾 ", block, "每一刀都要留行：差值、轴、目标沿轴、盒远端")
        self.assertGreater(block.count("mmd2maxToeSnapBack root limbs"), 0,
                           "#scale 把孩子沿轴推走了，同段必须自己跟回来（#38 同一笔账）")
        self.assertIn("足盒接趾复摆", SCRIPT[at:])

    def test_box_gap_readback_measures_faces_not_axes(self):
        """#41 的护栏：量**两块盒子之间看得见的那段空气**，别再和方向混为一谈。

        链向 只证明父盒的轴指得到子关节；平轴口径下它按构造读 29.93 度（那是 #39/#41
        换来的形状，不是断链），而第十六轮 链向 读 0.0 度全绿时用户看到的仍是牙签。
        判据全部来自回读到的包围盒与两个 pivot，一处不许引用 mmd2maxBoxWantDir
        —— 那是我们自己写进去的方向，再比一次就是恒等式。"""
        block = _code(_between(SCRIPT, "local gbb = undefined", "肢段长度复核"))
        self.assertLess(SCRIPT.index("local gapBad = 0"), SCRIPT.index("local gbb = undefined"),
                        "计数器必须声明在循环外，否则每只脚都归零")
        self.assertIn("nodeGetBoundingBox fnode fnode.transform", block)
        self.assertIn("mmd2maxBipedAxis fnode (normalize childVec)", block,
                      "沿轴必须是实测长度轴，不是 XYZ 里猜一根")
        self.assertIn("dot childVec gAx[2]", block, "沿轴坐标必须带符号（轴可能反着指）")
        self.assertIn("if offk < gmn[k] - 0.05 or offk > gmx[k] + 0.05", block,
                      "端面覆盖：平轴时子关节在轴下方 5.6cm，端面不够高就是两块盒子错开")
        self.assertIn("abs ggap > MMD2MAX_GAPTOL", block)
        self.assertIn("gapBad = gapBad + 1", block)
        self.assertIn("盒隙结论 没接上=", block, "必须有一行总结，验收不用自己数脚")
        self.assertNotIn("mmd2maxBoxWantDir", block, "比我们自己写的方向 = 恒等式，这条护栏就白加")
        self.assertGreater(SCRIPT.index("盒隙结论 没接上="),
                           SCRIPT.index("mmd2maxFigureMode root.controller false"),
                           "局轴与包围盒都要在退出 figure 模式之后量（规则 17）")

    def test_reach_switch_is_declared_once(self):
        code = _code(SCRIPT)
        # M2 之后 #41 这两个开关的数值在 mmd2max/targets.py；脚本里只许「从 CFG 读一次」
        # 和「按它分支一次」，写回字面量就是第二个来源（M1b 立零默认值要根除的正是这个）。
        for name in ("MMD2MAX_FOOTREACH", "MMD2MAX_GAPTOL"):
            self.assertEqual(len(re.findall(r"^\s*%s\s*=" % name, code, re.M)), 1,
                             "%s 只许在 mmd2maxLoadCfg 里赋值一次" % name)
            self.assertIn('%s = mmd2maxCfg' % name, code,
                          "%s 的唯一赋值必须来自 CFG" % name)
        self.assertIn("if MMD2MAX_FOOTREACH do", code,
                      "没有这一句就等于 #41 没有回滚位，只能整段删代码")


class RollbackCarrierContract(unittest.TestCase):
    """M4d 回滚载体（.fig）的契约：载体怎么用、什么时候用、关掉时一行都不许跑。

    全部来自探针实测（probes/figfile_m4h.txt），不是文档：`biped.saveFigFile` 收
    controller、必须在 Figure 模式内、一份 .fig 连骨盒一起还原、`saveFigFile` 自己会
    在进出模式的翻转里把姿态挪走 2.13cm，而 `.bip` 那条路是死的。这四条只要有一条被
    「顺手改回看起来更自然的写法」，产品里的回滚就退化成一句注释 —— 而且是在跑坏的
    那一轮才退化成注释，所以只能静态钉。
    第五条不在探针里，在第 27 轮的实跑报告里：`.fig` 不还原局部轴的账本，所以标记必须
    带朝向那一列（见 test_the_axis_frame_is_the_fourth_column）。
    """

    def helpers(self):
        """回滚那一节的七个辅助函数（从 FigPath 到自检函数结束）。"""
        return _between(SCRIPT, "fn mmd2maxFigPath", "fn mmd2maxApplyScale")

    def test_the_carrier_takes_the_controller_not_the_node(self):
        code = _code(self.helpers())
        fig = _between(code, "fn mmd2maxFigOp", "fn mmd2maxMarkNode")
        self.assertIn("biped.saveFigFile ctrl", fig)
        self.assertIn("biped.loadFigFile ctrl", fig)
        self.assertNotIn("saveFigFile bipRoot", fig,
                         "传节点实测抛「Unable to convert: $Biped_Object … to type: Controller」")
        self.assertIn("local ctrl = mmd2maxFigCtrl bipRoot", fig,
                      "controller 只能从 bipRoot.controller 拿，别处拿到的不是同一个东西")

    def test_figure_mode_is_borrowed_and_handed_back(self):
        """存/读都要在模式内做，做完把**调用方原来的状态**还回去。

        `figureMode = false` 写死等于在装配中途退出模式：:2319 那条规则（#scale 必须
        跑在退出之前）会被这一句悄悄破掉，而日志全绿。
        """
        code = _code(self.helpers())
        fig = _between(code, "fn mmd2maxFigOp", "fn mmd2maxMarkNode")
        self.assertIn("was = ctrl.figureMode", fig, "先把模式状态 peek 回来")
        self.assertLess(fig.index("mmd2maxFigureMode ctrl true"),
                        fig.index("biped.saveFigFile ctrl"),
                        "模式外存图实测报「Cannot run the operation in this mode」")
        self.assertLess(fig.index("biped.loadFigFile ctrl"),
                        fig.index("mmd2maxFigureMode ctrl was"),
                        "读完必须把模式还回去：裸 saveFigFile 自己就把姿态挪走 2.13cm")
        self.assertNotIn("mmd2maxFigureMode ctrl false", fig,
                         "写死 false = 在装配中途退出模式，#scale/转角全都失效")

    def test_the_mark_is_pose_plus_boxes_not_quaternions(self):
        """比的是「世界位置 + 本地盒三轴跨度 + #scale 三轴 + 三根局部轴的世界朝向」。

        探针量到 q 与 -q 表示同一个朝向：逐分量差会把一次完美还原读成 3.99。
        .fig 声称连骨盒一起还原（H2），所以验回滚也必须两样一起验 —— 只比 #pos
        会看不见「姿态回来了、骨头还粗着」。
        第四样（局部轴朝向）用 mmd2maxBipedAxes 取，不用四元数，理由同上。
        """
        code = _code(self.helpers())
        marks = _between(code, "fn mmd2maxMarkList", "fn mmd2maxMarkGap")
        self.assertIn("node.transform.translationpart", marks)
        self.assertIn("mmd2maxLocalExtent node 1", marks)
        self.assertIn("mmd2maxBipedScale node", marks)
        self.assertIn("mmd2maxBipedAxes node", marks)
        self.assertNotIn(".rotation", marks, "四元数逐分量差是误导读数（q 与 -q 同一个朝向）")
        self.assertIn("(findItem seen nm) == 0", marks,
                      "limbs 每行都带父骨，不去重同一根骨会被拍多次")
        gap = _between(code, "fn mmd2maxMarkGap", "fn mmd2maxRollbackSelfTest")
        self.assertIn("distance a[2] other[2]", gap)
        self.assertIn("mmd2maxVecAngleDeg a[5][k] other[5][k]", gap)
        self.assertIn("if other != undefined do", gap,
                      "骨名一边有一边没有就不许比：编一个 0 假装没事是这个项目的死法")

    def test_the_axis_frame_is_the_fourth_column(self):
        """回滚判据必须有「局部轴朝向」这一维 —— 第 27 轮用一整个红轮子换来的。

        那一轮 `rollback/pos_cm`、`box_cm`、`scale_cm` = 5.1e-05 / 1.9e-06 / 9.5e-07，
        三把尺全绿；同一轮锁骨的世界长度轴从 -162.237 度变成 -72.7144 度（差 89.5），
        双臂落位误差 0.955/1.015/0.143 → 1.957/1.844/1.720。也就是说 `.fig` 还原姿态与
        骨盒，**不还原「哪根局部轴被当成长度轴」这本账**，而 结构向/转角/射线 消费的
        恰恰是这本账。探针 M4h 从来没有朝向列，所以「载体无损」这个结论当初只覆盖到
        它能量的那三样 —— 静态这一条钉住第四样不许被删回去。
        """
        code = _code(SCRIPT)
        selftest = _between(code, "fn mmd2maxRollbackSelfTest", "fn mmd2maxApplyScale")
        self.assertIn("back[4]", selftest,
                      "自检的返回值里没有 MarkGap 的第 4 列（朝向差）")
        gap = _between(code, "fn mmd2maxMarkGap", "fn mmd2maxRollbackSelfTest")
        self.assertIn("local dAng = -1.0", gap,
                      "朝向列的「没测到」必须是 -1，不是 0 —— 否则新加的尺自己造假绿")
        caller = _between(code, 'mmd2maxReport "rollback" "saved"',
                          'mmd2maxLog "回滚自检：基准拍不出来')
        for row in ("pos_cm", "box_cm", "scale_cm", "angle_deg"):
            self.assertIn(f'mmd2maxReport "rollback" "{row}"', caller,
                          f"自检少了 {row} 这一行读数：注册表/规格表都按四列判")
        self.assertIn('mmd2maxReport "rollback" "foot_restore_angle_deg"',
                      _code(SCRIPT), "守卫退回后不比朝向 = 用同一个载体两次，只验一次")

    def test_the_snapshot_is_this_rounds_file(self):
        """基准落在 OBJ 同一个目录（一轮一个目录），文件名纯 ASCII。

        放系统临时目录就成了跨轮共享的状态 —— 上一轮的 .fig 给这一轮盖章，正是 #21
        那类「判据读到不属于本轮的产物」的形状。
        """
        code = _code(self.helpers())
        path = _between(code, "fn mmd2maxFigPath", "fn mmd2maxFigCtrl")
        self.assertIn("getFilenamePath MMD2MAX_OBJ", path)
        self.assertIn('dir + "mmd2max_rb_" + (tag as string) + ".fig"', path)
        for dead in ("getDir #temp", "getDir #export", "sysTempPath"):
            self.assertNotIn(dead, path, f"{dead} = 跨轮共享的基准")

    def test_the_carrier_is_proved_on_every_round(self):
        """自检排在**第一次骨盒写之前**：这一刻的场景是刚建好、还没被任何一刀写过的 Biped。

        探针跑过一次不够：这条路同时依赖 Max 版本、Figure 模式、「这一轮的中文目录真能
        写出文件」。三样任一变了，产品里的回滚就只有注释还在说话。
        """
        code = _code(SCRIPT)
        self.assertLess(code.index('mmd2maxReport "pass" "ran" "rollback_selftest"'),
                        code.index('mmd2maxReport "pass" "ran" "fit_segments"'),
                        "自检排到肢段拟合之后 = 基准里已经掺进了产品写的刀")
        self.assertLess(code.index('mmd2maxReport "pass" "ran" "biped_create"'),
                        code.index('mmd2maxRollbackSelfTest root limbs'),
                        "自检必须在 Biped 已经存在之后")
        selftest = _between(code, "fn mmd2maxRollbackSelfTest", "fn mmd2maxApplyScale")
        self.assertIn("mmd2maxScaleBoneAxis node lax 1.3", selftest,
                      "不扰动就没有「回滚真的把东西拿回来了」的证据，只有假绿")
        self.assertLess(selftest.index("mmd2maxScaleBoneAxis node lax 1.3"),
                        selftest.index("mmd2maxFigOp bipRoot figPath false"),
                        "顺序必须是 扰动 → 读图：先读再扰动等于什么都没验")
        self.assertIn("local pertName", selftest)
        self.assertIn('if pertName == "" do', selftest,
                      "一根能量到长度轴的骨都找不到时不许硬转，读数会全是 0 的假绿")

    def test_the_guard_sits_between_the_reach_and_the_second_snap(self):
        """守卫排在 `ran foot_reach` 之后、`ran foot_reach_guard` 之内、复摆之前。

        两条都是必须的：① 归因规则（audit_attribution）拿 ran 行切段，写在 foot_reach
        边界之前会被判成「由 foot_reach 那一段量的」；② 退回会把趾骨位置一起带走，
        复摆量的必须是退完的场景，否则 `toe_snap/residual_after_reach_cm` 是拿退回前
        的数交付。
        """
        code = _code(SCRIPT)
        reach = code.index('mmd2maxReport "pass" "ran" "foot_reach"')
        guard = code.index('mmd2maxReport "pass" "ran" "foot_reach_guard"')
        snap = code.index('mmd2maxReport "pass" "ran" "toe_snap_reach"')
        self.assertLess(reach, guard, "守卫的读数会落进 foot_reach 那一段（归因判红）")
        self.assertLess(guard, snap, "复摆排在退回之前 = 量的是没退的场景")
        block = code[reach:guard]
        for check in ("guard_saved", "foot_before_cm", "foot_after_cm",
                      "foot_worse_bones", "guard_fired"):
            self.assertIn(f'mmd2maxReport "rollback" "{check}"', block,
                          f"守卫少了 {check} 这一行：注册表里它的作者是 foot_reach_guard")
        pre = code[code.index("local rbFig = undefined"):reach]
        self.assertLess(pre.index("rbMarks = mmd2maxMarkList root limbs"),
                        pre.index("mmd2maxFigOp root rbFig true"),
                        "基准标记必须排在存图之前，否则存的是别的时刻")
        self.assertLess(pre.index("mmd2maxFigOp root rbFig true"),
                        pre.index("mmd2maxFitBoneReach frNode"),
                        "快照必须拍在接趾动手**之前**：退回去的才是没被拉过的那一幕")

    def test_the_trigger_is_the_reach_own_before_and_after_gap(self):
        """触发判的是「这一刀把盒隙拉得比动手前还远」，前后两个数都来自同一次调用。

        `gap` 会被后面的复摆改掉，所以「动手前的差」必须由 mmd2maxFitBoneReach 第 6 列
        当场带出来；阈值只能来自 CFG（MMD2MAX_RBTRIG），写死字面量就是第二个来源。
        """
        code = _code(SCRIPT)
        block = code[code.index("local rbGap0"):code.index('"foot_worse_bones"')]
        self.assertIn("abs r[6]", block, "动手前的盒隙没读进来，触发器就成了「现在还差多少」")
        self.assertIn("abs r[1]", block)
        self.assertIn("rbGap1 > rbGap0 + MMD2MAX_RBTRIG", block)
        self.assertIn("rbWorse += 1", block)
        self.assertIn("rbBefore = amax rbBefore rbGap0", block)
        self.assertIn("rbAfter = amax rbAfter rbGap1", block)
        self.assertIn("if rbWorse > 0 and rbSaveOk then", code,
                      "存图失败时只许跳过**退回**这一步：读数照样要报，整段关掉=什么都没量")

    def test_temporary_snapshots_are_cleaned_up(self):
        """两份 .fig（selftest / foot）用完都删，删不掉只留一个文件、不改判定。

        这里看的是**带注释的原文**：「允许失败」那条规则要求理由写在 `catch ()` 之后，
        剥了注释就看不见它到底在不在。
        """
        raw = SCRIPT
        self.assertEqual(raw.count("deleteFile figPath"), 1)
        self.assertEqual(raw.count("deleteFile rbFig"), 1)
        for line in raw.splitlines():
            if "deleteFile" in line:
                self.assertIn("try (", line, "删不掉是常态（文件被占用），裸删会把一轮好结果抛掉")
                self.assertLess(line.index("catch ("), line.index("-- 允许失败"),
                                "允许失败的理由必须写在 catch 之后（check_ms 的规则）")

    def test_no_dead_bip_route_anywhere(self):
        """`.bip` 那条路是死的：收 controller 报模式错、收节点报类型错（H5）。"""
        code = _code(self.helpers())
        self.assertNotIn(".bip", code)
        self.assertNotIn("biped.saveFile", code)
        self.assertNotIn("saveFigureFile", code)

    def test_the_whole_section_is_gated(self):
        """ROLLBACK=false 时这一节一行都不跑 —— 这是「关掉即几何中性」的静态证据。

        判据是「每个调用点之前 60 行内有一句提到 MMD2MAX_ROLLBACK 的门控」。这测不到
        全部（静态拿不到块边界），但它拦住最省事的那种改法：把某一行挪出门控。
        """
        code = _code(SCRIPT)
        tail = code[code.index("if MMD2MAX_ROLLBACK do"):]
        lines = tail.splitlines()
        gates = [i for i, line in enumerate(lines) if "MMD2MAX_ROLLBACK" in line]
        self.assertTrue(gates, "脚本里已经没有回滚门控了")
        callers = re.compile(r"mmd2max(FigOp|MarkList|MarkGap|FigPath|RollbackSelfTest)\b")
        strays = [f"第 {i} 行：{line.strip()[:70]}"
                  for i, line in enumerate(lines)
                  if callers.search(line) and not any(g < i <= g + 60 for g in gates)]
        self.assertEqual(strays, [], f"这些回滚调用跑在门控之外：{strays}")

    def test_registry_and_script_agree_on_the_two_new_passes(self):
        """注册表里两条新 pass 的形状：都是 optional，且守卫的前提是接趾本身。"""
        selftest = passes.BY_ID["rollback_selftest"]
        guard = passes.BY_ID["foot_reach_guard"]
        self.assertTrue(selftest.optional and guard.optional,
                        "不 optional 的话 ROLLBACK=false 的默认轮会自己判红")
        self.assertIn("foot_reach", guard.requires)
        self.assertLess(passes.order_of("foot_reach"), passes.order_of("foot_reach_guard"))
        self.assertLess(passes.order_of("foot_reach_guard"),
                        passes.order_of("toe_snap_reach"))
        self.assertLess(passes.order_of("biped_create"), passes.order_of("rollback_selftest"))
        self.assertLess(passes.order_of("rollback_selftest"),
                        passes.order_of("fit_segments"))
        # 自检扰动完就把场景放回原处（读的是自己刚存的图），所以它不作废任何读数；
        # 守卫退回的是整段接趾，凡被接趾改过的读数都必须重量 —— 靠 moves=True 声明。
        self.assertEqual(selftest.invalidates, ())
        self.assertTrue(set(passes.POSE_FACTS) <= set(guard.invalidates),
                        "守卫不作废姿态读数 = 退回之后拿退回前的数交付")
        for pass_obj in (selftest, guard):
            for phase, check in pass_obj.reads:
                self.assertEqual(passes.authors_of(phase, check), (pass_obj.id,),
                                 f"{phase}/{check} 的作者不是 {pass_obj.id}，报告行会判错段")


class ConfigSingleSourceContract(unittest.TestCase):
    """M1b：装配配置只有一个来源（MMD2MAX.CFG），缺配置就中止，绝不退回默认值。

    v3 的账：`obj_run.build_script` 在生成的 .ms 头部逐行注入 22 个 global，于是
    「自动跑的」和「人手 fileIn 这份模板的」是两套配置来源 —— 手跑少写一个 flag 就
    摆出不同的几何，两边日志照样全绿。M1b 之后手跑只需要设 `MMD2MAX_CFG` 一个全局。
    另一半动机是「默认值就是那条日志绿、场景错的路」：缺 spine_links 退 3、缺
    foot_pitch 退 true，每一次退都改变几何，而改变本身不留任何一行日志。
    """

    def test_main_reads_cfg_before_anything_else(self):
        main = _between(SCRIPT, "fn mmd2maxMain =", "try\n(\n    mmd2maxMain()")
        code = _code(main)
        self.assertIn("mmd2maxLoadCfg()", code)
        self.assertLess(code.index("mmd2maxLoadCfg()"), code.index("mmd2maxPrepareScene"),
                        "配置没读成之前连重置场景都不许做")
        self.assertLess(code.index("mmd2maxLoadCfg()"), code.index("mmd2maxInitLog()"),
                        "InitLog 打的这几行现在直接读 global，顺序反了就打印 undefined")

    def test_declaration_block_has_no_defaults(self):
        """顶部那些行只许声明名字（`global X`），一句都不许带 `=`。
        值一旦能写在声明处，「CFG 缺键」和「CFG 配了同一个值」就再也分不出来了。"""
        decl = SCRIPT[:SCRIPT.index("fn mmd2maxIsInteractive")]
        assigned = re.findall(r"^global\s+MMD2MAX_\w+\s*=", decl, flags=re.M)
        self.assertEqual(assigned, [], f"装配配置不许有默认值：{assigned}")
        # 21（M1b 的路径与开关，#56 删掉恒为 true 的 QUIET）+ 5（M2a 的目标值）
        # + 1（配置摘要，LoadCfg 算、Main 打）
        # + 2（M2b 的判据通道：规格表路径 + 报告路径）+ 1（M3 的收敛遍数上限）
        # + 2（M4d 的回滚位：快照开关 + 触发阈值）
        # + 6（#70 的胸部取景框：三轴各一对 —— 框是导出的事，脚本只读）
        # + 6（#71 的颈部取景框：同一套机制、另一组骨，所以是另一对全局名）
        # + 1（#84 的刷新调用缓存 MMD2MAX_REFRESHCALL：它不是配置，是「探针实测哪个
        #   刷新调用真跑得通」的赢家名字。这一项必须留在这份名单里数着 —— 顶部这块
        #   唯一的例外规则是「不许带 =」，加进来的每个全局名都得说清为什么存在。）
        # + 1（#99 的短骨门限 MMD2MAX_STUBCM：以前这个 1.0 在 .ms 里抄了三处、Python 另有
        #   一份，靠契约测试对撞；规则 IV 收的就是这类数）
        names = re.findall(r"^global\s+MMD2MAX_(\w+)$", decl, flags=re.M)
        self.assertEqual(len(names), 46)
        for target in ("DIRTOL", "ROLLTOL", "TOETOL", "GAPTOL", "FOOTREACH"):
            self.assertIn(target, names,
                          "M2 起这五个判据必须由 CFG 供值，声明都缺就说明改回写死在脚本里了")
        self.assertIn("CONVERGE_MAX", names,
                      "收敛遍数也得走 CFG：它写回字面量的那天，注册表和循环就又分叉了")
        # M4d 的两条：ROLLBACK 决定「有没有这条退路」，RBTRIG 决定「什么时候走」。
        # 后者是判据（厘米阈值），只能住在 targets.py；在 .ms 里留字面量就是第二个来源。
        # STUBCM 同理：短骨门限两侧各一份时，改一边只会让规格表悄悄多/少一行。
        for switch in ("ROLLBACK", "RBTRIG", "STUBCM"):
            self.assertIn(switch, names, f"{switch} 必须由 CFG 供值，不许写死在脚本里")

    def test_cfg_accessors_abort_instead_of_defaulting(self):
        strFn = _between(SCRIPT, "fn mmd2maxCfgStr", "fn mmd2maxCfgBool")
        boolFn = _between(SCRIPT, "fn mmd2maxCfgBool", "fn mmd2maxCfgInt")
        loadFn = _between(SCRIPT, "fn mmd2maxLoadCfg", "fn mmd2maxImportObj")
        self.assertIn("throw", _code(strFn), "缺键必须抛，不许 return \"\"")
        self.assertIn("MMD2MAX.CFG 里没有键", strFn)
        self.assertIn('if s == "true" do return true', _code(boolFn))
        self.assertIn('if s == "false" do return false', _code(boolFn))
        self.assertIn("throw", _code(boolFn), "1/0、True、yes 这类值要报错，不是悄悄当 false")
        for key in ("MMD2MAX_OBJ", "MMD2MAX_BONES", "MMD2MAX_WEIGHTS", "MMD2MAX_MAP",
                    "MMD2MAX_LIMBS", "MMD2MAX_OUTPUT", "MMD2MAX_MARKER", "MMD2MAX_LOG",
                    # M2b：这两个键一旦回退成「脚本里自己拼个路径」，规格表与报告就会
                    # 各指各的文件，拼表时只剩一句「报告里 0 行」。
                    "MMD2MAX_VERDICTS", "MMD2MAX_REPORT"):
            self.assertIn(f'mmd2maxCfgStr "{key}"', loadFn, f"CFG 里少了 {key} 的读取")

    def test_optional_number_distinguishes_absent_from_bad(self):
        """#70/#71 的 MMD2MAX_{CHEST,NECK}_{X,Y,Z}_{LO,HI} 是「允许空」的**数值**键，
        这里要分清三件事：
        没给（取景退回场景包围盒 + 高度比例带）、给了读不出（必须抛）、给了但越界（必须抛）。
        把它们合成一条 `catch ( 0.0 )` 就正好复刻本项目反复踩的那类默认值：日志绿、
        取景框却是另一个数。"""
        optFn = _between(SCRIPT, "fn mmd2maxCfgNumOpt", "fn mmd2maxLoadCfg")
        code = _code(optFn)
        self.assertIn("local s = mmd2maxCfgStr key", code,
                      "空与非空都归 mmd2maxCfgStr 判：键真的不在 CFG 里时照抛")
        self.assertIn('if s == "" do return undefined', code,
                      "空串的唯一含义是 undefined（= 这具模型量不出那张特写的骨）")
        self.assertIn("mmd2maxCfgNum key low high", code,
                      "非空必须走同一套数值校验，不许另起一条宽松的路")
        self.assertNotIn("catch", code,
                         "这一层一 catch 就把「CFG 写了个读不出的数」变成静默默认值")
        loadFn = _between(SCRIPT, "fn mmd2maxLoadCfg", "fn mmd2maxImportObj")
        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        import obj_run
        for prefix in obj_run.FRAMINGS:
            for key in obj_run.framing_cfg_keys(prefix):
                self.assertIn(f'mmd2maxCfgNumOpt "{key}"', loadFn, f"CFG 里少了 {key} 的读取")

    def test_every_framing_box_enters_the_config_summary(self):
        """每张特写的框都得进配置摘要，且「量的框」与「猜的带」必须是两种看得出来的形状。

        第 35/36 轮用的框根本没框住骨，日志却是绿的 —— 因为「框」在日志里只有一个形状。
        #71 把颈部接上同一套机制时最容易漏的就是摘要：取景那段 if/else 与 report 都在，
        摘要却还只写胸框，于是换一具模型退成兜底带时没人看得出来。
        文案「无(退回场景包围盒带)」只许住在 mmd2maxBoxSummary 一处：两处各写一遍，
        迟早一处说「无」一处说数字。
        """
        loadFn = _between(SCRIPT, "fn mmd2maxLoadCfg", "fn mmd2maxImportObj")
        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        import obj_run
        # 每次调用被自己的函数名切出来：那一段里只该有那一张特写的六个全局名。
        segs = " ".join(loadFn.split()).split("(mmd2maxBoxSummary ")
        self.assertEqual(len(segs) - 1, len(obj_run.FRAMINGS),
                         "摘要里的框条目数量和张数不一样：有一张特写没进摘要")
        for prefix in obj_run.FRAMINGS:
            keys = obj_run.framing_cfg_keys(prefix)
            hit = [s for s in segs[1:] if all(k in s for k in keys)]
            self.assertEqual(len(hit), 1,
                             f"{prefix} 的框没进配置摘要（或进了两处）：这一轮用的哪只框事后无从查证")
        # 注释里也会举这两种形状的例子（「 颈框=无(退回场景包围盒带)」），所以只数代码。
        self.assertEqual(_code(SCRIPT).count("无(退回场景包围盒带)"), 1,
                         "「退回兜底带」这句话只许住在 mmd2maxBoxSummary 一处")

    def test_the_script_reads_exactly_the_keys_the_launcher_writes(self):
        """CFG 的键名单与脚本的读取名单必须**互为子集**，两边只许有一份。

        这不是形式主义：`obj_run` 写 36 行而脚本只读 35 行时，那一个键的值就只存在于
        CFG 文件里 —— 日志、.max、规格表全都看不见它，而「改了配置几何没变」正是本项目
        最难查的那类事故（第 35 轮的取景带写在 .ms 里、CFG 里没有，就是这么躲过所有表的）。
        反过来脚本读一个 CFG 没写的键，`mmd2maxCfgStr` 会抛，整轮失败：那种至少会响，
        所以这里两个方向一起钉。
        """
        loadFn = _between(SCRIPT, "fn mmd2maxLoadCfg", "fn mmd2maxImportObj")
        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        import obj_run
        read = set(re.findall(r'mmd2maxCfg(?:Str|Bool|Int|Num|NumOpt) "(MMD2MAX_\w+)"', loadFn))
        self.assertEqual(read, set(obj_run.CFG_KEYS),
                         "写的一方与读的一方名单分叉了：差集就是那个「配了没人读」的键")

    def test_error_handler_survives_a_cfg_failure(self):
        """最外层 catch 自己绝不能再抛：配置没读成时 MMD2MAX_MARKER 是 undefined、
        `mmd2maxIsInteractive()` 返回 undefined，而 `if undefined do ...` 又是一次运行时错误
        —— 于是失败原因被「处理器自己炸了」盖掉，屏幕上只剩一个没人点的模态框
        （用户不在电脑前就是死锁）。"""
        tail = SCRIPT[SCRIPT.rindex("try\n(\n    mmd2maxMain()"):]
        code = _code(tail)
        self.assertNotIn("MMD2MAX_MARKER + \".error.txt\"", code,
                         "裸拼 MARKER 在配置失败时是 undefined + 字符串 = 二次抛错")
        self.assertIn("mmd2maxErrorFilePath()", code)
        self.assertIn("mmd2maxIsInteractiveNow()", code)
        self.assertNotIn("if mmd2maxIsInteractive() do", code,
                         "错误处理器里只许用带 bool 兜底的那个判定")
        helper = _between(SCRIPT, "fn mmd2maxErrorFilePath", "fn mmd2maxSkinEnabled")
        self.assertIn("mmd2max_cfg.error.txt", helper,
                      "定名文件要与 tools/obj_run.py 的 CFG_ERROR_NAME 对上")

    def test_data_reads_must_not_swallow_into_an_empty_table(self):
        """肢段/粗细两张表以前包在 `try ( … ) catch ( #() )` 里：读表报错被吞成
        「条目：0」，然后走「跳过拟合」那一句 —— 骨架退回 Biped 默认比例，日志一句错没有。"""
        code = _code(SCRIPT)
        self.assertNotIn("catch ( limbs = #() )", SCRIPT)
        self.assertNotIn("catch ( widths = #() )", SCRIPT)
        self.assertIn("local limbs = mmd2maxReadLimbs (MMD2MAX_LIMBS as string)", code)
        self.assertIn("if limbs.count == 0 do", code, "读空也要抛，不能默默跳过整段拟合")


class AccessoryBoneDirReadbackContract(unittest.TestCase):
    """#68：附属骨方向回读（`bone_rb`）在 .ms 那一侧的形状。

    第 32 轮的账：胸部骨那一刀（`_aim_breast_bones`，把 tail 改成从网格量出的乳头点）
    跑完之后，日志与报告里「胸」字出现 0 次 —— 附属骨那一段只报「挂 Biped=54 /
    挂 Bone=214」，一根骨**指哪**没有任何读数。于是那一轮的「与上一轮无实质差异」
    只证明了几何没被改坏，证明不了方向是对的。这组测试钉的是「读数确实由场景产生」
    这件事本身：量哪根轴、什么时候量、和谁对账。
    """

    @staticmethod
    def section() -> str:
        return _between(SCRIPT, "fn mmd2maxVerifyAccessoryDirs", "fn mmd2maxCreateBones")

    @staticmethod
    def main_slice() -> str:
        return _between(SCRIPT, "fn mmd2maxMain =", "try\n(\n    mmd2maxMain()")

    def test_readback_is_the_last_thing_before_the_scene_is_saved(self):
        """「写入那一刻的方向」不描述保存下来的场景：改挂要写回 worldTM、物理代理会换父级，
        所以只能在**所有会挪骨的段落之后**量，而且必须在 saveMaxFile 之前 ——
        排在保存之后的话，量的还是同一个场景，但注册表里它就成了「保存后还能改东西」。
        """
        main = self.main_slice()
        at = main.index("mmd2maxVerifyAccessoryDirs boneNodes")
        for mover in ("local boneNodes = mmd2maxCreateBones()",
                      "mmd2maxApplySkin meshes boneNodes",
                      "mmd2maxBuildPhysics boneNodes physFile",
                      "mmd2maxZoomFit()"):
            self.assertLess(main.index(mover), at, f"{mover} 排在回读之后 = 量完还能挪")
        self.assertLess(at, main.index("saveMaxFile MMD2MAX_OUTPUT"))

    def test_the_pass_reports_its_own_boundary(self):
        """注册表里 bone_dir_verify 是必经 pass，缺 ran 行就是 STALE/缺段判据（M3）。
        ran 行必须紧贴这一段：隔远了「报过 ran」和「真量过」就不是同一件事。
        """
        main = self.main_slice()
        call = main.index("mmd2maxVerifyAccessoryDirs boneNodes")
        ran = main.index('mmd2maxReport "pass" "ran" "bone_dir_verify" 1')
        self.assertLess(call, ran, "ran 排在建段之前 = 还没量就宣称跑过")
        self.assertLess(ran - call, 200, "ran 与调用之间塞进了别的东西")

    def test_the_measured_axis_is_the_bone_length_axis(self):
        """建骨传的是 `head -> head + xaxis * len`，长度轴是**局部 X**，
        所以回读只能比 `transform.row1`。量 row2/row3 会恒读 90 度 ——
        那不是「判据松紧」问题，是一条永远红的假判据。
        """
        code = _code(self.section())
        self.assertIn("mmd2maxVecAngleDeg tm.row1 want", code)
        for wrong in ("tm.row2", "tm.row3", ".transform.row2", ".transform.row3"):
            self.assertNotIn(wrong, code, f"{wrong} 垂直于骨，与 tail-head 恒 90 度")

    def test_row_count_mismatch_throws_instead_of_misaligning(self):
        """subject 的行号直接来自 boneNodes 的下标：两边差一行就是拿 A 骨的方向验 B 骨，
        而 227 条读数里只有错位的那几条会超标 —— 报告看起来像「几根骨歪了」。
        """
        code = _code(self.section())
        self.assertIn("if rows.count != boneNodes.count do", code)
        self.assertIn("throw (", code)

    def test_only_bone_geometry_nodes_are_measured(self):
        """人形骨复用 Biped 节点，它们的朝向归 struct_dir / box_rb（Biped 的「方向」是
        #dir/#scale，不是 transform.row1）。不筛的话同一根骨被两套判据抢。
        """
        code = _code(self.section())
        self.assertIn("if (classOf nd) != BoneGeometry do continue", code)

    def test_subject_carries_the_row_index_from_the_table(self):
        """改名重名时 Max 自动加后缀，`nodes[i].name` 已经不是 bones.tsv 里那个词；
        行号唯一且两边同源。`(i - 1)`：表是 1 基、tsv 的 idx 列是 0 基。
        """
        code = _code(self.section())
        self.assertIn('local subj = ((i - 1) as string) + "|" + (p[cName] as string)',
                      code)

    def test_the_section_writes_no_transform(self):
        """回读段必须对几何中性：它排在保存前，任何一次 setTransform 都会让
        「这轮与上轮无实质差异」变成假话，也会让 MMD2MAX.ROLLBACK 的开与关不再是同一场景。
        """
        code = _code(self.section())
        for setter in ("setTransform", "transform =", "#dir", "#scale", "rotate ",
                       "scaleX", "mmd2maxFigOp"):
            self.assertNotIn(setter, code, f"回读段里不许有写入：{setter}")

    def test_max_reports_numbers_and_holds_no_expectation(self):
        """M2b：判定在 Python 侧。这段里除了 over_tol 那个计数器，连「多少度算超标」
        都不许写死 —— 门限的唯一来历是 CFG 的 MMD2MAX_DIRTOL。
        """
        code = _code(self.section())
        for verdict_word in ("PASS", "FAIL", "失败", "判红"):
            self.assertNotIn(verdict_word, code)
        self.assertIn("if ang > MMD2MAX_DIRTOL do overTol += 1", code)
        self.assertNotIn("if ang > 3", code, "角度门限不许在 .ms 里另写一个数")

    def test_every_bone_rb_report_line_the_script_emits_is_known(self):
        """逐骨 dir_deg + 8 条聚合 = 9 个 report 调用。少一条就是规格表里多出一行
        「永远不会有人填」的判据（那条对撞本身在 MaxChannelContractTests 里）。
        """
        found = re.findall(r'mmd2maxReport "bone_rb" "([^"]+)"', self.section())
        self.assertEqual(sorted(found), sorted(
            ["dir_deg", "measurable", "short_skipped", "read_fail", "over_tol",
             "worst_deg", "worst_bone", "pivot_max_cm", "pivot_max_bone"]))

    def test_the_log_line_names_the_worst_bone(self):
        """227 条读数超标时，不先把报告 sort 一遍就不知道去看哪根骨 ——
        而这一轮跑完 Max 就 quitMax 了，场景在 190MB 的 .max 里。
        """
        code = _code(self.section())
        self.assertIn('mmd2maxLog ("附属骨回读：量到="', code)
        self.assertIn("+ worstSubj", code)
        self.assertIn("+ pivotSubj", code)


class ShotFramingContract(unittest.TestCase):
    """#69：特写「够不够特写」必须是一个读数，不是一句「图里有头」。

    第 33 轮的账：那一轮的报告、CHECK、日志全绿，而 `chest_front` 那张里胸部骨只有
    20 个像素 —— 因为取景框的水平跨度被张开的手臂撑到 119cm，`zoomToBounds` 拿最大的
    那一维贴视口，19.7cm 高的胸带纵向只剩 18% 画面。没有任何一行读数描述过这件事，
    所以「截图段跑过了」和「截图能用来验收」是两件没人对账的事。
    这组测试钉三件事：① tag 名单两边同源（Python 定尺，.ms 定拍法）；② 只有按高度
    比例定义的带才被收紧（按节点算出来的框，水平跨度就是内容本身，收紧会裁掉内容）；
    ③ 填充率量的是**真被取景的那只框**，而且排在取图之前（视口坏了也要留下这一行）。
    """

    @staticmethod
    def one() -> str:
        return _code(_between(SCRIPT, "fn mmd2maxShotOne outDir",
                              "fn mmd2maxShotOneTight"))

    @staticmethod
    def tight() -> str:
        return _code(_between(SCRIPT, "fn mmd2maxShotOneTight",
                              "fn mmd2maxScreenshot ="))

    @staticmethod
    def shots() -> str:
        return _between(SCRIPT, "fn mmd2maxScreenshot =", "fn mmd2maxPrepareScene")

    def test_the_tag_list_has_one_owner_and_two_uses(self):
        """名单住在 Python（verdicts.SHOT_TAGS），.ms 只许照着拍。

        分叉的两个方向都会红，而且都不是「红得没道理」：.ms 多一个 tag ⇒ 报告里出现
        规格表不认识的行（orphan）；.ms 少一个 ⇒ `shot_rb/tags` 那条 gate 数出来。
        """
        calls = re.findall(
            r'mmd2maxShotOne(?:Tight)? outDir baseName "([^"]+)"', self.shots())
        self.assertEqual(sorted(calls), sorted(verdicts.SHOT_TAGS),
                         "调用点与 Python 的名单分叉了")
        self.assertEqual(len(calls), len(set(calls)),
                         "同一个 tag 被拍两次：fill_pct 会被后写的那次盖掉，图上却有两张同名文件")

    def test_only_the_height_ratio_bands_get_tightened(self):
        """紧的只有颈部/胸部那 6 张：它们的框按包围盒**高度**比例定义，水平 ±10cm
        是余量，撑开它的却是手臂 —— 收水平跨度不裁任何要看的东西。
        脚/手那几张按节点落位算框，水平跨度就是内容本身，所以它们不许紧。
        「紧」在 .ms 那一侧是**函数名**（ShotOneTight），不是可选参数：见
        test_tightening_is_a_separate_function_not_an_optional_param。
        """
        tight = re.findall(
            r'mmd2maxShotOneTight outDir baseName "([^"]+)"', self.shots())
        self.assertEqual(sorted(tight), sorted(verdicts.SHOT_TIGHT_TAGS),
                         "走 ShotOneTight 的调用点与 Python 的名单分叉")
        self.assertTrue(set(verdicts.SHOT_TIGHT_TAGS) <= set(verdicts.SHOT_TAGS),
                        "tight 名单里有不在名单里的 tag：那一行 fill_pct 永远没人报")
        for tag in verdicts.SHOT_TIGHT_TAGS:
            hit = re.search(
                rf'mmd2maxShotOneTight outDir baseName "{tag}" #view_\w+ (\w+) (\w+)',
                self.shots())
            self.assertIsNotNone(hit, tag)
            self.assertIn(hit.group(1), ("chestLo", "neckLo"),
                          f"{tag} 紧的是非带形框：{hit.group(1)} 由节点算出来，收紧会裁掉内容")

    def test_fill_is_measured_on_the_box_that_was_framed(self):
        """量的是真被取景的那只框：报「框该有多满」而实际取景用了另一只框，
        这一行就是在给一个不存在的画面盖章。
        收紧之后这件事由两段共同保证：ShotOne 里 fit 与 fill 拿的是**同一对** lo/hi，
        而收过的框只在 ShotOneTight 里产生、收完立刻交进 ShotOne（中间没有第二次改框）。
        """
        code = self.one()
        self.assertIn("mmd2maxFitView viewKind lo hi", code)
        self.assertIn("mmd2maxShotFill viewKind lo hi", code)
        self.assertNotIn("mmd2maxAspectBox", code,
                         "ShotOne 自己不收框：收框只有一条路，读数与画面才不会分叉")
        tight = self.tight()
        self.assertIn("local tightBox = mmd2maxAspectBox viewKind lo hi", tight)
        self.assertIn("local fitLo = tightBox[1]", tight)
        self.assertIn("local fitHi = tightBox[2]", tight)
        self.assertIn("mmd2maxShotOne outDir baseName tag viewKind fitLo fitHi", tight)

    def test_fill_row_is_written_before_the_dib_is_grabbed(self):
        """填充率是取景这一段的读数，与「视口能不能给出位图」无关。排在取图之后的话，
        DIB 一坏这一行就没了，而 when=if_present 会把「没行」读成合法缺席 ——
        视口坏掉就伪装成「这轮没开截图」。
        """
        code = self.one()
        self.assertLess(code.index('mmd2maxReport "shot_rb" "fill_pct" tag fill'),
                        code.index("gw.getViewportDib()"))
        self.assertLess(code.index("mmd2maxFitView viewKind lo hi"),
                        code.index('mmd2maxReport "shot_rb" "fill_pct" tag fill'),
                        "取景之前量填充率 = 量的是上一张图的相机")

    def test_tightening_is_a_separate_function_not_an_optional_param(self):
        """第 34 轮的账：`fn mmd2maxShotOne ... /tight:false =` 在 Max 2022 里是**编译期**
        语法错误（解析探针原文「-- Syntax error: at /, expected =」），整份脚本一行不跑，
        日志、error.txt、报告全是空的，只剩一个读不到文字的 Auto-load 弹窗。
        所以收紧必须由一个独立函数承担，而 ShotOne 的签名必须是纯位置参数。
        """
        self.assertIn("fn mmd2maxShotOne outDir baseName tag viewKind lo hi =", SCRIPT)
        self.assertIn("fn mmd2maxShotOneTight outDir baseName tag viewKind lo hi =", SCRIPT)
        for sig in ("fn mmd2maxShotOne outDir", "fn mmd2maxShotOneTight outDir"):
            head = SCRIPT[SCRIPT.index(sig):].split("=", 1)[0]
            self.assertNotIn("/", head, f"{sig} 的参数表里又有斜杠可选参数")
        # 收紧的读数留在日志里：框被收过没有、收成什么样，事后要能查
        self.assertIn("紧框：", self.tight())

    def test_projection_uses_the_api_that_exists_on_this_max(self):
        """探针实测（Max 2022）：`gw.viewToScreen` 根本没有这个属性，能用的投影是
        `gw.transPoint`；`gw.GetVPWorldWidth` 要的是 Point3 而不是数字。
        投不出来必须返回 -1.0 这种**刺眼**的数，而不是被 try/catch 吞成「没有这一行」。
        """
        fill = _code(_between(SCRIPT, "fn mmd2maxShotFill", "fn mmd2maxAspectBox"))
        self.assertIn("gw.transPoint", fill)
        self.assertNotIn("gw.viewToScreen", fill)
        self.assertIn("return -1.0", fill)
        self.assertIn("gw.getWinSizeY()", fill)
        self.assertIn("100.0 * dPx / winY", fill)

    def test_aspect_box_only_ever_shrinks_the_horizontal_span(self):
        """收 = 只在框比视口「更宽」时把水平两侧往里挪；纵向一个字都不动，
        也不许把框放大（放大等于把别的内容拉进画面，那是上一轮 foot_top 的毛病）。
        """
        box = _code(_between(SCRIPT, "fn mmd2maxAspectBox", "fn mmd2maxFitView"))
        self.assertIn("if want >= hSpan do return #(lo, hi)", box)
        self.assertIn("newLo[hAxis] = mid - want / 2.0", box)
        self.assertIn("newHi[hAxis] = mid + want / 2.0", box)
        self.assertNotIn("newLo[vAxis]", box)
        self.assertNotIn("newHi[vAxis]", box)
        self.assertIn("gw.getWinSizeX()", box)

    def test_the_shot_pass_reports_its_own_boundary_and_two_counters(self):
        """ran 行排在还原之后（还原没做完不算收工），两条聚合计数排在 ran 之前。
        `tags` 的期望值 = Python 名单的长度：少一张图只是少一行读数的话，
        「哪几张真被量过」就又回到只有肉眼知道了。
        """
        body = _code(self.shots())
        self.assertIn('mmd2maxReport "shot_rb" "tags" "-" MMD2MAX_SHOTFILL.count', body)
        self.assertIn('mmd2maxReport "shot_rb" "projection_fail" "-" fillFail', body)
        ran = body.index('mmd2maxReport "pass" "ran" "shot_capture" 1')
        self.assertLess(body.index("mmd2maxRestoreShot()"), ran)
        self.assertLess(body.index('mmd2maxReport "shot_rb" "tags"'), ran,
                        "ran 之前没人计数 = 这一段自己不知道量了几张")

    def test_the_registry_puts_shot_capture_last_and_optional(self):
        """它是报告的最后一段（截图排在 saveMaxFile 与成功标记之后），
        而且 optional：--shot 没开时整段合法缺席。
        """
        self.assertEqual(passes.PASSES[-1].id, "shot_capture")
        item = passes.BY_ID["shot_capture"]
        self.assertTrue(item.optional)
        self.assertEqual(item.writes, ("shot_rb",))
        self.assertNotIn("shot_rb", passes.POSE_FACTS,
                         "填充率与「Biped 有没有被挪过」无关，进作废名单就是噪音")


class ShotClippingContract(unittest.TestCase):
    """#72：特写只许画取景框里的骨，而「有没有真裁」必须是一个读数。

    round38 的证据是 `香菱_bone_foot_top.png`：取景框只到 z=19.7，可画面正中那一大坨是
    头骨（z≈130），四周一圈是手指与辫子，两只鞋在画面下缘只剩几十像素 —— Max 的
    `zoomToBounds` 只定视野、不裁深度，所以框外的东西照样进同一个投影。代码里那句
    「俯视那张已经判不了脚」就是把这件事当成了既成事实，而用户要的「顶视看外八」从来
    没拍到过。这组测试钉四件事：① 裁剪只动骨（`mmd2maxIsBoneNode`：附属骨 + Biped 骨盒），
    且判「在不在框里」用 pivot **或** bounds（BoneGeometry 的 .bounds 可能是零尺寸，只信一个就会看错）；
    ② 名单是每张自己的，还原只还原这一张藏过的；③ 裁与还都住在 ShotOne 里，
    所以 19 张图没有漏掉某一张的写法；④ `clip_hidden` 的期望值两边同源，
    而且方向相反（整场景那几张必须是 0，特写必须 ≥1）—— 单看「裁了几根」永远绿，
    只有「全身那张一根都没裁」这条一起绿，才说明取景框本身没被谁改小。
    """

    @staticmethod
    def clip() -> str:
        return _code(_between(SCRIPT, "fn mmd2maxClipBones", "fn mmd2maxUnclipBones"))

    @staticmethod
    def unclip() -> str:
        return _code(_between(SCRIPT, "fn mmd2maxUnclipBones", "fn mmd2maxPrepareShot"))

    @staticmethod
    def hit() -> str:
        return _code(_between(SCRIPT, "fn mmd2maxBoxHit", "fn mmd2maxIsBoneNode"))

    @staticmethod
    def census() -> str:
        return _code(_between(SCRIPT, "fn mmd2maxBoneCensus", "fn mmd2maxClipBones"))

    @staticmethod
    def pred() -> str:
        return _code(_between(SCRIPT, "fn mmd2maxIsBoneNode", "fn mmd2maxBoneCensus"))

    @staticmethod
    def one() -> str:
        return _code(_between(SCRIPT, "fn mmd2maxShotOne outDir",
                              "fn mmd2maxShotOneTight"))

    @staticmethod
    def shots() -> str:
        return _between(SCRIPT, "fn mmd2maxScreenshot =", "fn mmd2maxPrepareScene")

    def test_bone_predicate_covers_both_kinds_of_bone(self):
        """「这是不是一根骨」只许有一条规则，而这条规则的两条腿都得是实测来的。

        两轮猜类名都猜错：round39 只按 BoneGeometry 筛，foot_top 藏掉 268 根 = 附属骨总数，
        53 根 Biped 骨盒一根没动，画面正中那一大坨还是骨盆盒；round40 补上
        `classOf o.controller == Biped_Control`（本文件清探针残留段用过的写法），分母**还是
        268** —— 那条一个都没认到。round41 的逐类计数把这件事算死了：`认定为骨=322`，
        而 322 = BoneGeometry 268 + Biped_Object 54，正好是两类类名之和 ⇒ 控制器那条
        一个对象都没多认到，于是删掉。所以这里钉的是：
        ① 两条类名腿都在（少一条就是悄悄退回 round39 的漏法）；
        ② 没有第三条没证据的猜测（`Biped_Control` 已被 322=268+54 判死，留着它下一个人
           就会以为「Biped 是靠控制器认的」）；
        ③ 网格类不许进来（round41 实测网格是 62 个 Editable_mesh，每个包围盒都盖住全身，
           裁它等于什么都没裁）；④ 分母与裁剪用同一个谓词，否则 `clip_hidden` 读不出
           「漏了哪一类」。
        """
        pred = self.pred()
        self.assertIn("(classof o) == BoneGeometry", pred)
        self.assertIn("(classof o) == Biped_Object", pred,
                      "本文件自己的探针记录写着「Biped 链接节点的 classof = Biped_Object」，"
                      "round41 的 322=268+54 证明起作用的就是它")
        self.assertNotIn("Biped_Control", pred,
                         "控制器那条已被逐类计数判死（一个对象都没多认到）：留着 = 下一个人"
                         "以为 Biped 靠控制器认")
        for mesh in ("Editable_Poly", "Editable_Mesh", "TriangleMesh"):
            self.assertNotIn(mesh, pred, f"网格不是骨：{mesh} 出现在谓词里等于按对象裁整场景")
        self.assertIn("if (mmd2maxIsBoneNode o) do", self.clip(),
                      "裁剪段自己不许再写一份类名判断")
        prepare = _code(_between(SCRIPT, "fn mmd2maxPrepareShot", "fn mmd2maxRestoreShot"))
        self.assertIn("mmd2maxBoneCensus()", prepare)
        self.assertIn('mmd2maxReport "shot_rb" "clip_bones_total" "-" census[2]', prepare,
                      "分母只报一个光秃秃的数就看不出「少的是哪一类」——必须是那句逐类计数")
        self.assertIn("if (mmd2maxIsBoneNode o) do total += 1", self.census(),
                      "分母必须与裁剪同一把尺，否则 clip_hidden 读不出「漏了哪一类」")

    def test_only_bones_outside_the_box_are_hidden_and_only_they_are_relisted(self):
        clip = self.clip()
        self.assertNotIn("Editable_Poly", clip,
                         "网格不参与按对象裁剪：身体是一个 Editable_Poly，包围盒盖住全身，"
                         "裁它等于什么都没裁")
        # 判「在不在框里」两条都走，缺一不可
        self.assertIn("o.transform.translationpart", clip,
                      "pivot 不看：关节在框外、骨身伸进框的骨（大腿根在髋）会被藏掉")
        self.assertIn("o.bounds", clip,
                      "bounds 不看：整根都在框里但 pivot 在框外的骨会被藏掉")
        self.assertIn("mmd2maxBoxHit", clip)
        self.assertNotIn("intersects", self.hit(),
                         "没在 Max 2022 里实测过的 API 不许用（本文件反复踩过这条）")
        # 本来就藏着的骨不进名单，否则还原会把用户自己藏的东西显示出来
        self.assertIn("(try ( o.isHidden ) catch ( true )) == false", clip)
        # 计数排在写回读之后：藏没藏上只有 Max 知道
        self.assertLess(clip.index("(try ( o.isHidden ) catch ( false ))"),
                        clip.index("append MMD2MAX_SHOTCLIP o"),
                        "没回读就计数 = clip_hidden 报的是「想藏几根」而不是「藏了几根」")

    def test_restore_uses_this_shots_own_list(self):
        unclip = self.unclip()
        self.assertIn("for o in MMD2MAX_SHOTCLIP do", unclip)
        self.assertIn("o.isHidden = false", unclip)
        self.assertIn("MMD2MAX_SHOTCLIP = #()", unclip)
        self.assertNotIn("for o in objects", unclip,
                         "对全部对象清 isHidden 会把用户本来就藏着的对象显示出来"
                         "（mmd2maxRestoreShot 同一条理由）")
        # 兜底：还原段自己也收一次尸，一张图中途抛异常不会把名单挂到下一张
        restore = _code(_between(SCRIPT, "fn mmd2maxRestoreShot", "fn mmd2maxScreenshot ="))
        self.assertIn("mmd2maxUnclipBones()", restore)

    def test_every_shot_clips_and_restores_inside_itself(self):
        """裁与还都住在 ShotOne 里 ⇒ 19 个调用点共用同一条，没有「某张忘了裁」这种写法。"""
        one = self.one()
        self.assertLess(one.index("mmd2maxClipBones lo hi"),
                        one.index("mmd2maxFitView viewKind lo hi"),
                        "取景之后才裁 = 第一帧的相机是上一张的")
        self.assertLess(one.index('mmd2maxReport "shot_rb" "clip_hidden" tag clipN'),
                        one.index("gw.getViewportDib()"),
                        "取图之后才报数 = DIB 一坏这一行就没了，而 when=if_present 会把"
                        "「视口坏了」读成「这轮没裁」")
        branch = one.index("if bmp == undefined then")
        self.assertLess(one.index("mmd2maxUnclipBones()"), branch,
                        "还原排在两条出口之后 = 取图为空那一支会把隐藏名单带进下一张")
        self.assertLess(one.index("gw.getViewportDib()"), one.index("mmd2maxUnclipBones()"),
                        "图还没拿到就还原，裁掉的东西又回到画面里")

    def test_the_clip_expectation_has_one_owner_and_opposite_directions(self):
        """整场景那五张必须是 0，其余十四张必须 ≥1，而且「哪五张是整场景」由 .ms 的调用点定。

        只钉「特写 ≥1」的话，把 front 的取景框改小也能绿；只钉 Python 名单的话，
        .ms 改成 `mmd2maxShotOne ... "front" #view_front footLo footHi` 就悄悄分叉。
        """
        scene = set(re.findall(
            r'mmd2maxShotOne outDir baseName "([^"]+)" #view_\w+ b\.min b\.max',
            self.shots()))
        self.assertEqual(scene, set(verdicts.SHOT_SCENE_TAGS),
                         "「哪几张用整场景框」有两份名单：Python 的期望值与 .ms 的调用点分叉")
        self.assertTrue(scene <= set(verdicts.SHOT_TAGS),
                        "SHOT_SCENE_TAGS 里有不在拍摄名单里的 tag")
        rows = {r[2]: r for r in verdicts.shot_fill_rows() if r[1] == "clip_hidden"}
        self.assertEqual(len(rows), len(verdicts.SHOT_TAGS),
                         "每张图都得有一行裁骨读数，少一张就是「那张没裁也没人知道」")
        for tag, row in rows.items():
            if tag in scene:
                self.assertEqual((row[3], row[5]), ("0", "max"),
                                 f"{tag} 是整场景框，裁出骨来只可能是取景框被改小了")
            else:
                self.assertEqual((row[3], row[5]), ("1", "min"),
                                 f"{tag} 只框住身体一截，一根都没裁 = 裁剪整段没生效")


class SkinZeroWeightContract(unittest.TestCase):
    """#76：权重正好 0.0 的链路在 .ms 两侧的写法。

    海灯节那一轮的账：`links_expected 45865 − links_read 45533 = 332`，而这 332 条全是
    权重表里字面的 `:0.000000`（PMX 权重 <5e-7，导出时 `%.6f` 印成零）。实测 Max 的
    `skinOps.ReplaceVertexWeights` 收到 0.0 会把这条链路整条丢掉、回读连骨号都不留，
    于是 links_read / links_missing / link_diff_max / link_mismatch_bones 四行一起为
    「对形变毫无影响」的数变红。这组测试钉的是「同一条规则在写入侧与期望侧各挡一次，
    而且零权有自己的一条读数」——任何一方把 `w > 0.0` 删回去，这里就红，而不是等下一轮
    实跑用四个看不懂的红灯来讲同一个故事。
    """

    @staticmethod
    def writer() -> str:
        return _between(SCRIPT, "fn mmd2maxApplySkin", "fn mmd2maxSkinCmp")

    @staticmethod
    def expectation() -> str:
        return _between(SCRIPT, "fn mmd2maxVerifySkin", "fn mmd2maxBuildMorpher")

    def test_the_writer_refuses_pairs_max_cannot_hold(self):
        body = _code(self.writer())
        self.assertIn("if w > 0.0 and", body,
                      "写入侧不再挡零权对 = 把挂不住的边发给 Skin，还虚报蒙皮覆盖")
        guard = body.index("if w > 0.0 and")
        for sink in ("append ids sid", "append weights w", "boneVertHits[sid]"):
            self.assertLess(guard, body.index(sink), f"{sink} 排在校验之前 = 挡不住")

    def test_the_expectation_applies_the_same_rule(self):
        body = _code(self.expectation())
        self.assertIn("if w > 0.0 then", body,
                      "期望侧不挡零权对 = 要场景里挂一条 Max 挂不住的边，四行一起红")
        guard = body.index("if w > 0.0 then")
        for counted in ("linksExpected += 1", "expLinks[sid]"):
            self.assertLess(guard, body.index(counted), f"{counted} 漏在校验外")
        self.assertLess(guard, body.index("else zeroLinks += 1"),
                        "zeroLinks 那条出口不在同一个校验上 = 同一条规则写了两遍，会分叉")

    def test_zero_pairs_are_reported_once_and_only_as_their_own_count(self):
        self.assertEqual(SCRIPT.count('mmd2maxReport "skin_rb" "zero_links"'), 1,
                         "zero_links 报两遍 = 规格表里同一主体两条读数互相打架")
        body = _code(self.expectation())
        self.assertIn('mmd2maxReport "skin_rb" "zero_links" "-" zeroLinks', body)
        self.assertEqual(body.count("local zeroLinks = 0"), 1,
                         "计数器声明漏在循环里 = 每个顶点重零一次，读数永远是最后一行的数")
        loop = body.index("for i = 1 to wlines.count do")
        self.assertLess(loop, body.index('mmd2maxReport "skin_rb" "zero_links"'),
                        "边扫边报 = 报的是中途计数")

    def test_a_zero_pair_on_a_dropped_bone_is_still_a_dropped_pair(self):
        """两条尺各管一件事：指向不蒙皮骨的对归 pairs_dropped，蒙皮骨上的零权归 zero_links。

        混在一起的话，「有人把权重表指到了 skin_flag=0 的骨」这种真缺陷会被读成
        「只是几条存不下的零」，而那条 gate 正是为它建的。
        """
        body = _code(self.expectation())
        self.assertLess(body.index("else zeroLinks += 1"),
                        body.index("else pairsDropped += 1"),
                        "嵌套顺序变了：零权分支掉出了骨号有效性判断")
        self.assertLess(body.index("if g >= 0 and"), body.index("if w > 0.0 then"),
                        "先判权再判骨号 = 指向不蒙皮骨的零被算进 zero_links")


class SkinAddBoneFailureCountContract(unittest.TestCase):
    """AddBone 失败只能计一次 —— 那行 throw 里的数字是给人看的，错了就没人再信它。

    旧写法 `try ( ...; added = true ) catch ( failedBones += 1; append ... )` 之后又跟
    一句 `if not added do ( failedBones += 1; append ... )`：`added` 只在 try 成功时置真，
    所以「没加上」与「抛了」是同一条出口，两遍叠加 ⇒ 报出来的 N 恒为真实数的一倍，
    名字表里每根出现两次。危害不是判错（`> 0` 照样 throw、装配照样中止），而是**这条
    信息本身不可信**：出问题时第一句要问的就是「哪几根、几根」。
    """

    @staticmethod
    def loop() -> str:
        return _between(SCRIPT, "local failedBones = 0", "local addElapsed")

    def test_one_failure_one_increment(self):
        body = _code(self.loop())
        self.assertEqual(body.count("failedBones += 1"), 1,
                         "AddBone 失败计数出现两次 = 同一根骨被记两遍，throw 里的 N 翻倍")
        self.assertEqual(body.count("append failedBoneNames"), 1,
                         "名字表同理：重复的名字会让「哪几根」读起来像六根而不是三根")

    def test_the_catch_is_not_empty_and_the_count_is_the_exit(self):
        body = _code(self.loop())
        self.assertLess(body.index("if not added do"), body.index("failedBones += 1"),
                        "计数没挂在 `not added` 这条唯一出口上 = 又回到两处各自加一遍")
        self.assertRegex(body, r"catch\s*\(\s*mmd2maxLog",
                         "catch 只吞不写 = 规则 V 禁的空 catch，失败原因从此进不了日志")


class PhysicsProxyContract(unittest.TestCase):
    """物理代理这一段第一次实跑是第 64 轮（`--physics`，香菱 530 PASS / 0 FAIL）。

    两条都是「日志里有一个数，但那个数的来历从没被读过」：`（失败 1）` 和 `旋转失败=254`。
    没跑物理的轮次（默认口径）完全碰不到这里，所以下面每条断言都写清了它是哪一轮
    读出来的，而不是「看着像」。
    """

    def test_rigid_registry_is_indexed_one_based(self):
        """PMX 刚体索引 0 基，Max 数组 1 基：`rigids[idx]` 对第一根（idx=0）必抛。

        第 64 轮那行 `刚体代理创建失败：-- Runtime error: array index must be positive
        number, got: 0` 就是它 —— 代理其实已经建好、也已经挂上父骨，死的是这本账，
        而外层 try 把它数成「创建失败」，汇总行于是同时说谎两次（少一根代理、
        多一次创建失败）。改成 `rigids[idx + 1]` 之后第 65 轮读 `254（失败 0）`。
        """
        block = _between(SCRIPT, "local rigids = #()", "for p in rows do")
        loop = _between(SCRIPT, "if idx >= 0 and idx < rigids.count", "try ( if layerRB != undefined")
        self.assertIn("rigids[idx + 1] = proxy", _code(loop))
        self.assertNotIn("rigids[idx] = proxy", _code(loop),
                         "0 基索引直接写进 1 基数组：第一根刚体必抛，而且抛的是记账那行")
        self.assertNotIn("rigids[0]", _code(block) + _code(loop))

    def test_rigid_rotation_is_a_plain_write_of_python_supplied_degrees(self):
        """Max 端只做一条写法：`proxy.rotation = (eulerAngles ax ay az)`，三个数是表里的度数列。

        探针 87 / 87b（test_runs/probe87/probe_api*.txt）在本机 Max 2022 逐条量过，
        「在 MAXScript 里自己算共轭再拆回欧拉」要用的写法全都抛：
            transpose <matrix3>           → No "transpose" function for (matrix3 ...)
            <Angle4>.order = #xyz         → Unknown property: "order"
            eulerAngles a b c order:#xyz  → Argument count error: EulerAngles wanted 3, got 6
            translate <point3>            → No "Translate" function for [1,2,3]
        第 64~66 轮就是踩在 `mmd2maxRotToMax` 的 transpose + `.order = #xyz` 上读到
        `旋转失败=254`（254 根代理一根没转 = 用户截图里「裙摆竖着排成一块板」）。
        现在数学在 coords.rigid_rotation_max，`eulerAngles` 的单位（度 vs PMX 的弧度）
        也在那边换算，这边不许再长出第二次换算。
        """
        dead = ("transpose ", "eulerToQuat", ".order =", "order:#", " as matrix3",
                "mmd2maxRotToMax", "mmd2maxCoordMatrix")
        body = _code(_between(SCRIPT, "fn mmd2maxBuildPhysics", "fn mmd2maxMain"))
        for needle in dead:
            self.assertNotIn(needle, body, f"MAXScript 里不许再出现「{needle.strip()}」：本机实测抛错")
        site = _code(_between(SCRIPT, "local rotOk = false", "/* 定心排在旋转之后"))
        self.assertIn("proxy.rotation = (eulerAngles ax ay az)", site)
        self.assertIn("local ax = p[cRRMaxX] as float", body)
        self.assertIn('mmd2maxColPos lines "rigid_rot_max_x"', body,
                      "朝向度数必须按列名取：位置写死一格就把 mass 当角度写给节点")

    def test_center_is_applied_after_rotation(self):
        """定心必须排在旋转之后：Box/Capsule 的 pivot 在底角，rotation 绕 pivot 转。

        探针 87b 的 7 行实测：height=1 的 Box 在单位 transform 下 center=[0,0,0.5]
        ⇒ pivot 在底角。先 center 再 rotation，形心会绕底角画弧离开刚体中心 ——
        那就是「代理明明转上了却整块飞出去」，不抛任何错。
        """
        site = _code(_between(SCRIPT, "proxy.name = (\"RB_\"", "if boneNew >= 0 do"))
        self.assertLess(site.index("proxy.rotation = (eulerAngles"),
                        site.index("proxy.center = [px, py, pz]"),
                        "center 在 rotation 之前 = 转完把形心从刚体中心甩开")
        self.assertIn("local dOff = (distance proxy.center [px, py, pz])", site,
                      "转完的形心必须回读，否则「代理落在刚体中心」这句话没有出处")
        self.assertIn("offFail += 1", site)

    def test_capsule_is_the_measured_shape_and_gets_centered(self):
        """胶囊的两件事都由探针 89 量出来，不是推的：`height:` 不含两端半球、pivot 在底面。

        B1 实测 `Capsule radius:1 height:8` 建在 z=30 → 世界盒 z[30,40]，总长 10 = 8 + 2×1
        ⇒ Max 的 `height:` 是**圆柱段**，两顶帽子它自己加。PMX 胶囊 size=(半径, 半高, 0)，
        映射到 Max 是 ez=半高，所以圆柱段写 `ez*2`，总长 = 2×(半径+半高) = MMD 的口径。
        旧写法 `ey*2 + radius*2` 用的是 sz 那一格，而胶囊的 size.z 恒为 0 ⇒ 圆柱段被写成
        2×半径，72 根胶囊每根都短一截（第 68 轮 front 截图里那些短一号的橙条）。

        B1 还实测胶囊 pivot 在底面（形心在 +总长/2）。C1 那句「球中心对称 ⇒ 只有球不写定心」
        在第 71 轮被探针 91 否掉：球虽然 pivot 就在球心，但给它写 `.rotation` 会把**整个节点**
        绕**世界原点**画弧（A2：半径 80.9 的球写 3.65 度走 5.28 厘米；B2：半径 48.6 的球写
        158.8 度走 94.6 厘米，center / tm[4] / .pos 三个一起走，世界盒中心与 center 始终一致），
        而 B3 实测转完补写一次 center 就精确回到 PMX 点 ⇒ **三种形状都要定心，一条排除都不许留**。
        第 70/71 轮 `挂父后世界偏心=4` 那四根（RB_10/11 左尻右尻、RB_20/21 左ひざ右ひざ）
        在 physics.tsv 里全是 shape=0，就是被那条排除规则漏掉的。
        """
        body = _code(_between(SCRIPT, "fn mmd2maxBuildPhysics", "fn mmd2maxMain"))
        self.assertIn("local hgt = amax (ez * 2.0) 0.002", body,
                      "圆柱段 = 2×PMX 半高；写 ey 就是拿恒为 0 的 size.z 当高度")
        self.assertNotIn("(ey * 2.0) + (radius * 2.0)", body,
                         "旧口径把 height 当成含两端的总高，本机实测不是")
        self.assertIn("try ( proxy.center = [px, py, pz] )", body,
                      "定心这一步必须在（三种形状共用）")
        self.assertNotIn("if shapeIdx != 0 do", body,
                         "把球排除出定心 = 转过的球整块绕世界原点画弧（探针 91 A2/B2）")
        self.assertNotIn("if shapeIdx != 0 and shapeIdx != 2 do", body,
                         "把胶囊排除出定心 = 每根胶囊从刚体中心往上挂半根")
        # 探针 92 第 6 步实测：`Capsule radius:2 height:8` 世界盒 (4,4,12)，
        # 退路 `Cylinder radius:2 height:8` 只有 (4,4,8) —— 退化天生短 2×半径。
        # 那条 catch 有体（check_ms 只看空体），所以「退了几根」必须自己数出来、进汇总行，
        # 否则换一台 Capsule 类不存在的机器上，72 根短一截而读数仍然 `254（失败 0）`。
        self.assertIn("proxy = Cylinder radius:radius height:hgt pos:[px,py,pz]", body,
                      "胶囊的退路仍在（这台机用不上，但退路本身要留着）")
        self.assertIn("capFalls += 1", body,
                      "退成圆柱这件事必须计数")
        self.assertIn('" 胶囊退圆柱=" + (capFalls as string)', body,
                      "计数必须进汇总行：只在日志里打前 3 条，第 72 根就查无此人")

    def test_world_position_is_read_back_after_parenting(self):
        """挂父之后必须再读一次世界量，并且读数要能**归属**（#89 → #90）。

        第 70 轮 `挂父后世界偏心=4 最大=94.6242`：第 4 根是谁查不到（文字只留前 3 条），
        而「谁搬的」更查不到。探针 90 把这一步洗清了：父骨「平移+绕 Y 转 90 度」（C1）
        和「scale=[2,2,2]」（C2）两种情况下，只设 `.parent` 之后的 center / tm[4] /
        世界盒与写 center 之后**逐行相同**（d 从 4.8e-07 变 5.3e-06）。
        所以这一格必须拆成三层读数，缺一层就退回「拿未实测的机制下结论」：
          挂父那一步搬了多少（linkShift，探针说该是 0）
          挂完之后离 PMX 点多远（worldFail，带最偏那根的名字）
          整个循环跑完后再读一遍（afterFail，搬代理的若在循环后面只有这里看得见）
        父骨的缩放一律从 `transform` 三行的长度读（探针 90 的 F2），不许再裸读 `.scale` ——
        Biped 骨没有这个属性，第 69 轮那 42 条创建失败就是它，而它让 `父骨带缩放`
        对挂在 Biped 上的那一大片**一个字都没说**（半盲的闸比没有闸更坏）。
        """
        body = _code(_between(SCRIPT, "fn mmd2maxBuildPhysics", "fn mmd2maxMain"))
        self.assertIn("local dW = (distance wC [px, py, pz])", body,
                      "世界偏心必须真量：把它写死成 0.0 的判据是恒绿的，第 69 轮那种 213 厘米看不见")
        self.assertLess(body.index("proxy.parent = boneNodes[boneNew + 1]"),
                        body.index("local dW = (distance wC [px, py, pz])"),
                        "世界量排在挂父之前 = 量的还是挂父前那个已经对的状态")
        self.assertLess(body.index("local centerBefore = undefined"),
                        body.index("proxy.parent = boneNodes[boneNew + 1]"),
                        "挂父之前要留下形心的读数，否则「挂父这一步搬了多少」没有出处")
        self.assertIn("local dLink = (distance centerBefore wC)", body)
        self.assertIn("linkShift += 1", body)
        self.assertLess(body.index("local worldTM = proxy.transform"),
                        body.index("proxy.parent = boneNodes[boneNew + 1]"),
                        "世界变换要在挂父之前记，挂完再记就已经是被搬走的那个了")
        self.assertIn("proxy.transform = worldTM", body,
                      "写回这一对要与附属骨那段（:5855）同写法：探针 90 实测它是恒等写，"
                      "两处不一致就没法用一份探针解释两份代码")
        self.assertLess(body.index("proxy.transform = worldTM"),
                        body.index("local dW = (distance wC [px, py, pz])"),
                        "世界量要排在写回之后，否则量的还是写回前那个已经错的状态")
        self.assertIn("if dW > 1.0 do", body)
        self.assertIn("worldFail += 1", body)
        self.assertIn('worstName = "RB_" + (idx as string)', body,
                      "最偏的那根必须留名字：计数封顶可以，名字封顶就查无此人")
        self.assertIn('" 挂父后世界偏心="', body)
        self.assertIn('" 挂父搬走="', body)
        self.assertIn('" 循环后仍偏="', body)
        self.assertIn("rigidWant[idx + 1] = [px, py, pz]", body,
                      "循环后再读要知道每根本该在哪")
        self.assertIn("for k = 1 to rigids.count do", body)
        self.assertIn("d2 = (distance wc2 want)", body)
        self.assertIn("afterFail += 1", body)
        self.assertNotIn(".scale", body,
                         "父骨/代理的缩放一律按 transform 行长度量：`.scale` 在 Biped 骨上实测抛，"
                         "而裸读它第 69 轮一次杀掉 42 根代理")
        self.assertIn("local psx = (distance parT[1] [0, 0, 0])", body,
                      "父骨缩放按 transform 行长度量，且不得叫 sx/sy/sz —— 那三个名字在这一段外层是 PMX 刚体尺寸")
        self.assertIn("do parentScaled += 1", body)
        self.assertIn('" 父骨带缩放="', body)
        self.assertIn("刚体世界量三对照", body,
                      "前几根要留 .pos / transform[4] / 世界盒 的三值对照，下一轮才知道尺对不对")

    def test_rotation_failure_is_counted_and_quoted(self):
        """只数不记文字 = 下一轮还得重新猜一遍是谁死的。第 64 轮读到 `旋转失败=254`
        时，那 254 次死因一个字都没留下，因为 catch 里只写了 `rotOk = false`；
        补上报错文字之后第 65 轮当场给出根因（上一条测试那件事）。"""
        site = _code(_between(SCRIPT, "local rotOk = false", "/* 定心排在旋转之后"))
        self.assertIn("catch ( rotErr = (getCurrentException() as string) )", site,
                      "失败文字要在 catch 里存下来，不能事后调 getCurrentException()")
        self.assertIn("rotFail += 1", site)
        self.assertIn("if rotFail <= 3 do", site, "前 3 条留字，后面只数：几百行同因日志会冲掉别的读数")
        self.assertIn("刚体没转上", site)
        self.assertIn("rotErr", site)

    def test_rotation_reading_is_not_vacuously_green(self):
        """`旋转失败=0` 单独看没有意义：三根欧拉轴全 0 的代理本来就不用转。

        汇总行必须同时报「有朝向的刚体」= PMX 给了非零朝向的根数，否则下一轮
        分不清「254 根全转上」和「一根有朝向的都没有」。这条把那个数钉进循环与汇总。
        """
        body = _code(_between(SCRIPT, "fn mmd2maxBuildPhysics", "fn mmd2maxMain"))
        self.assertIn("if (rx != 0.0) or (ry != 0.0) or (rz != 0.0) do rotNonZero += 1", body)
        self.assertLess(body.index("do rotNonZero += 1"), body.index('" 有朝向的刚体="'),
                        "先报数后累加 = 汇总里的数是少一根的")
        self.assertIn('rotNonZero as string', body)
        self.assertIn('offFail as string', body)

    def test_rotation_diagnostic_quotes_input_and_readback(self):
        """手性/镜像这类错在计数里是看不见的（`旋转失败=0` 也可能是每根都朝反）。

        所以前 3 根「PMX 确实给了朝向」的代理必须留一行三对照：
        PMX 弧度 → 写进 node.rotation 的度数 → Max 读回的 quat。
        这行是给用户眼睛对截图用的，不是给用户眼睛对日志用的。
        """
        site = _code(_between(SCRIPT, "local rotOk = false", "/* 定心排在旋转之后"))
        self.assertIn("if rotOk and (rotNonZero <= 3) do", site)
        self.assertIn("PMX弧度=", site)
        self.assertIn("写入度=", site)
        self.assertIn('读回=" + (proxy.rotation as string)', site)


if __name__ == "__main__":
    unittest.main(verbosity=2)
