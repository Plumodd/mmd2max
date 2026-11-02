/* #41 探针 v3：脚骨盒前端收成「刀尖」到底是 Biped 的显示 taper，还是我们缩出来的？
   只答三件事，别的都不碰：
   1) Biped **链接节点**自己身上有没有 taper/width/height 这一类可读可写的属性
      （上一轮反射的是 Biped 根上的 controller，节点本身一直没反射过 ——
       脚本里给 BoneGeometry 写 bone.taper = 90 是跑通的，Biped 节点同不同源没测过）；
   2) 控制器上到底有没有 setBoneParams 这一类函数（把整份接口按关键字筛一遍）；
   3) 若属性存在，写 0 之后回读还在不在，以及 nodeGetBoundingBox 跟不跟着变
      （跟 = 真体积；不跟 = 纯显示）。
   日志一律 ASCII：上一版用 openFile/format 写中文，落盘全变成 "?"。 */
global PROBE_OUT = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/biped_taper.txt"
global BIP = undefined

fn probeSay s =
(
    local f = openFile PROBE_OUT mode:"a"
    if f == undefined do f = createFile PROBE_OUT
    if f != undefined do ( format "%\n" s to:f; close f )
)

fn hasAny s keys =
(
    local low = toLower s
    for k in keys do if findString low k != undefined do return true
    false
)

/* 反射一份接口，只把带关键字的行写出来（整份 3400 行写盘会把要看的行淹掉） */
fn probeIface tag v =
(
    local arr = undefined
    local err = ""
    try ( arr = dumpInterface v ) catch ( err = getCurrentException() )
    if arr == undefined do
    (
        probeSay (tag + " dumpInterface EX=" + err)
        return 0
    )
    local n = 0
    local hits = 0
    for line in arr do
    (
        n = n + 1
        local s = line as string
        if hasAny s #("taper", "width", "height", "box", "fin", "boneparam",
                      "volume", "shape", "display", "scale") do
        (
            hits = hits + 1
            if hits <= 150 do probeSay (tag + " | " + s)
        )
    )
    probeSay (tag + " total=" + (n as string) + " hits=" + (hits as string))
    hits
)

/* 一个属性一行账：读得到吗 / 写 0 写得了吗 / 写完回读还在吗 / 盒变了吗 */
fn probeProp node p =
(
    local rv = undefined
    local e1 = ""
    try ( rv = getProperty node p ) catch ( e1 = getCurrentException() )
    local wrote = "skip"
    local e2 = ""
    if e1 == "" do
    (
        try ( setProperty node p 0.0; wrote = "yes" ) catch ( e2 = getCurrentException() )
    )
    local rv2 = undefined
    try ( rv2 = getProperty node p ) catch ()
    local bb = undefined
    try ( bb = nodeGetBoundingBox node node.transform ) catch ()
    probeSay ("prop " + (p as string) \
        + " read=" + (rv as string) \
        + " writeOk=" + wrote \
        + " readback=" + (rv2 as string) \
        + " wEX=[" + e1 + "] sEX=[" + e2 + "]" \
        + " boxMax=" + (if bb == undefined then "NA" else (bb[2] as string)))
    rv
)

(
    try ( deleteFile PROBE_OUT ) catch ()
    local f0 = createFile PROBE_OUT
    if f0 != undefined do close f0
    probeSay "===== taper start ====="
    BIP = undefined
    try
    (
        BIP = biped.createNew 160.0 0.0 [0.0, 0.0, 0.0] arms:true neckLinks:1 \
            spineLinks:3 legLinks:3 toes:1 toeLinks:1 trianglePelvis:false triangleNeck:true
    )
    catch ( probeSay ("createNew EX=" + (getCurrentException())) )
    if BIP == undefined do probeSay "NO-BIPED"
    if BIP != undefined do
    (
        local lf = getNodeByName "Bip001 L Foot" exact:true
        if lf == undefined do probeSay "NO-FOOT"
        if lf != undefined do
        (
            probeSay ("root=" + BIP.name + " footClass=" + (classof lf as string) \
                + " super=" + (superClassOf lf as string))
            probeIface "A-footNode" lf
            probeIface "B-footClass" (classof lf)
            probeIface "C-rootObj" BIP
            probeIface "D-controller" BIP.controller

            probeSay "-- E) 逐个试 Biped 节点上的显示属性 --"
            for p in #(#taper, #width, #height, #banSize, #banPos, #boneType,
                       #linkLength, #boxWidth, #boxHeight, #endWidth, #startWidth) do
                probeProp lf p

            probeSay "-- F) 控制器上按名字试 Biped 接口函数 --"
            for cn in #("setBoneParams", "getBoneParams", "setBoneDisplay", "setTaper",
                        "getTaper", "setVolumeType", "setBoneVolume", "setBonesDisplay",
                        "setBox", "setWidth", "setHeight") do
            (
                local e = ""
                local got = undefined
                try ( got = getProperty BIP.controller (cn as symbol) )
                catch ( e = getCurrentException() )
                probeSay ("fn " + cn + " -> " + (if e == "" then "EXISTS val=" + (got as string) else e))
            )

            probeSay "-- G) 整份 controller 接口里带 taper 的行（不筛关键字，直接数） --"
            local arr2 = undefined
            try ( arr2 = dumpInterface BIP.controller ) catch ()
            if arr2 != undefined do
            (
                local c = 0
                for line in arr2 do
                (
                    if findString (toLower (line as string)) "taper" != undefined do
                    (
                        c = c + 1
                        if c <= 30 do probeSay ("taperLine | " + (line as string))
                    )
                )
                probeSay ("taperLines=" + (c as string))
            )
        )
    )
    probeSay "===== taper done ====="
    try ( quitMax quiet:true ) catch ( try ( quitMax ) catch () )
)
