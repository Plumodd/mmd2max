/* M4d 探针（figfile_m4g）：回滚这条路只剩「骨盒尺寸归不归 .fig 管」一件事，把它单独问干净。

   前面几发探针已经定死、这一发**不再重复问**的（probes/readback_m4*.txt）：
     · `biped.saveFigFile` / `loadFigFile` 第一实参必须是 **controller**（m4b C1/C3）。
     · 存与读都**要求在 Figure 模式内**（m4b C1、m4c B10：非 Figure 模式一律「Cannot run
       the operation in this mode」）。
     · 进出 Figure 模式自己不动几何（m4c B4/B9 位移 0.0）。
     · `#pos` 与世界矩阵的还原精度 = 5e-05 厘米、可连续回滚 3 次（本文件上一发 G1/G2）。
     · 扩展名无所谓：`.fig` 与 `.bfc` 存出来 10939 字节完全相同、都能读回（上一发 G3）。
     · `biped.setBox/setWidth/setTaper` 本版本不存在（max_import_obj.ms:1615），
       骨盒只靠缩放写。

   上一发剩下两个没结论的点，这一发各自单独测，不再混在一起扰动：

   K1 `biped.setTransform n #scale v false` 之后 `biped.getTransform n #scale` **读数不动**
      （扰动后缩放差 = 0.0），所以「.fig 管不管缩放」上一发根本没答上 —— 要么这句写不进
      （Biped 把骨骨缩放吞了），要么这个读数本身是死的。产品的截面闭环（:2472
      mmd2maxScaleBoneAxis）走的就是这句，而 M4c 验收读的是 `nodeGetBoundingBox`
      （:2397 mmd2maxLocalExtent，:2436 已注明「它一直在响应 #scale」）。
      ⇒ 这一发改用**验收同一把尺**（本地包围盒的三个角差）来判，绕开读数陷阱。
   K2 直接赋 `n.scale = [..]`（第二条写法路）作为对照：若 K1 不动而 K2 动，产品就该知道
      自己的写句其实没生效；若两个都不动，Biped 骨盒在这个版本就不是缩放能改的。

   G1 每种扰动都单独问一次「回滚之后还剩多少」，四个数并排打：
      包围盒角差 / #pos 差 / 世界矩阵差 / `biped.getTransform #scale` 差。
   G2 **重新存图能不能当新基准**：上一发实测「回滚后再 save、再扰动、再 load」留下
      2.13 厘米残差（前三次回滚都只有 5e-05）。这一发把「存图前先退出 Figure 模式 +
      redrawViews + update」加进去对照：如果残差归零，就是模式切换没落定；
      如果还在，结论就是 **.fig 只能回到最初那一份**，产品的回滚设计必须按这个来。
   G4 连续回滚之后 Biped 之外的东西（Box / Skin 修改器 / BoneGeometry）还在不在 ——
       existence 一律用 `getNodeByName` 查：上一发实测 **`doesNodeExist` 不存在**
      （「Call needs function or class, got: undefined」），把整发跑到 G4 之后打断了。

   两条本发实测出来的 MAXScript 坑，写在这里免得下一个人再撞：
     · **标识符大小写不敏感**：`local box = try ( Box name:... ) catch undefined` 里的
       `Box` 被解析成正在定义的那个 local（此刻没值），报「got: undefined」。改名 wbox
       之后 Box 建出来了（修改器数=1）。⇒ 变量名不许与任何内建只差大小写。
     · **`deleteAllObjects` 在 Max 2022 不存在**：m4d:156 那句 `try ( deleteAllObjects() )
       catch ()` 是规则 V 的教科案例 —— 空 catch 把一个死调用藏了整整一轮，那轮
       「场景已清空」从来没发生过。`-U MAXScript` 起来本来就是空场景，不用清。
     · `max (max a b) c` 也不是函数（`max` 是 `max quit` 那种命令语法），取最大值用 `amax`。

   G9 探针自己的坑：把 `resetMaxFile #noPrompt` 放到**最末尾**单独试。正式脚本走
   `-U MAXScript <file>`（obj_run.py:684）跑了二十六轮没事，探针走 wrapper 的 `fileIn` ——
   若此行之后再无读数，就证实「fileIn 进来的脚本里不许 resetMaxFile」。

   全程不碰用户场景。 */

global PROBE_OUT = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/figfile_m4g.txt"
global FIG_A = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/m4g.fig"
global FIG_C = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/m4g_new.fig"
global CTRL = undefined

fn say s =
(
    local f = openFile PROBE_OUT mode:"a" encoding:#utf8
    if f != undefined do ( format "%\n" (s as string) to:f; close f )
)

fn S x = try ( x as string ) catch "<unprintable>"
fn trim s =
(
    local t = s as string
    if t.count > 120 then (substring t 1 120 + " [cut]") else t
)
fn ex = trim (try ( getCurrentException() as string ) catch "?")

/* 三个数取最大：用 amax 不用 max（`max` 是 `max quit` 那种命令语法，见文件头）。 */
fn amax3 a b c = amax (amax a b) c

/* 本地包围盒（= M4c 验收那把尺，max_import_obj.ms:2397 mmd2maxLocalExtent 同源）。
   读不到就返回 undefined，让 boxgap 打 -1 把它和「真的零差」分开。 */
fn localBox node =
(
    local bb = try ( nodeGetBoundingBox node node.transform ) catch undefined
    if (classOf bb) != Array do return undefined
    if bb.count < 2 do return undefined
    if (classOf bb[1]) != Point3 do return undefined
    if (classOf bb[2]) != Point3 do return undefined
    bb
)

/* 包围盒角差：min 与 max 两个角各取三轴最大差，再取两者的大者。
   量的是「盒子在这根骨自己的坐标系里挪了没有」—— M4c 判的就是这个。 */
fn boxgap a b =
(
    if a == undefined do return -1.0
    if b == undefined do return -1.0
    local dmin = amax3 (abs (a[1].x - b[1].x)) (abs (a[1].y - b[1].y)) (abs (a[1].z - b[1].z))
    local dmax = amax3 (abs (a[2].x - b[2].x)) (abs (a[2].y - b[2].y)) (abs (a[2].z - b[2].z))
    amax dmin dmax
)

fn p3gap a b =
(
    if (classOf a) != Point3 do return -1.0
    if (classOf b) != Point3 do return -1.0
    amax3 (abs (a.x - b.x)) (abs (a.y - b.y)) (abs (a.z - b.z))
)

/* 世界矩阵逐元素比：三行 + 平移共 12 个数取最大绝对差。
   不用四元数：上一发实测回滚后四元数分量差 = 3.99 而世界矩阵差只有 4e-05 ——
   那是 q 与 -q 同一个旋转，四元数分量差把「已还原」读成了「没还原」（m4d B5 的 4.0 同因）。 */
fn mgap a b =
(
    if (classOf a) != Matrix3 do return -1.0
    if (classOf b) != Matrix3 do return -1.0
    local m = 0.0
    local ra = #(a.row1, a.row2, a.row3)
    local rb = #(b.row1, b.row2, b.row3)
    for i = 1 to 3 do
    (
        for c = 1 to 3 do
        (
            local d = abs (ra[i][c] - rb[i][c])
            if d > m do m = d
        )
    )
    for c = 1 to 3 do
    (
        local d = abs (a.translation[c] - b.translation[c])
        if d > m do m = d
    )
    m
)

/* 快照一项 = #(节点, #pos, node.transform, 本地包围盒, #scale 读值, node.scale 读值)。
   后两列只是「有没有第二种读数能反映缩放」的对照，判据一律看包围盒。 */
fn snapAll =
(
    local sn = #()
    for o in objects where (classOf o) == Biped_Object do
    (
        local p = try ( biped.getTransform o #pos ) catch undefined
        local k = try ( biped.getTransform o #scale ) catch undefined
        local ns = try ( o.scale ) catch undefined
        append sn #(o, p, o.transform, (localBox o), k, ns)
    )
    sn
)

fn bipCount =
(
    local c = 0
    for o in objects where (classOf o) == Biped_Object do c += 1
    c
)

/* 整树最大差：#(包围盒角差, #pos 差, 世界矩阵差, #scale 差, node.scale 差, 差最大的骨名) */
fn worstNow sn =
(
    local db = 0.0
    local dp = 0.0
    local dm = 0.0
    local dk = 0.0
    local dn = 0.0
    local who = "-"
    for e in sn do
    (
        local n = e[1]
        local p = try ( biped.getTransform n #pos ) catch undefined
        local k = try ( biped.getTransform n #scale ) catch undefined
        local ns = try ( n.scale ) catch undefined
        local b = localBox n
        local x = boxgap b e[4]
        local y = if (classOf p) == Point3 then (distance p e[2]) else -1.0
        local z = mgap n.transform e[3]
        local u = p3gap k e[5]
        local v = p3gap ns e[6]
        if x > db do ( db = x; who = n.name )
        if y > dp do dp = y
        if z > dm do dm = z
        if u > dk do dk = u
        if v > dn do dn = v
    )
    #(db, dp, dm, dk, dn, who)
)

fn rline tag r =
(
    tag + " 盒=" + (S r[1]) + " pos=" + (S r[2]) + " 世界=" + (S r[3]) \
        + " #scale=" + (S r[4]) + " .scale=" + (S r[5]) + " @ " + (S r[6])
)

/* 三种扰动各管一件事，一次只动一个变量，读数才能归因。
   注意 `#rotation` 而不是 `#rot`：:1444 已实测 `#rot` 在 Max 2022 抛异常。 */
fn pertPosRot sn =
(
    for e in sn do
    (
        local n = e[1]
        try ( biped.setTransform n #pos (e[2] + [3.7, -2.1, 5.5]) false ) \
            catch ( say ("  扰动 #pos 抛 " + n.name + " " + (ex())) )
        local q = try ( biped.getTransform n #rotation ) catch undefined
        if (classOf q) == Quat do
            try ( biped.setTransform n #rotation (q * (angleaxis 12.0 [0.0, 0.0, 1.0])) false ) \
                catch ( say ("  扰动 #rotation 抛 " + n.name + " " + (ex())) )
    )
)
fn pertScaleAPI sn =
(
    for e in sn do
    (
        local n = e[1]
        try ( biped.setTransform n #scale [1.25, 0.8, 1.1] false ) \
            catch ( say ("  扰动 biped #scale 抛 " + n.name + " " + (ex())) )
    )
)
fn pertScaleNode sn =
(
    for e in sn do
    (
        local n = e[1]
        try ( n.scale = [1.25, 0.8, 1.1] ) \
            catch ( say ("  扰动 node.scale 抛 " + n.name + " " + (ex())) )
    )
)

fn figSave path =
(
    local a = try ( CTRL.figureMode = true ) catch ("EX:" + (ex()))
    local b = try ( biped.saveFigFile CTRL path ) catch ("EX:" + (ex()))
    local c = try ( CTRL.figureMode = false ) catch ("EX:" + (ex()))
    #(a, b, c)
)
fn figLoad path =
(
    local a = try ( CTRL.figureMode = true ) catch ("EX:" + (ex()))
    local b = try ( biped.loadFigFile CTRL path ) catch ("EX:" + (ex()))
    local c = try ( CTRL.figureMode = false ) catch ("EX:" + (ex()))
    #(a, b, c)
)

fn m4gRun =
(
    say "===== M4g rollback probe v3 ====="
    say ("G0 起手对象数=" + (S objects.count) + "（-U MAXScript 起来本来就是空场景）")

    local root = undefined
    try
    (
        root = biped.createNew 170.0 0.0 [0.0, 0.0, 0.0] arms:true neckLinks:1 \
            spineLinks:3 legLinks:3 toes:1 toeLinks:1 trianglePelvis:false triangleNeck:true
    )
    catch ( say ("G0 biped.createNew 抛 " + (ex())) )
    if root == undefined do
    (
        say "G0 建不出 Biped，探针到此为止。"
        return false
    )
    CTRL = undefined
    try ( CTRL = root.controller ) catch ( say ("G0 root.controller 抛 " + (ex())) )
    if CTRL == undefined do
    (
        say "G0 拿不到 controller，后面全废。"
        return false
    )

    /* 旁证对象：Box + Skin 修改器 + BoneGeometry。变量名一律不与内建差大小写。 */
    local wbox = undefined
    try ( wbox = Box name:"M4GBox" length:10.0 width:10.0 height:10.0 ) \
        catch ( say ("G0 Box 抛 " + (ex())) )
    local boxPos0 = if wbox != undefined then wbox.pos else undefined
    if wbox != undefined do
    (
        local sm = undefined
        try ( sm = Skin() ) catch ( say ("G0 Skin() 抛 " + (ex())) )
        if sm != undefined do ( try ( addModifier wbox sm ) catch ( say ("G0 挂 Skin 抛 " + (ex())) ) )
    )
    local wbone = undefined
    try ( wbone = BoneSys.createBone [0.0, 30.0, 0.0] [10.0, 30.0, 0.0] [0.0, 1.0, 0.0] ) \
        catch ( say ("G0 BoneSys.createBone 抛 " + (ex())) )
    if wbone != undefined do ( try ( wbone.name = "M4GBone" ) catch ( say ("G0 改骨名抛 " + (ex())) ) )

    local sn0 = snapAll()
    say ("G0 根=" + root.name + " ctrl=" + (S (classOf CTRL)) + " 节点=" + (S (bipCount)) \
        + " 快照=" + (S sn0.count) + " 对象数=" + (S objects.count) \
        + " Box=" + (S (wbox != undefined)) \
        + " 修改器=" + (if wbox != undefined then (S wbox.modifiers.count) else "-") \
        + " Bone=" + (S (wbone != undefined)))
    say ("  " + (rline "G0 基线（四个差都该是 0）" (worstNow sn0)))

    try ( deleteFile FIG_A ) catch ( say ("G0 删旧图抛 " + (ex())) )
    local sv = figSave FIG_A
    say ("G0 存原始图 进=" + (S sv[1]) + " 调用=" + (S sv[2]) + " 出=" + (S sv[3]) \
        + " size=" + (S (try ( getFileSize FIG_A ) catch ("EX:" + (ex())))))

    /* ---- K1 产品用的那条写句：biped.setTransform #scale（绝对值，见 :2472 同一条 API） ----
       三种差分开读：`盒` = 验收那把尺；`#scale` = 这条 API 自己的读数；`.scale` = 节点属性。
       上一发只读了 #scale，得到「扰动后 0.0」就没法区分「写不进」与「读数死」。 */
    pertScaleAPI sn0
    local k1p = worstNow sn0
    say ("  " + (rline "K1 写完 #scale" k1p))
    if k1p[1] <= 0.0 do say "K1 观察：#scale 写完了，但骨盒包围盒没动。"
    local ld1 = figLoad FIG_A
    say ("  K1 load=" + (S ld1[2]) + " | " + (rline "K1 回滚后" (worstNow sn0)))
    redrawViews()
    say ("  " + (rline "K1 redraw 之后" (worstNow sn0)))

    /* ---- K2 对照：直接赋 node.scale ---- */
    say ("  " + (rline "K2 起点" (worstNow sn0)))
    pertScaleNode sn0
    local k2p = worstNow sn0
    say ("  " + (rline "K2 写完 node.scale" k2p))
    if k2p[1] <= 0.0 do say "K2 观察：node.scale 也动不了骨盒包围盒。"
    local ld2 = figLoad FIG_A
    say ("  K2 load=" + (S ld2[2]) + " | " + (rline "K2 回滚后" (worstNow sn0)))
    redrawViews()
    say ("  " + (rline "K2 redraw 之后" (worstNow sn0)))

    /* ---- K3 落位与朝向（已知能回滚，这里只补一次「盒」这一列的观察） ---- */
    pertPosRot sn0
    say ("  " + (rline "K3 扰动 pos/rot 后" (worstNow sn0)))
    local ld3 = figLoad FIG_A
    say ("  K3 load=" + (S ld3[2]) + " | " + (rline "K3 回滚后" (worstNow sn0)))

    /* ---- G2 重新存图能不能当新基准 ----
       上一发实测「回滚后 save、扰动、load」留 2.13 厘米残差。这一发在 save 之前先
       退出 Figure 模式 + redrawViews + 逐骨 update，让模式切换落定，再看残差还在不在。 */
    redrawViews()
    local sn1 = snapAll()
    local sv2 = figSave FIG_C
    say ("G2 重新存图 进=" + (S sv2[1]) + " 调用=" + (S sv2[2]) + " 出=" + (S sv2[3]) \
        + " size=" + (S (try ( getFileSize FIG_C ) catch ("EX:" + (ex())))) \
        + " | 快照 " + (S sn1.count) + " 条（新基准与原始基准的差见下行）")
    say ("  " + (rline "G2 新基准 vs 原始快照" (worstNow sn0)))
    pertPosRot sn1
    say ("  " + (rline "G2 新基准扰动后" (worstNow sn1)))
    local ld4 = figLoad FIG_C
    say ("  G2 load 新图=" + (S ld4[2]) + " | " + (rline "G2 回滚到新基准" (worstNow sn1)))
    redrawViews()
    say ("  " + (rline "G2 redraw 之后" (worstNow sn1)))

    /* ---- G4 副作用（existence 用 getNodeByName：doesNodeExist 实测不存在） ---- */
    say ("G4 对象数=" + (S objects.count) \
        + " Box 读回=" + (S (try ( getNodeByName "M4GBox" exact:true ) catch ("EX:" + (ex())))) \
        + " Box 位移=" + (if boxPos0 != undefined then (S (distance wbox.pos boxPos0)) else "-") \
        + " Box 修改器=" + (if wbox != undefined then (S wbox.modifiers.count) else "-") \
        + " Bone 读回=" + (S (try ( getNodeByName "M4GBone" exact:true ) catch ("EX:" + (ex())))) \
        + " 节点=" + (S (bipCount)) + " 快照=" + (S ((snapAll()).count)))
    local nm = #()
    for i = 1 to (amax sn0.count 8) do append nm sn0[i][1].name
    say ("G4 骨名前 8 个=" + (trim (S nm)) + " | 最后一根骨盒=" \
        + (S (try ( localBox sn0[1][1] ) catch ("EX:" + (ex())))))

    /* ---- G9 探针坑：resetMaxFile 会不会打断 fileIn 进来的脚本 ---- */
    say "G9 即将 resetMaxFile #noPrompt（此行之后若再无读数，就证实这条坑）"
    try ( resetMaxFile #noPrompt ) catch ( say ("G9 resetMaxFile 抛 " + (ex())) )
    say ("G9 resetMaxFile 之后仍然在跑，对象数=" + (S objects.count))

    say "===== M4g rollback probe done ====="
    true
)

(
    try ( m4gRun() ) catch ( say ("ABORT: " + (ex())) )
    try ( quitMax quiet:true ) catch ( try ( quitMax ) catch () )
)
