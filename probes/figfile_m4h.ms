/* M4d 探针（figfile_m4h）：只剩两件事要量 —— ①骨盒尺寸（#scale）归不归 .fig 管；
   ②`biped.saveFigFile` 自己那一下模式切换，在**已经改过姿态**的 Biped 上代价多大。
   这两条直接决定回滚是「每个 pass 存一份」还是「整轮只存一份」。

   上一发（figfile_m4g v3，probes/figfile_m4g.txt）已经钉死、这一发**不再重复问**的：
     · 回滚精度：#pos 5.41143e-05 厘米、世界矩阵 4.11272e-05，连回 3 次不衰减（K1/K2/K3）。
     · **重新存图能当新基准**：存图前快照 sn1 → 扰动 18.7419 厘米 → load 新图 → 全列 0.0
       （连 5e-05 都没有）。上一发怀疑的「2.13 厘米残差」是拿新基准去比**最初那份**快照，
       比错了对象。
     · 副作用：Box / Skin 修改器 / BoneGeometry / 骨名 / 骨数在多次回滚后一律无损（G4）。
     · 产品用的写句 `biped.setTransform n #scale v false` 在**非 Figure 模式**下是彻底的
       空操作（盒差 2.38419e-07、`biped.getTransform #scale` 差 0.0）。产品早就把这条写成
       硬要求（max_import_obj.ms:2319「必须跑在 mmd2maxFigureMode ... false 之前」），
       是探针自己违反了。⇒ 本发所有 #scale 写全部包在 figureMode = true 里。
     · **Biped_Object 没有 scale 属性**：26 根骨逐根抛「Unknown property: "scale"」，
       所以上一发 `.scale` 那一列恒等于 0 是「读不到」不是「没变」，别再拿它当证据。
     · `fileIn` 进来的脚本里调 `resetMaxFile #noPrompt` 会当场整发死掉
       （「Call needs function or class, got: StringStream:""」，外层 catch 只剩一行
       ABORT）。正式脚本走 `-U MAXScript <file>` 没这问题（跑了 26 轮）。探针禁止 reset。

   本发怎么读（H2 那一行是主判据）：
     H1 Figure 模式内把每根骨的 #scale 三轴同乘 1.3 → 盒差**必须**明显大于 0，
        否则「骨盒根本改不动」，产品 :2481 那句也是空操作 —— 那是比回滚更要紧的发现，
        先把这一行的数字打出来。
     H2 load FIG_A 之后盒差回到多少：
        · 回到 ~0 ⇒ **.fig 连骨盒一起管**，回滚能同时抹掉「姿态写坏」与「骨盒改坏」。
        · 仍是 1.3 那个量 ⇒ .fig 只管姿态，M4d 范围写死成「姿态回滚」；骨盒要回滚只能
          自己在基准里存 #scale、回滚时逐骨写回 —— 这条备选路 H3 一并实测，不留给下一轮。
     H4 姿态先被挪开（不再是图姿态）再存图：`figSave` 自身的模式翻转代价多大、
        load 能不能把它抹掉。若这一行读数很大（比如直接把姿态拉回图姿态），
        「每 pass 存一份」就是错的写法，只能整轮存一次、坏了 load 一次。
     H5 `.bip`（saveBipFile / loadBipFile）对同一刀骨盒编辑的行为 —— 只在 .fig 不管骨盒
        时才是候选载体。排最后：这两条 API 有弹模态框的风险，前面的读数已经落盘了。

   全程不碰用户场景，跑完自己 quitMax。 */

global PROBE_OUT = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/figfile_m4h.txt"
global FIG_A = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/m4h.fig"
global FIG_D = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/m4h_d.fig"
global BIP_E = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/m4h.bip"
global CTRL = undefined
global ROOT_NODE = undefined

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

/* 三个数取最大用 amax：`max` 是 `max quit` 那种命令语法，不是函数（上一发实测）。 */
fn amax3 a b c = amax (amax a b) c

/* 本地包围盒 = 验收那把尺（max_import_obj.ms:2397 mmd2maxLocalExtent 同源）。
   读不到返回 undefined，让 boxgap 打 -1 把「读不到」和「真的零差」分开。 */
fn localBox node =
(
    local bb = try ( nodeGetBoundingBox node node.transform ) catch undefined
    if (classOf bb) != Array do return undefined
    if bb.count < 2 do return undefined
    if (classOf bb[1]) != Point3 do return undefined
    if (classOf bb[2]) != Point3 do return undefined
    bb
)

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

/* 世界矩阵逐元素比（12 个数）。不用四元数：回滚正确时四元数分量差照样能读到 3.99，
   那是 q 与 -q 同一个旋转，会把「已还原」读成「没还原」（m4d B5 同因）。 */
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

/* 快照一项 = #(节点, #pos, node.transform, 本地包围盒, #scale 读值)。
   判据一律看包围盒；#scale 只是「这条 API 自己的读数活不活」的旁证。
   不再读 node.scale —— 上一发实测 Biped_Object 没这个属性，逐根抛异常。 */
fn snapAll =
(
    local sn = #()
    for o in objects where (classOf o) == Biped_Object do
    (
        local p = try ( biped.getTransform o #pos ) catch undefined
        local k = try ( biped.getTransform o #scale ) catch undefined
        append sn #(o, p, o.transform, (localBox o), k)
    )
    sn
)

/* 整树最大差：#(包围盒角差, #pos 差, 世界矩阵差, #scale 差, 差最大的骨名) */
fn worstNow sn =
(
    local db = 0.0
    local dp = 0.0
    local dm = 0.0
    local dk = 0.0
    local who = "-"
    for e in sn do
    (
        local n = e[1]
        local p = try ( biped.getTransform n #pos ) catch undefined
        local k = try ( biped.getTransform n #scale ) catch undefined
        local x = boxgap (localBox n) e[4]
        local y = if (classOf p) == Point3 then (distance p e[2]) else -1.0
        local z = mgap n.transform e[3]
        local u = p3gap k e[5]
        if x > db do ( db = x; who = n.name )
        if y > dp do dp = y
        if z > dm do dm = z
        if u > dk do dk = u
    )
    #(db, dp, dm, dk, who)
)

fn rline tag r =
(
    tag + " 盒=" + (S r[1]) + " pos=" + (S r[2]) + " 世界=" + (S r[3]) \
        + " #scale=" + (S r[4]) + " @ " + (S r[5])
)

/* 单根骨的具体读数：只看「最差骨差」不够，H1 需要能说清「骨盒确实从几厘米变成几厘米」。
   写法注意：`if ... do ( ... ) else ( ... )` 在 MAXScript 是 Syntax error（do 不接 else），
   第一发就死在这上面 —— 这里用 guard + return 分开两条路。 */
fn detail tag name =
(
    local n = try ( getNodeByName name exact:true ) catch undefined
    if n == undefined do
    (
        say ("  " + tag + " " + name + " 读不到节点")
        return false
    )
    local k = try ( biped.getTransform n #scale ) catch ("EX:" + (ex()))
    local b = localBox n
    local yspan = 0.0
    if b != undefined do yspan = b[2].y - b[1].y
    say ("  " + tag + " " + name + " #scale=" + (S k) + " 盒=" + (S b) + " Y跨度=" + (S yspan))
    yspan
)

fn figOn flag =
(
    local r = try ( CTRL.figureMode = flag ) catch ("EX:" + (ex()))
    local back = try ( CTRL.figureMode ) catch ("EX:" + (ex()))
    say ("  figureMode := " + (S flag) + " 返回=" + (S r) + " 回读=" + (S back))
    back
)

/* 产品 :2481 那条写句，倍率版：读当前 #scale，三轴同乘 factor。
   三轴一起乘是最强信号 —— 只要这条 API 活着，包围盒不可能不动。 */
fn pertScaleRel sn factor =
(
    local okw = 0
    local err = ""
    for e in sn do
    (
        local n = e[1]
        local s = try ( biped.getTransform n #scale ) catch undefined
        if (classOf s) == Point3 do
        (
            local v = [s.x * factor, s.y * factor, s.z * factor]
            try ( biped.setTransform n #scale v false; okw += 1 ) \
                catch ( if err == "" do err = n.name + " 抛 " + (ex()) )
        )
    )
    say ("  写 #scale 骨数=" + (S okw) + "/" + (S sn.count) + (if err != "" then " 首个异常: " + err else ""))
)

/* H3 备选路：基准里存 #scale，回滚时逐骨写回。乘 1.0 就是「把基准值原样写回去」，
   拿它当「逐骨写回能不能复原骨盒」的判据（写不进的话这一步读数就还是 1.3 那个量）。 */
fn restoreScales sn =
(
    local okr = 0
    for e in sn do
    (
        local n = e[1]
        local s = e[5]
        if (classOf s) == Point3 do
            try ( biped.setTransform n #scale s false; okr += 1 ) catch ( say ("  写回抛 " + n.name + " " + (ex())) )
    )
    okr
)

/* 姿态扰动：把每根骨挪开并且**转一下**，让场景不再是图姿态 —— H4 要的就是这个前提。
   `#rotation` 而不是 `#rot`：:1444 实测 #rot 在 Max 2022 抛异常。 */
fn pertPosRot sn =
(
    for e in sn do
    (
        local n = e[1]
        if (classOf e[2]) == Point3 do
            try ( biped.setTransform n #pos (e[2] + [3.7, -2.1, 5.5]) false ) \
                catch ( say ("  扰动 #pos 抛 " + n.name + " " + (ex())) )
        local q = try ( biped.getTransform n #rotation ) catch undefined
        if (classOf q) == Quat do
            try ( biped.setTransform n #rotation (q * (angleaxis 12.0 [0.0, 0.0, 1.0])) false ) \
                catch ( say ("  扰动 #rotation 抛 " + n.name + " " + (ex())) )
    )
)

fn figSave path =
(
    local a = try ( CTRL.figureMode = true ) catch ("EX:" + (ex()))
    local b = try ( biped.saveFigFile CTRL path ) catch ("EX:" + (ex()))
    local c = try ( CTRL.figureMode = false ) catch ("EX:" + (ex()))
    say ("  figSave 进=" + (S a) + " 调用=" + (S b) + " 出=" + (S c) \
        + " size=" + (S (try ( getFileSize path ) catch ("EX:" + (ex())))))
    b
)
fn figLoad path =
(
    local a = try ( CTRL.figureMode = true ) catch ("EX:" + (ex()))
    local b = try ( biped.loadFigFile CTRL path ) catch ("EX:" + (ex()))
    local c = try ( CTRL.figureMode = false ) catch ("EX:" + (ex()))
    say ("  figLoad 进=" + (S a) + " 调用=" + (S b) + " 出=" + (S c))
    b
)

/* H5：.bip 这条载体。接收者先试 controller，抛了就试根节点，把活的那个记进 BIP_RECV
   （load 那半截要用同一个接收者）。 */
global BIP_RECV = ""
fn bipSave path =
(
    local r1 = try ( biped.saveBipFile CTRL path ) catch ("EX:" + (ex()))
    if (classOf r1) != String do ( BIP_RECV = "ctrl"; return r1 )
    local r2 = try ( biped.saveBipFile ROOT_NODE path ) catch ("EX:" + (ex()))
    if (classOf r2) != String do ( BIP_RECV = "node"; return r2 )
    say ("  saveBipFile 两种接收者都抛：ctrl=" + (trim r1) + " node=" + (trim r2))
    false
)

fn m4hRun =
(
    say "===== M4h box-roundtrip probe v4 ====="
    say ("H0 起手对象数=" + (S objects.count))

    local root = undefined
    try
    (
        root = biped.createNew 170.0 0.0 [0.0, 0.0, 0.0] arms:true neckLinks:1 \
            spineLinks:3 legLinks:3 toes:1 toeLinks:1 trianglePelvis:false triangleNeck:true
    )
    catch ( say ("H0 biped.createNew 抛 " + (ex())) )
    if root == undefined do ( say "H0 建不出 Biped，探针到此为止。"; return false )
    ROOT_NODE = root
    try ( CTRL = root.controller ) catch ( say ("H0 root.controller 抛 " + (ex())) )
    if CTRL == undefined do ( say "H0 拿不到 controller，后面全废。"; return false )

    figOn true
    local sn0 = snapAll()
    say ("H0 根=" + root.name + " 快照=" + (S sn0.count) + " 对象数=" + (S objects.count))
    say ("  " + (rline "H0 基线（各列都该是 0）" (worstNow sn0)))
    detail "H0" "Bip001 L Thigh"
    detail "H0" "Bip001 Head"

    try ( deleteFile FIG_A ) catch ( say ("H0 删旧图抛 " + (ex())) )
    figSave FIG_A
    /* 存图自己翻了一次模式（true→存→false）。先用**存图前**那份基线量一下这一下的代价，
       再把基准换成存图后的状态 —— 顺序反了就会把「存图的扰动」算进 H1「写 #scale 的效果」。 */
    redrawViews()
    figOn true
    say ("  " + (rline "H0 存图后 vs 存图前（>0 就是 figSave 自己的扰动）" (worstNow sn0)))
    sn0 = snapAll()

    /* ---- H1 Figure 模式内写 #scale：骨盒到底改不改得动 ---- */
    pertScaleRel sn0 1.3
    redrawViews()
    local h1 = worstNow sn0
    say ("  " + (rline "H1 写完 #scale x1.3（盒差为 0 就说明写不进）" h1))
    detail "H1" "Bip001 L Thigh"
    if h1[1] <= 1e-4 do say "H1 观察：Figure 模式内也改不动骨盒 —— 产品 :2481 那句是空操作，这条比回滚更要紧。"

    /* ---- H2 主判据：load FIG_A 之后骨盒回不回来 ---- */
    figLoad FIG_A
    redrawViews()
    local h2 = worstNow sn0
    say ("  " + (rline "H2 load 之后（盒差 ~0 ⇒ .fig 带骨盒；仍是 1.3 那个量 ⇒ 只管姿态）" h2))
    detail "H2" "Bip001 L Thigh"
    if h2[1] <= 1e-4 do say "H2 结论：.fig 连骨盒一起还原，回滚一份基准就够。"
    if h2[1] > 1e-4 do say "H2 结论：.fig 不还原骨盒，回滚范围只能写成「姿态」。"

    /* 退出 Figure 模式再看一眼：产品 :2500 说「退出 Figure 模式后仍在」，
       这句是骨盒拟合能验收的前提，顺手在同一条路上复核。 */
    figOn false
    redrawViews()
    say ("  " + (rline "H2b 退出 Figure 模式之后" (worstNow sn0)))

    /* ---- H3 备选路：基准存 #scale、回滚逐骨写回 ---- */
    figOn true
    pertScaleRel sn0 1.3
    redrawViews()
    local h3a = worstNow sn0
    say ("  " + (rline "H3 再写一次 #scale x1.3" h3a))
    local okr = restoreScales sn0
    redrawViews()
    local h3b = worstNow sn0
    say ("  H3 逐骨写回 骨数=" + (S okr) + " | " + (rline "写回后" h3b))
    if h3b[1] <= 1e-4 do say "H3 结论：逐骨把基准 #scale 写回去能复原骨盒 —— .fig 不管骨盒也有这条路。"
    if h3b[1] > 1e-4 do say "H3 结论：逐骨写回也复原不了骨盒，骨盒编辑在这版就是单向的。"
    figLoad FIG_A
    figOn false
    redrawViews()

    /* ---- H4 存图这个动作的代价（前提：姿态已不是图姿态） ----
       扰动放在 Figure 模式**内**做，退出模式再存图 —— 这才和产品的流水线同构
       （:3662 进模式、:4137 出模式，摆位全在中间）。在模式外写 #pos 是插 key，
       量出来的就不是「存图的代价」。 */
    figOn true
    pertPosRot sn0
    figOn false
    redrawViews()
    local sn2 = snapAll()
    say ("  " + (rline "H4 扰动后重拍基线（各列该是 0）" (worstNow sn2)))
    figSave FIG_D
    redrawViews()
    local h4a = worstNow sn2
    say ("  " + (rline "H4 只是存了个图（这行读数 = 存图的代价）" h4a))
    figLoad FIG_D
    redrawViews()
    local h4b = worstNow sn2
    say ("  " + (rline "H4 load 刚存的那份之后（0 ⇒ 存/读成对无损）" h4b))

    /* ---- H5 .bip 对骨盒编辑什么行为（放最后：这两条 API 有弹模态框的风险） ---- */
    figOn true
    local sn3 = snapAll()
    pertScaleRel sn3 0.7
    redrawViews()
    local h5a = worstNow sn3
    say ("  " + (rline "H5 写 #scale x0.7 之后" h5a))
    try ( deleteFile BIP_E ) catch ( say ("H5 删旧 bip 抛 " + (ex())) )
    local sv = bipSave BIP_E
    say ("  saveBipFile 接收者=" + (S BIP_RECV) + " 调用=" + (S sv) \
        + " size=" + (S (try ( getFileSize BIP_E ) catch ("EX:" + (ex())))))
    pertScaleRel sn3 1.5
    redrawViews()
    say ("  " + (rline "H5 再写 #scale x1.5" (worstNow sn3)))
    local ld = false
    if BIP_RECV == "ctrl" do ( ld = try ( biped.loadBipFile CTRL BIP_E ) catch ("EX:" + (ex())) )
    if BIP_RECV == "node" do ( ld = try ( biped.loadBipFile ROOT_NODE BIP_E ) catch ("EX:" + (ex())) )
    redrawViews()
    say ("  loadBipFile 调用=" + (S ld) + " | " + (rline "H5 load .bip 之后（盒差 ~0 ⇒ .bip 带骨盒）" (worstNow sn3)))

    say ("H9 收尾前对象数=" + (S objects.count) + "（探针里不许 resetMaxFile，见文件头）")
    say "===== M4h done ====="
    true
)

(
    try ( m4hRun() ) catch ( say ("ABORT: " + (ex())) )
    /* 关 Max 这一步允许失败：真抛了也只是进程留在后台，读数已经全部落盘。 */
    try ( quitMax quiet:true ) catch ( try ( quitMax ) catch ( say ("quitMax 抛 " + (ex())) ) )
)
