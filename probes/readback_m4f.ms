/* M4 探针第六轮（readback_m4f）：M4b 回读验收还差三个数没实测。

   前五轮把「写」这条路定死了（要预热、每顶点只能写一次、坍缩只能靠 unNormalizeVertex 救）。
   这一轮问的是「读」这一侧，三条都会直接决定 skin_rb 那批判据的写法：

   Q1 **每顶点骨数上限**。香菱 weights.tsv 的分布是 1 骨 11206 / 2 骨 7566 / 3 骨 76 / 4 骨 44
      —— 最多正好 4 根。Max 的权重表对话框里有个「最大骨骼数」，但 skinOps 的成员名单里
      （probes/skinops_members.txt，143 个成员）没有任何 MaxBones/SumThreshold 的 setter。
      那就只能实测：写 5 根进去，回读是 5 根还是被裁成 4 根？
      被裁 = 以后任何一具模型只要有一个顶点绑 5 根骨，回读就会报一个解释不了的 link 差，
      现在知道比到时候拿一具新模型去撞便宜。
      （skinOps 名单里有 WeightTable / weightTool 这些**对话框**入口，不动它，只问 API 路径。）

   Q2 **极小权重会不会被吞**。写 0.99999 / 0.00001 两根，回读还剩几根？
      这决定「链路计数」用 >1e-6 过滤是不是安全。香菱实测最小权重 0.002957、零权重对 0 条，
      所以这一发不是为了让香菱过关，是为了换模型时别把「被 Skin 吞掉」误读成「我们写丢了」。

   Q3 **IsVertexModified 能不能证明「这个顶点真被写过」**。
      没写过的顶点回读是 bone 1 @ 1.0（第一轮实测），也就是「读到非空」根本不能证明写进去过。
      如果 IsVertexModified 在写入后变 true、未写入保持 false，M4b 就多一条硬判据：
      unmodified 顶点数必须 0 —— 这是「写入侧覆盖到每一个顶点」的场景证据，
      比现在那条 written_vertices 计数（写入侧自述）强一个档次。

   每发都在**预热过的干净 Skin**（取一次 mesh.numVerts）上做，避免把第五轮的预热问题混进来。 */

global PROBE_OUT = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/readback_m4f.txt"

fn say s =
(
    local f = openFile PROBE_OUT mode:"a" encoding:#utf8
    if f != undefined do ( format "%\n" (s as string) to:f; close f )
)

fn S x = try ( x as string ) catch "<unprintable>"
fn trim s =
(
    local t = s as string
    if t.count > 90 then (substring t 1 90 + " [cut]") else t
)
fn ex = trim (try ( getCurrentException() as string ) catch "?")

fn rbline tag smod v =
(
    local x = try ( skinOps.GetVertexBoneIDsAndWeights smod v ) catch "EX"
    if (classOf x) != Array or x.count < 2 then (tag + " v" + (S v) + " 读不到")
    else
    (
        local ssum = 0.0
        for i = 1 to x[2].count do ssum += x[2][i]
        (tag + " v" + (S v) + " n=" + (S x[1].count) + " ids=" + (S x[1]) \
            + " ws=" + (S x[2]) + " sum=" + (S ssum))
    )
)

fn modline tag smod v =
(
    local m = try ( skinOps.IsVertexModified smod v ) catch ("EX:" + (ex()))
    local c = try ( skinOps.GetVertexWeightCount smod v ) catch ("EX:" + (ex()))
    (tag + " v" + (S v) + " IsVertexModified=" + (S m) + " WeightCount=" + (S c))
)

/* 干净 Skin：Box + Skin + 5 根骨，并**先预热**（第五轮 T1 证有效的那条路）。 */
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
    local boneList = #()
    for i = 1 to 5 do
    (
        local offX = ((i - 3) * 5.0)
        local bn = try ( BoneSys.createBone [offX, 0, 0] [offX + 5, 0, 0] [0, 1, 0] ) \
            catch undefined
        if bn != undefined do try ( bn.name = (tag + "Bone" + (S i)) ) catch ()
        append boneList bn
    )
    if smod != undefined do
    (
        try ( select mesh; modPanel.setCurrentObject smod ) catch ()
        for bn in boneList do
            if bn != undefined do try ( skinOps.AddBone smod bn 0 ) catch ()
        /* 预热：真实求值一次堆栈（第五轮结论），后面的写入才不算在半求值状态下测 */
        local n = mesh.numVerts
        say ("  [" + tag + "] 预热：numVerts=" + (S n) + " bones=" \
            + (S (try ( skinOps.GetNumberBones smod ) catch (ex()))))
    )
    smod
)

(
    say "===== M4f probe start ====="

    /* ---- A：没写过的顶点，IsVertexModified / WeightCount 的基线 ---- */
    local sa = freshSkin "A"
    if sa == undefined then
        say "  A freshSkin 失败，后面全部跳过"
    else
    (
        say ("  A1 未写基线：" + (modline "  A1" sa 5) + " | " + (rbline "  A1" sa 5))
        say ("  A2 未写基线（另一顶点）：" + (modline "  A2" sa 12))

        /* ---- B：写 5 根骨进同一个顶点 ---- */
        local w5 = try ( skinOps.ReplaceVertexWeights sa 5 #(1,2,3,4,5) \
            #(0.3,0.25,0.2,0.15,0.1); "OK" ) catch (ex())
        say ("  B1 写 5 骨 v5 -> " + (S w5) + " | " + (rbline "  B1" sa 5))
        say ("  B2 写后状态：" + (modline "  B2" sa 5))
        say ("  B3 隔壁没写的顶点：" + (modline "  B3" sa 6) + " | " + (rbline "  B3" sa 6))

        /* ---- C：极小权重会不会被吞（1e-5 与 1e-9 各一发）---- */
        local wc1 = try ( skinOps.ReplaceVertexWeights sa 7 #(1,2) #(0.99999, 0.00001); "OK" ) \
            catch (ex())
        say ("  C1 写 1e-5 v7 -> " + (S wc1) + " | " + (rbline "  C1" sa 7))
        local wc2 = try ( skinOps.ReplaceVertexWeights sa 8 #(1,2) #(0.999999999, 0.000000001) \
            ; "OK" ) catch (ex())
        say ("  C2 写 1e-9 v8 -> " + (S wc2) + " | " + (rbline "  C2" sa 8))

        /* ---- D：4 骨（香菱现状）与 SetVertexWeights 首写 ---- */
        local w4 = try ( skinOps.ReplaceVertexWeights sa 9 #(1,2,3,4) #(0.4,0.3,0.2,0.1); "OK" ) \
            catch (ex())
        say ("  D1 写 4 骨 v9 -> " + (S w4) + " | " + (rbline "  D1" sa 9))
        local sw = try ( skinOps.SetVertexWeights sa 10 #(1,2,3,4,5) #(0.2,0.2,0.2,0.2,0.2) \
            ; "OK" ) catch (ex())
        say ("  D2 SetVertexWeights 首写 5 骨 v10 -> " + (S sw) + " | " + (rbline "  D2" sa 10))
        say ("  D3 v10 状态：" + (modline "  D3" sa 10))

        /* ---- E：bone id 越界会不会被静默吞掉 ---- */
        local wb = try ( skinOps.ReplaceVertexWeights sa 11 #(1,99) #(0.5,0.5); "OK" ) \
            catch (ex())
        say ("  E1 写不存在的骨 99 v11 -> " + (S wb) + " | " + (rbline "  E1" sa 11))
    )

    /* ---- F：整表扫描的代价（18892 顶点要不要采样就看这一发）---- */
    local sf = freshSkin "F"
    if sf != undefined do
    (
        local t0 = timeStamp()
        local n = 0
        for v = 1 to 100 do
        (
            local x = try ( skinOps.GetVertexBoneIDsAndWeights sf v ) catch undefined
            if (classOf x) == Array do n += x[1].count
        )
        say ("  F1 读 100 顶点耗时 " + (S ((timeStamp() - t0) / 1000.0)) + " 秒，链路 " + (S n))
    )

    say "===== M4f readback probe done ====="
    try ( quitMax quiet:true ) catch ( try ( quitMax ) catch () )
)
