/* #41 探针 v2：只答一个问题 —— 把 Foot 的 #scale 沿长度轴拉长，骨盒的远端会不会跟着长，
   以及**子节点（Toe0 的 pivot）会不会被一起带走**。
   日志一律 ASCII：上一版用 openFile/format 写中文，落盘全变成 "?"，数字能看但标签废了。

   为什么这一条值一整轮：
   1) 上一版探针实测 stock Biped 的脚骨盒是
      本地 [-1.6,-3.409,-3.778]..[8.928,11.412,3.838] = 10.5 x 14.8 x 7.6 的**一整块**，
      远角落在子节点上（盒端到子节点还差 = 0.0）。
      而第十六轮我们场景里同一根骨是 [-1.443,-2.370,-3.408]..[8.054,7.935,3.462]，
      前端离子节点还差 3.29cm、竖直方向只有 2.32 —— 用户看到的「牙签 + 悬空的趾块」
      全是我们自己缩出来的，不是 Biped 的默认长相。
   2) 但「能不能拉回去」卡在一条**从未实测**的假设上：README 写「Y scale 就是 link 长度，
      动它会把 Toe0 一起搬走」，来源只是「肢段拟合就是这么用的」。而肢段拟合拿
      mmd2maxSegLen（两 pivot 间距）当判据去乘 scale Y —— 那只能证明「scale Y 影响
      两 pivot 间距」，不等于「骨盒远端 = 子节点」。谁跟谁，只有实测能定。
   写法一律抄脚本里跑通的调用：读 `biped.getTransform node #scale`（**不带 biped 参数**，
   上一版探针写成带 biped 的三元形式读回 undefined，B/C 两段被静默跳过），
   写 `biped.setTransform node #scale v false`（见 mmd2maxScaleBoneAxis）。
   判据 = 改完立刻回读 + 退出 Figure 模式 update 之后再回读，两遍都算数才叫真改了。 */
global PROBE_OUT = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/biped_gap2.txt"
global BIP = undefined

fn probeSay s =
(
    local f = openFile PROBE_OUT mode:"a"
    if f == undefined do f = createFile PROBE_OUT
    if f != undefined do ( format "%\n" s to:f; close f )
)

fn pnum v = (v as string)

fn normRow node k =
(
    local m = node.transform
    local v = [m.row1.x, m.row1.y, m.row1.z]
    if k == 2 do v = [m.row2.x, m.row2.y, m.row2.z]
    if k == 3 do v = [m.row3.x, m.row3.y, m.row3.z]
    local n = length v
    if n < 1e-8 do return [0.0, 0.0, 0.0]
    normalize v
)

/* 一行读数：link(两 pivot 间距) + 长度轴 + 盒远端沿轴坐标 + 盒端到子节点的差 + 本地盒两角 */
fn probePair tag pname cname =
(
    local pn = getNodeByName pname exact:true
    local cn = getNodeByName cname exact:true
    if pn == undefined do ( probeSay (tag + " NO-PARENT"); return undefined )
    if cn == undefined do ( probeSay (tag + " NO-CHILD"); return undefined )
    local pp = pn.transform.translationpart
    local cp = cn.transform.translationpart
    local cv = cp - pp
    local link = length cv
    local bb = undefined
    try ( bb = nodeGetBoundingBox pn pn.transform ) catch ( bb = undefined )
    if bb == undefined do ( probeSay (tag + " NO-BB"); return undefined )
    local mn = bb[1]
    local mx = bb[2]
    local bi = 1
    local bd = 0.0
    for k = 1 to 3 do
    (
        local d = dot (normRow pn k) cv
        if abs d > abs bd do ( bi = k; bd = d )
    )
    local u = normRow pn bi
    local along = dot cv u
    local face = if along >= 0.0 then mx[bi] else mn[bi]
    local sc = undefined
    try ( sc = biped.getTransform pn #scale ) catch ( sc = undefined )
    local r = tag + " link=" + pnum link + " ax=" + pnum bi \
        + " childAlong=" + pnum along + " boxFar=" + pnum face \
        + " gap=" + pnum (along - face) \
        + " min=[" + pnum mn.x + "," + pnum mn.y + "," + pnum mn.z + "]" \
        + " max=[" + pnum mx.x + "," + pnum mx.y + "," + pnum mx.z + "]" \
        + " scale=" + (sc as string)
    probeSay r
    #(cp, face, link, along, bi)
)

(
    probeSay "===== gap2 start ====="
    BIP = undefined
    try
    (
        BIP = biped.createNew 160.0 0.0 [0.0, 0.0, 0.0] arms:true neckLinks:1 \
            spineLinks:3 legLinks:3 toes:1 toeLinks:1 trianglePelvis:false triangleNeck:true
    )
    catch ( probeSay ("createNew EX=" + (getCurrentException())) )
    if BIP != undefined do
    (
        local lf = getNodeByName "Bip001 L Foot" exact:true
        local lt = getNodeByName "Bip001 L Toe0" exact:true
        if lf == undefined or lt == undefined do probeSay "NO-FOOT-NODES"
        if lf != undefined and lt != undefined do
        (
            probeSay ("biped " + BIP.name)
            local s0 = undefined
            try ( s0 = biped.getTransform lf #scale ) catch ( s0 = undefined )
            probeSay ("getScale node-only-form=" + (s0 as string))
            local a0 = probePair "A0 stock" "Bip001 L Foot" "Bip001 L Toe0"

            /* ---- B) 沿长度轴拉长 1.3 倍：盒远端跟不跟？子节点动不动？ ---- */
            probeSay "-- B Foot length-axis scale x1.3 --"
            if s0 != undefined and a0 != undefined do
            (
                local ai = a0[5]
                local sNew = [s0.x, s0.y, s0.z]
                if ai == 1 do sNew.x = s0.x * 1.3
                if ai == 2 do sNew.y = s0.y * 1.3
                if ai == 3 do sNew.z = s0.z * 1.3
                local fig = false
                try ( BIP.controller.figureMode = true; fig = true ) catch ()
                probeSay ("figureMode=" + (fig as string))
                local errW = ""
                try ( biped.setTransform lf #scale sNew false )
                catch ( errW = getCurrentException() )
                if errW != "" do probeSay ("write EX=" + errW)
                local s1 = undefined
                try ( s1 = biped.getTransform lf #scale ) catch ( s1 = undefined )
                probeSay ("readback=" + (s1 as string) + " want=" + (sNew as string))
                probePair "B1 in-figure" "Bip001 L Foot" "Bip001 L Toe0"
                try ( BIP.controller.figureMode = false ) catch ()
                try ( update 0.0 ) catch ()
                local b2 = probePair "B2 after-exit" "Bip001 L Foot" "Bip001 L Toe0"
                if b2 != undefined and a0 != undefined do
                    probeSay ("B-VERDICT childMoved=" + pnum (distance b2[1] a0[1]) \
                        + " boxFarDelta=" + pnum (b2[2] - a0[2]) \
                        + " linkDelta=" + pnum (b2[3] - a0[3]))
            )
            if s0 == undefined do probeSay "B SKIPPED no scale"

            /* ---- C) 只拉长 Toe0（链尾，没有子节点）：趾盒能不能往后长 ---- */
            probeSay "-- C Toe0 length-axis scale x2.4 --"
            local t0 = undefined
            try ( t0 = biped.getTransform lt #scale ) catch ( t0 = undefined )
            local c0 = probePair "C0 toe-back" "Bip001 L Toe0" "Bip001 L Foot"
            if t0 != undefined and c0 != undefined do
            (
                local ai2 = c0[5]
                local tNew = [t0.x, t0.y, t0.z]
                if ai2 == 1 do tNew.x = t0.x * 2.4
                if ai2 == 2 do tNew.y = t0.y * 2.4
                if ai2 == 3 do tNew.z = t0.z * 2.4
                try ( BIP.controller.figureMode = true ) catch ()
                local errT = ""
                try ( biped.setTransform lt #scale tNew false )
                catch ( errT = getCurrentException() )
                if errT != "" do probeSay ("write-toe EX=" + errT)
                try ( BIP.controller.figureMode = false ) catch ()
                try ( update 0.0 ) catch ()
                probeSay ("toeScale " + (t0 as string) + " -> " + (tNew as string))
                local c1 = probePair "C1 toe-back-after" "Bip001 L Toe0" "Bip001 L Foot"
                probePair "C1 foot-to-toe-after" "Bip001 L Foot" "Bip001 L Toe0"
                if c1 != undefined do
                    probeSay ("C-VERDICT toeMoved=" + pnum (distance c1[1] c0[1]) \
                        + " toeBoxFarDelta=" + pnum (c1[2] - c0[2]))
            )
            if t0 == undefined do probeSay "C SKIPPED no scale"
        )
    )
    probeSay "===== gap2 done ====="
    try ( quitMax quiet:true ) catch ( try ( quitMax ) catch () )
)
