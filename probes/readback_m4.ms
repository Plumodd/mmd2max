/* M4 探针（readback_m4）：把「回读验收」要用的 API 名字、下标基准与耗时一次问清楚。
   为什么必须先跑探针再写产品代码：M4 的全部价值在于「判据来自场景回读」，
   而 v4 之前踩过的每一次「以为这个 API 在」都代价一轮实跑 ——
   probes/skin_readback.txt 已经证明 skinOps 里 `GetVertexWeightValue` / `GetBoneIndex` /
   `GetVertexWeightTotal` / `GetMaxWeightsNumber` / `forceUpdate` **都不存在**（正式脚本里那句
   `try ( skinOps.forceUpdate skinMod ) catch ()` 其实一直在静默抛错），而成员表里挂着
   `GetVertexBoneIDsAndWeights` 这个名字却**从没被调用过**。没被调用过 = 不知道它返回什么形状。
   同样，`biped.saveFigFile` / `biped.loadFigFile` 是回滚的地基，参数与「load 之后场景是什么状态」
   必须实测，不能照文档写。

   本探针只回答四类问题，每条都落到文本里，不靠弹窗：
     A. skinOps 回读：哪个名字能用、顶点号从 1 还是 0 开始、权重号从 1 还是 0 开始、
        返回的是数组还是 #(骨号数组, 权重数组)、写进去 0.7/0.3 回读是不是 0.7/0.3、
        Skin 会不会自己归一化，以及**逐顶点回读全表要多久**（决定要不要抽样）。
     B. biped.saveFigFile / loadFigFile：名字在不在、几种参数写法各抛什么、
        load 之后刚才被 setTransform 挪走的骨是否真回到快照位置、耗时。
     C. 回读之后场景是否被改脏：loadFigFile 会不会顺手打开 Figure 模式、会不会打关键帧。
     D. 骨盒回读：nodeGetBoundingBox 在 Biped 骨与 BoneGeometry 上分别给什么
        （正式判据已经用它，这里只钉「M4 能不能拿它当回读真值」）。*/

global PROBE_OUT = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/readback_m4.txt"

fn say s =
(
    local f = openFile PROBE_OUT mode:"a" encoding:#utf8
    if f != undefined do ( format "%\n" (s as string) to:f; close f )
)

fn why = try ( getCurrentException() as string ) catch "?"
fn S x = try ( x as string ) catch "<unprintable>"

/* 把任意返回值打成「类型 + 结构」，数组要打到元素层级 —— 形状问题就是靠这行回答的。 */
fn describe x =
(
    local c = try ( classOf x ) catch "?"
    local out = (S c)
    if (classOf x) == Array do
    (
        out = out + "[" + (x.count as string) + "]"
        local lim = if x.count < 6 then x.count else 6
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

fn msk x = try ( x as string ) catch "<unprintable>"

(
    say "===== M4 readback probe start ====="
    /* 未定义全局名出现在调用位置会**中止整个文件**（v6 探针笔记第 1 条债），
       所以版本号的三种写法各包一层 try：拿不到只是少一行读数，不值得赔一整轮探针。 */
    local buildNo = try ( S (maxVersion())) catch "无 maxVersion"
    local buildNo2 = try ( S (getMaxVersionString())) catch "无 getMaxVersionString"
    local buildNo3 = try ( S (getMaxVersionNumber 0) ) catch "无 getMaxVersionNumber"
    say ("Max = " + buildNo + " | " + buildNo2 + " | " + buildNo3)

    -----------------------------------------------------------------------------
    -- A. skinOps 回读 ----------------------------------------------------------
    -----------------------------------------------------------------------------
    local mesh = try ( Box name:"m4Box" length:20.0 width:20.0 height:20.0 \
        lengthsegs:25 widthsegs:25 heightsegs:25 mapsize:5.0 ) \
        catch ( say ("  Box FAILED: " + why()); undefined )
    if mesh != undefined do
    (
        try ( convertTo mesh Editable_Mesh ) catch ( say ("  convertTo FAILED: " + why()) )
    )
    say ("  mesh = " + (if mesh == undefined then "NONE" else (S (classOf mesh))) \
        + " numVerts=" + (if mesh == undefined then "?" else (S mesh.numVerts)))

    local smod = undefined
    local sm = try ( Skin() ) catch ( say ("  Skin() FAILED: " + why()); undefined )
    if sm != undefined and mesh != undefined do
    (
        try ( sm.name = "m4Skin"; addModifier mesh sm; smod = mesh.modifiers[1] ) \
            catch ( say ("  addModifier FAILED: " + why()) )
    )
    say ("  modifier = " + (if smod == undefined then "none" else (S (classOf smod))))

    local b1 = try ( BoneSys.createBone [-5,0,0] [0,0,0] [0,1,0] ) catch ( say ("  bone1 FAILED: " + why()); undefined )
    local b2 = try ( BoneSys.createBone [0,0,0] [5,0,0] [0,1,0] ) catch ( say ("  bone2 FAILED: " + why()); undefined )
    local b3 = try ( BoneSys.createBone [5,0,0] [10,0,0] [0,1,0] ) catch ( say ("  bone3 FAILED: " + why()); undefined )
    if b1 != undefined do try ( b1.name = "m4BoneA" ) catch ()
    if b2 != undefined do try ( b2.name = "m4BoneB" ) catch ()
    if b3 != undefined do try ( b3.name = "m4BoneC" ) catch ()
    say ("  bones = " + (S (classOf b1)) + " " + (S (classOf b2)) + " " + (S (classOf b3)))

    if smod != undefined and b1 != undefined do
    (
        /* 正式脚本的同一套前置：先 select + setCurrentObject，否则第一个顶点写不进去 */
        try ( select mesh; modPanel.setCurrentObject smod ) catch ( say ("  setCurrentObject FAILED: " + why()) )
        say ("  AddBone A = " + (S (try ( skinOps.AddBone smod b1 0 ) catch why())))
        say ("  AddBone B = " + (S (try ( skinOps.AddBone smod b2 0 ) catch why())))
        say ("  AddBone C = " + (S (try ( skinOps.AddBone smod b3 0 ) catch why())))
        say ("  GetNumberBones = " + (S (try ( skinOps.GetNumberBones smod ) catch why())))
        say ("  GetBoneName 1 0 = " + (S (try ( skinOps.GetBoneName smod 1 0 ) catch why())))
        say ("  GetBoneName 1 1 = " + (S (try ( skinOps.GetBoneName smod 1 1 ) catch why())))
        say ("  GetBoneNode 1 = " + (describe (try ( skinOps.GetBoneNode smod 1 ) catch why())))
        say ("  GetBoneNodes = " + (describe (try ( skinOps.GetBoneNodes smod ) catch why())))
        say ("  GetNumberVertices = " + (S (try ( skinOps.GetNumberVertices smod ) catch why())))

        /* 写入三种已知形状，回读必须逐个对上：这是 A 类的判据本体 */
        say ("  RVW v1 #(1,2) #(0.7,0.3) = " + (S (try ( skinOps.ReplaceVertexWeights smod 1 #(1,2) #(0.7,0.3) ) catch why())))
        say ("  RVW v2 #(2,3) #(0.5,0.5) = " + (S (try ( skinOps.ReplaceVertexWeights smod 2 #(2,3) #(0.5,0.5) ) catch why())))
        say ("  RVW v3 #(3) #(0.4) = " + (S (try ( skinOps.ReplaceVertexWeights smod 3 #(3) #(0.4) ) catch why())))
        say ("  SetVertexWeights v4 #(1,3) #(0.25,0.75) = " + (S (try ( skinOps.SetVertexWeights smod 4 #(1,3) #(0.25,0.75) ) catch why())))

        say "-- 回读候选：名字在不在 + 下标基准 --"
        say ("  GetVertexBoneIDsAndWeights smod 1 = " + (describe (try ( skinOps.GetVertexBoneIDsAndWeights smod 1 ) catch why())))
        say ("  GetVertexBoneIDsAndWeights smod 0 = " + (describe (try ( skinOps.GetVertexBoneIDsAndWeights smod 0 ) catch why())))
        say ("  GetVertexBoneIDsAndWeights smod 2 = " + (describe (try ( skinOps.GetVertexBoneIDsAndWeights smod 2 ) catch why())))
        say ("  GetVertexBoneIDsAndWeights smod 4 = " + (describe (try ( skinOps.GetVertexBoneIDsAndWeights smod 4 ) catch why())))
        say ("  GetVertexWeightCount smod 1 = " + (S (try ( skinOps.GetVertexWeightCount smod 1 ) catch why())))
        say ("  GetVertexWeightCount smod 0 = " + (S (try ( skinOps.GetVertexWeightCount smod 0 ) catch why())))
        for k in #(0,1,2,3) do
            say ("  GetVertexWeight smod 1 k=" + (S k) + " -> " + (S (try ( skinOps.GetVertexWeight smod 1 k ) catch why())))
        for k in #(0,1,2,3) do
            say ("  GetVertexWeightBoneID smod 1 k=" + (S k) + " -> " + (S (try ( skinOps.GetVertexWeightBoneID smod 1 k ) catch why())))
        say ("  GetBoneWeight smod 1 1 = " + (S (try ( skinOps.GetBoneWeight smod 1 1 ) catch why())))
        say ("  GetBoneWeights smod 1 = " + (describe (try ( skinOps.GetBoneWeights smod 1 ) catch why())))

        say "-- 写入 vs 回读 对撞（形状对上之后才谈得上数值） --"
        for v in #(1,2,3,4) do
        (
            local rb = try ( skinOps.GetVertexBoneIDsAndWeights smod v ) catch "EX"
            local row = "  vert " + (S v) + " rb=" + (if rb == "EX" then "EX" else describe rb)
            if rb != "EX" and rb.count >= 2 do
            (
                local ids = rb[1]
                local ws = rb[2]
                local ssum = 0.0
                for i = 1 to ws.count do ssum += ws[i]
                row = row + "  n=" + (S ids.count) + " sum=" + (msk ssum)
            )
            row = row + "  countAPI=" + (S (try ( skinOps.GetVertexWeightCount smod v ) catch why()))
            say row
        )

        /* 全表逐顶点回读的耗时：决定 M4 能不能「一个都不漏」，还是必须抽样。
           正式跑 18892 顶点，这里按同一量级的一半先测，再看是否线性。 */
        local n = mesh.numVerts
        local t0 = timeStamp()
        local hits = 0
        for v = 1 to n do
        (
            local rb = try ( skinOps.GetVertexBoneIDsAndWeights smod v ) catch undefined
            if rb != undefined and rb.count >= 2 and rb[2].count > 0 do hits += 1
        )
        local ms = ((timeStamp() - t0) / 1000.0) as string
        say ("  全表回读 verts=" + (S n) + " 有读数=" + (S hits) + " 耗时=" + ms + " 秒")

        /* 只数非零顶点的第二种写法（GetVertexWeightCount）单独测一次耗时，
           产品代码若用它就得知道它比 GetVertexBoneIDsAndWeights 贵在哪。 */
        local t1 = timeStamp()
        local c2 = 0
        for v = 1 to n do
        (
            local cnt = try ( skinOps.GetVertexWeightCount smod v ) catch 0
            if cnt > 0 do c2 += 1
        )
        say ("  GetVertexWeightCount 全表 verts=" + (S n) + " 非零=" + (S c2) \
            + " 耗时=" + (((timeStamp() - t1) / 1000.0) as string) + " 秒")

        /* 默认状态：没写过的顶点回读给什么？（决定「零权重顶点」这类判据怎么写） */
        local v0 = n - 1
        say ("  未写过的 vert " + (S v0) + " rb=" + (describe (try ( skinOps.GetVertexBoneIDsAndWeights smod v0 ) catch why())) \
            + " count=" + (S (try ( skinOps.GetVertexWeightCount smod v0 ) catch why())))
    )

    -----------------------------------------------------------------------------
    -- B/C. biped 快照与回滚 ----------------------------------------------------
    -----------------------------------------------------------------------------
    try ( deleteAllObjects() ) catch ()
    local bp = undefined
    try ( bp = biped.createNew 170.0 0.0 [0,0,0] arms:true ) \
        catch ( say ("  biped.createNew FAILED: " + why()) )
    if bp != undefined do
    (
        say ("  biped = " + (S (classOf bp)) + " 名字=" + (S bp.name))
        local lfoot = try ( getNodeByName "Bip001 L Foot" exact:true ) catch undefined
        local pelvis = try ( getNodeByName "Bip001 Pelvis" exact:true ) catch undefined
        say ("  L Foot 节点 = " + (if lfoot == undefined then "NONE" else (S lfoot.name)))
        say ("  Pelvis 节点 = " + (if pelvis == undefined then "NONE" else (S pelvis.name)))

        if lfoot != undefined do
        (
            local p0 = try ( biped.getTransform lfoot #pos ) catch ("EX " + why())
            say ("  初始 #pos = " + (if (classOf p0) == Point3 then (msk p0.x) + "," + (msk p0.y) + "," + (msk p0.z) else (S p0)))
            say ("  getTransform #rotation = " + (describe (try ( biped.getTransform lfoot #rotation ) catch why())))
            say ("  getTransform #rot = " + (S (try ( biped.getTransform lfoot #rot ) catch why())))
            say ("  getTransform #scale = " + (S (try ( biped.getTransform lfoot #scale ) catch why())))

            /* 快照：几种官方写法全试一遍，产品代码只用真能跑通的那一种。 */
            local fig = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/m4_snap.fig"
            say ("  saveFigFile bip node = " + (S (try ( biped.saveFigFile bp fig ) catch why())))
            say ("  saveFigFile + quiet = " + (S (try ( biped.saveFigFile bp fig quiet:true ) catch why())))
            say ("  saveFigFile 文件在 = " + (S (doesFileExist fig)))
            if (doesFileExist fig) do
                say ("  saveFigFile 文件大小 = " + (S (getFileSize fig)))

            /* 挪开一点，再 load 回来：load 是否真能还原是回滚成立的前提。 */
            if p0 != undefined and (classOf p0) == Point3 do
            (
                local moved = p0 + [3.0, 0.0, 0.0]
                say ("  setTransform #pos = " + (S (try ( biped.setTransform lfoot #pos moved false ) catch why())))
                local p1 = biped.getTransform lfoot #pos
                say ("  挪后 #pos = " + (msk p1.x) + "," + (msk p1.y) + "," + (msk p1.z))
                local t2 = timeStamp()
                say ("  loadFigFile bip node = " + (S (try ( biped.loadFigFile bp fig ) catch why())))
                say ("  loadFigFile 耗时 = " + (((timeStamp() - t2) / 1000.0) as string) + " 秒")
                local p2 = biped.getTransform lfoot #pos
                say ("  load 后 #pos = " + (msk p2.x) + "," + (msk p2.y) + "," + (msk p2.z))
                say ("  还原误差 = " + (msk (distance p2 p0)) + "（挪开量 3.0，误差 ≈0 才算 load 真管用）")
                /* 关键：load 之后 controller.figureMode 是什么？若它把 Figure 模式打开着，
                   后面的 setTransform 改的就是 Figure 而不是动作，整条流水线会静默变形。 */
                local fm = "?"
                try ( fm = (S bp.controller.figureMode) ) catch ( fm = "EX " + why() )
                say ("  load 后 figureMode = " + fm)
                local p3 = undefined
                try
                (
                    biped.setTransform lfoot #pos (p0 + [1.0,0.0,0.0]) false
                    p3 = biped.getTransform lfoot #pos
                )
                catch ( p3 = "EX " + why() )
                say ("  load 后仍可 setTransform = " + (if (classOf p3) == Point3 then (msk p3.x) else (S p3)))
            )
        )
        /* 全身回读一次落位要多久：M4 若要在每遍收敛后做「回读式」判据得知道代价。 */
        local t3 = timeStamp()
        local npos = 0
        for o in objects do
        (
            if (classOf o) == Biped_Object or (matchPattern o.name pattern:"Bip001*") do
            (
                try ( biped.getTransform o #pos; npos += 1 ) catch ()
            )
        )
        say ("  逐节点 getTransform #pos 节点数=" + (S npos) + " 耗时=" \
            + (((timeStamp() - t3) / 1000.0) as string) + " 秒")
    )

    -----------------------------------------------------------------------------
    -- D. 骨盒回读 --------------------------------------------------------------
    -----------------------------------------------------------------------------
    local bg = try ( BoneSys.createBone [0,0,0] [10,0,0] [0,1,0] ) catch undefined
    if bg != undefined do
    (
        try ( bg.name = "m4BoxBone" ) catch ()
        local wb = try ( nodeWorldBounds bg ) catch ("EX " + why())
        say ("  BoneGeometry nodeWorldBounds = " + (S wb))
        local lb = try ( nodeGetBoundingBox bg (Matrix3 1) ) catch ("EX " + why())
        say ("  BoneGeometry nodeGetBoundingBox(identity) = " + (S lb))
    )
    if bp != undefined do
    (
        local lf = try ( getNodeByName "Bip001 L Foot" exact:true ) catch undefined
        if lf != undefined do
        (
            local bb = try ( nodeGetBoundingBox lf lf.transform ) catch ("EX " + why())
            say ("  Bip Foot 本地包围盒 = " + (S bb))
            local wb = try ( nodeWorldBounds lf ) catch ("EX " + why())
            say ("  Bip Foot nodeWorldBounds = " + (S wb))
        )
    )

    say "===== M4 readback probe done ====="
    try ( quitMax quiet:true ) catch ( try ( quitMax ) catch () )
)
