/* M4 探针第四轮（readback_m4d）：第三轮把两条产品路线逼到必须当场定死的程度。

   一、权重「二次写坍缩」的准确边界与**补救路线**（决定 M4b 的闭环能不能修）
      第三轮实测（同一个 Skin 修改器、488 顶点）：
        A1 连续写 v5/v6/v7 各一次 → 回读全部 0.7/0.3，正确。
        A2 写 v5 → 正确；写 v6 → 正确；**再写 v5** → 回读 0.0/0.0；
           此后对 v5 无论用 ReplaceVertexWeights 还是 SetVertexWeights、无论写什么值，
           回读恒为 0.0/0.0 —— 顶点被**永久**打空。
        A3 一次写满 40 个顶点（每顶点只写一次）→ 零个抛错、零个零权重。
      所以规则是「**每个顶点每轮只能写一次**」，不是「首调抛错」。正式脚本正好满足，
      这也是为什么 23 轮 failed_vertices=0 却从没人读过 —— 判据来自写入返回值。
      M4b 要做的是「写完读全表」；一旦读出坍缩顶点，必须知道**哪条 API 能救**，
      否则「发现坏了但修不了」的判据只能当红灯用。skinOps 成员表里挂了
      SetWeight / unNormalizeVertex / isUnNormalizeVertex / rigidVertex / RemoveZeroWeights /
      scaleWeight / SetBoneWeights 这些名字，全都没实测过 —— 这一轮逐条问。

   二、回滚用哪条路（决定 M4d 的实现）
      第三轮 `biped.setTransform node #pos v` 报 `wanted 4, got 3`：正式脚本一直用
      四参数形式（末尾的 false = 不打关键帧），探针漏了第 4 个参数 —— 是探针的错，
      不是 Max 的坑，这轮改回来。
      快照文件路线已验证：进 Figure 模式**不动几何**（位移 0.0），
      `biped.saveFigFile ctrl fig` 落盘 11126 字节；但 `loadFigFile` 也要求 Figure 模式。
      另一条是纯数据快照（getTransform 存 / setTransform 打）。
      这轮把两条的**还原误差、耗时、副作用**都量出来，选一条进产品。

   副作用必须查清：回滚只许动 Biped。带着一个已蒙皮的网格和一根 BoneGeometry 跑回滚，
   之后回读权重、数对象数 —— 若 loadFigFile 把 Skin 或别的对象抹掉，这条路直接作废。 */

global PROBE_OUT = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/readback_m4d.txt"

fn say s =
(
    local f = openFile PROBE_OUT mode:"a" encoding:#utf8
    if f != undefined do ( format "%\n" (s as string) to:f; close f )
)

fn why = try ( getCurrentException() as string ) catch "?"
fn S x = try ( x as string ) catch "<unprintable>"
fn msk x = try ( x as string ) catch "<unprintable>"

/* 异常文字截断：第三轮 `biped.SetMode` 不存在，getCurrentException 把整个 biped
   结构的成员表喷出来（12k 字符），读数全被冲走。截到 100 字符足够看清是哪种错。 */
fn trim s =
(
    local t = s as string
    if t.count > 110 then (substring t 1 110 + " [cut]") else t
)
fn ex = trim (why())

/* 一个顶点的回读压成一行：ids / ws / sum —— 产品判据用的就是这三样。 */
fn rbline smod v =
(
    local x = try ( skinOps.GetVertexBoneIDsAndWeights smod v ) catch "EX"
    if (classOf x) != Array or x.count < 2 then ("v" + (S v) + " " + (S x))
    else
    (
        local ssum = 0.0
        for i = 1 to x[2].count do ssum += x[2][i]
        ("v" + (S v) + " ids=" + (S x[1]) + " ws=" + (S x[2]) + " sum=" + (msk ssum))
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
    smod
)

(
    say "===== M4d probe start ====="

    -----------------------------------------------------------------------------
    -- A. 先复现坍缩，再逐条问补救 API ------------------------------------------
    -----------------------------------------------------------------------------
    local sa = freshSkin "A"
    if sa != undefined do
    (
        say ("  A0 首写 v5 = " + (S (try ( skinOps.ReplaceVertexWeights sa 5 #(2,3) #(0.7,0.3) ) catch (ex()))) \
            + " | " + (rbline sa 5))
        say ("  A1 二写 v5 = " + (S (try ( skinOps.ReplaceVertexWeights sa 5 #(2,3) #(0.7,0.3) ) catch (ex()))) \
            + " | " + (rbline sa 5))

        /* 候选一：SetWeight —— 名字表里有，签名未知。逐个参数个数试，
           Max 的 "Argument count error: SetWeight wanted N" 会自己把 N 报出来。 */
        say ("  A2 SetWeight 3 参 = " + (S (try ( skinOps.SetWeight sa 5 0.7 ) catch (ex()))))
        say ("  A2 读 " + (rbline sa 5))
        say ("  A3 SetWeight 4 参 = " + (S (try ( skinOps.SetWeight sa 5 2 0.7 ) catch (ex()))))
        say ("  A3 读 " + (rbline sa 5))
        say ("  A4 SetWeight 4 参(骨序在前) = " + (S (try ( skinOps.SetWeight sa 2 5 0.7 ) catch (ex()))))
        say ("  A4 读 " + (rbline sa 5))
        say ("  A5 SetWeight 5 参 = " + (S (try ( skinOps.SetWeight sa 5 2 0.7 0 ) catch (ex()))))

        /* 候选二：unNormalizeVertex —— 坍缩多半就是「归一化」在动手，
           先把顶点标成不参与归一化，再写，看能不能落住。 */
        say ("  A6 isUnNormalizeVertex v5 = " + (S (try ( skinOps.isUnNormalizeVertex sa 5 ) catch (ex()))))
        say ("  A7 unNormalizeVertex v5 true = " + (S (try ( skinOps.unNormalizeVertex sa 5 true ) catch (ex()))))
        say ("  A7b isUnNormalizeVertex v5 = " + (S (try ( skinOps.isUnNormalizeVertex sa 5 ) catch (ex()))))
        say ("  A8 之后重写 v5 = " + (S (try ( skinOps.ReplaceVertexWeights sa 5 #(2,3) #(0.7,0.3) ) catch (ex()))) \
            + " | " + (rbline sa 5))
        say ("  A8b SetVertexWeights = " + (S (try ( skinOps.SetVertexWeights sa 5 #(2,3) #(0.7,0.3) ) catch (ex()))) \
            + " | " + (rbline sa 5))
        say ("  A9 unNormalizeVertex v5 false = " + (S (try ( skinOps.unNormalizeVertex sa 5 false ) catch (ex()))) \
            + " | " + (rbline sa 5))

        /* 候选三：rigidVertex（刚性顶点） */
        say ("  A10 isRigidVertex v5 = " + (S (try ( skinOps.isRigidVertex sa 5 ) catch (ex()))))
        say ("  A11 rigidVertex v5 true = " + (S (try ( skinOps.rigidVertex sa 5 true ) catch (ex()))))
        say ("  A11b 重写后读 = " + (rbline sa 5))

        /* 候选四：RemoveZeroWeights —— 把零权重链路整条删掉，让顶点回到「没写过」状态 */
        say ("  A12 RemoveZeroWeights = " + (S (try ( skinOps.RemoveZeroWeights sa ) catch (ex()))))
        say ("  A12b 读 " + (rbline sa 5))
        say ("  A13 删零之后再写 v5 = " + (S (try ( skinOps.ReplaceVertexWeights sa 5 #(2,3) #(0.7,0.3) ) catch (ex()))) \
            + " | " + (rbline sa 5))

        /* 候选五：scaleWeight / SetBoneWeights —— 若只有「整列替换」能救，
           产品闭环就得按骨遍历，代价完全不同，必须知道。 */
        say ("  A14 scaleWeight v5 2.0 = " + (S (try ( skinOps.scaleWeight sa 5 2.0 ) catch (ex()))))
        say ("  A14b 读 " + (rbline sa 5))
        local col = #()
        for i = 1 to 488 do append col 0.0
        col[5] = 0.7
        say ("  A15 SetBoneWeights 4 参 = " + (S (try ( skinOps.SetBoneWeights sa 2 col ) catch (ex()))))
        say ("  A15b 读 " + (rbline sa 5))

        /* 候选六：RemoveBone + AddBone 重来（最后一招，代价最大：骨序号会变，
           全部顶点的 ids 都要重写 —— 只有前面全废才考虑）。 */
        say ("  A16 GetBoneWeight smod 2 5 = " + (S (try ( skinOps.GetBoneWeight sa 2 5 ) catch (ex()))) \
            + " (GetBoneWeight(bone,vert) 口径核对)")
    )

    -----------------------------------------------------------------------------
    -- B/C. 回滚两条路线：纯数据快照 vs fig 文件，误差 + 耗时 + 副作用 ---------
    -----------------------------------------------------------------------------
    try ( deleteAllObjects() ) catch ()
    local bp = undefined
    try ( bp = biped.createNew 170.0 0.0 [0,0,0] arms:true ) catch ( say ("  biped.createNew FAILED: " + ex()) )
    if bp != undefined do
    (
        local ctrl = try ( bp.controller ) catch undefined
        local foot = try ( getNodeByName "Bip001 L Foot" exact:true ) catch undefined
        local pelv = try ( getNodeByName "Bip001 Pelvis" exact:true ) catch undefined
        local hand = try ( getNodeByName "Bip001 R Hand" exact:true ) catch undefined

        /* 挂一个「不能被回滚弄坏」的旁证对象：已蒙皮的网格 + 一根独立 BoneGeometry。
           回滚跑完必须还能读到权重、对象数不变。 */
        local witnessMesh = try ( Box name:"m4wBox" length:10.0 width:10.0 height:10.0 lengthsegs:3 widthsegs:3 heightsegs:3 ) catch undefined
        local witnessBone = try ( BoneSys.createBone [0,30,0] [10,30,0] [0,1,0] ) catch undefined
        if witnessBone != undefined do try ( witnessBone.name = "m4wBone" ) catch ()
        local wmod = undefined
        if witnessMesh != undefined do
        (
            try ( convertTo witnessMesh Editable_Mesh ) catch ()
            local sm = try ( Skin() ) catch undefined
            if sm != undefined do ( try ( addModifier witnessMesh sm; wmod = witnessMesh.modifiers[1] ) catch () )
            if wmod != undefined and witnessBone != undefined do
            (
                try ( select witnessMesh; modPanel.setCurrentObject wmod ) catch ()
                try ( skinOps.AddBone wmod witnessBone 0 ) catch ()
                /* 每顶点只写一次：先写 v2、v3，回滚后必须还是 0.7/0.3 而不是 0.0 */
                try ( skinOps.ReplaceVertexWeights wmod 2 #(1) #(1.0) ) catch ( say ("  W v2 写失败: " + ex()) )
                try ( skinOps.ReplaceVertexWeights wmod 3 #(1) #(1.0) ) catch ( say ("  W v3 写失败: " + ex()) )
            )
        )
        local objBefore = objects.count
        say ("  B0 旁证：objects=" + (S objBefore) + " 蒙皮回读 " + (if wmod != undefined then (rbline wmod 2) else "无 wmod"))

        if ctrl != undefined and foot != undefined do
        (
            ---------------------------------------------------------------------
            -- B. 纯数据快照 / 还原（产品最想要的路线）
            ---------------------------------------------------------------------
            local nodes = #()
            for o in objects where (classOf o) == Biped_Object do append nodes o
            local snap = #()
            local t1 = timeStamp()
            for o in nodes do
            (
                local pp = try ( biped.getTransform o #pos ) catch undefined
                local rr = try ( biped.getTransform o #rotation ) catch undefined
                if pp != undefined and (classOf rr) == Quat do
                (
                    local item = #()
                    append item o
                    append item pp
                    append item rr
                    append snap item
                )
            )
            say ("  B1 快照项数 = " + (S snap.count) + " / 节点数 " + (S nodes.count) \
                + " 耗时 = " + (msk (((timeStamp() - t1) / 1000.0))) + " 秒")

            local f0 = biped.getTransform foot #pos
            local q0 = biped.getTransform foot #rotation
            local pv0 = if pelv != undefined then (biped.getTransform pelv #pos) else undefined
            local hd0 = if hand != undefined then (biped.getTransform hand #pos) else undefined
            try ( biped.setTransform foot #pos (f0 + [3.0,0,0]) false ) catch ( say ("  B2 setTransform 4 参 FAILED: " + ex()) )
            try ( biped.setTransform foot #rotation (q0 * (angleAxis 12.0 [0,1,0])) false ) catch ( say ("  B3 setTransform rot FAILED: " + ex()) )
            if pv0 != undefined do ( try ( biped.setTransform pelv #pos (pv0 + [0,0,5.0]) false ) catch () )
            if hd0 != undefined do ( try ( biped.setTransform hand #pos (hd0 + [0,2.0,0]) false ) catch () )
            say ("  B4 挪开 foot = " + (msk (distance (biped.getTransform foot #pos) f0)) \
                + " 旋转分量差 = " + (msk (abs ((biped.getTransform foot #rotation).x - q0.x) + abs ((biped.getTransform foot #rotation).y - q0.y) \
                + abs ((biped.getTransform foot #rotation).z - q0.z) + abs ((biped.getTransform foot #rotation).w - q0.w))))

            local t2 = timeStamp()
            for it in snap do
            (
                try ( biped.setTransform it[1] #pos it[2] false ) catch ()
                try ( biped.setTransform it[1] #rotation it[3] false ) catch ()
            )
            local restoreMs = ((timeStamp() - t2) / 1000.0)
            /* 逐节点最大误差：回滚的验收判据就是这个数，不能只看被挪的那根。 */
            local maxPos = 0.0
            local maxQuat = 0.0
            for it in snap do
            (
                local dp = distance (biped.getTransform it[1] #pos) it[2]
                local qq = try ( biped.getTransform it[1] #rotation ) catch undefined
                local dq = 0.0
                if qq != undefined and (classOf qq) == Quat do
                    dq = abs (qq.x - it[3].x) + abs (qq.y - it[3].y) + abs (qq.z - it[3].z) + abs (qq.w - it[3].w)
                if dp > maxPos do maxPos = dp
                if dq > maxQuat do maxQuat = dq
            )
            say ("  B5 纯数据还原：" + (S snap.count) + " 项耗时 " + (msk restoreMs) + " 秒" \
                + " 最大位置误差=" + (msk maxPos) + " 最大四元数分量差=" + (msk maxQuat))
            say ("  B6 还原后仍可写 = " + (S (try ( biped.setTransform foot #pos (f0 + [1.0,0,0]) false ) catch ("EX " + ex()))))
            try ( biped.setTransform foot #pos f0 false ) catch ()
            say ("  B7 旁证 Skin 权重还在 = " + (if wmod != undefined then (rbline wmod 2) else "无 wmod") \
                + " objects=" + (S objects.count))

            ---------------------------------------------------------------------
            -- C. fig 文件路线：Figure 模式 save / load
            ---------------------------------------------------------------------
            local fig = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/m4d_fig.bfc"
            try ( deleteFile fig ) catch ()
            local t3 = timeStamp()
            say ("  C1 figureMode=true = " + (S (try ( ctrl.figureMode = true ) catch ("EX " + ex()))))
            say ("  C2 saveFigFile = " + (S (try ( biped.saveFigFile ctrl fig ) catch ("EX " + ex()))))
            say ("  C3 文件 size = " + (S (try ( getFileSize fig ) catch why())))
            say ("  C4 figureMode=false = " + (S (try ( ctrl.figureMode = false ) catch ("EX " + ex()))))
            local saveMs = ((timeStamp() - t3) / 1000.0)
            /* 挪开（这次只挪位置，避免旋转语义与 Figure 模式打架）。 */
            try ( biped.setTransform foot #pos (f0 + [3.0,0,0]) false ) catch ()
            if pv0 != undefined do ( try ( biped.setTransform pelv #pos (pv0 + [0,0,5.0]) false ) catch () )
            if hand != undefined do ( try ( biped.setTransform hand #pos ((biped.getTransform hand #pos) + [0,2.0,0]) false ) catch () )
            local movedF = distance (biped.getTransform foot #pos) f0
            say ("  C5 挪开量 foot = " + (msk movedF) + "（先确认 setTransform 真的把骨挪动了，否则后面的「还原成功」是假的）")
            local t4 = timeStamp()
            say ("  C6 回滚前 figureMode = " + (S (try ( ctrl.figureMode ) catch ("EX " + ex()))))
            say ("  C7 figureMode=true = " + (S (try ( ctrl.figureMode = true ) catch ("EX " + ex()))))
            say ("  C8 loadFigFile = " + (S (try ( biped.loadFigFile ctrl fig ) catch ("EX " + ex()))))
            local inFig = distance (biped.getTransform foot #pos) f0
            say ("  C9 Figure 模式内 load 后 foot 离快照 = " + (msk inFig))
            say ("  C10 figureMode=false = " + (S (try ( ctrl.figureMode = false ) catch ("EX " + ex()))))
            local outFig = distance (biped.getTransform foot #pos) f0
            say ("  C11 出 Figure 后 foot 离快照 = " + (msk outFig) + " pelvis = " \
                + (if pv0 != undefined then (msk (distance (biped.getTransform pelv #pos) pv0)) else "?") \
                + " hand = " + (if hand != undefined then (msk (distance (biped.getTransform hand #pos) hd0)) else "?"))
            local loadMs = ((timeStamp() - t4) / 1000.0)
            say ("  C12 save 耗时 " + (msk saveMs) + " 秒 / load(含两次切模式) 耗时 " + (msk loadMs) + " 秒")
            say ("  C13 load 后仍可 setTransform = " + (S (try ( biped.setTransform foot #pos (f0 + [2.0,0,0]) false ) catch ("EX " + ex()))))
            say ("  C14 load 后旁证：objects = " + (S objects.count) + "（回滚前 " + (S objBefore) \
                + "） 蒙皮回读 = " + (if wmod != undefined then (rbline wmod 2) else "无 wmod") \
                + " BoneGeometry 在 = " + (S (try ( getNodeByName "m4wBone" exact:true ) catch "EX")))
            /* 快照文件的还原粒度：把整树逐节点误差都打出来，和 B5 对齐才能比较两条路。 */
            local maxPos2 = 0.0
            for it in snap do
            (
                local dp = distance (biped.getTransform it[1] #pos) it[2]
                if dp > maxPos2 do maxPos2 = dp
            )
            say ("  C15 fig 路线后逐节点最大位置误差 = " + (msk maxPos2) + "（对照 B5 的最大位置误差 " + (msk maxPos) + "）")
        )
    )

    say "===== M4d readback probe done ====="
    try ( quitMax quiet:true ) catch ( try ( quitMax ) catch () )
)
