/* M4 探针第二轮（readback_m4b）：第一轮把「哪个名字能用、下标从几开始」问清楚了，
   这一轮只问三件**会决定产品代码写法**的事，答不上就不许写 M4b/M4d：

   A. 写入权重与回读权重的归一化语义。
      第一轮实测：写单骨 #(0.4) 回读 1.0 —— Skin 会归一化。但「多骨、总和 0.6」到底
      是等比放大（0.4/0.2 → 0.667/0.333）还是原样保留，决定 M4b 的判据该拿什么当期望值：
      拿 weights.tsv 的原始值去比，若 Skin 改了数值，判据必假红；拿归一化后的比值去比，
      才是可重复的判据。总和 2.0 的情形同理（会不会被压回 1.0）。

   B. 「第一次 ReplaceVertexWeights 抛错」到底是顶点 1 的问题，还是每次新建 Skin 的
      第一次调用都抛。第一轮 select + modPanel.setCurrentObject 都做了，仍然在**第一个
      调用**上抛 `Exceeded the vertex count`；而正式脚本第二十三轮读数 failed_vertices=0。
      差别必须先定死：若「首调必抛」，M4b 的回读就会稳定报出一个缺权重的顶点，
      而正式跑却不报 —— 那判据就成了看运行顺序脸色的东西，不能这么留。

   C. biped.saveFigFile / loadFigFile 的正确参数与「回滚到底动了什么」。
      第一轮两种写法都失败：传 Biped 节点报 `Unable to convert ... to type: Controller`，
      加 quiet:true 报 `Argument count error: wanted 2, got 5` —— 说明它要的是**控制器**
      且只收两个参数。回滚是 M4d 的地基，必须实测：
        - 快照文件是否真落盘（doesFileExist / getFileSize）；
        - 挪开两根骨（远端 L Foot + 根节点 Pelvis）后 load，是否**都**回到原位；
        - load 之后 figureMode 是什么、还能不能继续 setTransform（不能 = 回滚等于把场景锁死）；
        - load 会不会顺手删掉别的对象或抹掉 Skin 权重（回滚必须只动 Biped）。

   默认权重这条也顺手钉死：第一轮没写过的顶点回读给的是 `1 号骨 @ 1.0`，
   不是空 —— 所以「回读非空」不能当「写成功」的证据，M4b 得靠每骨计数与主骨比对。 */

global PROBE_OUT = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/readback_m4b.txt"

fn say s =
(
    local f = openFile PROBE_OUT mode:"a" encoding:#utf8
    if f != undefined do ( format "%\n" (s as string) to:f; close f )
)

fn why = try ( getCurrentException() as string ) catch "?"
fn S x = try ( x as string ) catch "<unprintable>"
fn msk x = try ( x as string ) catch "<unprintable>"

/* 打成「类型[长度] 元素…」，数组要到元素层级 —— 归一化后的数值只有看得见才敢定判据。 */
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

/* 建一个干净的 Box + Skin + 三根骨，返回 Skin 修改器。
   每一节都要「新建」而不是复用：A 要干净的写入顺序，B 要干净的首调位置。
   tag 只用来让日志能对上号。 */
fn freshSkin tag =
(
    try ( deleteAllObjects() ) catch ()
    local mesh = try ( Box name:(tag + "Box") length:20.0 width:20.0 height:20.0 \
        lengthsegs:9 widthsegs:9 heightsegs:9 mapsize:5.0 ) \
        catch ( say ("  [" + tag + "] Box FAILED: " + why()); undefined )
    if mesh != undefined do ( try ( convertTo mesh Editable_Mesh ) catch () )
    local smod = undefined
    local sm = try ( Skin() ) catch ( say ("  [" + tag + "] Skin() FAILED: " + why()); undefined )
    if sm != undefined and mesh != undefined do
    (
        try ( sm.name = (tag + "Skin"); addModifier mesh sm; smod = mesh.modifiers[1] ) \
            catch ( say ("  [" + tag + "] addModifier FAILED: " + why()) )
    )
    local b1 = try ( BoneSys.createBone [-5,0,0] [0,0,0] [0,1,0] ) catch undefined
    local b2 = try ( BoneSys.createBone [0,0,0] [5,0,0] [0,1,0] ) catch undefined
    local b3 = try ( BoneSys.createBone [5,0,0] [10,0,0] [0,1,0] ) catch undefined
    if b1 != undefined do try ( b1.name = (tag + "A") ) catch ()
    if b2 != undefined do try ( b2.name = (tag + "B") ) catch ()
    if b3 != undefined do try ( b3.name = (tag + "C") ) catch ()
    if smod != undefined do
    (
        /* 正式脚本的同一套前置：先 select + setCurrentObject。 */
        try ( select mesh; modPanel.setCurrentObject smod ) catch ( say ("  [" + tag + "] setCurrentObject FAILED: " + why()) )
        try ( skinOps.AddBone smod b1 0 ) catch ( say ("  [" + tag + "] AddBone A FAILED: " + why()) )
        try ( skinOps.AddBone smod b2 0 ) catch ( say ("  [" + tag + "] AddBone B FAILED: " + why()) )
        try ( skinOps.AddBone smod b3 0 ) catch ( say ("  [" + tag + "] AddBone C FAILED: " + why()) )
    )
    say ("  [" + tag + "] smod=" + (if smod == undefined then "NONE" else "OK") \
        + " verts=" + (if mesh == undefined then "?" else (S mesh.numVerts)) \
        + " bones=" + (S (try ( skinOps.GetNumberBones smod ) catch why())))
    smod
)

(
    say "===== M4b probe start ====="

    -----------------------------------------------------------------------------
    -- A. 归一化语义：写进去的数与读回来的数之间到底有没有换算 --------------
    -----------------------------------------------------------------------------
    local sa = freshSkin "A"
    if sa != undefined do
    (
        /* 总和 0.6、两骨：等比放大 → 0.6667/0.3333；原样保留 → 0.4/0.2。 */
        say ("  A1 写 v2 #(2,3) #(0.4,0.2) = " + (S (try ( skinOps.ReplaceVertexWeights sa 2 #(2,3) #(0.4,0.2) ) catch why())))
        say ("  A1 读 v2 = " + (describe (try ( skinOps.GetVertexBoneIDsAndWeights sa 2 ) catch why())))
        /* 总和 0.6、三骨：再看是不是只按「主骨」拉平。 */
        say ("  A2 写 v3 #(1,2,3) #(0.1,0.2,0.3) = " + (S (try ( skinOps.ReplaceVertexWeights sa 3 #(1,2,3) #(0.1,0.2,0.3) ) catch why())))
        say ("  A2 读 v3 = " + (describe (try ( skinOps.GetVertexBoneIDsAndWeights sa 3 ) catch why())))
        /* 总和 2.0：会不会被压回 1.0（若压回，M4b 的 sum_err 判据才有意义）。 */
        say ("  A3 写 v5 #(2,3) #(1.0,1.0) = " + (S (try ( skinOps.ReplaceVertexWeights sa 5 #(2,3) #(1.0,1.0) ) catch why())))
        say ("  A3 读 v5 = " + (describe (try ( skinOps.GetVertexBoneIDsAndWeights sa 5 ) catch why())))
        /* 总和正好 1.0 但顺序被打乱：判据不能假设回读顺序 = 写入顺序。 */
        say ("  A4 写 v6 #(3,1) #(0.35,0.65) = " + (S (try ( skinOps.ReplaceVertexWeights sa 6 #(3,1) #(0.35,0.65) ) catch why())))
        say ("  A4 读 v6 = " + (describe (try ( skinOps.GetVertexBoneIDsAndWeights sa 6 ) catch why())))
        /* 逐条 GetVertexWeight 与整表 GetVertexBoneIDsAndWeights 是否一致（M4b 只用一条路就够，
           但要确认两条路不会给出不同的数，否则「用哪个 API」会变成判据的一部分）。 */
        say ("  A5 v6 GetVertexWeight k=1..2 = " + (msk (try ( skinOps.GetVertexWeight sa 6 1 ) catch why())) \
            + " / " + (msk (try ( skinOps.GetVertexWeight sa 6 2 ) catch why())))
        /* 写完再改同一顶点：ReplaceVertexWeights 是**替换**而不是累加（M4b 收敛循环会重写同一顶点）。 */
        say ("  A6 再写 v6 #(1) #(1.0) = " + (S (try ( skinOps.ReplaceVertexWeights sa 6 #(1) #(1.0) ) catch why())))
        say ("  A6 读 v6 = " + (describe (try ( skinOps.GetVertexBoneIDsAndWeights sa 6 ) catch why())))
    )

    -----------------------------------------------------------------------------
    -- B. 「首调抛错」的定位：干净 Skin 上第一次调用打在不同顶点号会怎样 --------
    -----------------------------------------------------------------------------
    local sb = freshSkin "B"
    if sb != undefined do
    (
        say ("  B1 干净 Skin 上第一次调用打 v7 = " + (S (try ( skinOps.ReplaceVertexWeights sb 7 #(1,2) #(0.7,0.3) ) catch why())))
        say ("  B2 第二次调用打 v8 = " + (S (try ( skinOps.ReplaceVertexWeights sb 8 #(1,2) #(0.7,0.3) ) catch why())))
        say ("  B3 第三次调用打 v9 = " + (S (try ( skinOps.ReplaceVertexWeights sb 9 #(1,2) #(0.7,0.3) ) catch why())))
        say ("  B4 回头补第一次的 v7 = " + (S (try ( skinOps.ReplaceVertexWeights sb 7 #(1,2) #(0.7,0.3) ) catch why())))
        say ("  B5 读 v7 = " + (describe (try ( skinOps.GetVertexBoneIDsAndWeights sb 7 ) catch why())))
        say ("  B6 读 v8 = " + (describe (try ( skinOps.GetVertexBoneIDsAndWeights sb 8 ) catch why())))
        /* SetVertexWeights 在同一个「第一次调用」位置是否也抛（若只有 Replace 抛，
           预热写法就该用别的 API，避免预热本身改动默认权重）。 */
        local sc = freshSkin "C"
        if sc != undefined do
        (
            say ("  B7 干净 Skin 上 SetVertexWeights 首调 v7 = " + (S (try ( skinOps.SetVertexWeights sc 7 #(1,2) #(0.7,0.3) ) catch why())))
            say ("  B8 读 v7 = " + (describe (try ( skinOps.GetVertexBoneIDsAndWeights sc 7 ) catch why())))
            /* 预热写法候选：先对「反正要写」的顶点调一次，看错误是否消失。
               这里只测「读操作能否顶掉首调副作用」——若读一次就能让写通，产品代码的
               顺序就不用改，只要在读回读前多做一次无害调用。 */
            local pre = try ( skinOps.GetVertexBoneIDsAndWeights sc 7 ) catch why()
            say ("  B9 先读一次再写 v8 = " + (S (try ( skinOps.ReplaceVertexWeights sc 8 #(1,2) #(0.7,0.3) ) catch why())) \
                + " （前一次读=" + (S (classOf pre)) + "）")
            /* GetBoneWeights 给整列（长度=顶点数）：M4b 想拿它做每骨计数，先确认下标基准 */
            local col = try ( skinOps.GetBoneWeights sc 1 ) catch ("EX " + why())
            if (classOf col) == Array then
                say ("  B10 GetBoneWeights smod 1 长度=" + (S col.count) + " 前三项=" \
                    + (msk col[1]) + "," + (msk col[2]) + "," + (msk col[3]))
            else
                say ("  B10 GetBoneWeights smod 1 = " + (describe col))
            local col0 = try ( skinOps.GetBoneWeights sc 0 ) catch ("EX " + why())
            say ("  B11 GetBoneWeights smod 0 = " + (describe col0))
        )
    )

    -----------------------------------------------------------------------------
    -- C/D. fig 快照与回滚：参数、落盘、还原范围、副作用 -----------------------
    -----------------------------------------------------------------------------
    try ( deleteAllObjects() ) catch ()
    local bp = undefined
    try ( bp = biped.createNew 170.0 0.0 [0,0,0] arms:true ) \
        catch ( say ("  biped.createNew FAILED: " + why()) )
    if bp != undefined do
    (
        local ctrl = try ( bp.controller ) catch ("EX " + why())
        say ("  bp.controller = " + (S (classOf ctrl)) + " " + (S (try ( ctrl != undefined ) catch why())))
        local fig = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/m4b_fig.bfc"
        try ( deleteFile fig ) catch ()

        say ("  C1 saveFigFile(ctrl,path) = " + (S (try ( biped.saveFigFile ctrl fig ) catch why())))
        say ("  C2 文件在 = " + (S (try ( doesFileExist fig ) catch why())) \
            + " size=" + (S (try ( getFileSize fig ) catch why())))
        /* 若 ctrl 不对，剩下的候选一次列完，省一轮 Max 启动。 */
        if not (try ( doesFileExist fig ) catch false) do
        (
            say ("  C3 saveFigFile(bp,path) = " + (S (try ( biped.saveFigFile bp fig ) catch why())))
            say ("  C4 saveFigFile(ctrl,name) 绝对路径带斜杠 = " + (S (try ( biped.saveFigFile ctrl "C:/tmp/m4b_fig.bfc" ) catch why())))
            local fo = try ( ctrl.figureObject ) catch ("EX " + why())
            say ("  C5 ctrl.figureObject = " + (S (classOf fo)))
            say ("  C6 saveFigure 方法在 ctrl 上? = " + (S (try ( isProperty ctrl #figureFile ) catch why())))
            say ("  C2b 文件在(重试) = " + (S (try ( doesFileExist fig ) catch false)))
        )

        local lfoot = try ( getNodeByName "Bip001 L Foot" exact:true ) catch undefined
        local pelvis = try ( getNodeByName "Bip001 Pelvis" exact:true ) catch undefined
        if lfoot != undefined and pelvis != undefined and (try ( doesFileExist fig ) catch false) do
        (
            local fp0 = biped.getTransform lfoot #pos
            local pp0 = biped.getTransform pelvis #pos
            /* 远端 +3cm、根节点 +5cm：只还原一根骨 = 快照其实是 per-node，回滚不能当整树恢复用。 */
            try ( biped.setTransform lfoot #pos (fp0 + [3.0,0,0]) ) catch ( say ("  C7 setTransform L Foot FAILED: " + why()) )
            try ( biped.setTransform pelvis #pos (pp0 + [0,0,5.0]) ) catch ( say ("  C8 setTransform Pelvis FAILED: " + why()) )
            local fp1 = biped.getTransform lfoot #pos
            local pp1 = biped.getTransform pelvis #pos
            say ("  C9 挪开量 foot=" + (msk (distance fp1 fp0)) + " pelvis=" + (msk (distance pp1 pp0)))

            local t0 = timeStamp()
            say ("  C10 loadFigFile(ctrl,path) = " + (S (try ( biped.loadFigFile ctrl fig ) catch why())))
            say ("  C11 load 耗时 = " + (msk (((timeStamp() - t0) / 1000.0))) + " 秒")
            local fp2 = biped.getTransform lfoot #pos
            local pp2 = biped.getTransform pelvis #pos
            say ("  C12 还原误差 foot=" + (msk (distance fp2 fp0)) + " pelvis=" + (msk (distance pp2 pp0)) \
                + "（≈0 才算 load 真把位置拉回快照）")
            say ("  C13 load 后 figureMode = " + (S (try ( ctrl.figureMode ) catch ("EX " + why()))))
            /* 回滚后必须还能继续摆位：不能 = 只能回滚一次，M4d 的循环设计直接作废。 */
            say ("  C14 load 后 setTransform = " + (S (try ( biped.setTransform lfoot #pos (fp0 + [1.0,0,0]) ) catch ("EX " + why()))))
            say ("  C15 load 后再 setTransform 读数 = " + (msk (distance (biped.getTransform lfoot #pos) fp0)))
            /* 再 load 一次：回滚可重复调用吗（收敛循环会多次回滚）。 */
            say ("  C16 二次 load = " + (S (try ( biped.loadFigFile ctrl fig ) catch ("EX " + why()))))
            say ("  C17 二次 load 后 foot 误差 = " + (msk (distance (biped.getTransform lfoot #pos) fp0)))

            /* D. 副作用：回滚会不会动到 Biped 之外的东西。
               先加一根 BoneGeometry 与一个带 Skin 的网格，load 之后检查它们还在不在、
               权重还在不在 —— 回滚只许动 Biped，否则「回滚」会把蒙皮一起抹掉。 */
            local beforeCount = objects.count
            local box = try ( Box name:"m4dBox" length:10.0 width:10.0 height:10.0 ) catch undefined
            local keepBone = try ( BoneSys.createBone [0,20,0] [10,20,0] [0,1,0] ) catch undefined
            if keepBone != undefined do try ( keepBone.name = "m4dBone" ) catch ()
            local smod = undefined
            if box != undefined do
            (
                try ( convertTo box Editable_Mesh; select box ) catch ()
                local sm = try ( Skin() ) catch undefined
                if sm != undefined do ( try ( addModifier box sm; smod = box.modifiers[1] ) catch () )
                if smod != undefined and keepBone != undefined do
                (
                    try ( modPanel.setCurrentObject smod ) catch ()
                    try ( skinOps.AddBone smod keepBone 0 ) catch ()
                    try ( skinOps.ReplaceVertexWeights smod 2 #(1) #(1.0) ) catch ()
                    try ( skinOps.ReplaceVertexWeights smod 3 #(1) #(1.0) ) catch ()
                )
            )
            say ("  D1 加对象前=" + (S beforeCount) + " 后=" + (S objects.count) + " 骨节点=" \
                + (S (try ( keepBone.name ) catch "?")))
            local preW = try ( describe (skinOps.GetVertexBoneIDsAndWeights smod 2) ) catch "EX"
            say ("  D2 load 前 Skin 回读 v2 = " + (S preW))
            say ("  D3 load = " + (S (try ( biped.loadFigFile ctrl fig ) catch ("EX " + why()))))
            local postW = try ( describe (skinOps.GetVertexBoneIDsAndWeights smod 2) ) catch "EX"
            say ("  D4 load 后 Skin 回读 v2 = " + (S postW) + " 对象数=" + (S objects.count))
            say ("  D5 load 后 BoneGeometry 还在 = " + (S (try ( getNodeByName "m4dBone" exact:true ) catch "EX")))
            /* 快照文件会不会被后续 setTransform 影响（load 是「恢复到写入那一刻」，不是「撤销一步」）。 */
            say ("  D6 load 后 foot 误差 = " + (msk (distance (biped.getTransform lfoot #pos) fp0)))
        )
    )

    say "===== M4b readback probe done ====="
    try ( quitMax quiet:true ) catch ( try ( quitMax ) catch () )
)
