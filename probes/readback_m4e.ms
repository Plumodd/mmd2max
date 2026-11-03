/* M4 探针第五轮（readback_m4e）：只问一件事 —— 写权重之前到底需不需要「先让修改器堆栈算一次」。

   为什么要第五轮：第四轮的 A 段整段作废，`ReplaceVertexWeights` 在**第一次调用**上就抛
   `Exceeded the vertex count`（而它面对的网格有 488 个顶点）；第三轮同样写法却全部落地。
   两轮唯一的可见差别，是第四轮把 freshSkin 里那句 `mesh.numVerts` 的日志删掉了 ——
   读一次网格顶点数会迫使堆栈求值。也就是说：
     「写不写得进去」可能取决于前面有没有人碰过场景，而不是取决于代码写了什么。
   正式脚本 23 轮 failed_vertices=0，但它数的是**写入返回值**；第三轮已经证明
   「返回 OK 而权重变 0」是真实存在的状态。所以 M4b 的闭环（写完读全表、坏的重写）
   必须先把这两件事定死：
     Q1 哪个动作能让新建 Skin 上的第一次写入真正落地（产品要不要加预热）；
     Q2 在「已经落地」的顶点上再写第二次，是否真的会坍缩成 0.0（决定闭环能不能重写）。
   Q2 在第三轮的读数是「会」，但那次的顶点也许本身就处在半求值状态，必须在干净前提下复测。

   每个候选都在**全新 Skin** 上单独跑一遍（freshSkin 里先 deleteAllObjects），
   打完首写与二写的回读就收工 —— 一次启动问完，不留第二次猜测。 */

global PROBE_OUT = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/readback_m4e.txt"

fn say s =
(
    local f = openFile PROBE_OUT mode:"a" encoding:#utf8
    if f != undefined do ( format "%\n" (s as string) to:f; close f )
)

fn why = try ( getCurrentException() as string ) catch "?"
fn S x = try ( x as string ) catch "<unprintable>"
fn trim s =
(
    local t = s as string
    if t.count > 90 then (substring t 1 90 + " [cut]") else t
)
fn ex = trim (why())

fn rbline smod v =
(
    local x = try ( skinOps.GetVertexBoneIDsAndWeights smod v ) catch "EX"
    if (classOf x) != Array or x.count < 2 then ("v" + (S v) + " 读不到")
    else
    (
        local ssum = 0.0
        for i = 1 to x[2].count do ssum += x[2][i]
        ("v" + (S v) + " ids=" + (S x[1]) + " ws=" + (S x[2]) + " sum=" + (S ssum))
    )
)

/* 干净 Skin：Box + Skin + 3 根骨，写完不做任何「顺手求值」，
   把求值这件事完全交给 trial 里的 primeKind。 */
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

/* 一个候选预热动作 + 首写 + 二写。primeKind 用字符串分派：
   传函数值也能写，但字符串能让日志自己说清「这一发到底做了什么」。 */
fn trial tag primeKind =
(
    local smod = freshSkin tag
    if smod == undefined then
        say ("  " + tag + " freshSkin 失败，跳过")
    else
    (
        local note = primeKind
        if primeKind == "none" then ( note = "什么都不做" )
        else if primeKind == "numVerts" then
        (
            local bx = try ( getNodeByName (tag + "Box") exact:true ) catch undefined
            if bx != undefined do ( local dummy = bx.numVerts )
            note = "读 mesh.numVerts（迫使堆栈求值）"
        )
        else if primeKind == "redraw" then
        (
            try ( redrawViews() ) catch ()
            note = "redrawViews()"
        )
        else if primeKind == "weightCount" then
        (
            local dummy = try ( skinOps.GetVertexWeightCount smod 5 ) catch "EX"
            note = "读一次 GetVertexWeightCount"
        )
        else if primeKind == "idsAndWeights" then
        (
            local dummy = try ( skinOps.GetVertexBoneIDsAndWeights smod 5 ) catch "EX"
            note = "读一次 GetVertexBoneIDsAndWeights"
        )
        else if primeKind == "disableRedraw" then
        (
            try ( disableSceneRedraw() ) catch ( say ("  " + tag + " disableSceneRedraw FAILED: " + (ex())) )
            note = "disableSceneRedraw()（正式脚本整段蒙皮期间就是关着的）"
        )
        local w1 = try ( skinOps.ReplaceVertexWeights smod 5 #(2,3) #(0.7,0.3); "OK" ) catch (ex())
        local r1 = rbline smod 5
        local w2 = try ( skinOps.ReplaceVertexWeights smod 5 #(2,3) #(0.6,0.4); "OK" ) catch (ex())
        local r2 = rbline smod 5
        say ("  [" + tag + "] 预热=" + note)
        say ("      首写 v5 -> " + (S w1) + " | " + r1)
        say ("      二写 v5 -> " + (S w2) + " | " + r2 + "（0.6/0.4 若读回 0.0/0.0 = 二次写坍缩成立）")
        /* 关掉重绘的候选不能把状态留给下一发：否则 T5 之后的每一发都同时在测两件事。 */
        try ( enableSceneRedraw() ) catch ()
    )
)

(
    say "===== M4e probe start ====="
    /* 顺序本身也是变量：第一轮候选永远跑在「刚启动、什么都没算过」的场景里。
       所以「none」放在最前，其余每个候选都各自 deleteAllObjects 后重建，互不借用状态。 */
    trial "T0" "none"
    trial "T1" "numVerts"
    trial "T2" "redraw"
    trial "T3" "weightCount"
    trial "T4" "idsAndWeights"
    trial "T5" "disableRedraw"

    /* 收尾一问：既然 RemoveZeroWeights 第四轮返回过 OK，在「干净且已求值」的 Skin 上
       它能不能把坍缩的顶点救回来（M4b 的补救路线只剩这条还没证伪）。 */
    local sm = freshSkin "T6"
    if sm != undefined do
    (
        local bx = try ( getNodeByName "T6Box" exact:true ) catch undefined
        if bx != undefined do ( local dummy = bx.numVerts )
        local w1 = try ( skinOps.ReplaceVertexWeights sm 5 #(2,3) #(0.7,0.3); "OK" ) catch (ex())
        local w2 = try ( skinOps.ReplaceVertexWeights sm 5 #(2,3) #(0.6,0.4); "OK" ) catch (ex())
        say ("  [T6] 首写=" + (S w1) + " 二写=" + (S w2) + " | " + (rbline sm 5))
        local rw = try ( skinOps.RemoveZeroWeights sm; "OK" ) catch (ex())
        say ("      RemoveZeroWeights -> " + (S rw) + " | " + (rbline sm 5))
        local w3 = try ( skinOps.ReplaceVertexWeights sm 5 #(2,3) #(0.6,0.4); "OK" ) catch (ex())
        say ("      删零后再写 -> " + (S w3) + " | " + (rbline sm 5))
        local un = try ( skinOps.unNormalizeVertex sm 5 true; "OK" ) catch (ex())
        local w4 = try ( skinOps.ReplaceVertexWeights sm 5 #(2,3) #(0.6,0.4); "OK" ) catch (ex())
        say ("      unNormalizeVertex true(" + (S un) + ") 后再写 -> " + (S w4) + " | " + (rbline sm 5))
        /* 整列替换能不能绕过顶点级坍缩（SetBoneWeight 四参：骨 + 顶点 + 值，按第四轮报错推） */
        local sb = try ( skinOps.SetBoneWeight sm 2 5 0.6; "OK" ) catch (ex())
        say ("      SetBoneWeight(smod,bone,vert,w) -> " + (S sb) + " | " + (rbline sm 5))
    )

    say "===== M4e readback probe done ====="
    try ( quitMax quiet:true ) catch ( try ( quitMax ) catch () )
)
