/* M4 探针第三轮（readback_m4c）：前两轮的读数把 M4 的产品设计逼到了两个岔口，
   这一轮只回答这两个岔口，答完就直接写产品代码，不再加探针轮。

   岔口一：ReplaceVertexWeights 的「返回 OK 但写进去是 0.0」。
      第二轮实测：
        A6 顶点 v6 已是 bone3@0.35 + bone1@0.65（总和 1.0），再 ReplaceVertexWeights
           写 #(1) #(1.0) → 回读 `{1:1} {1:0.0}` —— 调用成功、权重归零。
        B1 干净 Skin 上第一个 ReplaceVertexWeights 调用（v7，0.7/0.3）→ 回读 0.0/0.0；
           同一次里 B2 写 v8 → 回读 0.7/0.3 正常；B4 回头重写 v7 仍是 0.0/0.0（毒住了）。
        B7 同样位置改用 SetVertexWeights 首调 v7 → 回读 0.7/0.3 正常。
      正式脚本 23 轮里 `failed_vertices=0` 全靠 ReplaceVertexWeights 的返回值计数，
      而它自己会「成功地把权重写成 0」—— 这正是 M4 存在的理由，但也意味着：
      **换 API / 加读回写 的选法必须先测准**，否则 M4b 的闭环会挑一条同样会静默归零的路。
      收敛循环还会对同一顶点反复重写，所以「重复写是否幂等」是硬判据。

   岔口二：回滚用什么。
      第二轮 `biped.saveFigFile ctrl path` 报 `Cannot run the operation in this mode`
      —— 它要 Figure 模式。可是进 Figure 模式本身会不会把已经摆好的骨位改掉？
      如果会，「为了回滚而进 Figure 模式」就是拿几何去换保险，本末倒置。
      所以要测：切 figureMode 前后同一根骨的 #pos 有没有动；
      以及纯数据快照（getTransform 存下来、setTransform 打回去）能不能等价还原 ——
      后者不碰模式、不写文件、只还原我们自己动过的东西，是更稳的地基。 */

global PROBE_OUT = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/readback_m4c.txt"

fn say s =
(
    local f = openFile PROBE_OUT mode:"a" encoding:#utf8
    if f != undefined do ( format "%\n" (s as string) to:f; close f )
)

fn why = try ( getCurrentException() as string ) catch "?"
fn S x = try ( x as string ) catch "<unprintable>"
fn msk x = try ( x as string ) catch "<unprintable>"

fn describe x =
(
    local c = try ( classOf x ) catch "?"
    local out = (S c)
    if (classOf x) == Array do
    (
        out = out + "[" + (x.count as string) + "]"
        local lim = if x.count < 8 then x.count else 8
        for i = 1 to lim do
        (
            local e = x[i]
            local ec = try ( classOf e ) catch "?"
            if ec == Array and e.count >= 2 then
                out = out + " {" + (e.count as string) + ":" + (S e[1]) + "/" + (S e[2]) + "}"
            else if ec == Array then
                out = out + " {" + (e.count as string) + ":" + (S e[1]) + "}"
            else
                out = out + " " + (S e)
        )
    )
    out
)

/* 回读一个顶点并压成一行：主骨 + 权重列 + 总和。产品判据就是拿这几样比，
   探针里先按同一口径打，省得「探针说的」和「产品报的」是两种话。 */
fn rbline smod v =
(
    local x = try ( skinOps.GetVertexBoneIDsAndWeights smod v ) catch "EX"
    if (classOf x) != Array or x.count < 2 then ("v" + (S v) + " " + (S x))
    else
    (
        local ids = x[1]
        local ws = x[2]
        local ssum = 0.0
        for i = 1 to ws.count do ssum += ws[i]
        ("v" + (S v) + " ids=" + (S ids) + " ws=" + (S ws) + " sum=" + (msk ssum))
    )
)

fn freshSkin tag =
(
    try ( deleteAllObjects() ) catch ()
    local mesh = try ( Box name:(tag + "Box") length:20.0 width:20.0 height:20.0 \
        lengthsegs:9 widthsegs:9 heightsegs:9 mapsize:5.0 ) catch undefined
    if mesh != undefined do ( try ( convertTo mesh Editable_Mesh ) catch () )
    local smod = undefined
    local sm = try ( Skin() ) catch undefined
    if sm != undefined and mesh != undefined do
    (
        try ( sm.name = (tag + "Skin"); addModifier mesh sm; smod = mesh.modifiers[1] ) catch ()
    )
    local b1 = try ( BoneSys.createBone [-5,0,0] [0,0,0] [0,1,0] ) catch undefined
    local b2 = try ( BoneSys.createBone [0,0,0] [5,0,0] [0,1,0] ) catch undefined
    local b3 = try ( BoneSys.createBone [5,0,0] [10,0,0] [0,1,0] ) catch undefined
    if b1 != undefined do try ( b1.name = (tag + "A") ) catch ()
    if b2 != undefined do try ( b2.name = (tag + "B") ) catch ()
    if b3 != undefined do try ( b3.name = (tag + "C") ) catch ()
    if smod != undefined do
    (
        try ( select mesh; modPanel.setCurrentObject smod ) catch ()
        try ( skinOps.AddBone smod b1 0 ) catch ()
        try ( skinOps.AddBone smod b2 0 ) catch ()
        try ( skinOps.AddBone smod b3 0 ) catch ()
    )
    say ("  [" + tag + "] smod=" + (if smod == undefined then "NONE" else "OK") \
        + " verts=" + (if mesh == undefined then "?" else (S mesh.numVerts)))
    smod
)

(
    say "===== M4c probe start ====="

    -----------------------------------------------------------------------------
    -- A1. 首调毒化 vs 交错读：同一份写，读的时候机不同，结果是否不同 ----------
    -----------------------------------------------------------------------------
    local s1 = freshSkin "A1"
    if s1 != undefined do
    (
        /* 连续写三个顶点，中间不读 —— 第二轮里「第一个写」毒住、「第二个写」正常。
           若这里 v5 坏、v6/v7 好，就确认「毒只打在每次新建 Skin 的第一次调用上」。 */
        say ("  A1 RVW v5 = " + (S (try ( skinOps.ReplaceVertexWeights s1 5 #(2,3) #(0.7,0.3) ) catch why())))
        say ("  A1 RVW v6 = " + (S (try ( skinOps.ReplaceVertexWeights s1 6 #(2,3) #(0.7,0.3) ) catch why())))
        say ("  A1 RVW v7 = " + (S (try ( skinOps.ReplaceVertexWeights s1 7 #(2,3) #(0.7,0.3) ) catch why())))
        say ("  A1 集中回读 " + (rbline s1 5))
        say ("  A1 集中回读 " + (rbline s1 6))
        say ("  A1 集中回读 " + (rbline s1 7))
    )

    -----------------------------------------------------------------------------
    -- A2. 写一次读一次（产品闭环的写法）会不会自己把毒躲开 --------------------
    -----------------------------------------------------------------------------
    local s2 = freshSkin "A2"
    if s2 != undefined do
    (
        say ("  A2 RVW v5 = " + (S (try ( skinOps.ReplaceVertexWeights s2 5 #(2,3) #(0.7,0.3) ) catch why())) \
            + " | " + (rbline s2 5))
        say ("  A2 RVW v6 = " + (S (try ( skinOps.ReplaceVertexWeights s2 6 #(2,3) #(0.7,0.3) ) catch why())) \
            + " | " + (rbline s2 6))
        /* 关键：对被写坏的那个顶点，用 RVW 重写、还是必须换 SWV？ */
        say ("  A2 RVW 重写 v5 = " + (S (try ( skinOps.ReplaceVertexWeights s2 5 #(2,3) #(0.7,0.3) ) catch why())) \
            + " | " + (rbline s2 5))
        say ("  A2 SWV 重写 v5 = " + (S (try ( skinOps.SetVertexWeights s2 5 #(2,3) #(0.7,0.3) ) catch why())) \
            + " | " + (rbline s2 5))
        /* SWV 连打两次：收敛循环会对同一顶点反复写，幂等性必须实测。 */
        say ("  A2 SWV 三打 v5 = " + (S (try ( skinOps.SetVertexWeights s2 5 #(2,3) #(0.7,0.3) ) catch why())) \
            + " | " + (rbline s2 5))
        /* 已归一化（sum=1）的顶点上改权重值：SWV 能不能真的改数（RVW 在这里会变 0）。 */
        say ("  A2 SWV 改值 v5 0.9/0.1 = " + (S (try ( skinOps.SetVertexWeights s2 5 #(2,3) #(0.9,0.1) ) catch why())) \
            + " | " + (rbline s2 5))
        say ("  A2 RVW 改值 v5 0.8/0.2 = " + (S (try ( skinOps.ReplaceVertexWeights s2 5 #(2,3) #(0.8,0.2) ) catch why())) \
            + " | " + (rbline s2 5))
    )

    -----------------------------------------------------------------------------
    -- A3. 产品真正的使用姿势：一行代码写全表，然后集中回读一次 ----------------
    --     正式脚本就是「循环写完 → 不读」；M4b 要加的是「循环写完 → 读全表 → 补写坏的」。
    --     这里量：补写这一轮到底能不能把 0.0 救回来（救不回来就只能换 API 重写全表）。
    -----------------------------------------------------------------------------
    local s3 = freshSkin "A3"
    if s3 != undefined do
    (
        local bad = #()
        for v = 1 to 40 do
        (
            try ( skinOps.ReplaceVertexWeights s3 v #(2,3) #(0.7,0.3) ) catch ( append bad (-v) )
        )
        local zeroVerts = #()
        for v = 1 to 40 do
        (
            local x = try ( skinOps.GetVertexBoneIDsAndWeights s3 v ) catch undefined
            if (classOf x) == Array and x.count >= 2 do
            (
                local ssum = 0.0
                for i = 1 to x[2].count do ssum += x[2][i]
                if ssum < 0.5 do append zeroVerts v
            )
        )
        say ("  A3 连写 40 个顶点后：抛错顶点=" + (S bad) + " 零权重顶点=" + (S zeroVerts))
        /* 补写：先 RVW 再 SWV，看谁真能救回来。 */
        local stillBad = #()
        for v in zeroVerts do
        (
            try ( skinOps.ReplaceVertexWeights s3 v #(2,3) #(0.7,0.3) ) catch ()
            local x = try ( skinOps.GetVertexBoneIDsAndWeights s3 v ) catch undefined
            local ssum = 0.0
            if (classOf x) == Array and x.count >= 2 do for i = 1 to x[2].count do ssum += x[2][i]
            if ssum < 0.5 do append stillBad v
        )
        say ("  A3 RVW 补写后仍为零的顶点 = " + (S stillBad))
        local stillBad2 = #()
        for v in zeroVerts do
        (
            try ( skinOps.SetVertexWeights s3 v #(2,3) #(0.7,0.3) ) catch ()
            local x = try ( skinOps.GetVertexBoneIDsAndWeights s3 v ) catch undefined
            local ssum = 0.0
            if (classOf x) == Array and x.count >= 2 do for i = 1 to x[2].count do ssum += x[2][i]
            if ssum < 0.5 do append stillBad2 v
        )
        say ("  A3 SWV 补写后仍为零的顶点 = " + (S stillBad2))
        /* 全表回读耗时（产品要在每轮装配里跑，代价必须已知）。 */
        local n = 40
        local t0 = timeStamp()
        for v = 1 to n do ( try ( skinOps.GetVertexBoneIDsAndWeights s3 v ) catch () )
        say ("  A3 回读 " + (S n) + " 顶点耗时 = " + (msk (((timeStamp() - t0) / 1000.0))) + " 秒")
    )

    -----------------------------------------------------------------------------
    -- B. figureMode 与 saveFigFile：切模式会不会动几何 ------------------------
    -----------------------------------------------------------------------------
    try ( deleteAllObjects() ) catch ()
    local bp = undefined
    try ( bp = biped.createNew 170.0 0.0 [0,0,0] arms:true ) catch ( say ("  biped.createNew FAILED: " + why()) )
    if bp != undefined do
    (
        local ctrl = try ( bp.controller ) catch undefined
        local foot = try ( getNodeByName "Bip001 L Foot" exact:true ) catch undefined
        local pelv = try ( getNodeByName "Bip001 Pelvis" exact:true ) catch undefined
        say ("  ctrl = " + (S (classOf ctrl)) + " foot=" + (if foot == undefined then "NONE" else "OK"))
        if ctrl != undefined and foot != undefined do
        (
            local p0 = biped.getTransform foot #pos
            local r0 = try ( biped.getTransform foot #rotation ) catch "EX"
            say ("  B1 切模式前 foot #pos = " + (msk p0.x) + "," + (msk p0.y) + "," + (msk p0.z))
            /* 属性写法与函数写法都试：产品代码只能用实测存在的那一种。 */
            say ("  B2 figureMode 现值 = " + (S (try ( ctrl.figureMode ) catch ("EX " + why()))))
            say ("  B3 ctrl.figureMode = true -> " + (S (try ( ctrl.figureMode = true ) catch ("EX " + why()))))
            local p1 = biped.getTransform foot #pos
            say ("  B4 进 Figure 后 foot 位移 = " + (msk (distance p1 p0)) + "（>0 = 切模式自己动了几何，回滚不能走这条路）")
            say ("  B5 figureMode 读回 = " + (S (try ( ctrl.figureMode ) catch ("EX " + why()))))
            local fig = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/m4c_fig.bfc"
            try ( deleteFile fig ) catch ()
            say ("  B6 saveFigFile(在 Figure 模式) = " + (S (try ( biped.saveFigFile ctrl fig ) catch ("EX " + why()))))
            say ("  B7 文件在 = " + (S (try ( doesFileExist fig ) catch false)) \
                + " size=" + (S (try ( getFileSize fig ) catch why())))
            say ("  B8 ctrl.figureMode = false -> " + (S (try ( ctrl.figureMode = false ) catch ("EX " + why()))))
            local p2 = biped.getTransform foot #pos
            say ("  B9 出 Figure 后 foot 位移 = " + (msk (distance p2 p0)))
            /* 摆位之后在普通模式 load：这才是产品里「跑歪了退回去」的真实调用时机。 */
            if (try ( doesFileExist fig ) catch false) then
            (
                try ( biped.setTransform foot #pos (p0 + [3.0,0,0]) ) catch ()
                if pelv != undefined do ( try ( biped.setTransform pelv #pos ((biped.getTransform pelv #pos) + [0,0,5.0]) ) catch () )
                local pp0 = if pelv != undefined then (biped.getTransform pelv #pos) else [0,0,0]
                say ("  B10 非 Figure 模式 load = " + (S (try ( biped.loadFigFile ctrl fig ) catch ("EX " + why()))))
                local p3 = biped.getTransform foot #pos
                say ("  B11 load 后 foot 离快照 = " + (msk (distance p3 p0)) + "（≈3 = 没还原；≈0 = 还原成功）")
                if pelv != undefined do
                    say ("  B12 load 后 pelvis 离快照 = " + (msk (distance (biped.getTransform pelv #pos) pp0)))
                say ("  B13 load 后 figureMode = " + (S (try ( ctrl.figureMode ) catch ("EX " + why()))))
                say ("  B14 load 后仍可 setTransform = " + (S (try ( biped.setTransform foot #pos p0 ) catch ("EX " + why()))))
            )
            else
                say "  B10~B14 跳过（快照没落盘，load 无从谈起）"

            /* SetMode 系列写法留着当备选：若 saveFigFile 只有 Figure 模式能用而 Figure 模式动几何，
               就得知道有没有「只存不切」的写法。 */
            say ("  B15 biped.SetMode(ctrl,#fig) = " + (S (try ( biped.SetMode ctrl #fig ) catch ("EX " + why()))))
            say ("  B16 biped.SetMode(ctrl,#move) = " + (S (try ( biped.SetMode ctrl #move ) catch ("EX " + why()))))
        )

        ---------------------------------------------------------------------
        -- C. 纯数据快照/还原：不碰模式、不写文件，只还原我们自己动过的东西 --
        ---------------------------------------------------------------------
        if foot != undefined do
        (
            local nodes = #()
            for o in objects where (classOf o) == Biped_Object do append nodes o
            say ("  C1 采到 Biped 节点数 = " + (S nodes.count) + "（objects.count=" + (S objects.count) + "）")
            local snap = #()
            local t1 = timeStamp()
            for o in nodes do
            (
                local pp = try ( biped.getTransform o #pos ) catch undefined
                local rr = try ( biped.getTransform o #rotation ) catch undefined
                /* 快照项 = #(节点, 位置, 四元数)。节点存引用不存名字：回滚按引用打，
                   中途改名（BoneGeometry 那类）不会把还原打偏。 */
                if pp != undefined and (classOf rr) == Quat do
                (
                    local item = #()
                    append item o
                    append item pp
                    append item rr
                    append snap item
                )
            )
            say ("  C2 快照 " + (S snap.count) + " 项耗时 = " + (msk (((timeStamp() - t1) / 1000.0))) + " 秒")

            /* 动三根骨：位置 + 旋转都动，还原必须两样都回去。基线先存成变量，
               后面每一项都跟自己的基线比 —— 上一版把 hand 的误差拿去和 foot 的基线算，
               那条读数毫无意义，正是「恒等式看不见偏差」的探针版。 */
            local f0 = biped.getTransform foot #pos
            local q0 = biped.getTransform foot #rotation
            local pv0 = if pelv != undefined then (biped.getTransform pelv #pos) else undefined
            local hand = try ( getNodeByName "Bip001 L Hand" exact:true ) catch undefined
            local hd0 = if hand != undefined then (biped.getTransform hand #pos) else undefined
            try ( biped.setTransform foot #pos (f0 + [3.0,0,0]) ) catch ( say ("  C3 setTransform pos FAILED: " + why()) )
            try ( biped.setTransform foot #rotation (q0 * (angleAxis 12.0 [0,1,0])) ) catch ( say ("  C4 setTransform rot FAILED: " + why()) )
            if pv0 != undefined do ( try ( biped.setTransform pelv #pos (pv0 + [0,0,5.0]) ) catch ( say ("  C5 setTransform pelv FAILED: " + why()) ) )
            if hd0 != undefined do ( try ( biped.setTransform hand #pos (hd0 + [0,2.0,0]) ) catch ( say ("  C6 setTransform hand FAILED: " + why()) ) )
            say ("  C7 动过之后 foot 离基线 = " + (msk (distance (biped.getTransform foot #pos) f0)) \
                + " pelvis = " + (if pv0 != undefined then (msk (distance (biped.getTransform pelv #pos) pv0)) else "?"))

            local t2 = timeStamp()
            for it in snap do
            (
                try ( biped.setTransform it[1] #pos it[2] ) catch ()
                try ( biped.setTransform it[1] #rotation it[3] ) catch ()
            )
            say ("  C8 还原 " + (S snap.count) + " 项耗时 = " + (msk (((timeStamp() - t2) / 1000.0))) + " 秒")
            local f1 = biped.getTransform foot #pos
            local q1 = biped.getTransform foot #rotation
            say ("  C9 还原后 foot 位置误差 = " + (msk (distance f1 f0)) + "（≈0 = 位置真回去）")
            /* 四元数分量差：只看 |dot| 会漏掉「差一个符号」的 360°，这里两样都打。 */
            local dotv = try ( fDot q0 q1 ) catch "?"
            local dq = abs (q1.x - q0.x) + abs (q1.y - q0.y) + abs (q1.z - q0.z) + abs (q1.w - q0.w)
            say ("  C10 还原后 foot 旋转分量差 = " + (msk dq) + " dot=" + (msk dotv) + "（分量差≈0 且 dot≈1 = 姿态真回去）")
            if pv0 != undefined do
                say ("  C11 还原后 pelvis 位置误差 = " + (msk (distance (biped.getTransform pelv #pos) pv0)))
            if hd0 != undefined do
                say ("  C12 还原后 hand 位置误差 = " + (msk (distance (biped.getTransform hand #pos) hd0)))
            say ("  C13 还原后 figureMode = " + (S (try ( ctrl.figureMode ) catch ("EX " + why()))))
        )
    )

    say "===== M4c readback probe done ====="
    try ( quitMax quiet:true ) catch ( try ( quitMax ) catch () )
)
