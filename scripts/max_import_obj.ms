/* mmd2max：走 OBJ + 骨骼/权重数据的轻量装配路径。

   为什么不走 FBX：Max 2022 的 FBX 导入器处理 555 骨骼 + 12 万面时会卡死，
   因此这里改为导入 OBJ（Max 原生、快速）+ 按 TSV 数据建骨骼 + 手动挂 Skin 权重。
   重活全部在 Python 侧（多进程）完成，Max 只做装配。

   装配方式只有一种（v2 的纯 Bone 模式已删除）：
   人形骨 -> Biped 节点，衣服/头发/裙摆等附属骨 -> BoneGeometry。

   坐标与手性在 Python 侧一次性定死（见 mmd2max/coords.py），本脚本不做任何
   补偿旋转：OBJ 写 (x, y, -z)，Max 导入器转成 Z-up 后正好等于 (x, z, y)，
   即行列式 -1 的镜像映射 —— 保住「屏幕上的样子」，代价是三角面绕序翻一次。

   配置（M1b 起单源）：这份脚本只认一个入口 —— 全局变量 MMD2MAX_CFG 指向
   tools/obj_run.py 写出的 MMD2MAX.CFG。文件里每一项（OBJ/BONES/WEIGHTS/VERTS/
   OUTPUT/MAP/MARKER/LOG/MORPHS/PHYSICS/LIMBS/WIDTHS/SPINE_LINKS/FINGERS/
   FINGER_LINKS/FOOTBOXFLAT/DIRTOL/ROLLTOL/TOETOL/GAPTOL/FOOTREACH/
   SKIN/SHOT/SHOTDIR/INTERACTIVE）都由 mmd2maxLoadCfg
   读进来，缺文件、缺键、值不合法一律抛错中止，绝不退回默认值。
   MORPHS/PHYSICS/WIDTHS/SHOTDIR 允许是空串 = 该阶段整段跳过；LIMBS 及其余路径
   为空一律抛错（见 mmd2maxLoadCfg 的空路径校验，名单与 obj_run.py 的 CFG_EMPTY_OK 一致）。
   手跑（File ▸ Run Script 这份模板）与自动跑（生成的 mmd2max_run.ms 只注入
   MMD2MAX_CFG 一行）读到的是同一份配置，不可能分叉。

   可靠性约定（针对审查项 P0#3 / P0#6 / P1#18）：
   - 权重文件里的骨骼序号是「全局骨骼序号」，必须经由 globalToSkin 映射成 Skin 局部序号；
   - 任何骨骼加入失败、任何顶点写权重失败、顶点数不一致，都直接抛错，
     由外层 catch 写出 *.error.txt，绝不写出 success 标记；
   - 只有全部步骤 0 失败，才会写出 success 标记。
*/

/* 装配配置全局变量的**声明**（值全部由 mmd2maxLoadCfg 从 MMD2MAX.CFG 写入，
   这里一个都不给默认值）。声明排在最前面是为了 MAXScript 的解析期绑定：
   读这些名字的小函数定义得比赋值早，名字先绑成全局才不会撞上 #20 那类
   「Call needs function or class, got: undefined」。 */
global MMD2MAX_CFG
global MMD2MAX_OBJ
global MMD2MAX_BONES
global MMD2MAX_WEIGHTS
global MMD2MAX_MAP
global MMD2MAX_LIMBS
global MMD2MAX_WIDTHS
global MMD2MAX_MORPHS
global MMD2MAX_PHYSICS
global MMD2MAX_OUTPUT
global MMD2MAX_MARKER
global MMD2MAX_LOG
global MMD2MAX_VERDICTS
global MMD2MAX_REPORT
global MMD2MAX_SHOTDIR
global MMD2MAX_VERTS
global MMD2MAX_SPINE_LINKS
global MMD2MAX_FINGERS
global MMD2MAX_FINGER_LINKS
global MMD2MAX_FOOTBOXFLAT
/* 收敛循环的遍数上限。以前它是这一份脚本里的字面量 `for passNo = 1 to 3`，Python 侧
   只能用契约测试去钉一个自己看不见的数字；现在数值住在 mmd2max/passes.py 的注册表里，
   经 MMD2MAX.CFG 下发，校验器拿同一个数核对「报 ceiling 却只跑了两遍」这种自相矛盾。 */
global MMD2MAX_CONVERGE_MAX
global MMD2MAX_SKIN
global MMD2MAX_SHOT
global MMD2MAX_INTERACTIVE
/* 装配目标值（M2 起由 mmd2max/targets.py 的 TARGETS 写进 CFG，这里只声明、不给默认值）。
   声明必须排在任何读取者之前：以前这几个数就写在声明行末尾，于是「这份脚本自己带着判据」，
   Python 侧根本不知道有哪些数 —— 现在缺键一律中止，和路径/开关同一待遇。 */
global MMD2MAX_DIRTOL
global MMD2MAX_ROLLTOL
global MMD2MAX_TOETOL
global MMD2MAX_GAPTOL
global MMD2MAX_FOOTREACH
/* 快照回滚开关（M4d）。和 FOOTREACH 一样只声明不给默认值：值来自 CFG，
   而 CFG 的值来自 mmd2max/targets.py 的 ROLLBACK。它决定的是「一刀写坏了场景里剩什么」，
   不是某个目标值，所以必须能整段关掉来自证它不改几何。 */
global MMD2MAX_ROLLBACK
/* 接趾守卫的触发阈值（厘米）：骨盒远端比动手前更远这么多才算「推过头」。
   它和 ROLLBACK 是两件事 —— ROLLBACK 决定「有没有这条退路」，这个数决定「什么时候走」。 */
global MMD2MAX_RBTRIG
/* mmd2maxLoadCfg 算好的配置摘要，由 mmd2maxMain 在 mmd2maxInitLog **之后**打出来
   （InitLog 重建日志，先打的会被截掉）。这里同样不写 `= ""`：值没被 LoadCfg 赋上就是没读到配置。 */
global MMD2MAX_CFGSUMMARY

/* 交互开关：值一定来自 CFG（mmd2maxLoadCfg 已验过是 bool），所以这里**不再**
   写 try/catch + 默认值。以前那种写法把「配置没读到」和「配置是 false」压成同一个
   结果 —— 蒙皮被跳过、截图没抓，日志却一句错都没有。
   MMD2MAX_QUIET 已在 #56 删掉：obj_run 恒写 true，于是它唯一的用处是把
   mmd2maxPrepareScene 里的「要重置你的场景，问一声」永久短路 —— 一个只能为 true
   的开关不是开关，是死代码，而且它挡住的正是「--gen-only 在用户自己的 Max 里
   一句不问就 resetMaxFile，把没存的活儿清空」这条真事故路径。 */
fn mmd2maxIsInteractive = MMD2MAX_INTERACTIVE

/* 最外层错误处理器专用的交互判定，返回值一定是 bool。为什么不能直接用上面那个：
   mmd2maxLoadCfg() 自己抛错时 MMD2MAX_INTERACTIVE 还是 undefined，而
   `if undefined do ...` 会在错误处理器里**再**抛一次 —— 真正的失败原因被
   「处理器自己炸了」盖掉，屏幕上只剩一个没人点的模态弹窗（用户不在电脑前就是死锁）。
   判不出来时按非交互处理：配置都没读到，场景里什么都没有，保留现场没有价值，
   自己退出反而能让上层拿到 error.txt。 */
fn mmd2maxIsInteractiveNow =
(
    if MMD2MAX_INTERACTIVE == undefined do return false
    MMD2MAX_INTERACTIVE
)

/* 失败标记文件的路径。正常情况跟着成功标记走；CFG 没读成时 MARKER 还是
   undefined，就退回 CFG 同目录的定名文件 —— obj_run.py 两边都查（常量
   CFG_ERROR_NAME），所以「配置错误」这种最早期的失败也能落成磁盘证据。 */
fn mmd2maxErrorFilePath =
(
    if MMD2MAX_MARKER != undefined and MMD2MAX_MARKER != "" do
        return (MMD2MAX_MARKER + ".error.txt")
    local cfg = ""
    try ( cfg = MMD2MAX_CFG as string ) catch ( cfg = "" )
    if cfg == "" do return undefined
    (getFilenamePath cfg) + "mmd2max_cfg.error.txt"
)

/* Max 的 Skin 修改器在大网格 + 数百根骨骼下会卡死，
   因此提供开关：为 false 时只导入网格与骨架，不挂蒙皮。 */
fn mmd2maxSkinEnabled = MMD2MAX_SKIN

/* 截图开关：装配完成后额外抓一张「线框视口 + 可见骨骼」的 PNG，
   用于人工核对 Biped 是否贴合模型。 */
fn mmd2maxScreenshotEnabled = MMD2MAX_SHOT

/* ===== 日志三兄弟必须排在所有调用者之前 =====
   MAXScript 的「空格裸调」是**解析期**绑定的：解析 `f a b` 时如果 f 还没被定义，
   解析器只会把它当成「对一个未绑定全局变量做 apply」，运行期报
   `-- Type error: Call needs function or class, got: undefined`。
   实测教训（白跑一轮 Max）：截图三函数 mmd2maxSaveDib / mmd2maxShotOne /
   mmd2maxScreenshot 曾经定义在 mmd2maxLog 之前，函数体里那行
   `mmd2maxLog ("截图开始 " + ...)` 就是这么炸的 —— 连第一行截图日志都没落盘，
   而 `classof mmd2maxLog = MAXScriptFunction`（值早就在，只是解析期看不见）。
   把 JsonStr/Log/InitLog 提到最前面，任何函数都能安全调用。 */
fn mmd2maxJsonStr s =
(
    /* 把 Windows 路径里的反斜杠与引号转义，保证 success 标记是合法 JSON */
    local t = s as string
    t = substituteString t "\\" "\\\\"
    t = substituteString t "\"" "\\\""
    t
)

fn mmd2maxLog message =
(
    /* localTime 是系统全局变量，不是函数，绝对不能写成 localTime() */
    local stamp = localTime as string
    local body = message as string
    local text = stamp + "  " + body
    format "[mmd2max] %\n" text
    try
    (
        if MMD2MAX_LOG != undefined and MMD2MAX_LOG != "" do
        (
            local f = openFile MMD2MAX_LOG mode:"a" encoding:#utf8
            if f != undefined do ( format "%\n" text to:f; close f )
        )
    )
    catch ()
)

fn mmd2maxInitLog =
(
    /* 调用点紧跟 mmd2maxLoadCfg()，所以这里读到的 globals 一定已验过。
       以前写成 `(try (MMD2MAX_OBJ) catch ("?"))`：把「配置没读成」打印成一行 `?`，
       日志照样继续往下走 —— 正是 M1b 要根除的那类「缺配置还能绿」。 */
    try
    (
        if MMD2MAX_LOG != undefined and MMD2MAX_LOG != "" do
        (
            local f = createFile MMD2MAX_LOG encoding:#utf8
            if f != undefined do
            (
                format "mmd2max OBJ 装配日志\n" to:f
                local ver = "?"
                try ( ver = (maxVersion()) as string ) catch ( ver = maxVersion as string )
                format "3ds Max: %\n" ver to:f
                format "配置: %\n" (MMD2MAX_CFG as string) to:f
                format "OBJ: %\n" (MMD2MAX_OBJ as string) to:f
                format "输出: %\n" (MMD2MAX_OUTPUT as string) to:f
                format "期望顶点数: %\n" (MMD2MAX_VERTS as string) to:f
                close f
            )
        )
    )
    catch ()
)

/* ===== 判据通道（M2b）：Max 只报数，不判定 ===== */

/* 报告文件表头（线格式 v2）。前三列与 MMD2MAX.VERDICTS 逐字相同 —— 那是两张表的拼接键；
   第四列两张表刻意不同名（规格表 expected = 尺，报告 actual = 读数），同名反而会在拼表
   时把尺当成读数。两边各写一份、靠测试互相对撞：改一边不改另一边 = 拼表全部对不上。 */
fn mmd2maxReportHeader = "phase\tcheck\tsubject\tactual"

fn mmd2maxReportInit =
(
    /* 每轮开头重建报告文件。为什么这里**不许**退回 try/catch：
       报告通道一坏，这一轮就永远没法验收；把它写成一行日志继续跑，等于
       「绿日志 + 没有证据」—— 正是本项目反复踩的那类事故。缺路径或打不开 = 中止。 */
    if MMD2MAX_REPORT == undefined or MMD2MAX_REPORT == "" do
        throw "MMD2MAX_REPORT 为空：判据报告无处可写，装配中止"
    local f = createFile MMD2MAX_REPORT encoding:#utf8
    if f == undefined do
        throw ("判据报告打不开：" + (MMD2MAX_REPORT as string))
    /* 必须写成 (mmd2maxReportHeader())。第二十一轮实跑只写了 (mmd2maxReportHeader)
       —— 不带调用的函数名传给 format 打出的是**函数本身**，文件第一行落地成字面量
       "mmd2maxReportHeader()"，表头从此对不上，Python 侧整轮判据直接作废。 */
    format "%\n" (mmd2maxReportHeader()) to:f
    close f
)

fn mmd2maxReport phase check subject actual =
(
    /* 一次调用 = 一行读数，立刻落盘。不缓存到数组最后统一写：装配中途抛错时，
       已经量到的那些行正是「死在哪一段」的唯一证据。 */
    local f = openFile MMD2MAX_REPORT mode:"a" encoding:#utf8
    if f == undefined do
        throw ("判据报告写不进：" + (MMD2MAX_REPORT as string))
    format "%\t%\t%\t%\n" (phase as string) (check as string) \
        (subject as string) (actual as string) to:f
    close f
)

fn mmd2maxSceneBox =
(
    /* 全场景包围盒：网格用 .bounds，骨骼（Biped/BoneGeometry 的 .bounds 可能是零尺寸）
       至少用 pivot 参与，否则缩放会把骨架框掉。栅格/平面这类超大对象按尺寸滤掉。
       这里刻意不写 `lo = [ min lo.x p.x, ... ]`：MAXScript 把行内的 `max x y` 当成
       MAX 命令解析，实跑报「Compile error: Unrecognized MAX command」，整份脚本都没法载入。
       逐分量 if 比较啰嗦，但没有任何语法歧义。 */
    local lo = [1e8, 1e8, 1e8]
    local hi = [-1e8, -1e8, -1e8]
    for o in objects do
    (
        /* 位置一律从 transform 的平移分量取，不能用 o.pos：Biped_Object 没有 .pos
           属性，香菱 01:10 那轮就在截图前的取景这一步报
           「Unknown property: "pos" in $Biped_Object:Bip001」整轮判失败 ——
           同一个坑文件里已经写过两次（mmd2maxSegLen / 摆位段），这处是漏网的。 */
        local p = undefined
        try ( p = o.transform.translationpart ) catch ( p = undefined )
        if p != undefined do
        (
            if p.x < lo.x do lo.x = p.x
            if p.y < lo.y do lo.y = p.y
            if p.z < lo.z do lo.z = p.z
            if p.x > hi.x do hi.x = p.x
            if p.y > hi.y do hi.y = p.y
            if p.z > hi.z do hi.z = p.z
        )
        local b = undefined
        try ( b = o.bounds ) catch ( b = undefined )
        if b != undefined and (distance b.min b.max) < 1e6 do
        (
            if b.min.x < lo.x do lo.x = b.min.x
            if b.min.y < lo.y do lo.y = b.min.y
            if b.min.z < lo.z do lo.z = b.min.z
            if b.max.x > hi.x do hi.x = b.max.x
            if b.max.y > hi.y do hi.y = b.max.y
            if b.max.z > hi.z do hi.z = b.max.z
        )
    )
    box3 lo hi
)

fn mmd2maxZoomFit =
(
    /* 把 4 个视口都框到全场景包围盒。
       实跑探针（Temp/mmd2max_probe/viewport_probe4.ms / 5.ms，Max 2022）钉死的事实：
       - viewport.zoomToBounds 要 3 个实参：**两个角点**，不是 box3。
         上一版写 `viewport.zoomToBounds false b`（b 是 box3）实测报
         「Argument count error: ZoomToBounds wanted 3, got 2」，被外层 try 吞掉，
         所以 23:55 那轮截图才会「头被裁掉」—— 缩放压根没执行过。
         签名依据不是猜的：Max 自带插件 stdplugs\...\BulletScripts\BsVportTools.ms:307
         就是这么调的。
       - viewport.setViewport 与全局 setViewport 都不存在（Unknown property /
         Call needs function or class），换视口只有 viewport.activeViewport = <int> 可用；
       - max zoomextents / max zoomext 这类 MAX 命令在脚本里编译不过
         （Compile error: Unrecognized MAX command）；
       - 区域→视野是线性的（区域放大 10 倍，getFOV 从 49.3 变 494.3），但它只把区域
         「撑满」画面、不留余量，探针里 128cm 处的头顶块正好被裁在画面外，
         所以再 viewport.zoom 1.35 拉远一点（zoom <k> 的含义是视野尺寸 ×k）。 */
    local b = mmd2maxSceneBox()
    if (distance b.min b.max) <= 1.0 do ( return false )
    for v = 1 to (viewport.numViews) do
    (
        try ( viewport.activeViewport = v )
        catch ( mmd2maxLog ("切视口失败：" + (getCurrentException() as string)) )
        try ( viewport.zoomToBounds off b.min b.max )
        catch ( mmd2maxLog ("取景失败：" + (getCurrentException() as string)) )
        try ( viewport.zoom 1.35 ) catch ()
    )
    completeRedraw()
    true
)

/* 只挑真网格：Biped / BoneGeometry / Helper 都不算，
   否则「藏掉网格看骨架」会把骨架一起藏掉。 */
global MMD2MAX_SHOTMESH = #()

fn mmd2maxShotMeshes =
(
    local arr = #()
    for o in objects do
    (
        local c = classof o
        if (c == Editable_Poly) or (c == Editable_Mesh) do append arr o
    )
    arr
)

fn mmd2maxSetMeshHidden flag =
(
    for o in MMD2MAX_SHOTMESH do ( try ( o.isHidden = flag ) catch () )
    completeRedraw()
    MMD2MAX_SHOTMESH.count
)

/* 截图前的视口整理：隐藏栅格、取消选择、叠一层网格三角边。
   上一版这里设的是节点级 `o.wire = true`，探针实测 Max 2022 根本没这个属性
   （Unknown property: "wire" in $Box:VBody），`o.seeThru` 同样不存在，
   所以那版截图永远是纯实体、看不见骨架 —— 验收要的「骨架有没有对上」判不了。
   实测可用的两招：viewport.SetShowEdgeFaces true（实体上叠三角边，回读为 true）
   和 网格 isHidden（只留骨架）。两种各来一张，网格和骨架才都判得出来。
   这些都发生在 .max 落盘之后，抓完立刻还原，不污染用户接着看的场景。 */
fn mmd2maxPrepareShot =
(
    try ( hide (getNodeByName "Grid") ) catch ()
    try ( select #() ) catch ()
    MMD2MAX_SHOTMESH = mmd2maxShotMeshes()
    try ( viewport.SetShowEdgeFaces true )
    catch ( mmd2maxLog ("EdgeFaces 开关失败：" + (getCurrentException() as string)) )
    completeRedraw()
    MMD2MAX_SHOTMESH.count
)

fn mmd2maxRestoreShot =
(
    /* 只还原我们藏掉的那些：上一版对 objects 里每个对象都 isHidden = false，
       会把用户本来就藏着的对象一并显示出来。 */
    for o in MMD2MAX_SHOTMESH do ( try ( o.isHidden = false ) catch () )
    MMD2MAX_SHOTMESH = #()
    try ( viewport.SetShowEdgeFaces false ) catch ()
    completeRedraw()
    true
)

/* 抓一张视口图并落盘。
   位图写盘在 Max 2022 里有两种写法（<bmp>.filename = <path> 与 save <bmp> fileName:<path>），
   哪一种可用没法靠读代码确定，所以两种都试。
   但「试完看文件在不在」是错的判据：上一轮（22:12 香菱）front 那张报 落盘=true，
   文件时间戳却停在 19:45 —— 因为 shots 目录里已经有同名旧图，doesFileExist 天然为真，
   于是第一个分支（只改属性、根本不写盘）就把成功判了出来，真正的 save 被跳过。
   判据换成「写入前后的文件修改时间是否变了」：只有真落盘才算 true。 */
/* 文件的最后写入时刻（.NET DateTime.Ticks），取不到返回 -1。
   实测（23:49 香菱那一轮 front/top 全灭之后专门跑的 api_probe.ms）：
   getOsProperty / fileLength / getSecurityAttribute 这三个「按理该有」的全局函数在
   Max 2022 里统统不存在，一调就是 `Type error: Call needs function or class, got: undefined`；
   可用的是 dotNetClass "System.IO.File"。别再凭印象写。 */
fn mmd2maxFileTicks path =
(
    local ticks = -1
    if doesFileExist path do
    try ( ticks = ((dotNetClass "System.IO.File").GetLastWriteTime path).Ticks ) catch ()
    ticks
)

fn mmd2maxSaveDib bmp shotFile =
(
    local before = mmd2maxFileTicks shotFile
    local done = false
    try
    (
        bmp.filename = shotFile
        save bmp
    )
    catch
    (
        mmd2maxLog ("截图 filename 写法失败：" + (getCurrentException() as string))
    )
    done = ((mmd2maxFileTicks shotFile) != before)
    if not done do
    try
    (
        save bmp fileName:shotFile
    )
    catch
    (
        mmd2maxLog ("截图 save 写法失败：" + (getCurrentException() as string))
    )
    if not done do done = ((mmd2maxFileTicks shotFile) != before)
    done
)

/* 把「当前活动视口」切到 viewKind 并按 [lo, hi] 这块世界范围取景。
   和 mmd2maxZoomFit 同一套 API，只是只作用于一个视口、且能框局部区域
   （特写：颈部 / 脚尖）。抓图前一定 completeRedraw()：探针实测不重绘时
   gw.getViewportDib() 拿回来的是上一帧的图（v4 的 12 号图和 02 号图字节级相同）。 */
fn mmd2maxFitView viewKind lo hi =
(
    try ( viewport.setType viewKind )
    catch ( mmd2maxLog ("切视图失败：" + (getCurrentException() as string)) )
    local fitted = false
    try ( viewport.zoomToBounds off lo hi; fitted = true )
    catch ( mmd2maxLog ("取景失败：" + (getCurrentException() as string)) )
    try ( viewport.zoom 1.35 ) catch ()
    completeRedraw()
    fitted
)

fn mmd2maxShotOne outDir baseName tag viewKind lo hi =
(
    local shotFile = outDir + "\\" + baseName + "_" + tag + ".png"
    mmd2maxLog ("截图开始 " + tag + " -> " + shotFile)
    mmd2maxFitView viewKind lo hi
    /* 两条打点：上一轮 ShotOne 抛「Call needs function or class, got: undefined」，
       连「截图开始」都没落进日志。真凶是前向引用 —— 这段曾经排在 mmd2maxLog 的
       定义之前，而 MAXScript 的裸调在解析期绑定（详见文件开头的日志三兄弟）。
       打点保留：截图要靠它区分「没被调到」「视口取不到图」「落盘失败」。 */
    mmd2maxLog ("截图 " + tag + " 视口已缩放")
    local bmp = undefined
    try
    (
        bmp = gw.getViewportDib()
    )
    catch
    (
        mmd2maxLog ("截图取图失败：" + (getCurrentException() as string))
    )
    mmd2maxLog ("截图 " + tag + " 取图 class=" + (classof bmp as string))
    if bmp == undefined do
    (
        completeRedraw()
        try ( bmp = gw.getViewportDib() ) catch ()
    )
    if bmp == undefined then
    (
        mmd2maxLog ("截图取图为空（视口不可用）：" + shotFile)
        false
    )
    else
    (
        /* 调用一律用「空格分隔」形式：MAXScript 根本没有 f(a, b) 这种逗号实参表，
           写成那样是**编译期**语法错误（实测：-- Syntax error: at ,, expected <factor>），
           整份脚本一行都不跑。单参数的 f(x) 合法，因为那对括号只是分组。 */
        local okShot = mmd2maxSaveDib bmp shotFile
        mmd2maxLog ("截图 " + tag + " 落盘=" + (okShot as string) + " -> " + shotFile)
        okShot
    )
)

fn mmd2maxScreenshot =
(
    /* 截图目录只来自 MMD2MAX_SHOTDIR（生成脚本写的 <导出目录>\shots）；
       取不到就落在 .max 同级的 shots —— 绝不回退到任何写死的个人目录。 */
    local outDir = ""
    try ( outDir = (MMD2MAX_SHOTDIR as string) ) catch ( outDir = "" )
    if outDir == "" do
    (
        local maxPath = ""
        try ( maxPath = MMD2MAX_OUTPUT as string ) catch ( maxPath = "" )
        if maxPath != "" do outDir = (getFilenamePath maxPath) + "shots"
    )
    if outDir == "" do
    (
        mmd2maxLog "无可用截图目录（MMD2MAX_SHOTDIR 与 MMD2MAX_OUTPUT 都取不到），跳过截图。"
        return false
    )
    /* 文件名跟模型走，不写死某个角色的名字 */
    local baseName = "mmd2max"
    try
    (
        local p = MMD2MAX_OUTPUT as string
        if p != "" do baseName = getFilenameFile p
    )
    catch ( baseName = "mmd2max" )
    try ( makeDir outDir ) catch ()
    /* 目录到底在不在，用 doesFileExist 实测，绝不靠「makeDir 没报错」推断。 */
    local dirReady = false
    try
    (
        if doesFileExist (outDir + "\\") do dirReady = true
    )
    catch
    (
        mmd2maxLog ("截图目录探测失败：" + (getCurrentException() as string))
    )
    if not dirReady do mmd2maxLog ("截图目录不存在，跳过：" + outDir)
    if dirReady do
    (
        /* 每张单独包一层 try：上一版整段共用一个 try/catch，正视图的图已经落盘了，
           一个错误却把顶视图连同「落盘=」日志一起带走（实测：内层 catch 的
           「截图异常：」压根没出现，只有外层 mmd2maxMain 的兜底日志），
           定位不到是哪一步炸的。拆开才能既保住证据又拿到线索。
           调用形式必须是「空格分隔」的裸参表：MAXScript 没有 f(a, b) 逗号写法，
           写成逗号是编译期语法错误（实测 -- Syntax error: at ,, expected <factor>）。 */
        local b = mmd2maxSceneBox()
        local h = b.max.z - b.min.z
        /* 特写区域按包围盒高度取比例，换角色不用改：整场景一张图判不了细节 ——
           颈部落位（#17）与躯干倾角（#27）要看 0.55~0.98 这一段（从腰往上全覆盖，
           0.72 起框会把 Pelvis/Spine1 裁掉，用户说「Spine2 没后仰」时画面里没有它），
           Toe0 尺寸（#19）要看脚下 0~0.14 这一段。
           侧视是硬性要求：挺胸、脚尖前后向、躯干后仰都只有侧面看得出来。 */
        local neckLo = [b.min.x - 10.0, b.min.y - 10.0, b.min.z + h * 0.55]
        local neckHi = [b.max.x + 10.0, b.max.y + 10.0, b.min.z + h * 0.98]
        local footLo = [b.min.x - 10.0, b.min.y - 10.0, b.min.z - 2.0]
        local footHi = [b.max.x + 10.0, b.max.y + 10.0, b.min.z + h * 0.14]
        /* 俯视特写的取景框（#26）：脚骨偏航在正视图里被透视吃掉大半 —— 踝→趾这 11 度
           的水平分量投影下来只剩十几像素，用户那张截图就是这么被"看着不对、又说不清"的。
           只有正上方那张能一眼判「脚朝外还是朝中线」。取景按两只脚 + 两个脚趾骨的实际
           落位算，四周只扩 6cm 把鞋的轮廓带进画面 —— 扩到 15cm 时视口按 1.6 的宽高比
           自动加宽，整条手臂（±38cm）会被拉回画面，脚只剩中间几个像素（08:59 那轮实测）。
           找不到 Biped 节点就退回整宽度。
           同一只框现在也管 foot_front / foot_side（#37 的教训）：那两张原先按**全身**
           X/Y 边界取景，整只鞋在 984px 的画面里只占 100px（≈7.4px/cm），
           4.7cm 的骨盒错位画面上就是十几像素 —— round-10 的「脚穿模」在图上看不动，
           只能靠回读数字。按四只脚骨节点收紧到 ±6cm 后同一张约 22px/cm，翻三倍。 */
        local footPts = #()
        for nm in #("Bip001 L Foot", "Bip001 R Foot", "Bip001 L Toe0", "Bip001 R Toe0") do
        (
            local n = getNodeByName nm exact:true
            if n != undefined do append footPts n.transform.translationpart
        )
        local topLo = [b.min.x, b.min.y, b.min.z]
        local topHi = [b.max.x, b.max.y, b.min.z + h * 0.14]
        if footPts.count >= 2 do
        (
            local lo = footPts[1]
            local hi = footPts[1]
            for p in footPts do
            (
                lo = [amin lo.x p.x, amin lo.y p.y, amin lo.z p.z]
                hi = [amax hi.x p.x, amax hi.y p.y, amax hi.z p.z]
            )
            topLo = [lo.x - 6.0, lo.y - 6.0, b.min.z]
            topHi = [hi.x + 6.0, hi.y + 6.0, b.min.z + h * 0.14]
            footLo = [lo.x - 6.0, lo.y - 6.0, b.min.z - 2.0]
            footHi = [hi.x + 6.0, hi.y + 6.0, b.min.z + h * 0.14]
        )
        /* 取景框本身要写日志：08:59 那张 foot_top 把整条手臂都框进来了，
           分不清是「节点没查到、退回整宽度」还是「框算对了但被视口宽高比撑开」。 */
        mmd2maxLog ("foot_top 取景：脚骨节点=" + (footPts.count as string) \
            + " 框=" + ((topLo as string)) + ".." + ((topHi as string)) \
            + " 侧/正特写框=" + ((footLo as string)) + ".." + ((footHi as string)))
        /* 手部特写（#27）：手指不贴合结构是「骨向 vs 链向」的差，只有贴着手的
           近景看得出来；整场景那张里手指只有几个像素。取景按左手 + 左手指的
           实际落位算，只扩 6cm。 */
        /* 判「是不是节点」绝不能用 isNode —— 09:37 实测 `-- Type error: Call needs
           function or class, got: undefined`（MAXScript 没有这个函数），整段取景被
           catch 掉、手指节点=0，hand_front/hand_side 退化成全身图。
           另外 **getNodeByName 不支持通配符**：10:17 那轮 `getNodeByName "Bip001 L Finger*"`
           直接返回 undefined（手指节点=1 就是只剩 Hand 那一个点），框因此退回整场景边界，
           两张手部特写又等于没拍。要按名字模式收节点只能自己遍历 objects。 */
        local handPts = #()
        local fingerNodes = #()
        local bipCnt = 0
        try
        (
            local hn = getNodeByName "Bip001 L Hand" exact:true
            if hn != undefined do append handPts hn.transform.translationpart
            /* matchPattern 的第二个参数在 Max 自带脚本里一律写成 `pattern:"*"`
               （BsOpenTools.ms:72、MotionReverse_Tool.ms:165），10:34 用位置实参传
               进去得到 手指命中=0 —— 要么绑定不对要么静默抛错，所以照抄带标签的写法，
               并且**不再逐根 try catch**（异常交给外层，日志里能看到真实报错文字）。
               Biped 节点总数一并打出来：命中 0 而总数 >0 才是模式的问题，总数也 0 就是
               classOf 判据的问题。 */
            for n in objects do
            (
                if (classOf n) == Biped_Object do
                (
                    bipCnt += 1
                    if (matchPattern n.name pattern:"Bip001 L Finger*") do append fingerNodes n
                )
            )
            for n in fingerNodes do append handPts n.transform.translationpart
        )
        catch ( mmd2maxLog ("手部取景取点异常：" + (getCurrentException() as string)) )
        local handLo = [b.min.x, b.min.y, b.min.z]
        local handHi = [b.max.x, b.max.y, b.max.z]
        if handPts.count >= 2 do
        (
            local hlo = handPts[1]
            local hhi = handPts[1]
            for p in handPts do
            (
                hlo = [amin hlo.x p.x, amin hlo.y p.y, amin hlo.z p.z]
                hhi = [amax hhi.x p.x, amax hhi.y p.y, amax hhi.z p.z]
            )
            handLo = [hlo.x - 6.0, hlo.y - 6.0, hlo.z - 6.0]
            handHi = [hhi.x + 6.0, hhi.y + 6.0, hhi.z + 6.0]
        )
        /* 三个数分开打：`Biped节点=0` = classOf 判据不对；命中 0 而 Biped 节点 >0 = 名字
           模式没匹配上；命中正常但 `取景点数` 只有 1 = 只找到了 Hand。 */
        mmd2maxLog ("hand 取景：Biped节点=" + (bipCnt as string) \
            + " 手指命中=" + (fingerNodes.count as string) \
            + " 取景点数=" + (handPts.count as string) \
            + " 框=" + ((handLo as string)) + ".." + ((handHi as string)))
        mmd2maxPrepareShot()
        try ( mmd2maxShotOne outDir baseName "front" #view_front b.min b.max ) catch ( mmd2maxLog ("front 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "side" #view_left b.min b.max ) catch ( mmd2maxLog ("side 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "top" #view_top b.min b.max ) catch ( mmd2maxLog ("top 截图异常：" + (getCurrentException() as string)) )
        /* 网格藏掉、只留骨架：和上面三张对照，才能判「骨架对上网格轮廓」没有 */
        try ( mmd2maxSetMeshHidden true ) catch ( mmd2maxLog ("藏网格异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "bone_front" #view_front b.min b.max ) catch ( mmd2maxLog ("bone_front 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "bone_side" #view_left b.min b.max ) catch ( mmd2maxLog ("bone_side 截图异常：" + (getCurrentException() as string)) )
        /* 脚部也要藏网格的一套（#37）：foot_side / foot_front 里骨盒被鞋面挡住，
           只能靠「比例尺从图上反推 + 两个盒子的相对距离」判方向，估错一次就是一轮。
           同一只收紧过的 footLo/footHi 再各拍一张只剩骨架的，两张对照就能直接读出
           「那一坨到底是不是这根骨、它在鞋轮廓里还是外面」。
           foot_top 更要这一张：本轮实测取景框只到 z=19.67，但 Max 的 zoom 不裁剪
           高度，裙子就在脚的正上方，投影下来把两只鞋整个糊住 —— 俯视那张已经判不了脚。 */
        try ( mmd2maxShotOne outDir baseName "bone_foot_side" #view_left footLo footHi ) catch ( mmd2maxLog ("bone_foot_side 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "bone_foot_front" #view_front footLo footHi ) catch ( mmd2maxLog ("bone_foot_front 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "bone_foot_top" #view_top topLo topHi ) catch ( mmd2maxLog ("bone_foot_top 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxSetMeshHidden false ) catch ( mmd2maxLog ("显网格异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "neck_side" #view_left neckLo neckHi ) catch ( mmd2maxLog ("neck_side 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "neck_front" #view_front neckLo neckHi ) catch ( mmd2maxLog ("neck_front 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "foot_side" #view_left footLo footHi ) catch ( mmd2maxLog ("foot_side 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "foot_front" #view_front footLo footHi ) catch ( mmd2maxLog ("foot_front 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "foot_top" #view_top topLo topHi ) catch ( mmd2maxLog ("foot_top 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "hand_front" #view_front handLo handHi ) catch ( mmd2maxLog ("hand_front 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "hand_side" #view_left handLo handHi ) catch ( mmd2maxLog ("hand_side 截图异常：" + (getCurrentException() as string)) )
        mmd2maxRestoreShot()
        true
    )
)

fn mmd2maxPrepareScene =
(
    local doReset = true
    /* 只在「这一轮有人盯着屏幕」时问：自动跑（obj_run 默认）点不掉 queryBox，
       弹窗会把整轮挂在无人值守的 Max 上 —— 所以 MMD2MAX_INTERACTIVE 必须是 false。
       反过来 --manual / --gen-only 是人在场，场景里可能正存着用户自己的活儿，
       resetMaxFile 不问一句就是把它清空。 */
    if (mmd2maxIsInteractive()) and objects.count > 0 do
    (
        doReset = queryBox "脚本需要重置场景才能导入模型，是否继续？" title:"mmd2max"
    )
    if not doReset do throw "已取消。"
    resetMaxFile #noPrompt
    mmd2maxLog "场景已重置。"
)

/* MaxScript 没有内建 split，这里按单个分隔符切分 */
fn mmd2maxSplit text sep =
(
    local parts = #()
    local current = ""
    for i = 1 to text.count do
    (
        local ch = substring text i 1
        if ch == sep then
        (
            append parts current
            current = ""
        )
        else current = current + ch
    )
    append parts current
    parts
)

/* ================= 带表头的 TSV 读取（M1：跨语言线格式自描述）=================
   约定由 mmd2max/export_obj.py 的 _header_line / _kind_header 写出：
     第 1 行            = tab 分隔的列名；取数按列名，不再靠 p[12] 这种魔数下标。
     以 # 开头的行       = 给人看的注释，跳过。
     @<记录名>\t列名…    = 同一文件里**另一种记录**的列名（physics 的 JOINT、morphs 的 V）。
         这两种记录的同一列号含义不同（physics 第 4 列在 RIGID 是 rigid_name_en、在 JOINT
         是 joint_type），一行表头装不下；这些名字并进同一张列名表，各带 rigid_/joint_
         前缀，不会和主记录撞名。
   返回 #(列名, 列号, 数据行)：「数据行」每行是 mmd2maxSplit 出来的字段数组。
   为什么这里用 mmd2maxSplit 而不是 filterString：filterString 会**丢掉空字段**，
   一旦某列留空（骨名为空串就会发生），后面所有列号整体左移一位，读回来的数看着正常、
   其实是隔壁列的值。mmd2maxSplit 保留空字段，列号永远对得上表头。 */
fn mmd2maxReadTable path =
(
    local names = #()
    local colPos = #()
    local rows = #()
    if path == undefined or path == "" do return #(names, colPos, rows)
    local f = openFile path encoding:#utf8
    if f == undefined do throw ("无法打开数据文件：" + (path as string))
    local lineNo = 0
    while not (eof f) do
    (
        local line = readLine f
        lineNo += 1
        if line != undefined and line.count > 0 do
        (
            local fields = mmd2maxSplit line "\t"
            local head = fields[1]
            local first = if head.count > 0 then head[1] else ""
            local isSchema = lineNo == 1 or first == "@"
            if isSchema do
            (
                /* 主表头从第 1 个字段起对齐；`@KIND` 行的第 1 个字段是记录名本身，
                   所以它的列名从第 2 个字段起对齐 */
                local startAt = 1
                if lineNo > 1 do startAt = 2
                for i = startAt to fields.count do
                (
                    append names fields[i]
                    append colPos i
                )
            )
            if (not isSchema) and (first != "#") do append rows fields
        )
    )
    close f
    #(names, colPos, rows)
)

fn mmd2maxRows tbl = tbl[3]

fn mmd2maxColPos tbl colName =
(
    /* 列名 -> 行内字段号（1-based）。**缺列就抛**，不许静默返回 undefined：
       undefined 转成 float 是 0，日志里照样「成功」，这正是本项目反复踩的失败类别
       （骨盆改写静默失效、Toe0 静默 0 个顶点，都是这一类）。 */
    local found = findItem tbl[1] colName
    if found == 0 do
    (
        local msg = "数据文件缺列 " + (colName as string) + "，实际表头=" + (tbl[1] as string)
        mmd2maxLog msg
        throw msg
    )
    tbl[2][found]
)

fn mmd2maxCol tbl rowIndex colName =
(
    /* 第 rowIndex 行的 colName 列（字符串）。
       行号越界 = 表和行号对不上，抛错；
       行比该列短 = 变长尾列（limbs 只有 mode=foot 的行才有第 10~18 列），返回 ""，
       调用方必须先看过行宽再取 —— 这条不会静默，因为短行本来就只有尾列会缺。 */
    local rows = tbl[3]
    if rowIndex < 1 or rowIndex > rows.count do
        throw ("数据行号越界：" + (rowIndex as string))
    local pos = mmd2maxColPos tbl colName
    local fields = rows[rowIndex]
    if pos > fields.count then "" else fields[pos]
)

/* ================= 配置单源（M1b）=================
   装配需要的每一项输入路径与开关，都来自 <输出目录>/MMD2MAX.CFG（线格式 v2：
   第 1 行 `key<TAB>value`，键名就是下面这些 MMD2MAX_* 全局名）。写这份文件的是
   tools/obj_run.py，全场唯一一份。

   为什么不让生成脚本在头部逐行注入 global：v3 那份自动生成的 .ms 里有 22 行赋值，
   于是「自动跑的」和「人手 fileIn 这份模板跑的」是两套配置来源 —— 手跑时少写一个
   flag（MMD2MAX_FOOTPITCH 就是现成的例子）会摆出**不同的几何**，而两边日志照样全绿。
   现在手跑只需要设 MMD2MAX_CFG 一个全局变量，其余全从同一个文件读，两条路径不可能分叉。

   这里**没有任何默认值**：缺文件、缺键、值不是 true/false 或不是整数，一律抛错中止。
   默认值就是那条「日志绿、场景错」的路。 */
global MMD2MAX_CFG            /* 唯一需要外部注入的变量：CFG 文件路径 */
global MMD2MAX_CFG_TBL = undefined

fn mmd2maxCfgStr key =
(
    if MMD2MAX_CFG_TBL == undefined do
        throw "还没读 CFG 就取配置了（mmd2maxLoadCfg 必须排在所有装配步骤之前）"
    local rows = mmd2maxRows MMD2MAX_CFG_TBL
    local cKey = mmd2maxColPos MMD2MAX_CFG_TBL "key"
    local cVal = mmd2maxColPos MMD2MAX_CFG_TBL "value"
    for row in rows do
    (
        if row.count >= cVal and row[cKey] == key do return row[cVal]
    )
    throw ("MMD2MAX.CFG 里没有键 " + (key as string) + "（配置单源：这里不许有默认值）")
)

fn mmd2maxCfgBool key =
(
    /* 只认 "true"/"false" 两个字面值。写成 1/0、yes/no、True 的一律当错误抛掉：
       静默退成 false 就等于悄悄改了装配口径。 */
    local s = mmd2maxCfgStr key
    if s == "true" do return true
    if s == "false" do return false
    throw ("CFG 键 " + (key as string) + " 的值不是 true/false：" + (s as string))
)

fn mmd2maxCfgInt key low high =
(
    /* 整数值 + 取值范围一起验。范围写在装配侧这一处就够了：Python 侧
       （semantic）负责算出合理值，这里负责拒绝任何越界/非数字的配置。
       变量名别用 `ok`：那是 MAXScript 保留字，`local ok = ...` 直接编译失败（#20 同款）。 */
    local s = mmd2maxCfgStr key
    local digits = #("0","1","2","3","4","5","6","7","8","9")
    local isNumber = s.count > 0
    for i = 1 to s.count while isNumber do
    (
        local ch = substring s i 1
        local good = ((findItem digits ch) > 0)
        if i == 1 and ch == "-" do good = true
        if not good do isNumber = false
    )
    if not isNumber do
    (
        local msg = "CFG 键 " + (key as string) + " 的值不是整数："
        msg = msg + (s as string)
        throw msg
    )
    local value = s as integer
    if value < low or value > high do
    (
        local msg = "CFG 键 " + (key as string) + " 的值 " + (value as string)
        msg = msg + " 超出允许范围 [" + (low as string) + "," + (high as string) + "]"
        throw msg
    )
    value
)

fn mmd2maxCfgNum key low high =
(
    /* 浮点配置（容差那一批）。为什么不能直接 `s as float` 了事：Max 里
       "abc" as float 不抛错、给出 0 —— 0 度容差会让每根骨都报超标，看着像「判据生效」，
       实际是配置没读到，和 M1b 干掉的那类静默默认值是同一个形状。
       校验：数字 + 至多一个小数点 + 可选负号，越界照抛。
       上下限由 mmd2max/targets.py 声明、这里逐字重复一遍，tests/test_targets.py 钉住两边一致。 */
    local s = mmd2maxCfgStr key
    local digits = #("0","1","2","3","4","5","6","7","8","9")
    local isNumber = s.count > 0
    local dots = 0
    for i = 1 to s.count while isNumber do
    (
        local ch = substring s i 1
        local good = ((findItem digits ch) > 0)
        if not good and ch == "." do ( good = true; dots += 1 )
        if i == 1 and ch == "-" do good = true
        if dots > 1 do good = false
        if not good do isNumber = false
    )
    if not isNumber do
    (
        local msg = "CFG 键 " + (key as string) + " 的值不是数字："
        msg = msg + (s as string)
        throw msg
    )
    local value = s as float
    if value < low or value > high do
    (
        local msg = "CFG 键 " + (key as string) + " 的值 " + (value as string)
        msg = msg + " 超出允许范围 [" + (low as string) + "," + (high as string) + "]"
        throw msg
    )
    value
)

/* 读 CFG 并把每一项写进对应的全局变量。调用点只有一个：mmd2maxMain 的第一行。
   之后各 pass 只读这些 global —— 读不到值就是这里没配齐，会抛而不是用默认值。 */
fn mmd2maxLoadCfg =
(
    local path = ""
    try ( path = MMD2MAX_CFG as string ) catch ( path = "" )
    if path == "" do
        throw "没设 MMD2MAX_CFG。装配的全部配置来自 MMD2MAX.CFG：只注入这一个全局变量即可（见 README 第 4 节）。"
    if not (doesFileExist path) do throw ("配置文件不存在：" + (path as string))
    MMD2MAX_CFG_TBL = mmd2maxReadTable path
    if (mmd2maxRows MMD2MAX_CFG_TBL).count == 0 do
        throw ("配置文件是空的（没有数据行）：" + (path as string))
    MMD2MAX_OBJ = mmd2maxCfgStr "MMD2MAX_OBJ"
    MMD2MAX_BONES = mmd2maxCfgStr "MMD2MAX_BONES"
    MMD2MAX_WEIGHTS = mmd2maxCfgStr "MMD2MAX_WEIGHTS"
    MMD2MAX_MAP = mmd2maxCfgStr "MMD2MAX_MAP"
    MMD2MAX_LIMBS = mmd2maxCfgStr "MMD2MAX_LIMBS"
    MMD2MAX_WIDTHS = mmd2maxCfgStr "MMD2MAX_WIDTHS"
    MMD2MAX_MORPHS = mmd2maxCfgStr "MMD2MAX_MORPHS"
    MMD2MAX_PHYSICS = mmd2maxCfgStr "MMD2MAX_PHYSICS"
    MMD2MAX_OUTPUT = mmd2maxCfgStr "MMD2MAX_OUTPUT"
    MMD2MAX_MARKER = mmd2maxCfgStr "MMD2MAX_MARKER"
    MMD2MAX_LOG = mmd2maxCfgStr "MMD2MAX_LOG"
    /* 判据通道（M2b）：VERDICTS 是 Python 写的规格表（Max 只把路径打进日志留证据，
       **不读它的内容** —— 读了就等于让被验收的一方拿到了判据）；REPORT 是 Max 追加
       读数的地方。两个路径都必需非空：没有 REPORT 就没法验收这一轮，不许静默跳过。 */
    MMD2MAX_VERDICTS = mmd2maxCfgStr "MMD2MAX_VERDICTS"
    MMD2MAX_REPORT = mmd2maxCfgStr "MMD2MAX_REPORT"
    MMD2MAX_SHOTDIR = mmd2maxCfgStr "MMD2MAX_SHOTDIR"
    /* 空串只允许出现在「这个阶段可以整段跳过」的四项上（WIDTHS/MORPHS/PHYSICS/SHOTDIR）；
       其余路径为空 = Max 一定打不开，在这里抛，比在导入器里报一句看不懂的错早一整轮。
       LIMBS 属于必需项：没有肢段表就没有闭环缩放，Biped 会退回默认人体比例。
       这份名单与 tools/obj_run.py 的 CFG_EMPTY_OK 必须一致。 */
    for pair in #( #("MMD2MAX_OBJ", MMD2MAX_OBJ), #("MMD2MAX_BONES", MMD2MAX_BONES),
                   #("MMD2MAX_WEIGHTS", MMD2MAX_WEIGHTS), #("MMD2MAX_MAP", MMD2MAX_MAP),
                   #("MMD2MAX_LIMBS", MMD2MAX_LIMBS),
                   #("MMD2MAX_OUTPUT", MMD2MAX_OUTPUT), #("MMD2MAX_MARKER", MMD2MAX_MARKER),
                   #("MMD2MAX_LOG", MMD2MAX_LOG),
                   #("MMD2MAX_VERDICTS", MMD2MAX_VERDICTS),
                   #("MMD2MAX_REPORT", MMD2MAX_REPORT) ) do
    (
        if pair[2] == "" do throw ("CFG 里 " + (pair[1] as string) + " 是空路径")
    )
    MMD2MAX_VERTS = mmd2maxCfgInt "MMD2MAX_VERTS" 1 100000000
    MMD2MAX_SPINE_LINKS = mmd2maxCfgInt "MMD2MAX_SPINE_LINKS" 1 5
    MMD2MAX_FINGERS = mmd2maxCfgInt "MMD2MAX_FINGERS" 1 5
    MMD2MAX_FINGER_LINKS = mmd2maxCfgInt "MMD2MAX_FINGER_LINKS" 1 3
    MMD2MAX_FOOTBOXFLAT = mmd2maxCfgBool "MMD2MAX_FOOTBOXFLAT"
    /* 收敛遍数上限：值来自 mmd2max/passes.py 的注册表（MMD2MAX_CONVERGE_MAX）。
       上限 9 是留给「真需要多跑两遍」的余量；下限 1：0 遍的循环根本不该存在，
       而撞上限这件事本身要能被校验器复现，所以这里必须是个可核对的整数。 */
    MMD2MAX_CONVERGE_MAX = mmd2maxCfgInt "MMD2MAX_CONVERGE_MAX" 1 9
    /* 装配目标值（M2 起全部住在 mmd2max/targets.py，条数会随功能长，所以这里不写死数字）：
       范围与 TARGETS 元组逐字对应，
       由 tests/test_targets.py 钉住。这里不给默认值 —— 容差没读到就中止，
       比「按 0 度容差把每根骨都判成超标」或者「按上一轮的 5 度悄悄放行」都可诊断得多。
       9999 那个上限是回滚位：DIRTOL/ROLLTOL/TOETOL 同时是「比它小就别动这根骨」的门控，
       写 999 = 整段不动；上限压到 90 会让回滚操作自己在读 CFG 第一步就抛。 */
    MMD2MAX_DIRTOL = mmd2maxCfgNum "MMD2MAX_DIRTOL" 0.0 9999.0
    MMD2MAX_ROLLTOL = mmd2maxCfgNum "MMD2MAX_ROLLTOL" 0.0 9999.0
    MMD2MAX_TOETOL = mmd2maxCfgNum "MMD2MAX_TOETOL" 0.0 9999.0
    MMD2MAX_GAPTOL = mmd2maxCfgNum "MMD2MAX_GAPTOL" 0.0 50.0
    MMD2MAX_FOOTREACH = mmd2maxCfgBool "MMD2MAX_FOOTREACH"
    MMD2MAX_ROLLBACK = mmd2maxCfgBool "MMD2MAX_ROLLBACK"
    MMD2MAX_RBTRIG = mmd2maxCfgNum "MMD2MAX_RBTRIG" 0.0 50.0
    MMD2MAX_SKIN = mmd2maxCfgBool "MMD2MAX_SKIN"
    MMD2MAX_SHOT = mmd2maxCfgBool "MMD2MAX_SHOT"
    MMD2MAX_INTERACTIVE = mmd2maxCfgBool "MMD2MAX_INTERACTIVE"
    /* 分步拼接，不用行首 `+` 的跨行表达式：MAXScript 会把这种续行当成新语句。 */
    local cfgMsg = "配置来自 " + (path as string)
    cfgMsg = cfgMsg + "（" + ((mmd2maxRows MMD2MAX_CFG_TBL).count as string) + " 项）"
    cfgMsg = cfgMsg + " 顶点 " + (MMD2MAX_VERTS as string)
    cfgMsg = cfgMsg + " 脊柱 " + (MMD2MAX_SPINE_LINKS as string)
    cfgMsg = cfgMsg + " 手指 " + (MMD2MAX_FINGERS as string) + "x" + (MMD2MAX_FINGER_LINKS as string)
    cfgMsg = cfgMsg + " 脚盒压平轴=" + (MMD2MAX_FOOTBOXFLAT as string)
    cfgMsg = cfgMsg + " 蒙皮=" + (MMD2MAX_SKIN as string)
    cfgMsg = cfgMsg + " 方向容差=" + (MMD2MAX_DIRTOL as string)
    cfgMsg = cfgMsg + " 扭转容差=" + (MMD2MAX_ROLLTOL as string)
    cfgMsg = cfgMsg + " 趾落位容差=" + (MMD2MAX_TOETOL as string)
    cfgMsg = cfgMsg + " 盒隙容差=" + (MMD2MAX_GAPTOL as string)
    cfgMsg = cfgMsg + " 脚链拉伸=" + (MMD2MAX_FOOTREACH as string)
    cfgMsg = cfgMsg + " 快照回滚=" + (MMD2MAX_ROLLBACK as string)
    cfgMsg = cfgMsg + " 回滚触发=" + (MMD2MAX_RBTRIG as string)
    cfgMsg = cfgMsg + " 收敛上限=" + (MMD2MAX_CONVERGE_MAX as string)
    /* 判据通道两个路径进摘要：日志里要能看出「这轮按哪张规格表验收、读数落在哪个文件」，
       否则事后只剩一份日志、拼不出任何判定。 */
    cfgMsg = cfgMsg + " 规格=" + (MMD2MAX_VERDICTS as string)
    cfgMsg = cfgMsg + " 报告=" + (MMD2MAX_REPORT as string)
    /* 这条不能在这里 mmd2maxLog：mmd2maxInitLog 用 createFile 重建日志，比它晚，
       于是「配置来自 …」这一行以前每次都被自己截掉 —— 第十九轮核对时才发现日志里
       只有 InitLog 那几行，判据读数一句都看不见。改成存起来，由 mmd2maxMain 在
       InitLog 之后打，落进日志才算有证据。 */
    MMD2MAX_CFGSUMMARY = cfgMsg
    true
)

fn mmd2maxImportObj =
(
    mmd2maxLog ("导入 OBJ：" + (MMD2MAX_OBJ as string))
    /* 锁定 OBJ 导入选项（审查项 P1#17）：禁用 Weld，保证顶点数与 Python 侧一致、
       顶点顺序稳定，否则权重按顶点序号写入会错位。FlipZAxis 等坐标轴选项保持
       Max 当前设置，但必须实地验证一次（见脚本末尾的哨兵说明）。 */
    try
    (
        OBJIMP.setWeld false
        OBJIMP.setWeldThreshold 0.0
        mmd2maxLog "已设置 OBJ 导入器：Weld=false（保留顶点数/顺序）。"
    )
    catch ( mmd2maxLog "无法修改 OBJ 导入器选项，沿用 Max 当前设置（请确认未启用 Weld）。" )

    disableSceneRedraw()
    local started = timeStamp()
    local importOK = false
    /* catch 表达式内只允许不带参数的 throw，因此先在 catch 里保存异常，
       出 catch 之后再重新抛出。 */
    local caught = undefined
    try ( importOK = importFile MMD2MAX_OBJ #noPrompt using:OBJIMP )
    catch ( caught = getCurrentException() )
    enableSceneRedraw()
    if caught != undefined do
    (
        mmd2maxLog ("OBJ 导入抛错：" + caught)
        throw caught
    )
    local elapsed = ((timeStamp() - started) / 1000.0) as string
    mmd2maxLog ("OBJ 导入耗时：" + elapsed + " 秒，场景对象数 " + (objects.count as string))
    if not importOK do throw ("OBJ 导入失败（importFile 返回 false）：" + (MMD2MAX_OBJ as string))
    if objects.count == 0 do throw "OBJ 导入后场景为空。"

    /* 材质自检：确认 mtllib / map_Kd 真的被导入器接住了。
       早期贴图丢失的根因是 MTL 文件名与贴图路径含日文，导入器按 ANSI(GBK) 读成乱码
       → 材质在、贴图不在，模型就是一片灰。现在导出端统一改成 ASCII 名 + 相对路径，
       这里把结果打出来，一眼能判断是「没导入材质」还是「贴图路径解析失败」。
       注意：变量名不能用 MAXScript 保留字 —— `mapped` / `fn` 都是保留字，
       写成 `local mapped = 0` 会直接编译失败（用户截图 line 292）。 */
    try
    (
        local meshObj = undefined
        for o in objects where (superClassOf o == GeometryClass) do
            if meshObj == undefined do meshObj = o
        if meshObj == undefined or meshObj.material == undefined then
            mmd2maxLog "导入后网格没有材质（MTL 没被读到，贴图不会出现）。"
        else
        (
            local theMat = meshObj.material
            local mapN = 0
            local subN = 1
            if (classOf theMat) == Multimaterial then
            (
                subN = theMat.numsubs
                for k = 1 to theMat.numsubs do
                (
                    local sm = theMat[k]
                    if sm != undefined and sm.diffusemap != undefined do mapN += 1
                )
            )
            else if theMat.diffusemap != undefined do mapN = 1
            local msg = "导入后材质：" + theMat.name
            msg = msg + "，子材质 " + (subN as string) + " 个，带贴图 " + (mapN as string) + " 个"
            mmd2maxLog msg
            /* 抽第一个贴图，验证路径真的能打开 */
            local firstMap = undefined
            try
            (
                if (classOf theMat) == Multimaterial then
                (
                    for k = 1 to theMat.numsubs while firstMap == undefined do
                        if theMat[k] != undefined and theMat[k].diffusemap != undefined do
                            firstMap = theMat[k].diffusemap
                )
                else firstMap = theMat.diffusemap
            )
            catch ()
            if firstMap != undefined do
            (
                local mapFile = "?"
                try ( mapFile = firstMap.filename ) catch ( mapFile = "?" )
                local fileOK = false
                try ( fileOK = doesFileExist mapFile ) catch ( fileOK = false )
                mmd2maxLog ("贴图示例：" + mapFile + " 存在=" + (fileOK as string))
            )
        )
    )
    catch ( mmd2maxLog ("材质自检失败：" + (getCurrentException())) )

    /* v3：这里**不再有任何网格补偿旋转**。
       手性（MMD 左手系 → Max 右手系必然含一次镜像）与 Y-up→Z-up 的换算全部在
       Python 侧定死：OBJ 写 (x, y, -z) 并翻一次绕序，Max 的 OBJ 导入器按
       (a,b,c)->(a,-c,b) 转完，网格就正好落在骨骼用的 convert_vec(x, z, y) 上。
       v2 在这里绕世界 Z 转 180° 来对齐骨骼，代价是整张图成了 MMD 的镜像
       （左右手反），只能再在 semantic 里交换 L/R 骨名掩盖。现在两处一起改掉了。 */
)

/* 收集所有可蒙皮的网格对象 */
fn mmd2maxMeshes =
(
    local result = #()
    for obj in objects do
    (
        local cls = classOf obj
        if (cls == Editable_Mesh) or (cls == Editable_Poly) do
        (
            append result obj
            continue
        )
        /* 骨骼（BoneGeometry）与 Biped 节点同样满足 canConvertTo Editable_Mesh，
           必须显式排除。本函数在「建骨之后」调用，若不排除会把几百根骨骼/Biped
           节点一起算成网格（实跑踩到：报「产生了 446 个网格对象」而中止）。 */
        if cls == BoneGeometry do continue
        try ( if (classOf obj.controller) == Biped_Control do continue ) catch ()
        if cls == Biped_Object do continue
        if (canConvertTo obj Editable_Mesh) do append result obj
    )
    result
)

/* ================= Biped + 附属骨 =================
   人形骨 -> Biped 节点；衣服 / 头发 / 裙摆等附属骨 -> Bone。这是唯一支持的
   装配方式：纯 Bone 骨架在 v3 已删除（v2 的 MMD2MAX_MODE="bone" 分支），
   因为目标就是让角色可直接用 Max 的 Biped 步态/表演工具驱动。

   关键设计：boneNodes 的索引语义保持不变（索引 = 全局骨序号），
   只改变「该位置的节点从哪来」。因此 Skin 添加、权重写入、
   globalToSkin 映射等既有逻辑完全不需要修改。 */

/* 读 biped_map.tsv，返回 #(行号数组(1-based), Biped节点名数组)。
   注意：映射表以「骨序号」为键而非骨名 —— PMX 骨名是日文，
   按本地代码页解析会乱码，导致 getNodeByName 找不到节点。
   列按表头名取（bone_index / biped_node），不再写死「第 1、2 列」。 */
fn mmd2maxReadBipedMap path =
(
    local rowIds = #()
    local targetNames = #()
    if path == undefined or path == "" do return #(rowIds, targetNames)
    if not (doesFileExist path) do return #(rowIds, targetNames)
    local tbl = mmd2maxReadTable path
    local cIdx = mmd2maxColPos tbl "bone_index"
    local cNode = mmd2maxColPos tbl "biped_node"
    for row in mmd2maxRows tbl do
    (
        /* 两层 if：MAXScript 的 and 不短路，行宽不够时先取 row[cNode] 会读到 undefined */
        if row.count >= cNode do
        if row[cNode].count > 0 do
        (
            append rowIds ((row[cIdx] as integer) + 1)
            append targetNames row[cNode]
        )
    )
    #(rowIds, targetNames)
)

fn mmd2maxSpineLinks =
(
    /* Biped 脊柱节数：必须和 Python 侧算出的映射一致。
       MMD 里「腰/下半身」常在同一坐标，去重后只剩 3 级躯干 -> 只要 2 节脊柱；
       若仍按 3 节创建，会多出一节没人用的 Bip001 Spine2。
       值来自 CFG，范围已在 mmd2maxCfgInt 验过（1..5）—— 这里不再 clamping：
       把越界的配置悄悄夹回范围内，等于替模型编一个它没有的脊柱节数。 */
    MMD2MAX_SPINE_LINKS
)

fn mmd2maxFingerCount =
(
    /* Biped 建几根手指：由 Python 侧按模型实际映射算出来（本模型只有 3 指）。
       若仍按默认 5 指建，多出来的 Finger2/Finger4 会停在 Biped 默认位置戳出手掌。 */
    MMD2MAX_FINGERS
)

fn mmd2maxFingerLinks =
(
    /* 每根手指几节：本模型每指 2 节（０/１）。 */
    MMD2MAX_FINGER_LINKS
)

fn mmd2maxFigureMode ctrl flag =
(
    /* 探针实测：Max 2022 用 controller.figureMode；该版本 biped.setMode/getMode 不存在。 */
    if (try ( ctrl.figureMode = flag; true ) catch ( false )) do return true
    false
)

/* ================= Biped 肢段长度拟合（闭环 + 轴自探测）=================

   背景（实跑踩到，且已用真实数据验证）：
   1) 目标长度不能取 PMX 骨骼的 head->tail：MMD 骨骼链里插了扭转骨
      （右腕 -> 右腕捩 -> 右ひじ），tail 会指向「腕捩」，量到 12.37；
      真实上臂长度是 右腕head -> 右ひじhead = 20.61。误差 40%，肘直接跑到模型外。
      → 现在由 Python 侧生成 <model>_limbs.tsv（父关节/子关节/真实段长），
        脚本只读表，不再自己解析骨名与索引。
   2) 缩放机制存在不确定性：biped.setTransform #scale 的「长度轴」是哪个轴、
      是绝对还是相对缩放，各版本行为不一致。这里不猜：先做**探针**实测
      「改哪个轴会让两关节间距变化」，再按实测结果闭环迭代（每次量完再算下一次），
      并把每一段的 目标/默认/缩放后/误差 全部写进日志，一次运行即可定论。 */

fn mmd2maxBipedScale node =
(
    local s = [1.0, 1.0, 1.0]
    try
    (
        local v = biped.getTransform node #scale
        if v != undefined do s = v
    )
    catch ()
    s
)

fn mmd2maxSegLen a b =
(
    /* Biped 节点没有 .pos，位置要从 transform 的平移分量取 */
    distance (a.transform.translationpart) (b.transform.translationpart)
)

fn mmd2maxReadLimbs path =
(
    /* 读 limbs.tsv：
       parent_target  child_target  desired_len  mmd_parent  mmd_child  child_x child_y child_z  mode
       下列 child_x~child_z 是子关节（head）在 Max 世界坐标里的位置，**每一行都有**：
       骨盆改写要拿左右腿根这两列算 COM 锚点（见 mmd2maxHipAnchor）。
       mode=link 是普通段；mode=foot 再多 9 列（foot_len 踝→鞋尖、toe_len 脚掌→鞋尖(沿趾)、
       tip_x/tip_y/tip_z 鞋尖坐标、toe_w/toe_h 脚尖处宽高、foot_w/foot_h 踝处足背宽高）；
       mode=leg（#36）不带附加列，它标记的是「大腿→小腿」这一行：child 位置就是 PMX 的膝，
       mmd2maxLegRoll 拿它和实测膝比方位。列数不足的行按老格式（只有前 3 列）读，childPos 为 undefined。
       列号一律从表头名解析（mmd2maxReadTable + mmd2maxColPos），缺列就抛 ——
       原先写死的 parts[6]/parts[15] 这类下标，Python 侧列序一变就会静默读到隔壁列。 */
    local result = #()
    if path == undefined or path == "" do return result
    if not (doesFileExist path) do return result
    local tbl = mmd2maxReadTable path
    /* 列号在本函数开头一次取好：循环体每格都查表头的话，一轮下来白白做几万次 findItem。 */
    local cParent = mmd2maxColPos tbl "parent_target"
    local cChild = mmd2maxColPos tbl "child_target"
    local cDesired = mmd2maxColPos tbl "desired_len"
    local cChildX = mmd2maxColPos tbl "child_x"
    local cChildY = mmd2maxColPos tbl "child_y"
    local cChildZ = mmd2maxColPos tbl "child_z"
    local cMode = mmd2maxColPos tbl "mode"
    local cFootLen = mmd2maxColPos tbl "foot_len"
    local cToeLen = mmd2maxColPos tbl "toe_len"
    local cTipX = mmd2maxColPos tbl "tip_x"
    local cTipY = mmd2maxColPos tbl "tip_y"
    local cTipZ = mmd2maxColPos tbl "tip_z"
    local cToeW = mmd2maxColPos tbl "toe_w"
    local cToeH = mmd2maxColPos tbl "toe_h"
    local cFootW = mmd2maxColPos tbl "foot_w"
    local cFootH = mmd2maxColPos tbl "foot_h"
    for row in mmd2maxRows tbl do
    (
        if row.count >= cDesired do
        (
            local desired = 0.0
            try ( desired = row[cDesired] as float ) catch ( desired = 0.0 )
            if desired > 0.0001 do
            (
                local childPos = undefined
                local mode = ""
                local footLen = 0.0
                local toeLen = 0.0
                local tipPos = undefined
                if row.count >= cMode do
                (
                    try ( childPos = [(row[cChildX] as float), (row[cChildY] as float), (row[cChildZ] as float)] ) \
                    catch ( childPos = undefined )
                    mode = row[cMode]
                )
                /* 脚段额外 9 列：foot_len 踝→鞋尖、toe_len 脚掌→鞋尖(沿趾)、
                   tip_x~tip_z 鞋尖坐标、toe_w/toe_h 脚尖处宽高、foot_w/foot_h 踝处足背宽高
                   （foot_w/foot_h 是 #37：Foot 这根骨自己的截面，判据和 toe_w/toe_h 同一条，
                   只是量的对象从「脚尖那一小段」换成「踝→脚掌整根轴」。） */
                local toeW = 0.0
                local toeH = 0.0
                local footW = 0.0
                local footH = 0.0
                if row.count >= cTipZ do
                (
                    try ( footLen = row[cFootLen] as float ) catch ( footLen = 0.0 )
                    try ( toeLen = row[cToeLen] as float ) catch ( toeLen = 0.0 )
                    try ( tipPos = [(row[cTipX] as float), (row[cTipY] as float), (row[cTipZ] as float)] ) \
                    catch ( tipPos = undefined )
                )
                if row.count >= cToeH do
                (
                    try ( toeW = row[cToeW] as float ) catch ( toeW = 0.0 )
                    try ( toeH = row[cToeH] as float ) catch ( toeH = 0.0 )
                )
                if row.count >= cFootH do
                (
                    try ( footW = row[cFootW] as float ) catch ( footW = 0.0 )
                    try ( footH = row[cFootH] as float ) catch ( footH = 0.0 )
                )
                append result #(row[cParent], row[cChild], desired, childPos, mode, \
                    footLen, toeLen, tipPos, toeW, toeH, footW, footH)
            )
        )
    )
    result
)

fn mmd2maxReadWidths path =
(
    /* 读 widths.tsv：biped_target <tab> width（目标世界宽度），列按表头名取 */
    local result = #()
    if path == undefined or path == "" do return result
    if not (doesFileExist path) do return result
    local tbl = mmd2maxReadTable path
    local cTarget = mmd2maxColPos tbl "biped_target"
    local cWidth = mmd2maxColPos tbl "width"
    for row in mmd2maxRows tbl do
    (
        if row.count >= cWidth do
        (
            local w = 0.0
            try ( w = row[cWidth] as float ) catch ( w = 0.0 )
            if w > 0.001 do append result #(row[cTarget], w)
        )
    )
    result
)

fn mmd2maxVecAngleDeg a b =
(
    /* 两个向量之间的夹角（度）。acos 的入参必须夹在 [-1,1]：
       浮点误差让点积略微超出时 acos 会返回 NaN，而 NaN 一路传染到日志里
       变成「看不出问题的 0」。这里显式钳位，任何一根是零向量则返回 -1（当作没测到）。 */
    local la = length a
    local lb = length b
    if (la < 0.001) or (lb < 0.001) do return -1.0
    local c = (dot a b) / (la * lb)
    if c > 1.0 do c = 1.0
    if c < -1.0 do c = -1.0
    acos c
)

fn mmd2maxYawDiffDeg a b =
(
    /* 两个向量在水平面（XY）上的朝向差（度，落在 (-180,180]）。
       「脚朝中线偏」是绕竖直轴的偏航，用三维夹角量会被俯仰分量稀释 ——
       脚骨本身向下斜 30 度，13 度的偏航三维上只剩几度，正是上一轮「数字看着都
       对、用户看着就是不对」的原因之一。水平投影退化（接近竖直）时返回 -999：
       那是「这个方向量不出来」的显式标记，不许混进正常读数里。 */
    local la = sqrt (a.x * a.x + a.y * a.y)
    local lb = sqrt (b.x * b.x + b.y * b.y)
    if (la < 0.001) or (lb < 0.001) do return -999.0
    local d = (atan2 a.y a.x) - (atan2 b.y b.x)
    if d > 180.0 do d = d - 360.0
    if d <= -180.0 do d = d + 360.0
    d
)

fn mmd2maxAxisMoveDeg a b =
(
    /* 两次局轴读数之间最大的转角（度），取不到返回 -1.0。
       探针必须把「调用没抛错」和「骨头真的转了」分开报 —— 09:37 那轮已经证明
       Biped 可以在 pivot 全对的情况下把盒子留在原方向，所以只有局轴动才算接口通。 */
    if a == undefined or b == undefined do return -1.0
    local m = 0.0
    for k = 1 to 3 do
    (
        local d = mmd2maxVecAngleDeg a[k] b[k]
        if d > m do m = d
    )
    m
)

fn mmd2maxLeanDeg v =
(
    /* 矢状面内的倾角（度）：+ = 向 +Y（角色背后）倒，− = 向前倾。
       躯干/颈的「后仰 / 前倾」就是这个数，用三维夹角量不出来（用户 #27 的反馈：
       Spine2 该后仰 17.4°、颈该前倾 8.6°，而画出来是竖直的）。 */
    local l = sqrt (v.y * v.y + v.z * v.z)
    if l < 0.001 do return -999.0
    atan2 v.y v.z
)

fn mmd2maxWrap180 d =
(
    local r = d
    if r > 180.0 do r = r - 360.0
    if r <= -180.0 do r = r + 360.0
    r
)

/* ===== 长度轴（转角判据到底该看哪根轴）=====
   2026-11-02 第七轮的复核表把这条钉死了：`结构向 Bip001 L Finger3 轴3 夹角=0.789815`
   与 `肢段 Bip001 L Finger3 -> Bip001 L Finger31 … 轴=1` 是**互相矛盾**的两行 ——
   转角把「最顺的那根轴」对齐到了 PMX 方向，而决定子关节落在哪的长度轴被转到垂直方向。
   拿余弦定理反解真实夹角：`cos φ = (2.58958² + 2.56171² − 3.61871²)/(2·2.58958·2.56171)`
   ⇒ φ = 89.26 度，而 89.26 = 90 − 0.74 正好是那行报的 0.79 的补角；`L Finger4` 链
   独立算一遍也给 89.26。**所以「夹角达标」和「手指在模型上」可以同时成立**，
   这一轮的 6.39cm 指尖偏差就是这么被 45 次「改善」一路绿灯带过来的。
   长度轴不靠猜、也不在现场量（摆位之后父→子的方向已经被我们写成 PMX 方向了，
   量出来必然等于 PMX，是循环论证）—— 用 `肢段` 拟合那一步的实测结果：
   「缩这根轴能把父→子距离改掉」就是长度轴的操作定义，且它发生在任何摆位之前。 */
global MMD2MAX_LENAXIS_MAP = #()

fn mmd2maxLenAxisOf nm =
(
    /* 查这根骨的长度轴号（1/2/3）；没记账 ⇒ 0 = 退回「三根轴挑最顺的那根」。
       只有「恰好一个子关节」的骨会被记账（见 mmd2maxFitSegments）：Pelvis 有三个子
       （Spine + 两条腿，实测给 轴1 而复核给 轴2）、Hand 有五个子，它们的「长度」
       不是一个方向，记进去就会把躯干/骨盆一起带跑（#16 #17 的地盘）。
       定义必须排在 mmd2maxLogStructDir / mmd2maxStructErr 之前：MAXScript 在解析期
       绑定裸调，写在后面就是 #20 那个「Call needs function or class, got: undefined」。
       M1b 删掉了 MMD2MAX_LENAXIS 这个开关：没有任何一条路径会把它写成 false，
       留着它就等于留着一条从没跑过、也就从没验过的分支。 */
    if nm == undefined do return 0
    for e in MMD2MAX_LENAXIS_MAP do
    (
        if e[1] == nm do return e[2]
    )
    0
)

fn mmd2maxLogStructDir label axes headPos want gotPos nodeNm =
(
    /* 「结构方向」复核：PMX 每根骨自带 head 与 tail，骨头的**显示方向**就是
       tail - head —— 网格是照着这个方向做的，所以它才是用户眼里的「结构」。
       而我们只把 pivot 摆到 head，画出来的段是 head -> 下一个 head。离线按
       bones.tsv 算过：PMX 的手指本来就是 head-to-tail 串起来的，所以「若盒随
       pivot 走」手指应读 0 度 —— 真读出大夹角就说明**盒不随 pivot 转**（#26
       的脚骨正是这个机制），那时要修的是转角而不是落位。
       want 由调用方给：多数骨 = tail - head，脚链走 mmd2maxBoxWantDir（取点单独规定），
       与转角 pass 同一个判据（判据不同就会「修好了却仍报超标」）。
       方向天生没被 PMX 定义的段：`Bip001 L/R Hand`（一个父带 5 根指骨）、掌心
       跨度（腕->各指根，PMX 里没这根骨）、`Bip001 Spine`（PMX 下半身 骨指向下、
       Biped 段指向上，预测差 169.9 度，属已知不可比）。
       三根局轴都参与挑最近轴（脚骨长度在 Y、多数骨在 X，写死一根就是猜），
       再把偏差拆成矢状面倾角差 + 水平偏航差 —— 用户能看懂的是这两个数。
       **量哪根轴必须与 `mmd2maxStructErr` 同一个规则**（有长度轴记账就量长度轴），
       否则复核表会像第七轮那样给 6 根骨打 0.79 度的假绿灯，而指尖在 6.39cm 之外。
       两者不一致时把 `最近轴=N` 一起打出来：这一行的存在就是「规则在哪几根骨上
       真的改了行为」的清单，不用回头翻代码。 */
    if axes == undefined do return -1.0
    if headPos == undefined or want == undefined do return -1.0
    if (length want) < 0.01 do return -1.0
    local bestAng = 1e9
    local bestAx = 0
    for k = 1 to 3 do
    (
        local a = mmd2maxVecAngleDeg axes[k] want
        if a < bestAng do
        (
            bestAng = a
            bestAx = k
        )
    )
    local useAx = mmd2maxLenAxisOf nodeNm
    local axTag = "轴" + (bestAx as string)
    if useAx >= 1 and useAx <= 3 do
    (
        bestAng = mmd2maxVecAngleDeg axes[useAx] want
        axTag = "轴" + (useAx as string) + "按长度轴"
        if useAx != bestAx do axTag = axTag + " 最近轴=" + (bestAx as string)
    )
    local got = axes[bestAx]
    if useAx >= 1 and useAx <= 3 do got = axes[useAx]
    local wantLean = mmd2maxLeanDeg want
    local gotLean = mmd2maxLeanDeg got
    local wantYaw = atan2 want.y want.x
    local gotYaw = atan2 got.y got.x
    local dLean = mmd2maxWrap180 (gotLean - wantLean)
    local dYaw = mmd2maxWrap180 (gotYaw - wantYaw)
    local msg = "  结构向 " + label + " " + axTag
    msg = msg + " 目标倾=" + (wantLean as string) + " 实测倾=" + (gotLean as string)
    msg = msg + " 差倾=" + (dLean as string)
    msg = msg + " 差偏航=" + (dYaw as string)
    msg = msg + " 夹角=" + (bestAng as string)
    /* tail 离 head 不到 1cm = 模型里的显示 stub（香菱 右手首 只有 0.38cm，而左手首 3.26cm），
       方向本身没意义，标出来让调用方别计入超标。 */
    if (length want) < 1.0 do msg = msg + " 短骨"
    if gotPos != undefined do
    (
        local pd = distance gotPos headPos
        msg = msg + " pivot偏差=" + (pd as string)
    )
    mmd2maxLog msg
    bestAng
)

fn mmd2maxLogBoneDir label axes wantDir =
(
    /* 「画出来的骨头方向」vs 目标方向：三根局部轴各报一个三维夹角 + 一个水平偏航差，
       再挑出夹角最小的那根当「最近轴」。
       为什么不写死一根轴：脚骨的长度在局部 Y（实测 Foot 的 scale.y 才是骨长）、
       脚趾骨在 X，写死就等于猜，猜错时读数会假性变大。
       返回最近轴的水平偏航差（度）。 */
    if axes == undefined do return -1.0
    if wantDir == undefined do return -1.0
    local bestAng = 1e9
    local bestAx = 0
    local bestYaw = 0.0
    local msg = "  画线 " + label
    for k = 1 to 3 do
    (
        local a = mmd2maxVecAngleDeg axes[k] wantDir
        local y = mmd2maxYawDiffDeg axes[k] wantDir
        msg = msg + "  轴" + (k as string) + "=" + (a as string) + "度/偏航" + (y as string)
        if a < bestAng do
        (
            bestAng = a
            bestAx = k
            bestYaw = y
        )
    )
    msg = msg + "  最近轴=" + (bestAx as string)
    msg = msg + " 目标=" + ((normalize wantDir) as string)
    mmd2maxLog msg
    bestYaw
)

fn mmd2maxBipedAxes node =
(
    /* 取关节的三根局部轴在世界空间的方向（单位向量）。
       注意：**不能用 `biped.getTransform node #rot`** —— 该形式在 Max 2022 会抛异常
       （上一轮日志：长轴=0 夹角 -1.0，说明整段被吞掉了，脚一下都没转）。
       节点自身的 transform 矩阵是可靠的（位置校验一直在用它取 translationpart）。 */
    local m = undefined
    try ( m = node.transform ) catch ( return undefined )
    if m == undefined do return undefined
    local axes = #()
    /* `ok` 是 MAXScript 保留字（`local ok = ...` 直接编译失败），这里用 axesOK */
    local axesOK = true
    try
    (
        append axes (normalize [m.row1.x, m.row1.y, m.row1.z])
        append axes (normalize [m.row2.x, m.row2.y, m.row2.z])
        append axes (normalize [m.row3.x, m.row3.y, m.row3.z])
    )
    catch ( axesOK = false )
    if not axesOK do return undefined
    axes
)

fn mmd2maxBipedAxis node refDir =
(
    /* 取 Biped 关节的「长度轴」世界方向，返回 #(轴号 1/2/3, 单位向量)。
       为什么不能写死局部 X：多数肢骨确实是 X（Clavicle 的 scale.x=10.02 正好等于
       link 长度），但**脚骨**的 scale 是 [8.867(宽), 14.265(长), 7.325(高)]，
       长度其实在 Y 上。写死 X 会把脚原地转 90°。
       做法：把三根局部轴转到世界，取「与参考方向最接近」的那根作为长度轴。 */
    local axes = mmd2maxBipedAxes node
    if axes == undefined do return undefined
    local best = 0
    local bestDot = 0.0
    local bestVec = undefined
    for i = 1 to 3 do
    (
        local v = axes[i]
        if (length v) > 0.0001 do
        (
            local d = dot v refDir
            if d < 0 do d = -d
            if d > bestDot do ( bestDot = d; best = i; bestVec = v )
        )
    )
    if best == 0 do return undefined
    if (dot bestVec refDir) < 0 do bestVec = -bestVec
    #(best, bestVec)
)

fn mmd2maxScaleRow v s =
(
    /* 把单位向量按原行的长度乘回去：Biped 节点 TM 的行里可能带缩放，直接写单位
       向量等于顺手把缩放抹平 —— 骨盒尺寸会被连带改掉。 */
    local l = length v
    if l < 0.0001 do return v
    v * (s / l)
)

fn mmd2maxSetBoneRot node way m =
(
    /* 按探针选出来的写法把**绝对位姿**写进去：
       #bipedRotation → `biped.setTransform node #rotation <quat> false`（Max 自带
       BsRetargetTools.ms / BsBipedTools.ms 的写法，Straighten 拿它给 Toe 补 90°）；
       #tmAssign → 直接写 `node.transform = m`（10:34 实测在 Figure 模式内是 0.0度 =
       Biped 把外部直写整个忽略，所以这条基本不会被探针采用，留着只为对照）。
       **注意 #rotateCmd 不在这里**：`rotate` 命令是「绕世界轴转一个角度」的增量写法，
       吃不了绝对矩阵，由 mmd2maxRotateBoneAround 单独实现。
       返回值：成功 ""，失败=异常文字，绝不静默吞掉。 */
    local err = ""
    if way == #bipedRotation then
    (
        local q = m.rotation
        try ( biped.setTransform node #rotation q false ) \
            catch ( err = (getCurrentException() as string) )
    )
    else if way == #tmAssign then
    (
        try ( node.transform = m ) catch ( err = (getCurrentException() as string) )
    )
    else
    (
        err = "未知转角写法 " + (way as string)
    )
    err
)

fn mmd2maxRotateBoneAround node ang ax =
(
    /* 通用 `rotate` 命令：绕**世界**轴 ax 转 ang 度。10:34 实测这是 Figure 模式内唯一
       把局轴转动了并且刷新后仍在的写法（`rotate命令=30.0度/留=30.0度`）。它是增量的，
       所以调用方要给的是「从当前位置还要转多少」，不是目标姿态 —— 见
       mmd2maxRotateBoneToDir 里 net/cand 那套换算。返回 "" 或异常文字。 */
    local err = ""
    if (length ax) < 0.0001 do return "旋转轴退化"
    try ( rotate node (angleaxis ang ax) ) catch ( err = (getCurrentException() as string) )
    err
)

fn mmd2maxRefreshedMoveDeg node axRef =
(
    /* 「转完再逼 Biped 刷新一次」之后的局轴转角（度），取不到返回 -1.0。
       为什么必须有这一步：10:17 直接写 .transform / rotate 命令都读到 30 度，但那可能
       只是 MAXScript 自己的缓存 —— Biped 的控制器完全可以在下一次 update 时把 TM 按
       结构重算回去，那 30 度就是假的。做法：对同一根骨再发一次一直好用的 #pos（原值写回，
       位置不动），再 update 0，然后重量局轴。 */
    local p = undefined
    try ( p = node.transform.translationpart ) catch ()
    if p != undefined do
    (
        try ( biped.setTransform node #pos p false ) catch ()
    )
    try ( update 0 ) catch ()
    local ax = mmd2maxBipedAxes node
    if axRef == undefined or ax == undefined do return -1.0
    mmd2maxAxisMoveDeg axRef ax
)

fn mmd2maxStructErr node want =
(
    /* 这根骨「顺着 PMX 结构方向顺不顺」（度）；读不到返回 -1.0。
       这是转角写成没写成的唯一判据 —— setTransform 不抛错 ≠ 骨头动了。
       量哪根轴：有长度轴记账的骨（独子链骨）量**长度轴**，因为子关节就挂在它上面，
       长度轴歪多少、指尖就差多少；没记账的骨（Pelvis / Hand / 叶子）仍量三根轴里
       最顺的那根 —— 它们的方向没有「子关节」这个下游受害者，最顺轴就是它们的显示方向。 */
    local axes = mmd2maxBipedAxes node
    if axes == undefined do return -1.0
    local la = mmd2maxLenAxisOf node.name
    if la >= 1 and la <= 3 do
    (
        local dl = dot axes[la] want
        if dl > 1.0 do dl = 1.0
        if dl < -1.0 do dl = -1.0
        return (acos dl)
    )
    local best = 180.0
    for k = 1 to 3 do
    (
        local d = dot axes[k] want
        if d > 1.0 do d = 1.0
        if d < -1.0 do d = -1.0
        local a = acos d
        if a < best do best = a
    )
    best
)

fn mmd2maxShoeTip limbs name =
(
    /* 肢段表里「孩子 = name 的那条 foot 段」的鞋尖坐标（limbs.tsv 第 12~14 列，
       Python 端从网格实测）。找不到返回 undefined。 */
    for item in limbs do
    (
        if item.count >= 10 and item[5] == "foot" and item[2] == name do return item[8]
    )
    undefined
)

/* 脚链骨盒的俯仰（#37 引入，#39 压平过，#40 翻回斜轴，#41 又压平）：
   MMD2MAX_FOOTBOXFLAT = true 把盒子压成**水平**轴，false 按 PMX「踝 -> 球」的三维方向斜插。
   这个值由**导出侧**一处定（`mmd2max/export_obj.py: FOOT_BOX_FLAT`），经 meta 的
   `foot_box_flat` 原样进 MMD2MAX.CFG 到这里（M1b 起链上没有取反）。量截面的轴在
   Python 那边：两端必须同一个值，否则「按平轴量出来的高度」被写进斜着的盒子
   （或反过来），第十四轮之前就栽过一次不同源。
   第十五轮压平（#39）之后用户立刻看见后果（#40）：子关节 Toe0 的 pivot 被 段校验
   钉在 PMX 球部（踝 z=7.53、球 z=1.94），轴一压平，骨盒就不再经过球部 —— 骨轴和
   「指向自己子关节」差 29.93 度。上面「画线/偏航」两行比的是同一个压平后的目标，
   是恒等式，看不见这件事；所以 #40 加了 链向 复核（比**回读到的子节点位置**）。
   #40 当时留下的结论是「压平 + 高 8.66」和「斜轴 + 盒底贴鞋底」只能选一个，理由是
   盒底面垂直于轴。**这条结论被探针 biped_gap2.ms 推翻了**（stock Biped，脚本没碰过）：
     1) 骨盒远端面 == 子节点沿轴坐标，差 = 0.0 —— 断链不是 Biped 的画法，是我们把
        长度拟成了两 pivot 的三维间距（肢段拟合跑在摆位之前，之后没人回头补父骨长度）；
     2) 长度轴 #scale 乘 k，盒远端与子节点同步乘 k（×1.3：3.42361 == 3.42361），
        退出 figure 模式仍在 —— 盒子的沿轴长度**是可写的**（Max 2022 没有任何盒体积
        API，setBox/setWidth/setTaper 全都不存在，只有 #scale）。
        探针 biped_taper.ms 把这句话补到「连节点自己也没有」：Biped 链接节点的
        classof = Biped_Object，对它 getProperty #Taper/#width/#height/#banSize/#banPos/
        #boneType/#linkLength 一律 Unknown property（BoneGeometry 那套 bone.taper=90
        在 Biped 节点上不通）⇒ 骨盒前端收成楔形是 Biped 画骨骼的固定显示形状，
        写不了，也不该写（用户那张参考图的脚块同样是往后厚、往前薄的楔形）。
   ⇒ 平轴那只厚盒子可以被 足盒接趾 沿水平轴拉到「前端面正好过球部」，#39 的落地框跟
     和 #40/#41 的接趾同时成立。于是默认翻回压平，代价是 链向 按构造读 29.93 度
     （水平轴的定义，不是断链），真判据换成新加的 盒隙（父盒前端面到子关节沿轴的
     距离 + 子关节须落在端面矩形内）。
   这个值**不在这份脚本里定**：导出侧 `mmd2max/export_obj.py: FOOT_BOX_FLAT` 是唯一
   开关，值原样经 meta 的 `foot_box_flat` → MMD2MAX.CFG 的 `MMD2MAX_FOOTBOXFLAT` 到这里
   （M1b 起中间不再取反，也不再有「单独打开这份脚本时默认斜轴」那条分叉 —— 现在
   没有 CFG 就直接中止）。
   **回滚**：翻导出侧那一个词 ⇒ 量截面与画骨盒一起回到斜轴；
   再加上 `MMD2MAX_FOOTREACH = false`（改 mmd2max/targets.py 重跑导出）就退回第十六轮那根牙签。 */

fn mmd2maxBoxWantDir name headPos tailPos shoeTip =
(
    /* 骨盒「该指哪」= PMX 的 tail - head。脚链单独规定**取哪两个点**，
       俯仰归 MMD2MAX_FOOTBOXFLAT 管（true = 压成水平轴，与导出侧量截面用的是同一根轴）：
       11:11 那一版把脚链压成水平，是因为当时转角一扎 30 度就把**子关节一起拖到
       地里**（用户：「两边的脚也没有落在地面上，Y没有90度」）。那个因早就没了：
       肢段长度已按 |踝->球| 闭环（第十二轮 `段校验 L Foot -> L Toe0 实际=11.2281
       误差=4.8e-06`），子关节在每一道写之后都由 收尾重摆 + 脚趾复摆 跟回 PMX 落位
       （规则 31），所以转角现在只改「盒子指哪」，改不动「子关节在哪」。
       压平留下的却是实打实的错：踝 z=7.53、球 z=1.94，盒子被钉成一根水平的板卡在
       脚背上方，前端离趾骨盒还差 5.6cm 高 —— 第十二轮藏网格脚部特写直接拍到，就是
       用户那句「Bip001 Foot的底部没有够到脚的下面」。
       第十四轮回读推掉了这条账：Biped 脚盒在骨自己坐标系里**不绕长度轴对称**（竖直
       15.2% 在轴上方、84.8% 在轴下方、沿轴往后还探出 21.11% 段长），而 Python 端
       `_foot_link_box` 当年按「上下各一半」量，把一只本来就挂在轴下方的盒子塞进对称
       余量里，只剩 1.53055 那一小条 —— 「前端差 5.6cm」是量错了高度，不是压平的错。
       按真实摆位重新量以后，盒高要 8.7 才够到鞋底，斜轴容不下（盒底顶穿鞋底 5.74cm、
       盒角从脚后跟后面戳出 2.7cm），压平才容得下，同时正好回到 #28 那句「平踩地面」。
       所以轴的口径由导出侧 `FOOT_BOX_FLAT` 一处定，经 meta 的 foot_box_flat → CFG 的
       MMD2MAX_FOOTBOXFLAT 到这里；两端必须同一个值，否则 Python 按平轴量出来的高度，
       Max 又斜着摆。
       偏航与 #26 的读数两种口径都不变（水平分量还是同一个方向、长度也还是原长，
       复核表靠 `length want` 判短骨 stub）。
       地面保护改写成**斜度上限**：脚骨从踝到球物理上不可能陡过 45 度，超过就判
       「这根骨的 tail 是关节/踩地点约定、不是盒子走向」，退路沿用 #28 那条宁可不转。
         Foot -> PMX 足首 tail（= 左つま先 的 head，PMX 本来就 head-to-tail 串着）
         Toe0 -> 网格实测鞋尖方向（PMX 给的是竖直 stub，没有方向信息）
       返回 undefined = 这根骨的方向不可信，调用方一律别转，复核也不参与。 */
    local isFoot = matchPattern name pattern:"* Foot"
    local isToe = matchPattern name pattern:"* Toe0"
    if (not isFoot) and (not isToe) do return tailPos - headPos
    local raw = tailPos - headPos
    if isToe and shoeTip != undefined do raw = shoeTip - headPos
    local horiz = sqrt (raw.x * raw.x + raw.y * raw.y)
    if horiz < 0.001 do return undefined
    /* 极性直读：CFG 里 true = 压平 ⇒ 这里就压平，和导出侧 FOOT_BOX_FLAT 同真同假。
       （M1b 前这里是 `if not MMD2MAX_FOOTPITCH`，值又从 foot_pitch 反过一次，
       一条链上三次取反，翻开关的人数不清最终摆哪根轴。） */
    if MMD2MAX_FOOTBOXFLAT do return [raw.x, raw.y, 0.0] * ((length raw) / horiz)
    if (abs raw.z) > horiz do return undefined
    raw
)

fn mmd2maxRotateBoneToDir node wantDir way =
(
    /* 把骨盒转到「有一根局部轴正好顺着 PMX 结构方向」，其余两根尽量保住原来的滚转。
       写法按探针给出的路走：#rotateCmd 是**增量**的（绕世界轴转最小弧，两个方向都试），
       #bipedRotation / #tmAssign 是**绝对**的（构造目标矩阵再整块写）；绝对路线上
       先钉住 want 那根，再在另两根里挑与 want 最不平行的一根做施密特正交化，最后一根
       用叉乘补出来（保持 det=+1），这样得到的是最小弧转角，而且完全避开 MAXScript
       四元数乘法的左右手约定。
       写完一律立刻回读夹角：不比原来好就不留在场景里（绝对路写回原矩阵、增量路把净角
       转回 0），绝不留下更差的姿态。返回 #(转角前夹角, 转角后夹角, 抛错文字)。 */
    local axes = mmd2maxBipedAxes node
    if axes == undefined do return #(-1.0, -1.0, "局轴读不到")
    local want = normalize wantDir
    local bestAx = 1
    local bestDot = -2.0
    for k = 1 to 3 do
    (
        local d = dot axes[k] want
        if d > bestDot do ( bestDot = d; bestAx = k )
    )
    /* 有长度轴记账 ⇒ 转的就是它，不再挑「目前最顺的那根」。第七轮那 6 根左手指骨
       就是被这条老规则放过的：挑到 轴3、对齐到 0.79 度，长度轴 轴1 却被转到 89.26 度，
       指尖飞出去 3.6~6.4cm，而日志一路报「改善」。 */
    local la = mmd2maxLenAxisOf node.name
    if la >= 1 and la <= 3 do
    (
        bestAx = la
        bestDot = dot axes[la] want
    )
    if bestDot > 1.0 do bestDot = 1.0
    if bestDot < -1.0 do bestDot = -1.0
    local before = acos bestDot
    if before < 1.0 do return #(before, before, "")
    local rest = #()
    for k = 1 to 3 do
    (
        if k != bestAx do append rest k
    )
    local keep = rest[1]
    if (abs (dot axes[rest[2]] want)) < (abs (dot axes[rest[1]] want)) do keep = rest[2]
    local drop = rest[1]
    if keep == rest[1] do drop = rest[2]
    local v = axes[keep] - want * (dot axes[keep] want)
    if (length v) < 0.001 do return #(before, before, "其余轴与结构方向共线")
    local err = ""
    local after = before
    if way == #rotateCmd then
    (
        /* 探针 10:34 证实有效的这条是**增量**写法：`rotate` 命令吃「绕世界轴转多少度」，
           吃不下绝对矩阵（上一版把 #rotateCmd 也按 `node.transform = m` 实现，结果
           `尝试=35 改善=0` —— 用的正是那轮刚被否掉的直写路）。这里把「最接近结构方向的
           那根轴 -> want」换算成最小弧：轴 = 两者叉乘，角度 = 两者夹角。
           angleaxis 的正方向（左/右手）在 Biped 上纸面定不下来，所以 +ang 与 -ang 都试，
           取回读更好的一个；两个都不更好就把净角转回 0（回到原姿态）。
           net 记「相对原位已经转了多少度」，因此每一步只补 (目标 - net)。 */
        local axU = cross axes[bestAx] want
        if (length axU) < 0.001 do axU = axes[keep]
        axU = normalize axU
        local ang = mmd2maxVecAngleDeg axes[bestAx] want
        if ang < 0.0 do return #(before, before, "夹角读不到")
        local net = 0.0
        local bestNet = 0.0
        local bestErr = before
        for cand in #(ang, (-ang)) do
        (
            local step = cand - net
            if (abs step) > 0.0001 do
            (
                local e = mmd2maxRotateBoneAround node step axU
                if e != "" and err == "" do err = e
                net = cand
                local a = mmd2maxStructErr node want
                if a >= 0.0 and a < bestErr - 0.05 do ( bestErr = a; bestNet = cand )
            )
        )
        after = bestErr
        if bestNet != net do
        (
            local bk = mmd2maxRotateBoneAround node (bestNet - net) axU
            if bk != "" and err == "" do err = bk
        )
    )
    else
    (
        /* 绝对位姿写法（#bipedRotation / #tmAssign）：三行先构造单位正交系（钉住 want →
           另两根里挑最不平行的一根做施密特正交化 → 第三根叉乘补出来，行列式保持 +1），
           再逐行按原矩阵的行长乘回去、平移照原 row4 写回 —— 直接写单位向量等于把骨盒
           缩放顺手抹成 1，盒子尺寸会跟着变。 */
        local rows = #(axes[1], axes[2], axes[3])
        rows[bestAx] = want
        rows[keep] = normalize v
        if drop == 1 do rows[1] = cross rows[2] rows[3]
        if drop == 2 do rows[2] = cross rows[3] rows[1]
        if drop == 3 do rows[3] = cross rows[1] rows[2]
        local m0 = undefined
        try ( m0 = node.transform ) catch ()
        if m0 == undefined do return #(before, before, "当前位姿读不到")
        rows[1] = mmd2maxScaleRow rows[1] (length [m0.row1.x, m0.row1.y, m0.row1.z])
        rows[2] = mmd2maxScaleRow rows[2] (length [m0.row2.x, m0.row2.y, m0.row2.z])
        rows[3] = mmd2maxScaleRow rows[3] (length [m0.row3.x, m0.row3.y, m0.row3.z])
        /* 括号表达式后面直接跟属性是本项目的已知编译期陷阱（同 viewport.getTM().translation），
           必须先落到变量里 */
        local mTarget = matrix3 rows[1] rows[2] rows[3] m0.row4
        err = mmd2maxSetBoneRot node way mTarget
        local a = mmd2maxStructErr node want
        if a >= 0.0 do after = a
        if after > before - 0.5 do
        (
            /* 写没换来改善（或压根没写进去）：把原矩阵整块写回，绝不留下更差的姿态。 */
            local back = mmd2maxSetBoneRot node way m0
            if back != "" and err == "" do err = back
            after = before
        )
    )
    #(before, after, err)
)

/* ===== Biped 接口反射（探针层第一步：别再猜 token）=====
   `biped` 这个接口的全部方法名 + 参数表可以用 getMethodCount / getMethod 反射出来。
   为什么现在要它：#rotation 那条线连着三轮把「Biped 转不动」当成结论，实际是
   eulerToQuat 的参数没构造出来（§6）；橡皮筋 / Tie Down / 骨长这类开关要是还靠
   一轮试一个猜出来的名字，光试候选就得好几轮。整份翻出来落到磁盘上，以后
   「Biped 到底有没有 X」这类问题不用开 Max 就能回答。
   只把命中关键词的行打进日志，全表写进日志同目录的 biped_api.txt。
   任何一步失败都只降级成一句「反射没拿到」，绝不拦主流程。 */
fn mmd2maxDumpBipedApi logTag =
(
    /* 13:11 那句 `反射失败：getMethodCount 两次都拿到 0` 不是「Biped 没有接口」，是用错了
       工具：getMethodCount 面向 struct 实例，而 `biped` 是个 interface 值 —— 枚举接口方法
       要用 `dumpInterface`，它直接返回一段可读文本。两条路都留着，先 dumpInterface，
       一行没拿到再退回 getMethodCount；**两边的异常文字都进日志** —— 分不清「属性不存在」
       和「参数不对」时，反射这一条就还是黑盒，不许再拿一轮去猜名字。 */
    local rows = #()
    local n = 0
    local how = ""
    local dumpErr = ""
    local dumpVal = undefined
    try ( dumpVal = dumpInterface biped ) catch ( dumpErr = getCurrentException() )
    if (classOf dumpVal) == String do
    (
        how = "dumpInterface"
        for line in mmd2maxSplit dumpVal "\n" do
        (
            local s = substituteString line "\r" ""
            if s != "" do append rows s
        )
        n = rows.count
    )
    if n == 0 do
    (
        local def = undefined
        local oldErr = ""
        try ( def = classOf biped ) catch ( oldErr = getCurrentException() )
        if def != undefined do
        (
            try ( n = getMethodCount def ) catch ( oldErr = getCurrentException() )
        )
        if n == 0 do
        (
            try ( n = getMethodCount biped ) catch ()
            if n > 0 do def = biped
        )
        if n == 0 do return (logTag + " 反射失败：dumpInterface=" + dumpErr \
            + " | getMethodCount=0 " + oldErr)
        how = "getMethodCount"
        for i = 1 to n do
        (
            local one = ""
            try ( one = ((getMethod def i) as string) ) catch ()
            if one != "" do append rows one
        )
    )
    local kw = #("rubber", "tie", "length", "slide", "mode", "figure", "update", "scale")
    local hits = #()
    for s in rows do
    (
        for k in kw do
        (
            if matchPattern s pattern:("*" + k + "*") do
            (
                if findItem hits s == 0 do append hits s
            )
        )
    )
    /* 全表落盘：日志同目录。MMD2MAX_LOG 没设（比如手工在 Max 里跑脚本）就跳过写文件，
       只把命中行留在日志里 —— 反射本身不依赖路径。 */
    local apiPath = ""
    try ( apiPath = (getFilenamePath MMD2MAX_LOG) + "biped_api.txt" ) catch ()
    local wrote = "未写文件"
    if apiPath != "" do
    (
        local fh = undefined
        try ( fh = createFile apiPath encoding:#utf8 ) catch ()
        if fh != undefined do
        (
            for s in rows do ( format "%\n" s to:fh )
            close fh
            wrote = "全表已写 " + apiPath
        )
    )
    local shown = 0
    for s in hits do
    (
        if shown < 40 do
        (
            mmd2maxLog (logTag + " " + s)
            shown += 1
        )
    )
    (logTag + " 来源=" + how + " 行数=" + (n as string) \
        + " 命中关键词=" + (hits.count as string) + " " + wrote)
)

/* ===== 方向 / 落位 冲突时的取舍表（机制层的核心）=====
   MMD2MAX_DIRTOL / MMD2MAX_ROLLTOL 的**数值与定价理由**都在 mmd2max/targets.py，
   由 tools/obj_run.py 落进 MMD2MAX.CFG，这里只列消费点：
     DIRTL  三处消费同一个数 —— ① mmd2maxRotatePass 的尝试门控（比它小就不再去动那根骨）
            ② mmd2maxFitScore 的「方向超标」计数（循环的收敛判据，必须和 ① 同数，
               否则「尝试=0 就等于方向全部达标」这条推理不成立）
            ③ 退出 Figure 模式后的 `结构向 复核：夹角>N度=`（用户看图前的最后一道数）
            以前这个数在三个地方各写死一份 5.0，改一处就悄悄分家。
     ROLLTOL 只有 mmd2maxLegRoll 消费；同时是 #36「整段不转腿」的单一回滚开关。
   值不再写死在这一行：这里是 5 个声明（见文件头），装配跑到一半才发现判据被本地的
   默认值盖住，正是 M1b 之后不允许存在的那类「日志绿、口径错」。 */

fn mmd2maxRotVecAbout v axis c s =
(
    /* Rodrigues 旋转：把 v 绕**单位**轴 axis 转，角度已经化成 cos/sin 传进来
       （三根轴各算一遍三角函数是浪费，也方便调用方复用同一个数）。
       为什么不用 angleaxis/quat：MAXScript 里 quat 与 point3 的乘法次序在本项目
       无法离线证实（Biped 反射接口是死的，`dumpInterface` 一类都不存在），
       而 cross / dot / cos / sin 是这一路一直在用、日志里已经验证过的原语。 */
    v * c + (cross axis v) * s + axis * ((dot axis v) * (1.0 - c))
)

fn mmd2maxLegRoll root limbs rotWay =
(
    /* 大腿扭转闭环（#36）。用户原话：「两个小腿的朝向有问题，内八了，实际上并没有。」
       round-9 的回读把责任钉得很死：髋和踝都钉在 PMX 目标上（L/R Thigh 偏差 0.106、
       L/R Foot 偏差 6.8e-06/4.2e-05），可两膝各自被拽向中线 —— 校验 L Calf X 6.224→4.870
       （-1.354）、R Calf X -6.224→-5.048（+1.176），偏差 1.36/1.25cm，画面上就是两个
       膝盖对着腿中间拧。膝到「髋→踝」连线的距离实测 2.838、PMX 是 2.63：那 0.2cm 是
       肢长闭环的账（段长合计比 PMX 折线长 0.215，已定价，不在这一条里）；**方位**差
       约 26 度才是缺陷，而它没人管过 —— 髋与踝固定之后，膝剩下的唯一自由度就是这条腿
       绕「髋→踝」轴的扭转，全脚本没有任何一处写过 roll（转角 pass 转的是「骨盒指哪」，
       改不了「这条腿拧了几度」）。
       判据只有一个数：把「实测膝 - 髋」和「PMX 膝 - 髋」各自去掉沿轴分量，两个垂向向量
       之间的有符号角 = 还差多少。符号来自 (实测 × 目标)·轴，所以左右腿共用同一段代码，
       不需要按名字分侧（骨名匹配只允许出现在 mmd2maxChainPriority 那张表里）。
       做法：绕该轴把大腿的三根局轴转过来（行长度按原矩阵乘回去，骨盒尺寸不受牵连），
       写完立刻复测 —— 没改善就整块回滚（和转角 pass 同一条规矩：绝不留下更差的姿态）；
       转完把踝按 PMX 目标重摆一次（扭转会把踝甩出去），并把踝的偏差一起打出来。
       最多两遍：第一遍把方位转到位，第二遍留给「重摆踝时 Biped 顺手吃掉一点转角」；
       两遍还不到位，就说明这条腿的 IK 不认这个自由度 —— 日志里的 残差/迭代 就是证据，
       这一轮无论哪种结果都有信息量。
       返回值 = 真的转过角度且最终有改善的腿数（>0 时收尾的两道写必须再跑一遍，
       把被甩出去的踝、以及被带着转了 26 度的脚链方向一并补回来）。 */
    local rolled = 0
    for item in limbs do
    (
        if item.count >= 5 and item[5] == "leg" do
        (
            local tName = item[1]
            local kName = item[2]
            local kneeTgt = item[4]
            local hipTgt = undefined
            local ankleTgt = undefined
            local ankleName = undefined
            for row in limbs do
            (
                if row.count >= 4 and row[4] != undefined do
                (
                    if row[2] == tName do hipTgt = row[4]
                    if row[1] == kName do ( ankleTgt = row[4]; ankleName = row[2] )
                )
            )
            local tnode = getNodeByName (root.name + (substring tName 7 -1)) exact:true
            local knode = getNodeByName (root.name + (substring kName 7 -1)) exact:true
            local fnode = undefined
            if ankleName != undefined do
                fnode = getNodeByName (root.name + (substring ankleName 7 -1)) exact:true
            if tnode != undefined and knode != undefined and fnode != undefined \
               and hipTgt != undefined and kneeTgt != undefined and ankleTgt != undefined do
            (
                local hipP = tnode.transform.translationpart
                local ankleP = fnode.transform.translationpart
                local axis = ankleP - hipP
                local seg = length axis
                if seg >= 1.0 then
                (
                    axis = axis / seg
                    local dev0 = distance knode.transform.translationpart kneeTgt
                    local devPrev = dev0
                    local msg = "  大腿扭转 " + tName + " 起始膝偏差=" + (dev0 as string)
                    local iter = 0
                    local step = 0
                    local settled = false
                    local improved = false
                    while step < 2 and not settled do
                    (
                        step += 1
                        local vc = knode.transform.translationpart - hipP
                        local vt = kneeTgt - hipP
                        vc = vc - axis * (dot vc axis)
                        vt = vt - axis * (dot vt axis)
                        local lc = length vc
                        local lt = length vt
                        if lc < 0.5 or lt < 0.5 then
                        (
                            msg = msg + " 第" + (step as string) + "遍:膝离轴太近，方位读不出(" \
                                + (lc as string) + "/" + (lt as string) + ") 已停"
                            settled = true
                        )
                        else
                        (
                            local ang = atan2 (dot (cross vc vt) axis) (dot vc vt)
                            if abs ang <= MMD2MAX_ROLLTOL then
                            (
                                msg = msg + " 第" + (step as string) + "遍:残差=" + (ang as string) \
                                    + "度 已达容差" + (MMD2MAX_ROLLTOL as string) + " 收敛"
                                settled = true
                            )
                            else
                            (
                                local axes = mmd2maxBipedAxes tnode
                                if axes == undefined then
                                (
                                    msg = msg + " 第" + (step as string) + "遍:局轴读不到 已停"
                                    settled = true
                                )
                                else
                                (
                                    local m0 = tnode.transform
                                    local l1 = length [m0.row1.x, m0.row1.y, m0.row1.z]
                                    local l2 = length [m0.row2.x, m0.row2.y, m0.row2.z]
                                    local l3 = length [m0.row3.x, m0.row3.y, m0.row3.z]
                                    local cosv = cos ang
                                    local sinv = sin ang
                                    local n1 = mmd2maxScaleRow \
                                        (mmd2maxRotVecAbout axes[1] axis cosv sinv) l1
                                    local n2 = mmd2maxScaleRow \
                                        (mmd2maxRotVecAbout axes[2] axis cosv sinv) l2
                                    local n3 = mmd2maxScaleRow \
                                        (mmd2maxRotVecAbout axes[3] axis cosv sinv) l3
                                    local fwd = matrix3 n1 n2 n3 m0.row4
                                    /* 回滚快照另取一份：MAXScript 的 node.transform 返回的是
                                       同一个 Matrix3 引用，写完再读 m0 可能已经是转过的姿态。 */
                                    local back = matrix3 (mmd2maxScaleRow axes[1] l1) \
                                        (mmd2maxScaleRow axes[2] l2) (mmd2maxScaleRow axes[3] l3) \
                                        m0.row4
                                    local err = mmd2maxSetBoneRot tnode rotWay fwd
                                    iter += 1
                                    local posOk = false
                                    try ( biped.setTransform fnode #pos ankleTgt false; posOk = true ) \
                                        catch ( posOk = false )
                                    local aDev = -1.0
                                    if posOk do
                                    (
                                        aDev = distance fnode.transform.translationpart ankleTgt
                                    )
                                    local dev1 = distance knode.transform.translationpart kneeTgt
                                    msg = msg + " 第" + (step as string) + "遍 转=" + (ang as string) \
                                        + "度 膝偏差" + (devPrev as string) + "->" + (dev1 as string) \
                                        + " 踝偏差=" + (aDev as string)
                                    if err != "" do msg = msg + " 写角失败=" + err
                                    if dev1 < devPrev - 0.01 then
                                    (
                                        devPrev = dev1
                                        improved = true
                                    )
                                    else
                                    (
                                        /* 转了但膝没靠近目标（或写根本没生效）：整块退回转之前的矩阵。
                                           和转角 pass 同一条规矩 —— 绝不留下更差的姿态。 */
                                        local rb = mmd2maxSetBoneRot tnode rotWay back
                                        msg = msg + " 没改善->回滚"
                                        if rb != "" do msg = msg + "(回滚也失败)"
                                        try ( biped.setTransform fnode #pos ankleTgt false ) catch ()
                                        settled = true
                                    )
                                )
                            )
                        )
                    )
                    msg = msg + " 迭代=" + (iter as string)
                    if not settled do msg = msg + " (两遍未收敛)"
                    if improved do
                    (
                        rolled += 1
                        msg = msg + " 净改善"
                    )
                    mmd2maxLog msg
                    /* 报的是**当下回读**的膝/踝偏差，不是循环里的计数器：
                       计数器只说「我试着改过」，回读才说「现在离 PMX 目标多远」。
                       没进步的那根骨（残差本来就小于 ROLLTOL）也照样留一行，
                       否则判据表里会缺它，看上去像「这根骨没被量过」。 */
                    mmd2maxReport "thigh_roll" "knee_err_cm" tName \
                        (distance knode.transform.translationpart kneeTgt)
                    mmd2maxReport "thigh_roll" "ankle_err_cm" tName \
                        (distance fnode.transform.translationpart ankleTgt)
                )
                else mmd2maxLog ("  大腿扭转 " + tName + " 跳过：髋→踝只有 " + (seg as string))
            )
        )
    )
    rolled
)

/* MMD2MAX_TOETOL（脚趾复摆的落位容差，厘米）来自 mmd2max/targets.py → CFG，
   消费点只有 mmd2maxToeSnapBack：它同时是门控（已在容差内就不动），
   所以写 999 等于整段复摆跳过。 */

fn mmd2maxToeSnapBack root limbs checkNm =
(
    /* 脚趾复摆（#38，#36 带出来的回归）。round-10 回读：
       `段校验 Bip001 L Foot -> Bip001 L Toe0 目标=11.2281 实际=9.7335 误差=1.49461`
       （R 1.49644），而 round-9 同一行是 3.2e-05；`结构向 Bip001 L Toe0 pivot偏差=4.7362`
       （R 4.73899）。Foot 自己的 pivot（6.5e-06）与朝向（夹角 0.0198、倾/偏航 0.0）都还是准的
       —— 坏的只有它的子节点。
       机制：脚趾例程在收敛循环**之前**就把 Toe0 摆到 PMX 的 つま先，之后 ① 收敛循环的转角
       pass、② 大腿扭转（把整条腿绕髋→踝轴转 -28.03°/+45.89°→-13.07°）、③ 扭转后那次
       「收尾重摆(全摆)」都会带着这根链尾一起刚体走，而 `收尾转角(只转方向优先的骨): 尝试=0`
       说明这一遍之后没有任何一道写再管过它。扭转里已经补了「重摆踝」，唯独没人管踝的孩子。
       侧视截图与算术对得上：4.7362 拆成沿轴 8.83 + 垂直 4.09，那只绿盒子（≈37×15px，
       按 7.4px/cm 折 5.0×2.0cm = Toe0 的 5.32×1.86）中心离地约 6cm，而它该在趾根 z=1.94cm。
       判据仍然只有一个数：Toe0 的 pivot 到 PMX つま先 的距离。写完立刻复测，没靠近 0.01cm
       就退回摆之前的那个点（Point3 是值类型，快照安全；矩阵才需要另建 matrix3）。
       两道写各自把**自己造成的连带**打出来：踝的偏差、以及摆完的踝→趾实测长度 ——
       这两列决定「摆趾到底是把小腿转了、还是把足骨抻长了」，是这一轮唯一的新信息，
       所以无论改没改成都要留痕。
       开关只有一个：MMD2MAX_TOETOL。写 999 就等于整段不动（每根都在容差内被跳过），
       与 MMD2MAX_ROLLTOL（扭转角）、MMD2MAX_DIRTOL（骨盒画出来的朝向）互不相干。 */
    local snapped = 0
    for item in limbs do
    (
        if item.count >= 10 and item[5] == "foot" do
        (
            local fName = item[1]
            local cName = item[2]
            local ballPos = item[4]
            /* 踝目标 = 「子节点是这只脚 Foot」那一行（Calf 行）的落位 */
            local ankleTgt = undefined
            for row in limbs do
            (
                if row.count >= 4 and row[2] == fName do ( ankleTgt = row[4] )
            )
            local fnode = getNodeByName (root.name + (substring fName 7 -1)) exact:true
            local cnode = getNodeByName (root.name + (substring cName 7 -1)) exact:true
            if fnode != undefined and cnode != undefined \
               and ballPos != undefined and ankleTgt != undefined then
            (
                local dev0 = distance cnode.transform.translationpart ballPos
                if dev0 <= MMD2MAX_TOETOL then
                (
                    mmd2maxLog ("  脚趾复摆 " + cName + " 偏差=" + (dev0 as string) \
                        + " 已在容差" + (MMD2MAX_TOETOL as string) + " 内，不动")
                    mmd2maxReport "toe_snap" checkNm cName dev0
                )
                else
                (
                    local before = cnode.transform.translationpart
                    local aDev0 = distance fnode.transform.translationpart ankleTgt
                    local msg = "  脚趾复摆 " + cName + " 起始偏差=" + (dev0 as string)
                    local posed = false
                    try ( biped.setTransform cnode #pos ballPos false; posed = true ) \
                        catch ( posed = false )
                    local toeP = cnode.transform.translationpart
                    local dev1 = distance toeP ballPos
                    local aDev1 = distance fnode.transform.translationpart ankleTgt
                    local seg1 = distance fnode.transform.translationpart toeP
                    msg = msg + " 摆后=" + (dev1 as string) + " 踝偏差" + (aDev0 as string) \
                        + "->" + (aDev1 as string) + " 足长=" + (seg1 as string)
                    if not posed do ( msg = msg + " 摆位抛错" )
                    if posed and dev1 < dev0 - 0.01 then
                    (
                        snapped += 1
                        msg = msg + " 净改善"
                    )
                    else
                    (
                        try ( biped.setTransform cnode #pos before false ) catch ()
                        local back1 = distance cnode.transform.translationpart ballPos
                        local backA = distance fnode.transform.translationpart ankleTgt
                        msg = msg + " 没改善->回滚 回读=" + (back1 as string) \
                            + " 踝回读=" + (backA as string)
                    )
                    mmd2maxLog msg
                    mmd2maxReport "toe_snap" checkNm cName \
                        (distance cnode.transform.translationpart ballPos)
                )
            )
            else mmd2maxLog ("  脚趾复摆 " + cName + " 跳过：节点或踝目标缺失")
        )
    )
    snapped
)

fn mmd2maxChainPriority name =
(
    /* 每根骨在「方向」与「落位」互相冲突时保哪一个。以前每发现一处冲突就加一个函数
       （脚链单独判据、指链单独门控、锁骨单独回滚），这张表把它换成数据：
       #dir  = 方向优先 → 「重摆」这一遍整根跳过它；
       #pos  = 落位优先 → 「转角」这一遍整根跳过它；
       #both = 两个都要（躯干/腿/颈：摆位与转角互不吃）。
       依据全部来自 12:04 那轮的实测：
         重摆对躯干/颈/锁骨是净赚 —— Neck 4.90→7.6e-06、Head 4.15→0.0、
           L/R Clavicle 4.26→4e-07，而 `结构向 复核：夹角>5度=` 没有从 2 弹回 35，
           说明落位吃不掉躯干的转角 ⇒ #both。
         重摆对指链是净亏 —— L Finger32 落位 7.83→6.47（只换回 1.4cm）而方向
           3.5→5.63 变差、L Finger42 同理 ⇒ #dir。
         脚链的方向本身就是用户判据（「两边的脚也没有落在地面上，Y没有90度」），
           而它的落位本来就是 1e-5 ⇒ #dir。
         锁骨是 Biped 唯一带额外约束的骨，两轮实测转角写不进去
           （`转角 Bip001 L Clavicle 夹角=6.60331->6.60331` 后按设计回滚），
           继续追方向就是拿肩点跟 Biped 抢 ⇒ #pos（= 用户选的 b 方案）。
       14:42 那轮又添两条，判据是同一条：**「转的那根是错的、没转的那根是对的」**。
         Hand —— `转角1:Bip001 L Hand 夹角=43.1174->0.0` 之后用户判「L Hand 错位，
           反而 R Hand 正常」。因在源文件：`左手首` 的 tail 只有 3.26cm、方向
           (1.29,-2.04,-2.20)（倾 -137 度），而「腕→五指根重心」是 5.44cm、两者差 43 度；
           `右手首` 的 tail 只有 0.38cm ⇒ 被 `短骨`(<1cm) 门挡掉、**根本没转** ⇒ 正常。
           Hand 是五子骨，#28 的长度轴账（准入条件是「独子」）进不来，仍走最顺轴。
         Head —— 用户判「脖子刚刚好，但 Head 明显歪、后仰很多、还向左偏」。
           `Neck 轴1按长度轴 目标倾=-8.5847 夹角=0.0` 是对的；`頭` 的 tail-head =
           (-3.84,-5.73,+5.33) ⇒ `目标倾=-47.108` 还带 -3.84 的横向分量（=左偏），
           `转角1:Bip001 Head 夹角=48.889->0.0` 把这个显示 stub 当成了骨的方向。
           Head 是叶子骨（无子可量）⇒ 同样没有长度轴账。
         共同点：这两根的 PMX 尾向量是**画给 MMD 看的 stub，不是骨的方向**。
           为什么不补一条「按子骨重心/父骨续接」的通用判据：全模型 PMX 尾方向与
           子骨重心方向差 >30 度的只有 3 根 —— 下半身→Spine 169.7 度、R Hand 131.1 度、
           L Hand 43.0 度，而 **Spine 现在 `夹角=0.0` 且用户认可**，通用规则会顺手打坏它。
           所以仍用这张表的既有语义：#pos = 不转 = 认 Biped 自己的方向
           （Hand 跟着前臂、Head 跟着颈），落位照旧按 PMX 摆。
       以后新出现的冲突一律改这张表，不要再加特例分支。 */
    if matchPattern name pattern:"* Finger*" do return #dir
    if matchPattern name pattern:"* Foot" do return #dir
    if matchPattern name pattern:"* Toe0" do return #dir
    if matchPattern name pattern:"* Clavicle" do return #pos
    if matchPattern name pattern:"* Hand" do return #pos
    if matchPattern name pattern:"* Head" do return #pos
    #both
)

fn mmd2maxRayFromParent name =
(
    /* 落位目标不抄 PMX 绝对位置、而按「父骨骨盒射线」派生的骨。目前只有 UpperArm：
       它的父骨 Clavicle 是 #pos（转角写不进去），硬把肩点往 PMX 的位置摆，Biped 每轮
       都把它拽回锁骨的骨盒射线上 —— 这个来回拉扯正是下游 `Forearm 1.48 / Hand 1.01`
       跟着偏的原因。认了射线，肩点不再抢，肘/腕才摆得准。 */
    matchPattern name pattern:"* UpperArm"
)

fn mmd2maxShoulderRayTarget clavNode pmxTarget =
(
    /* 肩点的可达集合 = 以锁骨 pivot 为心、|锁骨 pivot→PMX 肩点| 为半径的球，
       与「锁骨骨盒长度轴」这条射线的交点 —— 也就是沿射线走同样长的距离
       （骨长是 mmd2maxFitSegments 早先按 PMX 缩好的，这里不改它）。
       代价：肩关节沿射线留在比模型肩点高约 1.9cm 处，UpperArm 骨盒随之偏一点。
       读不到轴/目标与 pivot 重合时返回 undefined = 用回 PMX 原目标，绝不返回猜的点。 */
    if clavNode == undefined do return undefined
    local base = clavNode.transform.translationpart
    local toTarget = pmxTarget - base
    local seg = length toTarget
    if seg < 0.001 do return undefined
    local ax = mmd2maxBipedAxis clavNode toTarget
    if ax == undefined do return undefined
    base + ax[2] * seg
)

fn mmd2maxFitScore root lines rowIds targetNames limbs =
(
    /* 收敛循环内部的打分：只出三个数，不打逐骨日志 —— 逐骨读数是循环结束后那张
       结构向 复核表，它是**退出 Figure 模式之后**的权威值。两者对不上是已知的
       （锁骨 pass 内 6.60 / 复核表 13.64），所以这个分数只在轮与轮之间可比，
       不能拿去和复核表对数。判据与转角 pass 同源（mmd2maxBoxWantDir），
       短于 1cm 的显示 stub 不计方向，读不到轴（-1.0）也不计。
       返回 #(方向超标数, 落位偏差和cm, 落位最大偏差cm) */
    local rows = mmd2maxRows lines
    local cHeadX = mmd2maxColPos lines "head_x"
    local cHeadY = mmd2maxColPos lines "head_y"
    local cHeadZ = mmd2maxColPos lines "head_z"
    local cTailX = mmd2maxColPos lines "tail_x"
    local cTailY = mmd2maxColPos lines "tail_y"
    local cTailZ = mmd2maxColPos lines "tail_z"
    local badDir = 0
    local sumPos = 0.0
    local maxPos = 0.0
    for k = 1 to targetNames.count do
    (
        local t = targetNames[k]
        if t != "Bip001" do
        (
            local row = rowIds[k]
            if row <= rows.count do
            (
                local p = rows[row]
                if p.count >= cTailZ do
                (
                    local sHead = [(p[cHeadX] as float), (p[cHeadY] as float), (p[cHeadZ] as float)]
                    local sTail = [(p[cTailX] as float), (p[cTailY] as float), (p[cTailZ] as float)]
                    local sNode = getNodeByName (root.name + (substring t 7 -1)) exact:true
                    if sNode != undefined do
                    (
                        local dp = distance sNode.transform.translationpart sHead
                        sumPos += dp
                        if dp > maxPos do maxPos = dp
                        local sWant = mmd2maxBoxWantDir t sHead sTail (mmd2maxShoeTip limbs t)
                        if sWant != undefined do
                        if (length sWant) >= 1.0 do
                        if (mmd2maxStructErr sNode (normalize sWant)) > MMD2MAX_DIRTOL do badDir += 1
                    )
                )
            )
        )
    )
    #(badDir, sumPos, maxPos)
)

fn mmd2maxConvergeGate dirOver =
(
    /* 收敛循环的闸门（M3）：只看**场景读数**，不看写入侧的自述。
       以前退出条件是 `rp[1] == 0`（这一遍没有骨需要转）。那个数是转角 pass 自己的
       门控计数——「夹角比容差小我就不动这根骨」——它说的是「我打算干什么」，
       不是「场景现在是什么样」。第十七轮实测：方向超标 4→5→5、落位最大值三遍
       一动不动，循环是靠 `for passNo = 1 to 3` 撞上限退出的，而日志里那句
       `收敛 第3遍: …` 和真达标长得一模一样。
       现在闸门 = mmd2maxFitScore 报的方向超标根数为 0。门槛仍是 MMD2MAX_DIRTOL
       （打分与尝试门控同源那条契约测试没动），所以这**不会**让循环少跑一遍或多跑
       一遍：香菱本轮方向超标 5 根，两种写法都跑到上限 ⇒ 几何不变（diff_round 对证）。
       变的是「停下来之后怎么说自己为什么停」。 */
    dirOver == 0
)

fn mmd2maxRotatePass root lines rowIds targetNames limbs rotWay passNo onlyDir =
(
    /* 骨盒转角 pass（#27 的修法），现在是收敛循环里的一遍。
       遍历顺序按 targetNames（父骨在前），子骨读到的父骨转角已经是转好的。
       每根都「写完立刻回读」，不比原来好 0.5 度以上就把原矩阵写回去
       （见 mmd2maxRotateBoneToDir），所以这一遍的方向只会被改善、不会被写坏。
       门控 `夹角 > MMD2MAX_DIRTOL` 同时是循环的收敛判据：尝试=0 就等于「方向全部达标」
       （这条推理要求打分用同一个数，见 mmd2maxFitScore）。
       落位优先（#pos）的骨整根跳过 —— 锁骨追方向 = 拿肩点跟 Biped 抢（#29）。
       onlyDir:true = 收尾那一遍，只许动方向优先（#dir）的骨：收尾先把所有骨摆回 PMX
       （含 #dir），再转它们把方向补回来；#both/#pos 的骨就此停在「落位」这一侧。
       开关是**位置参数**，不用 MAXScript 的可选参数（见 §6 编译期陷阱）。
       **必须跑在 `mmd2maxFigureMode ... false` 之前**：Figure 模式里 setTransform 改的是
       结构性姿态，退出之后再转就是在做动作、会插 key。
       返回 #(尝试, 改善, 未改善, 抛错) */
    local rows = mmd2maxRows lines
    local cHeadX = mmd2maxColPos lines "head_x"
    local cHeadY = mmd2maxColPos lines "head_y"
    local cHeadZ = mmd2maxColPos lines "head_z"
    local cTailX = mmd2maxColPos lines "tail_x"
    local cTailY = mmd2maxColPos lines "tail_y"
    local cTailZ = mmd2maxColPos lines "tail_z"
    local total = 0
    local better = 0
    local worse = 0
    local errCount = 0
    if rotWay == undefined do
    (
        if passNo == 1 do
            mmd2maxLog "转角 pass：探针未跑通（没有任何写法把局轴转过 15 度并且刷新后仍在），整段跳过"
        return #(0, 0, 0, 0)
    )
    for k = 1 to targetNames.count do
    (
        local t = targetNames[k]
        if t != "Bip001" do
        (
            local row = rowIds[k]
            if row <= rows.count do
            (
                local p = rows[row]
                if p.count >= cTailZ do
                (
                    local rHead = [(p[cHeadX] as float), (p[cHeadY] as float), (p[cHeadZ] as float)]
                    local rTail = [(p[cTailX] as float), (p[cTailY] as float), (p[cTailZ] as float)]
                    /* 脚链单独规定取哪两个点，其余骨仍是 PMX tail - head（见 mmd2maxBoxWantDir） */
                    local want = mmd2maxBoxWantDir t rHead rTail (mmd2maxShoeTip limbs t)
                    local prio = mmd2maxChainPriority t
                    if passNo == 1 do
                    (
                        if want == undefined do
                            mmd2maxLog ("  转角不做 " + t + " 脚链方向读不出（鞋尖缺失、来向竖直或斜过 45 度）")
                        if prio == #pos do
                            mmd2maxLog ("  转角不做 " + t + " 落位优先（Biped 约束骨，转角写不进去）")
                    )
                    /* 短于 1cm 的是模型自己的显示 stub（香菱 右手首 0.38cm），方向没意义。
                       分两层 if：`length undefined` 会抛，不赌 and 的短路 */
                    if want != undefined do
                    if prio != #pos do
                    if (not onlyDir or prio == #dir) do
                    if (length want) >= 1.0 do
                    (
                        local node = getNodeByName (root.name + (substring t 7 -1)) exact:true
                        if node != undefined do
                        (
                            local b = mmd2maxStructErr node (normalize want)
                            if b > MMD2MAX_DIRTOL do
                            (
                                local r = mmd2maxRotateBoneToDir node want rotWay
                                total += 1
                                if r[3] != "" do errCount += 1
                                if r[1] >= 0.0 and r[2] >= 0.0 do
                                (
                                    if r[2] < r[1] - 0.5 do better += 1
                                    if r[2] >= r[1] - 0.5 do worse += 1
                                )
                                local rmsg = "  转角" + ((passNo as string) + ":") + t \
                                    + " 夹角=" + (r[1] as string) + "->" + (r[2] as string)
                                if r[3] != "" do rmsg = rmsg + " 抛错=" + r[3]
                                mmd2maxLog rmsg
                            )
                        )
                    )
                )
            )
        )
    )
    #(total, better, worse, errCount)
)

fn mmd2maxLocalExtent node axisIdx =
(
    /* 量「节点本地包围盒」在指定局部轴上的跨度，用来估算 Biped 骨的实际长度。
       为什么要这个东西：**趾骨的 #scale 是比例（本模型读到 1.15662，三轴相同），
       不是厘米**（脚骨才是厘米，如 [8.87, 14.27, 7.33]）。把 7.12 直接写进趾骨 scale
       会把它撑成 6 倍大，所以只能「量当前长度 → 乘倍率」闭环。
       注意 nodeGetBoundingBox 的第 2 个参数传对象自身 transform 时得到的是**本地**包围盒。 */
    local e = 0.0
    local bb = undefined
    try ( bb = nodeGetBoundingBox node node.transform ) catch ( bb = undefined )
    if bb != undefined do
    (
        local mn = bb[1]
        local mx = bb[2]
        if axisIdx == 1 then e = mx.x - mn.x
        else if axisIdx == 2 then e = mx.y - mn.y
        else e = mx.z - mn.z
    )
    if e < 0.0001 do
    (
        try
        (
            if axisIdx == 1 then e = node.max.x - node.min.x
            else if axisIdx == 2 then e = node.max.y - node.min.y
            else e = node.max.z - node.min.z
        )
        catch ( e = 0.0 )
    )
    e
)

fn mmd2maxLogLocalBox tag node =
(
    /* 打印骨盒在**对象坐标系**里的两个角（pivot 在原点）。
       为什么现在才打：#19/#37 整条「骨头必须待在鞋里」的判据都压在一句话上 ——
       「Biped 的肢骨盒子以长度轴为中心对称」，于是「在鞋里」才被翻译成
       「轴上每一段、四个方向的净空都容得下盒子」（Python 侧 _foot_link_box / _toe_sections
       都是这句话的实现）。可从没有人看过包围盒本身：第十二轮只证明了 node.min/node.max
       在 Biped_Object 上退化成一个点，而 mmd2maxLocalExtent 走的 nodeGetBoundingBox
       一直在响应 #scale（截面闭环能收敛就是证据），所以这对手柄至少是能量的，只是没打印过。
       读数怎么判：
         - 长度轴（脚上实测 = 2/Y）应当一端 ≈ 0、另一端 ≈ 段长 ⟹ 盒子从 pivot 往前伸；
         - 另两根若 min ≈ -max ⟹ 中心对称，现判据成立，脚跟框不进去是**几何必然**
           （踝在脚的最高最靠后处，从它往前伸的盒子再怎么放大也到不了踝的后面）；
         - 若有一端 ≈ 0 ⟹ 盒子挂在轴的一侧（默认 Biped 的脚盒就是踩在地面上的），
           那「踝->脚掌」这根斜轴收得再紧也还是错的，得把轴摆平、按「轴到鞋底」给高。
       返回值原样交给调用方，判据不在这儿。 */
    local bb = undefined
    try ( bb = nodeGetBoundingBox node node.transform ) catch ( bb = undefined )
    if bb == undefined then ( mmd2maxLog ("  " + tag + " 本地包围盒读不到") )
    else ( mmd2maxLog ("  " + tag + " 本地 min=" + (bb[1] as string) \
        + " max=" + (bb[2] as string)) )
    bb
)

/* #41 的两个开关，都只作用于脚链，别的手不要顺手接过去（数值在 mmd2max/targets.py）：
   把 MMD2MAX_FOOTREACH 配成 false ⇒ 「把骨盒远端拉到子关节」整段跳过，退回第十六轮（牙签 + 空 3.29cm）
   MMD2MAX_GAPTOL 是盒隙容差（厘米），只当判据用，不改变任何写法 */

fn mmd2maxBoxFarEnd node axisIdx toward =
(
    /* 骨盒沿 axisIdx、在 toward 那一侧的端面坐标（本地厘米，pivot 为原点）。
       符号自己判：该轴的单位向量与 toward 点乘，正取 max、负取 min。 */
    local bb = undefined
    try ( bb = nodeGetBoundingBox node node.transform ) catch ( bb = undefined )
    if bb == undefined do return undefined
    local axes = mmd2maxBipedAxes node
    if axes == undefined do return undefined
    local mn = bb[1]
    local mx = bb[2]
    local v = 0.0
    if (dot axes[axisIdx] toward) >= 0.0 then ( v = mx[axisIdx] ) else ( v = mn[axisIdx] )
    v
)

fn mmd2maxScaleBoneAxis node axisIdx factor =
(
    /* 只对**单根**局部轴乘倍率。Biped 的 #scale 是三根轴各自的比例，
       整体乘（上一版的做法）会把刚缩好的宽/高又带跑。 */
    local s = mmd2maxBipedScale node
    local v = [s.x, s.y, s.z]
    if axisIdx == 1 do v.x = v.x * factor
    if axisIdx == 2 do v.y = v.y * factor
    if axisIdx == 3 do v.z = v.z * factor
    try ( biped.setTransform node #scale v false ) catch ()
    v
)

fn mmd2maxFitBoneReach node axisIdx pivot targetPos =
(
    /* 把骨盒的**远端面**沿长度轴缩到「子关节该在的那个点」（PMX 目标，**不是**回读到的
       子节点位置 —— 缩放会把子节点沿轴一起推走，拿它当目标就是自己追自己的尾巴）。
       实测前提（探针 biped_gap2.ms，stock Biped，一次都没被脚本碰过）：
         1) 骨盒远端 == 子节点沿轴坐标，差 = 0.0 —— Biped 本来就把骨头画到子关节；
         2) 长度轴 #scale 乘 k，盒远端与子节点**同步**乘 k（14.2651→18.5446 那一轮
            子节点位移 3.42361 == 盒远端变化 3.42361，退出 Figure 模式后仍在）。
       所以「盒子接不上自己的孩子」不是 Biped 的画法，是我们自己的流水线：肢段拟合跑在
       摆位**之前**、判据又是两 pivot 的三维间距；摆位随后把子节点搬到 PMX 目标，
       却没人回头补父骨的长度 ⇒ 第十六轮盒前端停在 7.935、子节点在 11.228，
       中间 3.29cm 空气（用户两张对照图里那块「悬在牙签前方的趾骨」）。
       返回 #(最终差值, 迭代次数, 停滞?, 目标沿轴坐标, 盒远端坐标, 动手前的差值)，
       读不到返回 undefined。第 6 列是给快照回滚（M4d）用的：判「这一刀有没有把盒子推得
       比动手之前更远」需要动手前后各一个数，而只有这里量得到动手前那个。 */
    local axes = mmd2maxBipedAxes node
    if axes == undefined do return undefined
    local cv = targetPos - pivot
    if (length cv) < 0.05 do return undefined
    local u = axes[axisIdx]
    if (dot u cv) < 0.0 do u = -u
    local iter = 0
    local stalled = false
    local gap = 1e9
    local gap0 = 1e9
    local along = 0.0
    local far = 0.0
    while iter < 4 do
    (
        far = mmd2maxBoxFarEnd node axisIdx cv
        if far == undefined do exit
        along = dot cv u
        gap = along - far
        if iter == 0 do gap0 = gap
        if abs gap <= 0.02 * along do exit
        if far < 0.05 do exit
        local f = along / far
        if f < 0.2 do f = 0.2
        if f > 5.0 do f = 5.0
        mmd2maxScaleBoneAxis node axisIdx f
        iter += 1
        local chk = mmd2maxBoxFarEnd node axisIdx cv
        if chk == undefined do exit
        if abs (chk - far) < 0.01 do ( stalled = true; exit )
    )
    #(gap, iter, stalled, along, far, gap0)
)

fn mmd2maxFitBoneExtent node axisIdx target =
(
    /* 闭环：把节点本地包围盒在 axisIdx 上的跨度缩放到 target（厘米）。
       最多 4 轮；倍率限幅 0.2~5 防一次跳飞；量值对缩放没反应（nxt≈cur）就停，
       免得同一个倍率被连乘四次把骨头撑爆。返回 #(最终跨度, 迭代次数)。 */
    local cur = mmd2maxLocalExtent node axisIdx
    local iter = 0
    local stalled = false
    while iter < 4 and (not stalled) and cur > 0.05 and target > 0.05 \
          and (abs (cur - target)) > (0.02 * target) do
    (
        iter += 1
        local f = target / cur
        if f < 0.2 do f = 0.2
        if f > 5.0 do f = 5.0
        mmd2maxScaleBoneAxis node axisIdx f
        local nxt = mmd2maxLocalExtent node axisIdx
        if (abs (nxt - cur)) < 0.01 do stalled = true
        cur = nxt
    )
    #(cur, iter, stalled)
)

fn mmd2maxWidthHeightAxes node lengthAxis =
(
    /* 「除长度轴之外，哪根是宽、哪根是高」—— 这条口径全脚本只有这一份（M4c）。
       规则是第十九轮给 Toe0 定的：两根候选轴里谁更贴近世界 Z，谁是「高」；脚上的骨
       都是平躺的，竖直那根才是厚度方向。旧写法是「谁现在更粗谁是宽」，宽高接近时靠运气。
       为什么必须收成一份：写入用 mmd2maxFitCrossSection，回读用 mmd2maxBoxDims，两边
       以前各刻了一遍这条判据。两份一旦分叉，回读报的「宽」量的其实是「高」，gate 会
       红在尺子上而不是几何上 —— 而读者只会去改几何。
       读不到局部轴时返回 undefined：退回默认顺序（写侧）还是算没量到（读侧）是**策略**，
       可以不同；口径本身只有一个。 */
    local other = #()
    for k = 1 to 3 do ( if k != lengthAxis do append other k )
    local axes = mmd2maxBipedAxes node
    if axes == undefined do return undefined
    if (abs (axes[other[1]].z)) > (abs (axes[other[2]].z)) do return #(other[2], other[1])
    other
)

fn mmd2maxFitCrossSection node dir targetW targetH =
(
    /* 把「除长度轴之外」的两根轴收到 targetW / targetH（厘米），长度轴一根手指都不碰
       （它归肢段闭环管）。读不到长度轴返回 undefined，绝不猜一根轴去缩。
       判据是 #19 给 Toe0 定的那一条，现在搬到 Foot 上 —— 用户两次说的是同一句话：
       「我要整块骨头呆在鞋子里」/「脚的骨骼并没有包在脚的里面，实际上脚穿模出来了」。
       哪根是宽、哪根是高**不看谁现在更粗**（旧写法），只看谁更贴近世界 Z：
       脚上的骨都是平躺的，竖直那根才是「高」。旧判据在宽高接近时靠运气，
       而新口径下 Foot 的 3.9 / 1.5 差了两倍半，一旦对调骨头立刻戳出鞋面。
       返回 #(缩后宽, 缩后高, 宽轴, 高轴, 缩前截面A, 缩前截面B, 长度轴, 宽迭代, 高迭代, 宽停滞, 高停滞)。 */
    local info = mmd2maxBipedAxis node dir
    if info == undefined do return undefined
    local ai = info[1]
    local other = #()
    for k = 1 to 3 do ( if k != ai do append other k )
    local oA = other[1]
    local oB = other[2]
    local eA = mmd2maxLocalExtent node oA
    local eB = mmd2maxLocalExtent node oB
    local wh = mmd2maxWidthHeightAxes node ai
    local wIdx = oA
    local hIdx = oB
    if wh != undefined do (wIdx = wh[1]; hIdx = wh[2])
    local rW = mmd2maxFitBoneExtent node wIdx targetW
    local rH = mmd2maxFitBoneExtent node hIdx targetH
    #(rW[1], rH[1], wIdx, hIdx, eA, eB, ai, rW[2], rH[2], rW[3], rH[3])
)

fn mmd2maxLengthAxisIndex node =
(
    /* 判断这个 Biped 关节的「长度轴」是局部 X/Y/Z 中的哪一根：
       拿它到各个子关节的方向，取与某根局部轴最贴近的那根。
       为什么不写死 X：肢骨大多是 X，但**脚骨**的长度在 Y 上（scale=[8.867,14.265,7.325]，
       实测 link 长度 14.45 ≈ Y）。写死轴会把骨头压扁或拉长。 */
    local kids = #()
    try ( kids = node.children ) catch ( kids = #() )
    if kids.count == 0 do return 0
    local axes = mmd2maxBipedAxes node
    if axes == undefined do return 0
    local origin = node.transform.translationpart
    local bestAxis = 0
    local bestDot = 0.0
    for k in kids do
    (
        local p = undefined
        try ( p = k.transform.translationpart ) catch ( p = undefined )
        if p != undefined do
        (
            local d = p - origin
            if (length d) > 0.0001 do
            (
                d = normalize d
                for ai = 1 to 3 do
                (
                    local dv = dot axes[ai] d
                    if dv < 0 do dv = -dv
                    if dv > bestDot do ( bestDot = dv; bestAxis = ai )
                )
            )
        )
    )
    bestAxis
)

fn mmd2maxApplyWidths root widths =
(
    /* 收缩手掌/手指的**横截面**（只动非长度轴，长度轴一律不碰）。
       为什么需要：Biped 默认手指很粗（本模型 2.39），网格手指直径只有 1.5~2，
       不收缩的话手指骨会明显比手指大一圈（用户截图）。
       目标宽度来自 Python 侧对网格的实测（顶点到骨轴的垂距 30 分位 × 2）。
       长度轴在运行时判定（见 mmd2maxLengthAxisIndex），横截面取另外两轴的平均值。 */
    local adjusted = 0
    for item in widths do
    (
        local name = item[1]
        local target = item[2]
        local node = getNodeByName (root.name + (substring name 7 -1)) exact:true
        if node != undefined do
        (
            local lax = mmd2maxLengthAxisIndex node
            if lax > 0 do
            (
                local s = mmd2maxBipedScale node
                local comps = #(s.x, s.y, s.z)
                /* 横截面 = 另外两轴的平均 */
                local others = #()
                for ai = 1 to 3 do if ai != lax do append others comps[ai]
                local cur = (others[1] + others[2]) / 2.0
                if cur > 0.001 do
                (
                    local f = target / cur
                    if f < 0.20 do f = 0.20
                    if f > 1.20 do f = 1.20
                    if (abs (f - 1.0)) > 0.05 do
                    (
                        for ai = 1 to 3 do if ai != lax do comps[ai] = comps[ai] * f
                        try ( biped.setTransform node #scale [comps[1], comps[2], comps[3]] false ) catch ()
                        local s2 = mmd2maxBipedScale node
                        mmd2maxLog ("  手部粗细 " + name + " 长轴=" + (lax as string) \
                            + " 目标宽=" + (target as string) \
                            + " scale: (" + (s.x as string) + "," + (s.y as string) + "," + (s.z as string) + ")" \
                            + " -> (" + (s2.x as string) + "," + (s2.y as string) + "," + (s2.z as string) + ")")
                        adjusted += 1
                    )
                )
            )
        )
    )
    adjusted
)

fn mmd2maxBoxDims node dir =
(
    /* 只读不写：量骨盒在长度轴 / 宽 / 高三根局部轴上的本地跨度（厘米），M4c 的尺。
       轴的定义直接调用写入那一份（mmd2maxWidthHeightAxes）：长度轴 = 与 dir 最贴近的
       那根，另两根谁更贴近世界 Z 谁是「高」。不另写一遍的理由见那个函数的注释头。
       返回 #(宽, 高, 长, 长度轴号)；读不到返回 undefined，绝不猜一根轴去量。
       第 4 个返回值不是装饰：第二十轮实测脚链两根骨的长度轴不同（Foot=2、Toe0=1），
       一旦回读挑到的轴和写入时不一样，前三行报的就全是别的轴的数 —— 只有这一行能揭穿。 */
    local info = mmd2maxBipedAxis node dir
    if info == undefined do return undefined
    local ai = info[1]
    local wh = mmd2maxWidthHeightAxes node ai
    if wh == undefined do return undefined
    local wid = mmd2maxLocalExtent node wh[1]
    local hei = mmd2maxLocalExtent node wh[2]
    local lng = mmd2maxLocalExtent node ai
    /* 三根里任何一根量到 0 都是「没量到」，不是「这根骨薄成了一张纸」：nodeGetBoundingBox
       失败时 mmd2maxLocalExtent 退回 node.min/max，而那两个属性在 Biped_Object 上退化成
       一个点（第十二轮实测），所以 0 只可能是读不到。宁可报「没量到」也不报 0 —— 后者
       会被拼表当成「这根骨的截面真的是 0」判红，把测量故障说成几何故障。 */
    if (wid <= 0.0001) or (hei <= 0.0001) or (lng <= 0.0001) do return undefined
    #(wid, hei, lng, ai)
)

fn mmd2maxReportBoxDims subject node dir =
(
    /* 一根骨：量 + 落四行读数 + 一行日志。返回 1=量到 / 0=没量到，调用方只负责数数。
       这边**一个判定都不写**（规则 II）：宽高等待不等于几厘米由 MMD2MAX.VERDICTS 说，
       Max 只把量到的数交出去。 */
    local d = mmd2maxBoxDims node dir
    if d == undefined do return 0
    mmd2maxReport "box_rb" "width_cm" subject d[1]
    mmd2maxReport "box_rb" "height_cm" subject d[2]
    mmd2maxReport "box_rb" "length_cm" subject d[3]
    mmd2maxReport "box_rb" "len_axis" subject d[4]
    mmd2maxLog ("  骨盒回读 " + subject + " 宽=" + (d[1] as string) \
        + " 高=" + (d[2] as string) + " 长=" + (d[3] as string) \
        + " 长度轴=" + (d[4] as string))
    1
)

fn mmd2maxVerifyBoxes root limbs widths =
(
    /* ===== 骨盒回读（M4c）：最终场景里把骨盒的粗细与长度再量一遍 =====
       为什么前面那三段日志不算验收：`足背 … -> 宽=3.71424 高=8.65601`、
       `脚趾 … -> 5.31702/1.86092`、`手部粗细 … -> (8.63193, 3.6286, 3.6286)` 全是
       **写入那一刻**的自述。写完这根骨盒之后还有收尾转角（它会把「哪根轴离世界 Z 最近」
       重新排一次）与足盒接趾（一次 #scale，第二十轮实测把 Y 从 10.30 拉到 12.64）。
       #37/#41 反复的形状就是「缩完又被谁改了 / 缩错了轴」，所以尺必须在最后量。
       期望值一个都不来自写入侧：脚链那四根的宽高来自 limbs.tsv 脚段第 15~18 列
       （Python 沿骨轴量网格得到的截面），趾长来自第 11 列；手掌手指来自 widths.tsv。
       两把尺分开报的理由见 mmd2max/verdicts.box_targets 的注释（脚链读包围盒跨度，
       手部 gate 读 #scale、跨度只要求出现）。
       参考方向与写入同一条：足背 = 实测踝 pivot → PMX 脚掌，脚趾 = PMX 脚掌 → 鞋尖。
       返回 #(量到的节点数, 读不到的节点数)：读不到的也算一条失败读数，因为「没量」
       和「量了没问题」在报告里长得一样这件事，v3 已经吃过 40 轮。 */
    local measured = 0
    local failed = 0
    for item in limbs do
    (
        if item.count >= 12 and item[5] == "foot" do
        (
            local fName = item[1]
            local cName = item[2]
            local ballPos = item[4]
            local tipPos = item[8]
            local fnode = getNodeByName (root.name + (substring fName 7 -1)) exact:true
            local cnode = getNodeByName (root.name + (substring cName 7 -1)) exact:true
            if fnode != undefined and ballPos != undefined then
            (
                local fp = fnode.transform.translationpart
                local fv = ballPos - fp
                local fl = length fv
                if fl > 0.5 then
                (
                    local okF = mmd2maxReportBoxDims fName fnode (fv / fl)
                    if okF == 1 then measured += 1
                    else failed += 1
                )
                else
                (
                    failed += 1
                    mmd2maxLog ("  骨盒回读 " + fName + " 跳过：踝→脚掌只有 " + (fl as string))
                )
            )
            else
            (
                failed += 1
                mmd2maxLog ("  骨盒回读 " + fName + " 节点或 PMX 脚掌目标读不到")
            )
            if cnode != undefined and ballPos != undefined and tipPos != undefined then
            (
                local tv = tipPos - ballPos
                local tl = length tv
                if tl > 0.5 then
                (
                    local okC = mmd2maxReportBoxDims cName cnode (tv / tl)
                    if okC == 1 then measured += 1
                    else failed += 1
                )
                else
                (
                    failed += 1
                    mmd2maxLog ("  骨盒回读 " + cName + " 跳过：脚掌→鞋尖只有 " + (tl as string))
                )
            )
            else
            (
                failed += 1
                mmd2maxLog ("  骨盒回读 " + cName + " 节点或脚掌/鞋尖目标读不到")
            )
        )
    )

    /* 手掌与手指：gate 落在 #scale 的两个非长度分量上（写入侧就是这两格），
       同一根骨的包围盒跨度另报一行。长度轴用 mmd2maxLengthAxisIndex（= 写入侧那一个），
       不重新按 dir 挑：手指链的子关节才是它的长度方向，这里没有第二个参考方向可读。 */
    for item in widths do
    (
        local name = item[1]
        local node = getNodeByName (root.name + (substring name 7 -1)) exact:true
        local lax = 0
        if node != undefined do lax = mmd2maxLengthAxisIndex node
        if node == undefined or lax <= 0 then
        (
            failed += 1
            mmd2maxLog ("  骨盒回读 " + name + " 节点或长度轴读不到")
        )
        else
        (
            local others = #()
            for ai = 1 to 3 do if ai != lax do append others ai
            local s = mmd2maxBipedScale node
            local comps = #(s.x, s.y, s.z)
            local scaleMean = (comps[others[1]] + comps[others[2]]) / 2.0
            local eA = mmd2maxLocalExtent node others[1]
            local eB = mmd2maxLocalExtent node others[2]
            if (eA <= 0.0001) or (eB <= 0.0001) then
            (
                failed += 1
                mmd2maxLog ("  骨盒回读 " + name + " 包围盒跨度读到 0")
            )
            else
            (
                local boxMean = (eA + eB) / 2.0
                mmd2maxReport "box_rb" "hand_scale_cm" name scaleMean
                mmd2maxReport "box_rb" "hand_box_cm" name boxMean
                mmd2maxLog ("  骨盒回读 " + name + " scale 截面=" + (scaleMean as string) \
                    + " 盒跨度截面=" + (boxMean as string) + " 长度轴=" + (lax as string))
                measured += 1
            )
        )
    )
    #(measured, failed)
)

/* ================= 快照回滚（M4d） =================

   这一节回答的问题是「一刀写坏了，场景里还剩什么」。以前答案是「剩下那一刀」——
   #scale 把骨盒撑爆（#37）、复摆把趾骨摆飞（#38）、接趾留下牙签（#41），全都只能
   在事后复核里被读出来，写的那一刻没有任何退路。

   载体由探针定死（probes/figfile_m4h.txt，全部是实测而不是文档）：
     · `biped.saveFigFile` / `biped.loadFigFile` 收的是 **controller**（`root.controller`），
       传节点抛「Unable to convert: $Biped_Object:Bip001 … to type: Controller」；
     · 两者都必须在 Figure 模式内，否则「Cannot run the operation in this mode」；
     · **一份 .fig 连骨盒一起还原**：把 L Thigh 的 #scale 乘 1.3（盒跨度 40.86→53.12）
       再 load 回来，盒角差读到 5.72205e-06、#scale 差 4.76837e-07（H2）——所以快照
       不必自己再抄一张 #scale 表（H3 证明逐骨写回同样能复原，留作 .fig 失效的退路）；
     · `saveFigFile` 这个动作**自己**在进出 Figure 模式的翻转里把姿态挪走 2.13cm（H0）
       /2.23cm（H4）。所以这里的存与读都先把模式** peek 回来再还原**，绝不在装配中途
       写 `figureMode = false`（:2319 那条规则说的就是它）；
     · 重新存图能当新基准：扰动 18.74cm 之后 load 刚存的那份，三列读数 6.8e-05（H4）
       —— 这就是下面 self-test 每轮要复验的那件事。
   `.bip` 那条路是死的：收 controller 报模式错、收节点报类型错（H5），别再来试。

   开关：MMD2MAX_ROLLBACK（mmd2max/targets.py 的 ROLLBACK）。默认 false，且关掉时
   本节一段代码都不跑 —— 它改的是「写坏了剩什么」而不是某个目标值，所以必须能整段
   自证几何中性。 */

fn mmd2maxFigPath tag =
(
    /* 基准落在 OBJ 同一个目录（一轮一个目录），文件名纯 ASCII。
       为什么不放系统临时目录：放那里就成了跨轮共享的状态，上一轮的 .fig 给这一轮
       盖章正是 #21 那类事故的形状（判据读到了不属于本轮的产物）。
       try/catch 一律语句形式 + 显式失败值（契约 rule 10：`local x = try (...)` 编译期
       就死，探针里跑出过读数也不值得拿一整轮实跑去赌边界）。 */
    local dir = undefined
    try ( dir = getFilenamePath MMD2MAX_OBJ ) catch ( dir = undefined )
    if dir == undefined or dir == "" do return undefined
    dir + "mmd2max_rb_" + (tag as string) + ".fig"
)

fn mmd2maxFigCtrl bipRoot =
(
    local ctrl = undefined
    try ( ctrl = bipRoot.controller ) catch ( ctrl = undefined )
    ctrl
)

fn mmd2maxFigOp bipRoot figPath modeFlag =
(
    /* 存（true）或读（false）一份 .fig，并且**把调用方的 Figure 模式状态原样还回去**。
       返回 #(成功?, 调用方原来在不在 Figure 模式里)；controller 拿不到时返回 #(false, false)。 */
    local ctrl = mmd2maxFigCtrl bipRoot
    if ctrl == undefined do return #(false, false)
    local was = false
    try ( was = ctrl.figureMode ) catch ( was = false )
    local did = false
    mmd2maxFigureMode ctrl true
    if modeFlag then
        did = try ( biped.saveFigFile ctrl figPath; true ) catch ( false )
    else
        did = try ( biped.loadFigFile ctrl figPath; true ) catch ( false )
    mmd2maxFigureMode ctrl was
    #(did, was)
)

fn mmd2maxMarkNode bipRoot targetName =
(
    /* limbs 表里是 "Bip001 L Foot" 这种导出骨名，场景里的节点名是 root.name 加上
       去掉前七个字符的那一段。这串拼接在脚本里已有十几处一模一样的写法（每处都被
       实跑验证过），这里只服务回滚这条新链路，不去改动老代码。 */
    if bipRoot == undefined or targetName == undefined do return undefined
    local found = undefined
    try ( found = getNodeByName (bipRoot.name + (substring targetName 7 -1)) exact:true ) \
        catch ( found = undefined )
    found
)

fn mmd2maxMarkList bipRoot limbs =
(
    /* 给一组 Biped 骨拍「世界位置 + 本地包围盒三根轴的跨度 + #scale 三根轴 + 三根局部轴
       的世界朝向」的标记。
       回滚到底回干净没有，就比这四样 —— .fig 声称同时带姿态与骨盒（H2），所以验回滚
       也必须两样一起验：只比 #pos 会看不见「姿态回来了、骨头还粗着」。
       不比四元数：探针量到 q 与 -q 表示同一个朝向，逐分量差能把一次完美还原读成 3.99。
       第 4 样（轴朝向）是第 27 轮补的：那一轮位置/盒/scale 三列全部回零（5.1e-05 /
       1.9e-06 / 9.5e-07），锁骨的世界长度轴却从 -162.237 度变成 -72.7144 度（差 89.5），
       双臂落位误差因此从 0.955 涨到 1.957 —— 下游 结构向/转角/射线 吃的恰恰是这根轴，
       只比位置/盒/scale 的尺会把它读成一次完美还原。
       **但这一列自己也是读数**：第 27、28 轮它都量到 0.0，也就是说 load 的那一刻帧是
       好的，坏在后面的 #scale 写刀上（延迟失效）。所以四列一起只担保「载体当场无损」，
       不担保「这一轮没被回滚碰过」—— 后者由 placement 的 lock 行看着（targets.py 里
       记了完整的两轮数字）。
       骨名去重：limbs 每行都带父骨，一根骨会被列多次（父/子各一次）。 */
    local marks = #()
    local seen = #()
    for item in limbs do
    (
        local nm = item[1]
        if nm != undefined and (findItem seen nm) == 0 do
        (
            append seen nm
            local node = mmd2maxMarkNode bipRoot nm
            if node != undefined do
            (
                local sc = mmd2maxBipedScale node
                append marks #(nm, node.transform.translationpart,
                              #(mmd2maxLocalExtent node 1, mmd2maxLocalExtent node 2,
                                mmd2maxLocalExtent node 3), #(sc.x, sc.y, sc.z),
                              mmd2maxBipedAxes node)
            )
        )
    )
    marks
)

fn mmd2maxMarkGap markA markB =
(
    /* 两组标记的逐骨最大差：#(位置cm, 盒cm, scale, 轴朝向度)。
       骨名一边有一边没有 ⇒ 不参与比较（不编一个 0 假装没事）；「骨头的集合变了」
       这种事故由别的判据管，这里量的是同一根骨回没回来。
       轴朝向那一列初值 -1，和 mmd2maxVecAngleDeg 的「没测到」约定同一个数：一根骨
       都量不到时读数是 -1，不是 0。这个区别有代价也有必要 —— 第 4 列是第 27 轮为了
       「三列全零而锁骨转了 89.5 度」补的，如果它自己也能在没量到时装成 0，就又把
       同一类假绿换了一把尺子重新制造一遍。 */
    local dPos = 0.0
    local dBox = 0.0
    local dScl = 0.0
    local dAng = -1.0
    for a in markA do
    (
        local other = undefined
        for b in markB do if b[1] == a[1] do other = b
        if other != undefined do
        (
            dPos = amax dPos (distance a[2] other[2])
            for k = 1 to 3 do
            (
                dBox = amax dBox (abs (a[3][k] - other[3][k]))
                dScl = amax dScl (abs (a[4][k] - other[4][k]))
            )
            if a[5] != undefined and other[5] != undefined do
            (
                for k = 1 to 3 do
                (
                    local deg = mmd2maxVecAngleDeg a[5][k] other[5][k]
                    if deg >= 0.0 do dAng = amax dAng deg
                )
            )
        )
    )
    #(dPos, dBox, dScl, dAng)
)

fn mmd2maxRollbackSelfTest bipRoot limbs =
(
    /* 每轮在**这一具真 Biped** 上把回滚载体跑通一次：拍基准 → 存图 → 把一根骨的骨盒
       沿长度轴撑大 30% → 读图 → 三列差值必须回 0。
       为什么不「探针跑过一次就够了」：这条路同时依赖 Max 版本、Figure 模式、以及
       「这一轮的目录真能写出文件」（中文目录名从来没被证明过）。三样任何一样变了，
       产品里的「回滚」就退化成一句注释 —— 而注释不会在跑坏的那一轮响。
       1.3 是测试夹具不是判据，所以不进 CFG；它唯一的用处是让 perturb 那一列必须 > 0，
       否则「回滚后差值为 0」只是根本没扰动过的假绿。
       返回 #(存图?, 读图?, 扰动后位置差, 回滚后位置差, 回滚后盒差, 回滚后 scale 差,
             裸存图自身的扰动, 被扰动那根骨的名字, 回滚后局部轴朝向差)。
       最后一列是第 27 轮的实测教训加上的：那一轮前三列全部回零，同一轮锁骨事后却读出
       89.5 度的长度轴偏移，代价由双臂落位误差付。第 28 轮带上这一列再跑，它自己读 0.0
       —— 所以这一列量到的是「当场」，那条 89.5 度是延后结的账：自检通过 ≠ 这一轮没被碰。 */
    local marks0 = mmd2maxMarkList bipRoot limbs
    if marks0.count == 0 do return undefined
    local figPath = mmd2maxFigPath "selftest"
    if figPath == undefined do return undefined
    local savedOp = mmd2maxFigOp bipRoot figPath true
    /* 装配中途本来就在 Figure 模式里，所以这里没有模式翻转。这一行读数回答的是
       「裸的一次 saveFigFile 动不动场景」—— 探针那次 2.13cm 到底算存图还是算翻转，
       只有在这里能被拆开。 */
    local cost = mmd2maxMarkGap marks0 (mmd2maxMarkList bipRoot limbs)
    local pertName = ""
    for m in marks0 do
    (
        if pertName == "" do
        (
            local node = mmd2maxMarkNode bipRoot m[1]
            if node != undefined do
            (
                local lax = mmd2maxLengthAxisIndex node
                if lax > 0 do
                (
                    mmd2maxScaleBoneAxis node lax 1.3
                    pertName = m[1]
                )
            )
        )
    )
    if pertName == "" do return undefined
    local pert = mmd2maxMarkGap marks0 (mmd2maxMarkList bipRoot limbs)
    local loadOp = mmd2maxFigOp bipRoot figPath false
    local back = mmd2maxMarkGap marks0 (mmd2maxMarkList bipRoot limbs)
    try ( deleteFile figPath ) catch () -- 允许失败：删不掉只多留一个文件，读数已经落盘
    #(savedOp[1], loadOp[1], pert[1], back[1], back[2], back[3], cost[1], pertName,
      back[4])
)

fn mmd2maxApplyScale node axis factor method =
(
    /* axis: 1=x 2=y 3=z 4=三轴等比例（用于脚这种「长度不是 X」的节点）
       实测：UpperArm 的 scale 是 [23.13,23.13,23.13]（X 就是 link 长度），
       但 Foot 是 [8.87(宽), 14.27(长), 7.33(高)]——X 是宽度！
       拿 X 去缩脚，只会把脚压成细针，长度几乎不变（11.70 vs 目标 9.90）。 */
    local s = mmd2maxBipedScale node
    local ns = [s.x, s.y, s.z]
    if axis == 4 then ns = [s.x * factor, s.y * factor, s.z * factor]
    else if axis == 1 then ns = [s.x * factor, s.y, s.z]
    else if axis == 2 then ns = [s.x, s.y * factor, s.z]
    else ns = [s.x, s.y, s.z * factor]
    local done = false
    if method == "node.scale" then
    (
        try ( node.scale = ns; done = true ) catch ( done = false )
    )
    else
    (
        try ( biped.setTransform node #scale ns false; done = true ) catch ( done = false )
    )
    done
)

fn mmd2maxProbeScaleAxis root =
(
    /* 返回 #(axis, method)。axis：1=x 2=y 3=z，0=三种方法都没能改变骨长。
       探针只在「Bip001 L UpperArm -> L Forearm」上做，代价极小。 */
    local result = #(0, "")
    local pnode = getNodeByName (root.name + " L UpperArm") exact:true
    local cnode = getNodeByName (root.name + " L Forearm") exact:true
    if pnode == undefined or cnode == undefined do
    (
        mmd2maxLog "缩放探针：找不到 L UpperArm / L Forearm，放弃轴探测。"
        return result
    )
    local base = mmd2maxSegLen pnode cnode
    if base < 0.0001 do
    (
        mmd2maxLog "缩放探针：默认骨长为 0，放弃轴探测。"
        return result
    )
    local s0 = mmd2maxBipedScale pnode
    mmd2maxLog ("缩放探针：基准长度=" + (base as string) + " 初始scale=" + (s0 as string))

    /* 方法一：biped.setTransform #scale，逐轴放大 1.5 倍看距离是否变化 */
    for axis = 1 to 3 do
    (
        if result[1] == 0 do
        (
            local s = [s0.x, s0.y, s0.z]
            if axis == 1 then s = [s0.x * 1.5, s0.y, s0.z]
            else if axis == 2 then s = [s0.x, s0.y * 1.5, s0.z]
            else s = [s0.x, s0.y, s0.z * 1.5]
            local okSet = false
            try ( biped.setTransform pnode #scale s false; okSet = true ) catch ( okSet = false )
            if okSet do
            (
                local d1 = mmd2maxSegLen pnode cnode
                mmd2maxLog ("  探针 biped.setTransform axis=" + (axis as string) \
                    + " : " + (base as string) + " -> " + (d1 as string))
                if (abs (d1 - base)) > 0.001 do result = #(axis, "biped.setTransform")
                try ( biped.setTransform pnode #scale s0 false ) catch ()
            )
        )
    )
    /* 方法二：直接写节点 transform 的 scale（等价于交互式 Scale 工具） */
    if result[1] == 0 do
    (
        for axis = 1 to 3 do
        (
            if result[1] == 0 do
            (
                local s = [s0.x, s0.y, s0.z]
                if axis == 1 then s = [s0.x * 1.5, s0.y, s0.z]
                else if axis == 2 then s = [s0.x, s0.y * 1.5, s0.z]
                else s = [s0.x, s0.y, s0.z * 1.5]
                local okSet = false
                try ( pnode.scale = s; okSet = true ) catch ( okSet = false )
                if okSet do
                (
                    local d1 = mmd2maxSegLen pnode cnode
                    mmd2maxLog ("  探针 node.scale axis=" + (axis as string) \
                        + " : " + (base as string) + " -> " + (d1 as string))
                    if (abs (d1 - base)) > 0.001 do result = #(axis, "node.scale")
                    try ( pnode.scale = s0 ) catch ()
                )
            )
        )
    )
    if result[1] == 0 then
    (
        mmd2maxLog "缩放探针：三种轴都改不了骨长 —— Biped 缩放被锁死，肢段长度无法脚本修正。"
    )
    else
    (
        mmd2maxLog ("缩放探针结论：长度轴=" + (result[1] as string) + " 方法=" + result[2])
    )
    result
)

fn mmd2maxSetScale node s method =
(
    if method == "node.scale" then
        try ( node.scale = s ) catch ()
    else
        try ( biped.setTransform node #scale s false ) catch ()
    true
)

fn mmd2maxFitOnePair pnode cnode desired axis method =
(
    /* 单轴闭环：每轮重量当前距离再乘 desired/cur，最多 5 轮。返回 #(最终误差, 轴)。 */
    for iter = 1 to 5 do
    (
        local cur = mmd2maxSegLen pnode cnode
        if cur < 0.0001 do exit
        if (abs (cur - desired)) < 0.05 do exit
        mmd2maxApplyScale pnode axis (desired / cur) method
    )
    local after = mmd2maxSegLen pnode cnode
    #((abs (after - desired)), axis)
)

fn mmd2maxFitSegments root limbs axis method =
(
    /* 闭环 + 逐段找轴。
       为什么要逐段找轴：Biped 的 #scale 三个分量对不同节点含义不同——
       肢骨通常 X = link 长度；但 **Pelvis 的 X 是 link 长度、横向髋宽在 Y/Z 上**，
       拿 X 去缩骨盆，髋关节的横向距离根本不变（上一轮髋差 1.45 就是这个原因）。
       所以：默认轴先试；不收敛就恢复原值、换轴重试，取第一个收敛的轴。
       **顺手记长度轴账**（`MMD2MAX_LENAXIS_MAP`）：这一段跑在任何摆位之前，父→子的
       方向还是 Biped 自己解出来的原样，「缩这根轴能改掉父→子距离」就是长度轴的实测
       定义。转角判据从此用这个数，不再现场挑「目前最顺的轴」（见 mmd2maxLenAxisOf）。
       脚本在同一个 Max 会话里重跑时账要清零，否则上一轮的骨名会留下来冒充这一轮的。 */
    MMD2MAX_LENAXIS_MAP = #()
    local okCount = 0
    local failCount = 0
    local totalErr = 0.0
    for item in limbs do
    (
        local parentName = item[1]
        local childName = item[2]
        local desired = item[3]
        local pnode = getNodeByName (root.name + (substring parentName 7 -1)) exact:true
        local cnode = getNodeByName (root.name + (substring childName 7 -1)) exact:true
        /* M1b 删掉了 `mode == "flat"` 这条豁免：导出侧从不写 flat（_limb_rows 只发
           link / leg / foot 三种），留着它就是一条从没跑过、也就从没验过的分支。
           脚段的长度本来就要缩（`段校验 L Foot -> L Toe0` 是第十轮以来的实读）。 */
        if pnode == undefined or cnode == undefined or axis == 0 then
        (
            failCount += 1
        )
        else
        (
            local before = mmd2maxSegLen pnode cnode
            local s0 = mmd2maxBipedScale pnode
            /* 骨盆是整副骨架的根：缩放它有可能把「身高/整体比例」一起改了，
               所以先记下 Pelvis->Head 的距离作为护栏，超差就回退。 */
            local guardNode = undefined
            local guardBefore = 0.0
            if parentName == "Bip001 Pelvis" do
            (
                guardNode = getNodeByName (root.name + " Head") exact:true
                if guardNode != undefined do guardBefore = mmd2maxSegLen pnode guardNode
            )
            local best = mmd2maxFitOnePair pnode cnode desired axis method
            local usedAxis = axis
            if best[1] > 0.05 do
            (
                /* 默认轴没收敛：恢复，再逐轴试（含「三轴等比例」），取第一个收敛的 */
                mmd2maxSetScale pnode s0 method
                for ax = 1 to 4 do
                (
                    if best[1] > 0.05 and ax != axis do
                    (
                        local r2 = mmd2maxFitOnePair pnode cnode desired ax method
                        if r2[1] <= 0.05 then
                        (
                            best = r2
                            usedAxis = ax
                        )
                        else
                        (
                            mmd2maxSetScale pnode s0 method
                        )
                    )
                )
                /* 全都没收敛：回滚到原 scale，绝不留下被压扁/拉细的骨。
                   （Foot 的 X 是宽度：上一版把脚宽 8.87 压到 2.58 却只让长度动了 2.7） */
                if best[1] > 0.05 do
                (
                    mmd2maxSetScale pnode s0 method
                    usedAxis = 0
                    mmd2maxLog ("  肢段 " + parentName + " -> " + childName \
                        + " 各轴都不收敛，已回滚到默认比例")
                )
            )
            local after = mmd2maxSegLen pnode cnode
            local err = abs (after - desired)
            if parentName == "Bip001 Pelvis" and guardNode != undefined and guardBefore > 0.001 do
            (
                local guardAfter = mmd2maxSegLen pnode guardNode
                /* 阈值 25%：拟合 Pelvis->Spine 本身就会让上半身整体移动（本模型约 6.7%），
                   那是正常的；只有像「整副骨架被等比缩放」这种量级的异常才回退。 */
                if (abs (guardAfter - guardBefore) / guardBefore) > 0.25 do
                (
                    mmd2maxSetScale pnode s0 method
                    after = mmd2maxSegLen pnode cnode
                    err = abs (after - desired)
                    local gmsg = "  骨盆缩放牵连整体骨架（Pelvis->Head "
                    gmsg = gmsg + (guardBefore as string) + " -> " + (guardAfter as string) + "），已回退"
                    mmd2maxLog gmsg
                )
            )
            totalErr += err
            if err < 0.05 then okCount += 1 else failCount += 1
            local msg = "  肢段 " + parentName + " -> " + childName
            msg = msg + " 目标=" + (desired as string)
            msg = msg + " 默认=" + (before as string)
            msg = msg + " 缩放后=" + (after as string)
            msg = msg + " 轴=" + (usedAxis as string)
            msg = msg + " 误差=" + (err as string)
            /* 附上父骨的 scale 前后值：区分「link 长度」与「比例缩放」两种语义，
               同时能看出骨盆被缩放时是否把整副骨架带偏 */
            msg = msg + " scale: " + (s0 as string) + " -> " + ((mmd2maxBipedScale pnode) as string)
            mmd2maxLog msg
            /* 长度轴记账。三个条件缺一不可：
               1) 这段真的收敛（err<=0.05）—— 不收敛说明 usedAxis 是「试出来的假轴」；
               2) usedAxis 是 1/2/3 —— 4 是「三轴等比例」，那没指出长度在哪根轴上；
               3) 父骨**只有这一个子关节** —— Pelvis 有三个（Spine + 两腿）、Hand 有五个，
                  它们的「长度方向」不是一个方向，记进去会把躯干/骨盆一起带跑。
               键用 Max 节点名（不是 PMX 骨名）：场景里已有同名 Biped 时节点会被改名，
               两边都用 node.name 才对得上。分两层 if 不赌 and 短路：children 会抛。 */
            if err <= 0.05 do
            if usedAxis >= 1 and usedAxis <= 3 do
            (
                local kids = #()
                try ( kids = pnode.children ) catch ( kids = #() )
                if kids.count == 1 and kids[1] == cnode do
                    append MMD2MAX_LENAXIS_MAP #(pnode.name, usedAxis)
            )
        )
    )
    local sumMsg = "Biped 肢段拟合: 成功=" + (okCount as string)
    sumMsg = sumMsg + " 失败=" + (failCount as string)
    sumMsg = sumMsg + " 累计误差=" + (totalErr as string)
    mmd2maxLog sumMsg
    mmd2maxReport "fit" "segment_count" "-" okCount
    mmd2maxReport "fit" "failed_segments" "-" failCount
    mmd2maxReport "fit" "cum_len_err_cm" "-" totalErr
    okCount
)

/* 把 PMX 人形骨的目标位置写到对应 Biped 关节（必须在 Figure 模式内调用）。
   passList 是「先父后子」分批的节点名数组；返回 #(成功, 失败, 节点缺失)。
   单独成函数是因为骨盆闭环要「平移 COM → 重摆一遍」：挪 COM 会把所有子关节带走，
   位置必须重新按 PMX 目标写回去。 */
/* 末位 skipDir 用**位置参数**，不用 MAXScript 的可选参数 `[name:type=default]`：
   13:06 那轮 `[skipDir:false]` 直接 `-- Syntax error: at [, expected =`（解析器把
   `false` 当类型名、然后等 `=`），一轮实跑就这么烧掉了。写法本身没验过第二遍，
   所以这里退到绝对安全的形状 —— 四个调用点各写一个 true/false，不赌语法。 */
fn mmd2maxPlaceBipedNodes root lines rowIds targetNames passList skipDir =
(
    /* skipDir:true = 这是「转角之后的重摆」：方向优先（#dir）的骨整根跳过。
       12:04 实测重摆对指链是净亏（落位只换回 1.4cm，转角却被吃掉 2 度以上），
       而脚链的方向就是用户判据。判据只有一处：mmd2maxChainPriority。 */
    local rows = mmd2maxRows lines
    local cHeadX = mmd2maxColPos lines "head_x"
    local cHeadY = mmd2maxColPos lines "head_y"
    local cHeadZ = mmd2maxColPos lines "head_z"
    local cTailX = mmd2maxColPos lines "tail_x"
    local placed = 0
    local poseFailed = 0
    local poseMissing = 0
    for nameList in passList do
    (
        for targetName in nameList do
        (
            if skipDir do
            if (mmd2maxChainPriority targetName) == #dir do continue
            local srcIdx = findItem targetNames targetName
            if srcIdx > 0 do
            (
                local row = rowIds[srcIdx]
                if row <= rows.count do
                (
                    local p = rows[row]
                    if p.count >= cTailX do
                    (
                        /* bones.tsv 的 pivot 三列按表头名取（head_x/head_y/head_z），
                           尾端列只是「这行是完整骨行」的宽度判据 */
                        local pos = [(p[cHeadX] as float), (p[cHeadY] as float), (p[cHeadZ] as float)]
                        local actual = root.name + (substring targetName 7 -1)
                        local node = getNodeByName actual exact:true
                        if node != undefined then
                        (
                            /* b 方案（#29）：肩点按父骨（锁骨）的骨盒射线给，不跟 Biped 抢。
                               读不到射线时保持 PMX 原目标，日志里两点对比着打。 */
                            if mmd2maxRayFromParent targetName do
                            (
                                local clavName = substituteString actual "UpperArm" "Clavicle"
                                local clavNode = getNodeByName clavName exact:true
                                local rayTgt = mmd2maxShoulderRayTarget clavNode pos
                                if rayTgt != undefined do
                                (
                                    /* 轴夹角 = 锁骨骨盒长度轴 与「锁骨 pivot→PMX 肩点」的夹角。
                                       只有「差」分不清 0.477→0.955 这一路是轴在转还是段长在变：
                                       差 ≈ 2·段长·sin(半角)，把角打出来下一轮就能离线对账。
                                       pivot 与两点重合的情形 helper 已经挡掉（seg<0.001 返回
                                       undefined），这里不重复判。 */
                                    local cbase = clavNode.transform.translationpart
                                    local rayAng = acos (dot (normalize (rayTgt - cbase)) (normalize (pos - cbase)))
                                    mmd2maxLog ("  摆位按骨盒射线 " + targetName + " 父骨=" + clavName \
                                        + " PMX肩点=" + (pos as string) + " 射线肩点=" + (rayTgt as string) \
                                        + " 差=" + (distance pos rayTgt as string) \
                                        + " 轴夹角=" + (rayAng as string) + "度")
                                    pos = rayTgt
                                )
                            )
                            /* 注意：biped.setTransform 真实签名是
                               biped.setTransform <node> #pos <point3> <bool_setkey> [limb:<node>]
                               根本没有 adjustLength 参数！第 4 参数是「是否打关键帧」，所以 setTransform
                               只能移动关节 pivot，不会重算 Biped 骨骼长度——肢骨长度在创建时锁死为默认
                               人体比例。长度必须用「缩放」修正，且必须在摆位之前完成
                               （见 mmd2maxFitSegments）。这里只负责把关节摆到 PMX 目标位置。
                               另一个例外是 Pelvis：它的 pivot 由 COM 决定，这里怎么设都不动，
                               所以另有 mmd2maxFitPelvis 闭环。 */
                            try ( biped.setTransform node #pos pos false; placed += 1 ) \
                                catch ( poseFailed += 1; \
                                        if poseFailed <= 5 do mmd2maxLog ("  摆位失败: " + actual) )
                        )
                        else
                        (
                            poseMissing += 1
                            if poseMissing <= 5 do mmd2maxLog ("  Biped 缺少节点: " + actual)
                        )
                    )
                )
            )
        )
    )
    #(placed, poseFailed, poseMissing)
)

/* 左右髋关节（Thigh pivot）的中点。实测：Biped 骨盆盒把两髋对称地挂在
   Pelvis pivot 两侧，所以这个中点恰好等于 Pelvis pivot —— 它才是「腿挂在哪」的把手。 */
fn mmd2maxHipMean root =
(
    local lNode = getNodeByName (root.name + " L Thigh") exact:true
    local rNode = getNodeByName (root.name + " R Thigh") exact:true
    if lNode == undefined or rNode == undefined do return undefined
    (lNode.transform.translationpart + rNode.transform.translationpart) / 2.0
)

/* COM 锚点的「目标位置」= 肢段表里左右腿根目标位置的中点（纯算术，不需要场景节点）。
   为什么还要一个「计划里的」中点：肢段长度必须在**缩放之前**就按 COM 量，
   而 mmd2maxHipMean 量的是**当前**髋位置（那时骨盆还没摆好）。
   实测（19:45 香菱）：Pelvis->L Thigh 照抄 PMX 腰→腿根 = 9.0278，
   于是 Biped 把两髋对称撑到 ±8.97；PMX 真正要的是相对髋中点 ±7.0013、
   dy/dz 全为 0 —— 左右髋各偏 2.2288。骨盘的子段一律按这个锚点重算才对得上。 */
fn mmd2maxHipAnchor limbs =
(
    local lp = undefined
    local rp = undefined
    for item in limbs do
    (
        if item[2] == "Bip001 L Thigh" do lp = item[4]
        if item[2] == "Bip001 R Thigh" do rp = item[4]
    )
    if lp == undefined or rp == undefined then undefined
    else [ (lp.x + rp.x) / 2.0, (lp.y + rp.y) / 2.0, (lp.z + rp.z) / 2.0 ]
)

/* 骨盆闭环：把「两髋中点」逼到 PMX 左右腿根骨的中点。
   Biped 的 Pelvis pivot 只跟着 COM（Bip001 对象）走，setTransform #pos 对它无效；
   髋关节又长在骨盆盒上，摆位循环给 Thigh 设的位置会被盒子吸回去（实测设 50 个节点
   全部「成功」，L Thigh 仍停在 [8.97,2.92,83.44]）。
   上一版把 Pelvis pivot 直接对齐 PMX 腰：腰在髋上方 1.8、后方 5.4，于是整条腿链
   被平移了 6~7cm（L Thigh 偏差 6.99 / 踝 1.57）。把手仍是 COM，但测量点换成髋中点
   —— 髋落对了腿才直，踝才够得到目标。收敛不了就停，绝不来回震荡。 */
fn mmd2maxFitPelvis root lines rowIds targetNames passList pelvisPos hipTarget =
(
    local pnode = getNodeByName (root.name + " Pelvis") exact:true
    local want = hipTarget
    if want == undefined do want = pelvisPos
    if pnode == undefined or want == undefined do
    (
        mmd2maxLog "  骨盆闭环：没有 Pelvis 节点，也没有髋/腰的目标位置，跳过。"
        return 0.0
    )
    local prevErr = 1000000.0
    local err = 0.0
    for iter = 1 to 4 do
    (
        local got = mmd2maxHipMean root
        if got == undefined do got = pnode.transform.translationpart
        err = distance got want
        local msg = "  骨盆闭环 第" + (iter as string) + "轮 髋中点目标=" + (want as string)
        msg = msg + " 实际=" + (got as string) + " 偏差=" + (err as string)
        msg = msg + " Pelvis=" + (pnode.transform.translationpart as string)
        if err < 0.05 or err >= prevErr do
        (
            mmd2maxLog (msg + " 停止")
            exit
        )
        prevErr = err
        local com = root.transform.translationpart
        local wantCom = com + (want - got)
        mmd2maxLog (msg + " COM " + (com as string) + " -> " + (wantCom as string))
        try ( biped.setTransform root #pos wantCom false ) \
            catch ( mmd2maxLog ("  骨盆闭环：平移 COM 失败 " + (getCurrentException() as string)) )
        /* COM 把整副骨架带走了，按 PMX 目标重摆一次 */
        mmd2maxPlaceBipedNodes root lines rowIds targetNames passList false
    )
    err
)

/* 创建 Biped 并按 PMX 人形骨位置摆位。
   bipedNodes 为输出数组（长度 = 骨骼行数），命中的位置写入对应 Biped 节点。 */
fn mmd2maxBuildBiped lines rowIds targetNames bipedNodes =
(
    /* lines 现在是 mmd2maxReadTable 的表对象（bones.tsv 的列名 + 数据行）：
       列号在本函数开头按名字取一次，下面 5 处读数都按名对位，不再写死 p[6]/p[9]。
       行数要用 mmd2maxRows 的行数 —— 表对象自己的 .count 是 3（列名/列号/数据行）。 */
    local rows = mmd2maxRows lines
    local cHeadX = mmd2maxColPos lines "head_x"
    local cHeadY = mmd2maxColPos lines "head_y"
    local cHeadZ = mmd2maxColPos lines "head_z"
    local cTailX = mmd2maxColPos lines "tail_x"
    local cTailY = mmd2maxColPos lines "tail_y"
    local cTailZ = mmd2maxColPos lines "tail_z"
    local headPos = undefined
    local footZ = undefined
    local centerPos = undefined
    local pelvisPos = undefined
    local hipPosL = undefined
    local hipPosR = undefined
    for k = 1 to rowIds.count do
    (
        local row = rowIds[k]
        if row <= rows.count do
        (
            local p = rows[row]
            if p.count >= cTailX do
            (
                /* pivot 三列：head_x / head_y / head_z（尾端列 tail_x 只是行宽判据） */
                local pos = [(p[cHeadX] as float), (p[cHeadY] as float), (p[cHeadZ] as float)]
                local t = targetNames[k]
                if t == "Bip001 Head" do headPos = pos
                if t == "Bip001" do centerPos = pos
                if t == "Bip001 Pelvis" do pelvisPos = pos
                if t == "Bip001 L Thigh" do hipPosL = pos
                if t == "Bip001 R Thigh" do hipPosR = pos
                if t == "Bip001 L Foot" or t == "Bip001 R Foot" do
                (
                    if footZ == undefined then footZ = pos.z
                    else footZ = amin footZ pos.z
                )
            )
        )
    )
    if centerPos == undefined do centerPos = [0.0, 0.0, 0.0]
    local height = 160.0
    if headPos != undefined and footZ != undefined do
        height = amax (headPos.z - footZ) 1.0
    /* 身高优先用导入网格的包围盒（含脚底）。
       原先只用「头顶 - 脚踝」，而脚踝高于脚底，导致 Biped 比角色矮一截。 */
    local meshMin = undefined
    local meshMax = undefined
    for obj in objects do
    (
        local cls = classOf obj
        if (cls == Editable_Mesh) or (cls == Editable_Poly) do
        (
            if meshMin == undefined do
            (
                meshMin = obj.min.z
                meshMax = obj.max.z
            )
            meshMin = amin meshMin obj.min.z
            meshMax = amax meshMax obj.max.z
        )
    )
    if meshMin != undefined and meshMax != undefined do
        height = amax (meshMax - meshMin) 1.0
    mmd2maxLog ("Biped 高度：" + (height as string))

    /* 实测朝向：脚尖(Toe0) - 脚踝(Foot) 的水平投影，**两脚取角平分线**。
       原先硬编码 -90 与模型无关（用户报告「朝向相反」）；改成实测又踩了第二个坑：
       只取左脚。MMD 的脚是外八的（香菱：左右各 6.69 度），拿左脚方向当全局朝向，
       等于把整副骨架拧向左脚 —— 左脚正好、右脚整体朝人物中线偏 13.4 度
       （用户截图：「你的 Bip001 R Foot 又向人物中间偏了」）。
       Biped 只有这一个全局偏航旋钮（各骨自身的 setTransform #pos 摆的是落位，
       摆不出偏航），所以它必须落在两脚的正中间，误差才会左右对半、谁都不偏。 */
    local fwdX = 0.0
    local fwdY = 1.0
    local footP = undefined
    local toeP = undefined
    local footP2 = undefined
    local toeP2 = undefined
    for k = 1 to rowIds.count do
    (
        local row = rowIds[k]
        if row <= rows.count do
        (
            local pp = rows[row]
            if pp.count >= cTailZ do
            (
                local pos = [(pp[cHeadX] as float), (pp[cHeadY] as float), (pp[cHeadZ] as float)]
                if targetNames[k] == "Bip001 L Foot" do footP = pos
                if targetNames[k] == "Bip001 L Toe0" do toeP = pos
                if targetNames[k] == "Bip001 R Foot" do footP2 = pos
                if targetNames[k] == "Bip001 R Toe0" do toeP2 = pos
            )
        )
    )
    /* 各脚的水平单位向量；两脚都有就取角平分线，缺一只才退回单脚 */
    local uL = undefined
    local uR = undefined
    if footP != undefined and toeP != undefined do
    (
        local dv = toeP - footP
        local flatLen = sqrt (dv.x * dv.x + dv.y * dv.y)
        if flatLen > 0.0001 do uL = [(dv.x / flatLen), (dv.y / flatLen), 0.0]
    )
    if footP2 != undefined and toeP2 != undefined do
    (
        local dv2 = toeP2 - footP2
        local flatLen2 = sqrt (dv2.x * dv2.x + dv2.y * dv2.y)
        if flatLen2 > 0.0001 do uR = [(dv2.x / flatLen2), (dv2.y / flatLen2), 0.0]
    )
    local gotFwd = false
    if uL != undefined and uR != undefined do
    (
        local bis = uL + uR
        local bisLen = sqrt (bis.x * bis.x + bis.y * bis.y)
        if bisLen > 0.0001 do
        (
            fwdX = bis.x / bisLen
            fwdY = bis.y / bisLen
            gotFwd = true
        )
    )
    if not gotFwd and uL != undefined do ( fwdX = uL.x; fwdY = uL.y; gotFwd = true )
    if not gotFwd and uR != undefined do ( fwdX = uR.x; fwdY = uR.y; gotFwd = true )
    /* 两脚各自相对所选朝向的偏角：这就是「外八」的量，也是下一轮判断
       R Foot 还偏不偏的基准 —— 两只脚应当各偏同样的度数、方向相反。
       注意 `as string` 的优先级：整个差值必须先括起来再转字符串。 */
    local spreadMsg = "  两脚外八："
    if uL != undefined do
    (
        local dL = (atan2 uL.y uL.x) - (atan2 fwdY fwdX)
        spreadMsg = spreadMsg + " L=" + (dL as string)
    )
    if uR != undefined do
    (
        local dR = (atan2 uR.y uR.x) - (atan2 fwdY fwdX)
        spreadMsg = spreadMsg + " R=" + (dR as string)
    )
    /* biped.createNew 的 angle 零点方向随 Max 版本/本地化而变：实测本环境
       angle=0 对应 +X（不是想当然的 +Y），按 +Y 推算会整整偏 90 度
       （用户截图：角色朝 +Y 而骨骼朝 -X）。因此不再猜测约定，改为「探针自校正」：
       先建一个 angle=0 的临时 Biped，实测它的真实朝向角，再换算出目标角。 */
    local probeAngle = 0.0
    local probe = undefined
    /* 探针选出来的转角写法：#bipedRotation / #tmAssign / #rotateCmd 三选一，
       真骨架的转角 pass 用它。探针没跑通就一直是 undefined，pass 整段跳过
       （不拿未证实的接口写真骨架）。判据 = 「局轴真的转了 15 度以上 **并且** 逼
       Biped 刷新一次之后还在」，只看写没抛错会被 MAXScript 缓存骗过去。 */
    local rotWay = undefined
    /* 记录探针前的场景对象：delete 只删根节点，Biped 的子节点有可能残留成游离对象，
       残留的 Bip001 会让后续 getNodeByName 取到「旧的假骨架」——这是隐蔽的错位源。 */
    local beforeObjs = for o in objects collect o
    /* 先把 Biped 接口整份反射出来（纯只读，不碰任何节点），这样同一轮里
       「橡皮筋开关叫什么、参数怎么给」在日志/文件里就有答案，不用为它单开一轮。 */
    mmd2maxLog (mmd2maxDumpBipedApi "Biped接口")
    try
    (
        probe = biped.createNew height 0.0 [0.0, 0.0, 0.0] arms:true neckLinks:1 \
            spineLinks:(mmd2maxSpineLinks()) \
            legLinks:3 fingers:(mmd2maxFingerCount()) fingerLinks:(mmd2maxFingerLinks()) \
            toes:1 toeLinks:1 trianglePelvis:false \
            triangleNeck:true
    )
    catch ()
    if probe != undefined do
    (
        local plf = getNodeByName (probe.name + " L Foot") exact:true
        local prf = getNodeByName (probe.name + " R Foot") exact:true
        if plf != undefined and prf != undefined do
        (
            local lp = plf.transform.translationpart
            local rp = prf.transform.translationpart
            local leftV = lp - rp
            /* forward = left × up（up=+Z）；(a,b,0)×(0,0,1) = (b,-a,0) */
            probeAngle = atan2 (-leftV.x) leftV.y
            /* 旋转接口探针（#27）：09:37 那轮的 结构向 表证明**骨盒不随 pivot 走** ——
               整条脊柱 实测倾 全是 -0.0456（竖直）、手指全是 179.988（笔直朝下）、
               Foot 实测倾 -90（平放），而同一行的 pivot偏差 只有 1e-5：落位全对，盒子不动。
               上一轮 `#rot` 报 "Unknown transform type, unsupplied" ⇒ 不是「Biped 没有转角」，
               是 token 猜错了：Max 自带的 scripts/BulletScripts/BsRetargetTools.ms 与
               BsBipedTools.ms 里全是 `biped.setTransform node #rotation <quat> <bool>`
               （Straighten 函数用它给 Toe 补 90°，Retarget 用它把源骨的 .transform.rotation
               整个写进 Biped 骨）。这里把候选 token 逐个试 × 两种实参类型（point3 当欧拉角 /
               quat），每次立刻回读局轴：**只有轴真的动了才算通**，并把通过的写法记进 rotWay。
               末尾再试两条不经过 biped.setTransform 的路（直接写 .transform / rotate 命令）。
               全部只碰这具必删的临时骨架。 */
            /* 10:17 那轮补上的两条判据（当时的日志只有 p3/q 两个「立刻读数」，不够）：
               1) **刷新后还在**才算通。`.transform=` 和 `rotate` 命令当时都读到 30 度，但那
                  可能只是 MAXScript 自己的缓存 —— Biped 求解器下一次 update 完全能把 TM 按
                  结构重算回去。所以每条路都再走 mmd2maxRefreshedMoveDeg（原值重发一次一直
                  好用的 #pos + update 0）拿「留」值，>= 15 度才采用。
               2) **要在 Figure 模式里测**。真骨架的转角 pass 就跑在
                  `mmd2maxFigureMode root.controller true` 与 `false` 之间（跟 #scale/#pos 一样，
                  Figure 模式里改的是结构性姿态，退出后同一条调用就成了做动作）。上一轮探针
                  没开 Figure 模式，`#rotation` 读出 0.0 度很可能就是这个原因，不能算否决。
                  这里先在 Figure 模式内测，退出后再补一次对照，两个数都进日志。 */
            local figOn = false
            try ( figOn = mmd2maxFigureMode probe.controller true ) catch ()
            /* 10:34 最重要的一条：`eulerToQuat [0.0, 0.0, 30.0]` **自己就抛**
               `Unable to convert: [0,0,30] to type: EulerAngles`（旧代码只打 errP，
               这条一直藏在日志里）。也就是说 09:37/10:17 两轮看到的 `#rotation q=0.0度`
               根本不是「Biped 不接受转角」—— 四元数压根没构造出来，`setTransform #rotation`
               到今天一次都没被真正调用过。Max 自带 BsBipedTools.ms:1491 的写法是
               `eulerToQuat (eulerAngles 0 y z) order:1`，照抄这个形式。 */
            local q30 = undefined
            try ( q30 = eulerToQuat (eulerAngles 0.0 0.0 30.0) order:1 ) catch ()
            local rotMsg = "  旋转接口探针：Figure模式=" + (figOn as string)
            if q30 == undefined do rotMsg = rotMsg + " quat构造失败"
            local tokList = #(#rotation, #rot, #rotate, #move, #worldRot, #rotEuler)
            for ti = 1 to tokList.count do
            (
                local tok = tokList[ti]
                local ax0 = mmd2maxBipedAxes plf
                local errP = "ok"
                try ( biped.setTransform plf tok [0.0, 0.0, 30.0] false ) \
                    catch ( errP = (getCurrentException() as string) )
                local ax1 = mmd2maxBipedAxes plf
                local mvP = mmd2maxAxisMoveDeg ax0 ax1
                local errQ = "ok"
                if q30 != undefined do
                    try ( biped.setTransform plf tok q30 false ) \
                        catch ( errQ = (getCurrentException() as string) )
                local ax2 = mmd2maxBipedAxes plf
                local mvQ = mmd2maxAxisMoveDeg ax1 ax2
                local keepQ = mmd2maxRefreshedMoveDeg plf ax1
                rotMsg = rotMsg + "  " + (tok as string) \
                    + " p3=" + (mvP as string) + "度" \
                    + " q=" + (mvQ as string) + "度/留=" + (keepQ as string) + "度"
                if errP != "ok" do rotMsg = rotMsg + "  p3抛错=" + errP
                if errQ != "ok" do rotMsg = rotMsg + "  q抛错=" + errQ
                /* 谁把轴转过了 15 度（要的是 30 度，允许只转一半的部分生效）**并且**
                   刷新后仍 >= 15 度，谁就是接口 */
                if rotWay == undefined and mvQ >= 15.0 and keepQ >= 15.0 do
                    rotWay = #bipedRotation
            )
            /* 两条不经过 biped.setTransform 的直写路，同样按「立刻 / 留」两个数判。
               10:34 实测：Figure 模式内 `.transform=` 直写 **0.0度**（Biped 在 Figure 模式
               里把外部直写整个忽略），而 `rotate` 命令 30.0度 且刷新后仍在 ⇒ 后者是唯一
               被证实有效的写法，且它是**增量**的（绕世界轴转一个角度），吃不了绝对矩阵 ——
               所以 rotWay 走到 #rotateCmd 时，转角 pass 必须换算成 轴+角度 再调它。 */
            local axA = mmd2maxBipedAxes plf
            local errTm = "ok"
            try ( plf.transform = rotateZMatrix 30.0 * plf.transform ) \
                catch ( errTm = (getCurrentException() as string) )
            local axB = mmd2maxBipedAxes plf
            local mvTm = mmd2maxAxisMoveDeg axA axB
            local keepTm = mmd2maxRefreshedMoveDeg plf axA
            local axC = mmd2maxBipedAxes plf
            local errRot = "ok"
            try ( rotate plf (angleaxis 30.0 [0.0, 0.0, 1.0]) ) \
                catch ( errRot = (getCurrentException() as string) )
            local axD = mmd2maxBipedAxes plf
            local mvRot = mmd2maxAxisMoveDeg axC axD
            local keepRot = mmd2maxRefreshedMoveDeg plf axC
            rotMsg = rotMsg + "  .transform=" + (mvTm as string) + "度/留=" \
                + (keepTm as string) + "度"
            rotMsg = rotMsg + "  rotate命令=" + (mvRot as string) + "度/留=" \
                + (keepRot as string) + "度"
            if errTm != "ok" do rotMsg = rotMsg + "  TM抛错=" + errTm
            if errRot != "ok" do rotMsg = rotMsg + " rotate抛错=" + errRot
            if rotWay == undefined and keepTm >= 15.0 do rotWay = #tmAssign
            if rotWay == undefined and keepRot >= 15.0 do rotWay = #rotateCmd
            /* 退出 Figure 模式再对照一次 `#rotation`：如果「模式内留=0、模式外留>0」，
               pass 的摆放位置就得重想；两个数并排放日志里，不用猜。 */
            try ( mmd2maxFigureMode probe.controller false ) catch ()
            local axE = mmd2maxBipedAxes plf
            local errOut = "ok"
            if q30 != undefined do
                try ( biped.setTransform plf #rotation q30 false ) \
                    catch ( errOut = (getCurrentException() as string) )
            local axF = mmd2maxBipedAxes plf
            local mvOut = mmd2maxAxisMoveDeg axE axF
            local keepOut = mmd2maxRefreshedMoveDeg plf axE
            rotMsg = rotMsg + "  退出Figure(#rotation)=" + (mvOut as string) \
                + "度/留=" + (keepOut as string) + "度"
            if errOut != "ok" do rotMsg = rotMsg + " 退出后抛错=" + errOut
            if rotWay != undefined do
                rotMsg = rotMsg + "  采用=" + (rotWay as string)
            mmd2maxLog rotMsg
            /* ===== 橡皮筋基准：搬一个关节，子树到底跟多少 =====
               「转角甩开子骨落位」「重摆吃掉子骨转角」这对拉扯的来源就是 Biped 在
               Figure 模式里搬父关节会带着子树走。这里不调任何新接口，只把「跟了多少
               厘米」量成一个数记进日志；等上面那份 biped_api.txt 给出橡皮筋开关的
               真名，下一轮在同一处再量一次，两个数一比就知道那个开关管不管事
               —— 判据照旧是行为，不是「调用没抛错」（#pos 就是接受参数而根本不转的先例）。
               上面刚退出 Figure 模式，这里再进一次：真骨架的摆位/重摆全跑在模式内。 */
            try ( mmd2maxFigureMode probe.controller true ) catch ()
            local rbPairs = #()
            append rbPairs #("L Calf", "L Foot")
            append rbPairs #("L Foot", "L Toe0")
            append rbPairs #("L Clavicle", "L UpperArm")
            append rbPairs #("L Finger3", "L Finger31")
            for pr in rbPairs do
            (
                local pNode = getNodeByName (probe.name + " " + pr[1]) exact:true
                local cNode = getNodeByName (probe.name + " " + pr[2]) exact:true
                if pNode != undefined and cNode != undefined do
                (
                    local p0 = pNode.transform.translationpart
                    local c0 = cNode.transform.translationpart
                    try ( biped.setTransform pNode #pos (p0 + [0.0, 0.0, 5.0]) false ) catch ()
                    local p1 = pNode.transform.translationpart
                    local c1 = cNode.transform.translationpart
                    mmd2maxLog ("  橡皮筋基准 " + pr[1] + "->" + pr[2] \
                        + " 父动=" + ((distance p0 p1) as string) \
                        + " 子跟=" + ((distance c0 c1) as string) + "cm")
                    /* 复位：不然后一对的读数被前一对污染 */
                    try ( biped.setTransform pNode #pos p0 false ) catch ()
                )
            )
            /* ===== b 方案的几何前提自检：肩点真的落在锁骨骨盒射线上吗 =====
               mmd2maxShoulderRayTarget 假定「UpperArm pivot = 锁骨 pivot + 骨盒长度轴 × 段长」。
               这条不成立的话 b 方案整个白做，所以先在弃用骨架上量：拿锁骨自己的轴去
               反推肩点，与真实肩点差多少厘米。差得远 = `mmd2maxBipedAxis` 挑错了轴，
               真骨架那遍就别信射线目标（它自己会退回 PMX 目标，但日志要看得见）。 */
            local kNode = getNodeByName (probe.name + " L Clavicle") exact:true
            local sNode = getNodeByName (probe.name + " L UpperArm") exact:true
            if kNode != undefined and sNode != undefined do
            (
                local sp = sNode.transform.translationpart
                local rayT = mmd2maxShoulderRayTarget kNode sp
                if rayT == undefined do
                    mmd2maxLog "  锁骨射线自检：读不到骨盒长度轴（真骨架那遍会退回 PMX 目标）"
                if rayT != undefined do
                    mmd2maxLog ("  锁骨射线自检 射线反推肩点 与 真实肩点差=" \
                        + ((distance rayT sp) as string) + "cm")
            )
            try ( mmd2maxFigureMode probe.controller false ) catch ()
        )
        delete probe
    )
    /* 清掉探针残留：任何「探针前不存在」的 Biped 对象都要删干净，
       否则残留节点会与真骨架同名/近名，后续按名字取节点就会取错。 */
    local leftover = 0
    local toDelete = #()
    for o in objects do
    (
        if findItem beforeObjs o == 0 do
        (
            local isBiped = false
            try ( if (classOf o.controller) == Biped_Control do isBiped = true ) catch ()
            try ( if (classOf o) == Biped_Object do isBiped = true ) catch ()
            if isBiped do append toDelete o
        )
    )
    /* 先收集再删除：边遍历 objects 边 delete 会跳过对象 */
    for o in toDelete do
    (
        try ( delete o; leftover += 1 ) catch ()
    )
    if leftover > 0 do mmd2maxLog ("清理探针残留 Biped 节点：" + (leftover as string))
    local wantAngle = atan2 fwdY fwdX
    local facing = wantAngle - probeAngle
    local facingMsg = "实测朝向=(" + (fwdX as string) + "," + (fwdY as string) + ")"
    facingMsg = facingMsg + " 目标角=" + (wantAngle as string)
    facingMsg = facingMsg + " 探针基准角(angle=0 时)=" + (probeAngle as string)
    facingMsg = facingMsg + " 采用 facing=" + (facing as string)
    facingMsg = facingMsg + spreadMsg
    mmd2maxLog facingMsg

    local root = undefined
    /* COM 建在哪：Biped 的 Pelvis pivot 只能跟着 COM（Bip001 对象）走——实测给
       "Bip001 Pelvis" 调 setTransform #pos 完全无效（香菱：腰在 z=83.44，Pelvis 仍停在
       センター 的 68.29）。髋关节挂在骨盆盒上，于是整条腿被拽低 12.75，膝部只能折起来
       （用户截图「大腿小腿折叠」）。所以 COM 直接用 MMD 腰（Pelvis 的映射骨），
       建完再按「Pelvis 实际 vs 目标」闭环平移。 */
    local comStart = centerPos
    if pelvisPos != undefined do comStart = pelvisPos
    mmd2maxLog ("COM 落位：センター=" + (centerPos as string) \
        + " 腰(Pelvis)=" + ((if pelvisPos == undefined then centerPos else pelvisPos) as string) \
        + " 采用=" + (comStart as string))
    try
    (
        root = biped.createNew height facing comStart arms:true neckLinks:1 \
            spineLinks:(mmd2maxSpineLinks()) \
            legLinks:3 fingers:(mmd2maxFingerCount()) fingerLinks:(mmd2maxFingerLinks()) \
            toes:1 toeLinks:1 trianglePelvis:false \
            triangleNeck:true
    )
    catch ( mmd2maxLog ("biped.createNew 抛错：" + (getCurrentException())) )
    if root == undefined do throw "创建 Biped 失败：请确认 3ds Max 的 Biped（Character Studio）可用。"
    local angleMsg = "已创建 Biped：" + root.name
    angleMsg = angleMsg + "，采用角度=" + (facing as string)
    mmd2maxLog angleMsg
    /* 诊断：输出 Biped 初始左右脚位置，用于核对 facing 约定与左右是否匹配。
       Biped 若朝向 +Y，其 L Foot 应落在 -X 一侧。 */
    local diagL = getNodeByName (root.name + " L Foot") exact:true
    local diagR = getNodeByName (root.name + " R Foot") exact:true
    if diagL != undefined and diagR != undefined do
    (
        /* Biped 节点没有 .pos 属性，必须从 transform 取平移分量 */
        local lp = diagL.transform.translationpart
        local rp = diagR.transform.translationpart
        local dmsg = "Biped 初始: L Foot=" + (lp as string)
        dmsg = dmsg + " R Foot=" + (rp as string)
        mmd2maxLog dmsg
    )
    if footP != undefined do
    (
        local cmsg = "角色侧: L Foot 目标位置=" + (footP as string)
        mmd2maxLog cmsg
    )

    local figOk = mmd2maxFigureMode root.controller true
    mmd2maxLog ("进入 Figure 模式：" + (figOk as string))
    /* pass 边界（M3）：注册表里每个 pass 收工时补一行 `pass/ran`，报告表的行序就是
       时间轴，Python 侧拿它做三件事 —— 顺序对不对（requires）、读数有没有被后来的
       pass 作废（invalidates ⇒ STALE）、每条读数是不是落在声明它的那一段里（归因）。
       这一行必须排在它自己那些读数**之后**：ran 是「收工」，不是「开工」。 */
    mmd2maxReport "pass" "ran" "biped_create" 1

    /* ===== 先修正肢段长度，再摆位 =====
       顺序至关重要：必须在「摆位之前」缩放。因为摆位会把子关节直接放到 PMX 目标上，
       此时量到的距离必然等于目标长度，缩放因子就是 1.0（等于没缩）——上一版就是
       栽在这里：骨骼网格仍是默认人体比例，于是小臂/小腿被缩成一小点、手掌接在大臂上。
       现在改为：默认姿势下先按真实段长把每节拉到位（闭环实测），再去摆位。 */
    /* 这里不再包 try/catch：以前读表失败会被吞成「肢段表条目：0」，然后走下面那句
       「跳过肢段拟合」——骨架保持 Biped 默认比例，日志却一句错都没有。肢段表是拟合的
       唯一事实来源，读不到/读空就是这一轮不成立，直接抛。 */
    local limbs = mmd2maxReadLimbs (MMD2MAX_LIMBS as string)
    mmd2maxLog ("肢段表条目：" + (limbs.count as string))
    if limbs.count == 0 do
    (
        local msg = "肢段表为空或读不到：" + (MMD2MAX_LIMBS as string)
        msg = msg + "（没有肢段表就没有闭环缩放，Biped 会保持默认人体比例）"
        throw msg
    )

    /* ===== 快照回滚自检（M4d）=====
       排在肢段表读进来之后、第一次 #scale 之前：这一刻的场景是「刚建好、还没被任何
       一刀写过」的 Biped，从这里拍基准最干净。它自己会扰动一根骨再退回来，所以
       必须排在真正干活那一段之前 —— 而且扰动是**当场撤销**的（读的是自己刚存的图），
       注册表里因此不给它声明作废任何读数：它留下的场景和进来时一样，
       而「一样」这件事由 rollback/pos_cm、box_cm、scale_cm、angle_deg 四行数字负责证明。
       注意第四行是第 27 轮之后才有的，而那一轮的教训恰恰是「前三行全绿不等于场景一样」：
       锁骨事后读出 89.5 度的长度轴偏移，双臂落位误差 0.955 → 1.957。而第 28 轮证明连
       第四行也拦不住它（它当场读 0.0）—— 这段自检扰动的是**真骨**，它对场景不是无害的，
       而它自己报不出这份代价。开关打开的那一轮要靠 placement 的 lock 行来响。 */
    if MMD2MAX_ROLLBACK do
    (
        local rb = mmd2maxRollbackSelfTest root limbs
        if rb != undefined then
        (
            mmd2maxReport "rollback" "saved" "-" rb[1]
            mmd2maxReport "rollback" "loaded" "-" rb[2]
            mmd2maxReport "rollback" "perturb_cm" "-" rb[3]
            mmd2maxReport "rollback" "pos_cm" "-" rb[4]
            mmd2maxReport "rollback" "box_cm" "-" rb[5]
            mmd2maxReport "rollback" "scale_cm" "-" rb[6]
            mmd2maxReport "rollback" "snap_cost_cm" "-" rb[7]
            mmd2maxReport "rollback" "perturbed_bone" "-" rb[8]
            mmd2maxReport "rollback" "angle_deg" "-" rb[9]
            local rbMsg = "回滚自检：" + (rb[8] as string) + " 撑大 30% 后位置差="
            rbMsg = rbMsg + (rb[3] as string) + " 退回后 位置差=" + (rb[4] as string)
            rbMsg = rbMsg + " 盒差=" + (rb[5] as string) + " scale 差=" + (rb[6] as string)
            rbMsg = rbMsg + " 局部轴朝向差=" + (rb[9] as string)
            rbMsg = rbMsg + "（存图动作自身的扰动=" + (rb[7] as string) + "）"
            mmd2maxLog rbMsg
        )
        else mmd2maxLog "回滚自检：基准拍不出来（骨名或输出目录不可用），本轮没有回滚载体"
        mmd2maxReport "pass" "ran" "rollback_selftest" 1
    )

    /* 骨盘的子段（L/R Thigh、Spine）把目标长度改写成「离 COM 锚点的距离」。
       为什么必须改：骨盆 pivot 只跟 COM 走，闭环又把 COM 抬到**髋中点**（不是 PMX 腰）。
       肢段表里的 desired 是 PMX 腰→子骨的长度（腿根 9.03、脊柱 6.49），照它缩就把
       髋撑到 ±8.97（PMX 要 ±7.00，左右各偏 2.23），Pelvis->Spine 也变成 6.49 而
       摆位后实测只有 3.51（段校验误差 2.98）。改按锚点量，缩放目标与摆位目标才同源。
       直接改写 item[3]：拟合与段校验读同一个值，两处不会各算各的。
       Python 侧的同一把尺在 verdicts.segment_expectation（门槛 0.05、锚点=两髋中点
       都由 test_the_ms_side_uses_the_same_inputs 钉在一起，改一边不改另一边就红）。 */
    local hipAnchor = mmd2maxHipAnchor limbs
    if hipAnchor != undefined do
    (
        for item in limbs do
        (
            if item[1] == "Bip001 Pelvis" and item[4] != undefined do
            (
                local want = distance hipAnchor item[4]
                if want > 0.05 do
                (
                    local rmsg = "  骨盆段目标按髋中点改写 " + item[2]
                    rmsg = rmsg + "：" + (item[3] as string) + " -> " + (want as string)
                    mmd2maxLog rmsg
                    item[3] = want
                )
            )
        )
    )
    if hipAnchor == undefined do mmd2maxLog "肢段表里没有左右腿根，骨盆子段目标沿用 PMX 腰。"
    local scaleAxis = 0
    local scaleMethod = ""
    if limbs.count > 0 do
    (
        local probed = mmd2maxProbeScaleAxis root
        scaleAxis = probed[1]
        scaleMethod = probed[2]
        if scaleAxis > 0 do mmd2maxFitSegments root limbs scaleAxis scaleMethod
    )
    if scaleAxis == 0 do mmd2maxLog "未探到可用的长度轴，跳过肢段拟合（骨架比例将保持 Biped 默认）。"
    mmd2maxReport "pass" "ran" "fit_segments" 1

    /* 手部粗细：Biped 默认手指远比网格粗，按网格实测半径收缩横截面。
       必须放在肢段拟合之后——拟合用的 setTransform #scale 会整组覆盖 scale。
       这项是纯外观（不改关节位置），所以文件缺失=按默认粗细继续，但表头缺列照抛。 */
    local widths = mmd2maxReadWidths (MMD2MAX_WIDTHS as string)
    if widths.count > 0 do
    (
        local wid = mmd2maxApplyWidths root widths
        mmd2maxLog ("Biped 手部粗细：调整=" + (wid as string) + " / " + (widths.count as string))
    )
    if widths.count == 0 do mmd2maxLog "未读到手部粗细表，跳过（手掌/手指将保持 Biped 默认粗细）。"
    mmd2maxReport "pass" "ran" "apply_widths" 1

    /* 先看核心链（父->子顺序），再摆手指等其余节点。
       "Bip001"（COM）不在名单里：它由 createNew + 骨盆闭环负责，摆位循环碰它
       会把闭环的修正覆盖回 センター（MMD 的 センター 常常比腰低十几厘米）。 */
    local ordered = #("Bip001 Pelvis", "Bip001 Spine", "Bip001 Spine1", \
        "Bip001 Spine2", "Bip001 Neck", "Bip001 Head", "Bip001 L Clavicle", \
        "Bip001 L UpperArm", "Bip001 L Forearm", "Bip001 L Hand", \
        "Bip001 R Clavicle", "Bip001 R UpperArm", "Bip001 R Forearm", "Bip001 R Hand", \
        "Bip001 L Thigh", "Bip001 L Calf", "Bip001 L Foot", \
        "Bip001 R Thigh", "Bip001 R Calf", "Bip001 R Foot")
    local passList = #(ordered)
    /* limbs.tsv 第 9 列 mode：非空的行的**子关节**退出「其余节点」那一遍摆位，
       交由后续专门阶段处理。当前带 mode 的行有两类，都不影响腿：
         - mode=foot 的子关节是 Toe0 —— 它不在 ordered 里，靠这一列退出主摆位，
           改由「脚趾两段」那一段亲手把 pivot 摆到 MMD 的 つま先（脚掌 ball）上；
           脚掌的两段结构就靠这个关节。
         - mode=leg（#36）的子关节是 L/R Calf —— 它本来就在 ordered 里，
           findItem ordered t == 0 先把它挡掉了，所以这一列对摆位名单**没有影响**；
           它存在的意义只是给 mmd2maxLegRoll 指认「哪一行的 child 是膝」，
           而不是靠骨名匹配（取舍表 mmd2maxChainPriority 仍是全脚本唯一按名字分支的地方）。 */
    local flatChildren = #()
    for item in limbs do
        if item.count >= 5 and item[5] != "" do append flatChildren item[2]
    local rest = #()
    for k = 1 to targetNames.count do
    (
        local t = targetNames[k]
        if t != "Bip001" and findItem ordered t == 0 \
           and findItem flatChildren t == 0 do
            append rest t
    )
    append passList rest

    local pose = mmd2maxPlaceBipedNodes root lines rowIds targetNames passList false
    /* 闭环的测量目标：PMX 左右腿根骨（映射到 L/R Thigh）的中点。
       没有这两根骨（非人形/缺骨）才退回腰（Pelvis）自己的目标。 */
    local hipTarget = undefined
    if hipPosL != undefined and hipPosR != undefined do
        hipTarget = [ (hipPosL.x + hipPosR.x) / 2.0, \
                      (hipPosL.y + hipPosR.y) / 2.0, \
                      (hipPosL.z + hipPosR.z) / 2.0 ]
    local pelvisErr = mmd2maxFitPelvis root lines rowIds targetNames passList pelvisPos hipTarget
    local poseMsg = "Biped 摆位: 成功=" + (pose[1] as string)
    poseMsg = poseMsg + " 失败=" + (pose[2] as string)
    poseMsg = poseMsg + " 节点缺失=" + (pose[3] as string)
    poseMsg = poseMsg + " 髋中点残余偏差=" + (pelvisErr as string)
    poseMsg = poseMsg + " 锚点=" + ((if hipTarget == undefined then "腰" else "髋中点") as string)
    mmd2maxLog poseMsg
    mmd2maxReport "pose" "placed_nodes" "-" pose[1]
    mmd2maxReport "pose" "failed_nodes" "-" pose[2]
    mmd2maxReport "pose" "missing_nodes" "-" pose[3]
    mmd2maxReport "pose" "hip_residual_cm" "-" pelvisErr
    mmd2maxReport "pass" "ran" "pose_initial" 1

    /* ===== 踝关节优先复核 =====
       Biped 的脚骨有最短长度限制（本模型 11.70，而 MMD 的 踝->趾 只有 9.90）。
       把 Toe0 摆到目标后，脚踝会被顶回去约 1.8 —— 用户看到的「Toe0 在正确位置、
       但 Foot 对不上脚」就是这个。
       这里最后再把脚踝摆一次：宁可脚趾骨长一点，也不能让 IK 支点（脚踝）偏。
       方向由前面 Toe0 的落位确定，纯平移不改变朝向。 */
    for footName in #("Bip001 L Foot", "Bip001 R Foot") do
    (
        local srcIdx = findItem targetNames footName
        if srcIdx > 0 do
        (
            local row = rowIds[srcIdx]
            if row <= rows.count do
            (
                local pp = rows[row]
                if pp.count >= cTailX do
                (
                    local want = [(pp[cHeadX] as float), (pp[cHeadY] as float), (pp[cHeadZ] as float)]
                    local actualName = root.name + (substring footName 7 -1)
                    local fnode = getNodeByName actualName exact:true
                    if fnode != undefined do
                    (
                        local beforeDev = distance (fnode.transform.translationpart) want
                        try ( biped.setTransform fnode #pos want false ) catch ()
                        local afterDev = distance (fnode.transform.translationpart) want
                        local fmsg = "  踝关节复核 " + footName
                        fmsg = fmsg + " 偏差 " + (beforeDev as string) + " -> " + (afterDev as string)
                        mmd2maxLog fmsg
                    )
                )
            )
        )
    )

    mmd2maxReport "pass" "ran" "ankle_recheck" 1
    /* ===== 脚趾两段：Toe0 接在 Foot 末端、整块呆在鞋里 =====
       用户原话（看截图后，两轮）：
         第一轮：「Bip001 L/R Toe0 太大了。要求：Toe0 的 pivot 接在 Bip001 Foot 的
                  终侧（link 末端），并且它的宽和高与 Foot 末端一致；
                  长度按图中蓝框（贴着脚尖的一小段）」。
         第二轮：照「与 Foot 末端一致」量到 5.86×4.19，比 3.57 的趾长还高，
                  侧视里上沿戳出鞋面 ⇒「我要整块骨头呆在鞋子里」。
       ⇒ pivot 与趾长两条不变，宽高改成「脚尖那一段网格装得下的中心对称盒子」。
       limbs.tsv 的脚段带着 Python 从网格量好的目标值（列 10~18）：
         10 踝→鞋尖   11 脚掌→鞋尖（沿趾方向）  12~14 鞋尖坐标  15 脚尖处宽  16 脚尖处高
         17 足背处宽  18 足背处高（#37：同一句话管到 Foot 这根骨自己）
       上一版踩的坑：Python 端把「+y 最大」当鞋尖，而这个坐标系里脚掌指向 **-y**，
       量到的其实是脚后跟那一片 —— toe_len 被写成 12.28（比整只脚 11.23 还长），
       Max 端照着它把 Toe0 撑成一根大骨头。改对之后实测：趾长 3.57。

       为什么三根轴都走闭环：Biped 的 #scale 是三根轴**各自的比例**，不是厘米
       （实跑读到趾骨 scale=(12.27,8.59,8.59) 而它的包围盒才 1.04cm），
       所以只能「量本地包围盒跨度 → 单轴乘 target/cur → 复测」。
       顺序：先宽、再高、最后长度 —— 长度留在最后，缩完粗细也不会把它带跑。 */
    for item in limbs do
    (
        if item.count >= 10 and item[5] == "foot" do
        (
            local cName = item[2]
            local ballPos = item[4]
            local toeLen = item[7]
            local tipPos = item[8]
            local toeW = item[9]
            local toeH = item[10]
            local cnode = getNodeByName (root.name + (substring cName 7 -1)) exact:true
            if cnode != undefined and ballPos != undefined \
               and toeLen > 0.001 and tipPos != undefined then
            (
                local msg = "  脚趾 " + cName
                /* 1) pivot 落在 Foot link 末端（= 脚掌关节 つま先 的 head） */
                local poseOk = false
                try ( biped.setTransform cnode #pos ballPos false; poseOk = true ) \
                    catch ( poseOk = false )
                local dev = distance (cnode.transform.translationpart) ballPos
                msg = msg + " pivot偏差=" + (dev as string)
                if not poseOk do msg = msg + "(摆位失败)"

                /* 2) 三根轴各归各位：长度轴 = 趾长，另两根 = 脚尖处能量下的宽/高。
                   「哪根是宽、哪根是高」的判据与 Foot 段共用（见 mmd2maxFitCrossSection：
                   谁更贴近世界 Z 谁是高，不看谁现在更粗）。 */
                local dir = normalize (tipPos - ballPos)
                mmd2maxLogLocalBox ("趾盒框 " + cName) cnode
                local cs = mmd2maxFitCrossSection cnode dir toeW toeH
                if cs == undefined then
                    msg = msg + " 长度轴读取失败"
                else
                (
                    local ai = cs[7]
                    local lenBefore = mmd2maxLocalExtent cnode ai
                    local rL = mmd2maxFitBoneExtent cnode ai toeLen
                    msg = msg + " 趾长 " + (lenBefore as string) + " -> " + (rL[1] as string)
                    msg = msg + " 目标=" + (toeLen as string)
                    msg = msg + " 截面 " + (cs[5] as string) + "/" + (cs[6] as string)
                    msg = msg + " -> " + (cs[1] as string) + "/" + (cs[2] as string)
                    msg = msg + " 宽/高目标=" + (toeW as string) + "/" + (toeH as string)
                    msg = msg + " 宽高轴=" + (cs[3] as string) + "/" + (cs[4] as string)
                    msg = msg + " 长度轴=" + (ai as string) + " 迭代=" + (rL[2] as string)
                    if rL[3] do msg = msg + "(长度对缩放没反应，已停)"
                    if cs[10] or cs[11] do msg = msg + "(宽高对缩放没反应，已停)"
                )
                msg = msg + " 鞋尖=" + ((tipPos as string))
                mmd2maxLog msg
            )

            /* 3) Foot 自己的截面（#37）。用户原话：「你的脚的骨骼也并没有包在脚的里面，
                  实际上脚穿模出来了。」这一段以前是**真空**：widths.tsv 只有手，
                  肢段闭环只管长度，脚趾例程只管 Toe0 —— 全脚本没有一处量过 Foot 的宽高，
                  于是 round-9 回读的骨盒 8.00×6.61cm 一直撑在鞋里（同一只脚量到的鞋内切
                  盒子只有 3.93×1.53cm）。目标值就写在 limbs.tsv 脚段第 17/18 列，
                  由 Python 沿「踝→脚掌」轴逐段量网格（判据与第 15/16 列同一条）。
                  参考方向取**实测踝 pivot → 脚掌**，不取 PMX 名义方向：要缩的是这根骨盒，
                  量它此刻真正指向哪最不容易把宽高对调。 */
            local fName = item[1]
            local footW = 0.0
            local footH = 0.0
            if item.count >= 12 do ( footW = item[11]; footH = item[12] )
            local fnode = getNodeByName (root.name + (substring fName 7 -1)) exact:true
            if fnode != undefined and ballPos != undefined \
               and footW > 0.05 and footH > 0.05 do
            (
                local ankleP = fnode.transform.translationpart
                local fl = distance ankleP ballPos
                if fl > 0.5 then
                (
                    local fdir = (ballPos - ankleP) / fl
                    /* 缩之前先把默认骨盒的两个角打出来：它挂在轴的哪一侧，决定
                       「框住脚跟」这件事到底是能做的还是几何上不可能的。 */
                    mmd2maxLogLocalBox ("足盒框 " + fName) fnode
                    local fc = mmd2maxFitCrossSection fnode fdir footW footH
                    mmd2maxLogLocalBox ("足盒缩后 " + fName) fnode
                    if fc == undefined then
                        mmd2maxLog ("  足背 " + fName + " 长度轴读不到，截面未收")
                    else
                    (
                        local fmsg = "  足背 " + fName + " 截面 " + (fc[5] as string)
                        fmsg = fmsg + "/" + (fc[6] as string)
                        fmsg = fmsg + " -> 宽=" + (fc[1] as string)
                        fmsg = fmsg + " 高=" + (fc[2] as string)
                        fmsg = fmsg + " 宽/高目标=" + (footW as string) + "/" + (footH as string)
                        fmsg = fmsg + " 宽高轴=" + (fc[3] as string) + "/" + (fc[4] as string)
                        fmsg = fmsg + " 长度轴=" + (fc[7] as string)
                        fmsg = fmsg + " 迭代=" + (fc[8] as string) + "/" + (fc[9] as string)
                        if fc[10] or fc[11] do fmsg = fmsg + "(对缩放没反应，已停)"
                        mmd2maxLog fmsg
                    )
                )
                else mmd2maxLog ("  足背 " + fName + " 跳过：踝→脚掌只有 " + (fl as string))
            )
        )
    )

    mmd2maxReport "pass" "ran" "toe_box" 1
    /* ===== 摆位 <-> 转角 收敛循环（机制层）=====
       09:37 的结论是「pivot 全对、盒子不动」，那么能改方向的只有转角；而转角写法
       由上面那具必删的临时骨架现场探出来（rotWay）。探针没跑通就整段跳过 —— 不拿
       未证实的接口去写真骨架，本轮就仍然只是一轮测量。
       原来这里是一次转角 + 一次重摆，两边各吃掉对方一半：躯干的落位修好了
       （Neck 4.90->7.6e-06），指链的方向却被重摆吃掉（L Finger32 3.5->5.63）。
       现在改成反复做，直到**场景读数**达标（闸门见 mmd2maxConvergeGate，不是「这一遍没有
       骨需要转」）：
         每一遍 = （第 2 遍起）先按优先级重摆落位（跳过 #dir）→ 再把还差超过 MMD2MAX_DIRTOL 的骨转过来。
       遍数上限来自 MMD2MAX.CFG（MMD2MAX_CONVERGE_MAX，数值住在 mmd2max/passes.py 的注册表），
       防的是 A 的转角甩开 B、B 的重摆又甩开 A 这种来回。
       **撞上限不等于收敛**：循环停下来之后，必须把「跑了几遍 / 为什么停 / 判据到底达没
       达标」三件事报进判据通道，由 Python 侧 passes.audit_gate 互相对账。第十七轮实测
       方向超标 4→5→5、落位最大值三遍一动不动，是靠 `for passNo = 1 to 3` 撞上限退出的，
       而日志里那句 `收敛 第3遍: …` 与真达标完全同形 —— 这就是「循环结束了被当成收敛了」。
       收尾是**两道写**：先把所有骨（含 #dir）摆回 PMX，再只转 #dir 的骨把方向补回来
       （见下面那段注释）。于是 #dir 的骨最终状态=方向+落位、#both/#pos 的骨最终状态=落位
       —— 取舍表（mmd2maxChainPriority）说的还是同一件事，变的只是最后一道写是谁。
       每遍打一次 结构向 打分：那是 Figure 模式内的相对数，权威读数仍是循环外
       退出 Figure 模式之后的 结构向 复核表。 */
    local rotTotal = 0
    local rotBetter = 0
    local rotWorse = 0
    local rotErr = 0
    local lastAttempt = 0
    local lastPass = 0
    local convergeIters = 0
    local convergeDirOver = 0
    local convergeExit = "ceiling"
    for passNo = 1 to MMD2MAX_CONVERGE_MAX do
    (
        if passNo > 1 do
        (
            local poseN = mmd2maxPlaceBipedNodes root lines rowIds targetNames passList true
            mmd2maxLog ("第" + (passNo as string) + "遍重摆(方向优先的骨跳过): 成功=" \
                + (poseN[1] as string) + " 失败=" + (poseN[2] as string) \
                + " 节点缺失=" + (poseN[3] as string))
        )
        local rp = mmd2maxRotatePass root lines rowIds targetNames limbs rotWay passNo false
        rotTotal += rp[1]
        rotBetter += rp[2]
        rotWorse += rp[3]
        rotErr += rp[4]
        lastAttempt = rp[1]
        lastPass = passNo
        local sc = mmd2maxFitScore root lines rowIds targetNames limbs
        mmd2maxLog ("收敛 第" + (passNo as string) + "遍: 转角尝试=" + (rp[1] as string) \
            + " 改善=" + (rp[2] as string) + " 未改善=" + (rp[3] as string) \
            + " 抛错=" + (rp[4] as string) + " | 方向超标=" + (sc[1] as string) \
            + " 落位偏差和=" + (sc[2] as string) + " 落位最大=" + (sc[3] as string))
        local pn = passNo as string
        mmd2maxReport "converge" "raised" pn rp[4]
        mmd2maxReport "converge" "not_improved" pn rp[3]
        mmd2maxReport "converge" "dir_over" pn sc[1]
        mmd2maxReport "converge" "place_sum_cm" pn sc[2]
        mmd2maxReport "converge" "place_max_cm" pn sc[3]
        convergeIters = passNo
        convergeDirOver = sc[1]
        /* 出口只由读数决定。`rp[1] == 0`（这一遍没人要转）以前是退出条件，现在它
           只是日志里的一个数：方向超标为 0 才叫达标，尝试为 0 而读数没到 0 不算。 */
        if (mmd2maxConvergeGate sc[1]) do
        (
            convergeExit = "gate"
            break
        )
    )
    local gateHit = (convergeExit == "gate")
    mmd2maxLog ("收敛闸门: 出口=" + (convergeExit as string) \
        + " 遍数=" + (convergeIters as string) + "/" + (MMD2MAX_CONVERGE_MAX as string) \
        + " 方向超标=" + (convergeDirOver as string) + " 判据达标=" + (gateHit as string))
    mmd2maxReport "converge" "iters" "-" convergeIters
    mmd2maxReport "converge" "exit_reason" "-" convergeExit
    mmd2maxReport "converge" "gate_satisfied" "-" (if gateHit then "true" else "false")
    mmd2maxReport "pass" "ran" "converge" 1
    /* ===== 大腿扭转闭环（#36，两膝被拽向中线 = 用户看到的「内八」）=====
       排在收尾**之前**，理由只有一条：转完大腿会把踝甩出去、并且把整条脚链的局轴
       带着转过同样的角度，而收尾那两道写（先按先父后子全摆位、再只转 #dir 的骨）
       干的恰好就是「把踝摆回来 + 把脚链方向补回来」。放在收尾之后就等于把这两件
       事留给一个不存在的第三次收尾。 */
    local legRolled = mmd2maxLegRoll root limbs rotWay
    mmd2maxReport "pass" "ran" "thigh_roll" 1
    if (lastAttempt > 0 or legRolled > 0) do
    (
        /* 收尾（2026-11-02 用户定「顺序换过来」）：**先全摆，再只转方向优先的骨**。
           旧写法是「重摆但整根跳过 #dir」，于是 #dir 的骨自上一遍父骨转角之后就再没人管：
           13:11 实测 `转角1:Bip001 L Hand 夹角=43.1174->0.0` 把手掌转了 43 度，
           五根指链跟着甩出去，`结构向 ... L Finger32 pivot偏差=10.6619`、
           L Finger42=8.70、L Finger0=2.46，而右手（那一遍没转 Hand）全在 0.15 以内
           —— 左手指节整条不在模型手上，比 Forearm 那 3.4 度大一个数量级。
           脚链同理：`校验 Bip001 L Foot 偏差=1.44357` vs R Foot 0.108101。
           现在收尾先把**所有**骨（含 #dir）按先父后子摆回 PMX，再单独转一遍 #dir 的骨
           把方向补回来；#both/#pos 的骨就此停在「落位」那一侧 —— 取舍表说的还是同一件事，
           变的只是最后一道写是谁。 */
        local poseEnd = mmd2maxPlaceBipedNodes root lines rowIds targetNames passList false
        mmd2maxLog ("收尾重摆(全摆，含方向优先的骨): 成功=" + (poseEnd[1] as string) \
            + " 失败=" + (poseEnd[2] as string) + " 节点缺失=" + (poseEnd[3] as string))
        mmd2maxReport "pose_end" "placed_nodes" "-" poseEnd[1]
        mmd2maxReport "pose_end" "failed_nodes" "-" poseEnd[2]
        mmd2maxReport "pose_end" "missing_nodes" "-" poseEnd[3]
        local rd = mmd2maxRotatePass root lines rowIds targetNames limbs rotWay \
            (lastPass + 1) true
        mmd2maxLog ("收尾转角(只转方向优先的骨): 尝试=" + (rd[1] as string) \
            + " 改善=" + (rd[2] as string) + " 未改善=" + (rd[3] as string) \
            + " 抛错=" + (rd[4] as string))
        mmd2maxReport "rotate_end" "raised" "-" rd[4]
        mmd2maxReport "rotate_end" "not_improved" "-" rd[3]
        rotTotal += rd[1]
        rotBetter += rd[2]
        rotWorse += rd[3]
        rotErr += rd[4]
        /* 收尾是**两道写**算一个 pass；它整段可以合法不跑（门控在外面那个 if），
           所以 ran 排在 if 内部：没跑就不该有这一行。 */
        mmd2maxReport "pass" "ran" "finish_pose" 1
    )
    /* ===== 脚趾复摆（#38）=====
       排在收尾**之后**、退出 figure 模式之前：收尾那两道写是最后动过链的地方，
       谁在它们之后被拽走就归谁管（round-10 实测 Toe0 被拽走 4.74cm，而收尾转角
       尝试=0 再没人回头）。不按 legRolled 加门控 —— 容差本身就是门控：摆到位的
       趾骨 dev≈1e-06 会被直接跳过，只有真被拽走的才补一刀，所以它也顺手当第九轮
       那种「没人碰它」情况下的保险。 */
    local toeSnapped = mmd2maxToeSnapBack root limbs "residual_after_roll_cm"
    if toeSnapped > 0 do
    (
        mmd2maxLog ("脚趾复摆：净改善 " + (toeSnapped as string) + " 根跟回 PMX 脚掌落位")
    )
    mmd2maxReport "pass" "ran" "toe_snap_roll" 1
    /* ===== 足盒接趾（#41）：链已经摆完，最后把骨盒的**远端面**拉到 PMX 脚掌 =====
       用户两张对照图：左边 = 第十六轮（一根斜牙签从踝往下探，趾骨盒悬在它前方），
       右边 = 一块落地的厚盒子，趾方块贴它的正面。牙签的「细」由 足背截面 管（#37，
       按导出侧 FOOT_BOX_FLAT 量到的是踝→鞋底 clearance，不再是被逐带取紧的 2.32），
       「悬空」这一段以前**没人管**：肢段拟合跑在摆位之前、判据又是两 pivot 的三维
       间距（11.228），盒前端却只到 7.935 —— 差的 3.29cm 就是图里那块空气。
       为什么排在这里而不是 脚趾两段 里：这里的长度轴与踝 pivot 都是**转角和收尾之后**
       的最终值。提前拟合会把一个已经不在原地的轴当基准 —— 转角 pass 恰恰会把长度轴
       转到指向目标（along 从投影 9.736 变成全长 11.228），收尾重摆还会再把踝挪
       1.44cm（第十六轮实测 校验 Bip001 L Foot 偏差=1.44357）。
       target 用 PMX 的 つま先（limbs 第 4 列），不用回读到的 Toe0 位置：#scale 会把
       子节点沿轴一起推走（探针 biped_gap2：子节点位移 3.42361 == 盒远端位移 3.42361），
       拿被自己推走的东西当目标是追尾巴；推走的由紧随其后的 复摆 摆回来（判据同为
       「离 PMX つま先 多远」），退出 figure 模式不改这两者（探针 B2 after-exit 与
       B1 in-figure 逐字相同）。
       开关只有一个：MMD2MAX_FOOTREACH=false ⇒ 整段跳过，逐字退回第十六轮。 */
    if MMD2MAX_FOOTREACH do
    (
        /* 接趾这一刀是「推骨盒远端」的写，而它唯一的失效方式是**推过了头或推歪**：
           闭环把倍率限在 0.2~5，限幅本身就能把盒子一次推飞。所以这里在动手前拍一份
           基准，动完手逐根比「盒远端到 PMX 脚掌的距离」有没有比动手之前更远
           （mmd2maxFitBoneReach 第 6 列就是动手前那个数）。变远了就整段退回基准 ——
           注意退回的是「接趾」这一段，前面的摆位、转角、扭转一概不动，因为基准就是
           在它们全部做完、接趾还没开始的那一刻拍的。
           判据阈值 MMD2MAX_RBTRIG 走 CFG（mmd2max/targets.py），不在这里写死字面量。 */
        local rbFig = undefined
        local rbMarks = undefined
        local rbSaveOk = false
        local rbBefore = 0.0
        local rbAfter = 0.0
        local rbWorse = 0
        if MMD2MAX_ROLLBACK do
        (
            rbFig = mmd2maxFigPath "foot"
            if rbFig != undefined do
            (
                rbMarks = mmd2maxMarkList root limbs
                local rbSave = mmd2maxFigOp root rbFig true
                /* 存图失败不关掉整个守卫：基准（rbMarks）还在内存里，那三列「比动手前
                   更远没有」照样量得到，照样要报。只是**退回**这一步没法做（没有图可读），
                   所以下面那个 if 多加一个 rbSaveOk。把守卫整段关掉的话，这一轮就变成
                   「什么都没量」，而「什么都没量」在这个项目里从来不是失败的意思。 */
                rbSaveOk = rbSave[1]
            )
        )
        for item in limbs do
        (
            if item.count >= 10 and item[5] == "foot" do
            (
                local frName = item[1]
                local frBall = item[4]
                local frNode = getNodeByName (root.name + (substring frName 7 -1)) exact:true
                if frNode != undefined and frBall != undefined do
                (
                    local frPivot = frNode.transform.translationpart
                    local frLen = distance frPivot frBall
                    if frLen > 0.5 then
                    (
                        local frAx = mmd2maxBipedAxis frNode ((frBall - frPivot) / frLen)
                        if frAx == undefined then
                            mmd2maxLog ("  足盒接趾 " + frName + " 长度轴读不到，未拉")
                        else
                        (
                            local r = mmd2maxFitBoneReach frNode frAx[1] frPivot frBall
                            if r == undefined then
                                mmd2maxLog ("  足盒接趾 " + frName + " 骨盒读不到，未拉")
                            else
                            (
                                local rmsg = "  足盒接趾 " + frName + " 差=" + (r[1] as string)
                                rmsg = rmsg + " 长度轴=" + (frAx[1] as string)
                                rmsg = rmsg + " 目标沿轴=" + (r[4] as string)
                                rmsg = rmsg + " 盒远端=" + (r[5] as string)
                                rmsg = rmsg + " 迭代=" + (r[2] as string)
                                if r[3] do rmsg = rmsg + "(长度对缩放没反应，已停)"
                                mmd2maxLog rmsg
                                mmd2maxLogLocalBox ("足盒拉后 " + frName) frNode
                                if MMD2MAX_ROLLBACK and rbMarks != undefined do
                                (
                                    local rbGap0 = abs r[6]
                                    local rbGap1 = abs r[1]
                                    if rbGap1 > rbGap0 + MMD2MAX_RBTRIG do rbWorse += 1
                                    rbBefore = amax rbBefore rbGap0
                                    rbAfter = amax rbAfter rbGap1
                                )
                            )
                        )
                    )
                    else mmd2maxLog ("  足盒接趾 " + frName + " 跳过：踝→脚掌只有 " + (frLen as string))
                )
            )
        )
        mmd2maxReport "pass" "ran" "foot_reach" 1
        /* ===== 接趾守卫（M4d）=====
           接趾这一刀唯一的失效方式是**把盒子推过了头**：闭环的倍率限在 0.2~5，限幅
           本身就能一次把骨盒推飞。上面逐根留了「动手前的盒隙」（mmd2maxFitBoneReach
           的第 6 列），这里比一比：哪一根现在比动手之前还远，就把整段接趾退回动手前
           拍的基准 —— 退的只有接趾，摆位/转角/扭转一概不动，因为基准就拍在它们全都
           做完、接趾还没开始的那一刻。
           排在复摆**之前**：退回之后趾骨的位置也一起回去了，复摆量的必须是退完的场景，
           否则 `toe_snap/residual_after_reach_cm` 就成了「拿退回前量的数交付」。
           读数全部排在 `ran foot_reach` 之后、`ran foot_reach_guard` 之前：归因规则
           （audit_attribution）拿 ran 行切段，`rollback/foot_*` 这几行的作者登记在
           foot_reach_guard 那一段里，写在前面会被判成「由 foot_reach 那一段量的」。 */
        if MMD2MAX_ROLLBACK do
        (
            /* 基准拍不出来（OBJ 目录不可用）时这一段什么都量不到，但**收工边界必须照报**：
               漏报 ran 会被归因规则当成「这一段根本不存在」，那几条 `always` 读数于是报出
               「必须出现的读数没有报」——那是真的，可它被埋在一句时间轴噪音里。这里让缺的
               只是读数行（红），边界行自己别红。拍不出基准就编一个 0 假装量过，是这个
               项目反复死掉的那条路，不走。 */
            if rbMarks != undefined then
            (
                mmd2maxReport "rollback" "guard_saved" "-" rbSaveOk
                mmd2maxReport "rollback" "foot_before_cm" "-" rbBefore
                mmd2maxReport "rollback" "foot_after_cm" "-" rbAfter
                mmd2maxReport "rollback" "foot_worse_bones" "-" rbWorse
                mmd2maxReport "rollback" "guard_fired" "-" (rbWorse > 0)
                if rbWorse > 0 and rbSaveOk then
                (
                    local rbLoad = mmd2maxFigOp root rbFig false
                    local rbBack = mmd2maxMarkGap rbMarks (mmd2maxMarkList root limbs)
                    mmd2maxReport "rollback" "foot_restored" "-" rbLoad[1]
                    mmd2maxReport "rollback" "foot_restore_pos_cm" "-" rbBack[1]
                    mmd2maxReport "rollback" "foot_restore_box_cm" "-" rbBack[2]
                    mmd2maxReport "rollback" "foot_restore_scale_cm" "-" rbBack[3]
                    mmd2maxReport "rollback" "foot_restore_angle_deg" "-" rbBack[4]
                    local rbMsg = "足盒接趾回滚：推过头 " + (rbWorse as string) + " 根，最差盒隙 "
                    rbMsg = rbMsg + (rbBefore as string) + " -> " + (rbAfter as string)
                    rbMsg = rbMsg + "，退回后与基准差 位置=" + (rbBack[1] as string)
                    rbMsg = rbMsg + " 盒=" + (rbBack[2] as string) + " scale=" + (rbBack[3] as string)
                    rbMsg = rbMsg + " 局部轴朝向=" + (rbBack[4] as string)
                    mmd2maxLog rbMsg
                )
                else
                (
                    local rbOkMsg = "足盒接趾复核：没有一根比动手前更远（最差 "
                    rbOkMsg = rbOkMsg + (rbBefore as string) + " -> " + (rbAfter as string)
                    rbOkMsg = rbOkMsg + "），不回滚"
                    mmd2maxLog rbOkMsg
                )
                try ( deleteFile rbFig ) catch () -- 允许失败：删不掉只多留一个文件，读数已经落盘
            )
            else mmd2maxLog "足盒接趾守卫：基准拍不出来（OBJ 目录不可用），这一轮没有退回这条路"
            mmd2maxReport "pass" "ran" "foot_reach_guard" 1
        )
        /* 上面每一刀 #scale 都把孩子沿轴推走一次，这里统一跟回 PMX 脚掌 */
        local toeSnapped2 = mmd2maxToeSnapBack root limbs "residual_after_reach_cm"
        if toeSnapped2 > 0 do
            mmd2maxLog ("足盒接趾复摆：净改善 " + (toeSnapped2 as string) + " 根跟回 PMX 脚掌落位")
        mmd2maxReport "pass" "ran" "toe_snap_reach" 1
    )
    if rotTotal > 0 do
        mmd2maxLog ("转角 pass：接口=" + (rotWay as string) + " 累计尝试=" + (rotTotal as string) \
            + " 改善=" + (rotBetter as string) + " 未改善=" + (rotWorse as string) \
            + " 抛错=" + (rotErr as string))

    mmd2maxFigureMode root.controller false
    mmd2maxReport "pass" "ran" "figure_exit" 1

    /* ===== 脚骨「画出来的朝向」复核（#26 第二轮）=====
       第一版在这里比的是 (Toe0head - Foothead) 与 PMX 的 (つま先 - 足首) —— 那仍然是
       恒等式：两个点都是我们自己摆到位的，夹角必然 ≈0（08:59 那轮实测 L=0.0198°
       R=0.0°，全绿，而用户看到的骨头方向照旧是错的）。
       真正没被任何校验覆盖的量是**节点自己的局部轴**：Biped 骨头的可见朝向由
       「全局 facing + 关节转角」决定，setTransform #pos 只搬 pivot。Toe0 是链尾、
       没有子节点，它的 tail 完全由自身局轴决定 —— 全场景唯一一个「摆位摆不到、
       距离量不出、位置校验不覆盖」的自由度，恰好就是用户红箭头画的那一段。
       所以这一轮改成量局轴：Foot 比 (つま先 - 足首)，Toe0 比 (鞋尖 - つま先)
       —— 与转角 pass 同一个 mmd2maxBoxWantDir 判据。这一段仍然只测不改，
       改方向的是上面的 骨盒转角 pass（接口已由探针定为 rotWay）。 */
    local chainBad = 0
    local gapBad = 0
    for item in limbs do
    (
        if item.count >= 10 and item[5] == "foot" do
        (
            local fName = item[1]
            local cName = item[2]
            local toeTgt = item[4]
            local tipPos = item[8]
            /* 踝目标 = 「子节点是这只脚 Foot」那一行（Calf 行）的落位 */
            local ankleTgt = undefined
            for row in limbs do
            (
                if row.count >= 4 and row[2] == fName do ankleTgt = row[4]
            )
            local fnode = getNodeByName (root.name + (substring fName 7 -1)) exact:true
            local cnode = getNodeByName (root.name + (substring cName 7 -1)) exact:true
            if fnode != undefined and cnode != undefined and toeTgt != undefined \
               and ankleTgt != undefined do
            (
                /* 判据与转角 pass 逐字同源：两根脚骨都比同一个 mmd2maxBoxWantDir 给的
                   方向（Foot = 踝->球、Toe0 = 球->网格鞋尖），压不压平全看
                   MMD2MAX_FOOTBOXFLAT（true = 压成水平轴，与导出侧 FOOT_BOX_FLAT 同真同假）。
                   #28 那版在这里压平是为了让「刚修平的脚」读出 0 度，代价是盒子被钉成
                   水平的板、前端离趾骨盒还差 5.6cm 高（第十二轮藏网格特写拍到）。
                   地面保护统一收在 mmd2maxBoxWantDir 的 45 度斜度上限里；Toe0 这一行
                   也必须走同一个函数，否则翻回滚开关时「写」变了、「读」没变。 */
                local yawF = mmd2maxLogBoneDir fName (mmd2maxBipedAxes fnode) \
                    (mmd2maxBoxWantDir fName ankleTgt toeTgt tipPos)
                local yawT = mmd2maxLogBoneDir cName (mmd2maxBipedAxes cnode) \
                    (mmd2maxBoxWantDir cName toeTgt toeTgt tipPos)
                /* 一行结论，方便和用户的截图对齐：正负号 = 朝哪边偏 */
                mmd2maxLog ("  脚骨偏航结论 " + fName + " 骨=" + (yawF as string) \
                    + "度 趾=" + (yawT as string) + "度")
                mmd2maxReport "foot_yaw" "bone_yaw_deg" fName yawF
                mmd2maxReport "foot_yaw" "toe_yaw_deg" fName yawT
                /* #40 的护栏：**骨轴 vs 自己的子关节**。
                   上面两行比的是 mmd2maxBoxWantDir 给的目标，而那个目标就是写进去的
                   方向 —— 同源即恒等式，第十五轮压平之后它照样读 骨=0.0 趾=0.0 全绿，
                   用户看到的却是「Bip001 Toe0 的起始轴没接上 Foot 的末端」。
                   这里换 target：用**回读到的子节点位置**（段校验 把它钉在 PMX 球部，
                   压不压平都不动）。三根局轴里取最接近的那根 = 最乐观的读数，
                   平轴读 29.93 度、斜轴读 ~0 度，>3 度就是骨盒真没指向自己的孩子。 */
                local fAxes = mmd2maxBipedAxes fnode
                local childVec = cnode.transform.translationpart \
                    - fnode.transform.translationpart
                if fAxes != undefined and (length childVec) > 0.001 do
                (
                    local minAng = 1e9
                    for k = 1 to 3 do
                    (
                        local a = mmd2maxVecAngleDeg fAxes[k] childVec
                        if a >= 0.0 and a < minAng do minAng = a
                    )
                    local cmsg = "  链向 " + fName + " -> " + cName
                    cmsg = cmsg + " 骨轴与子关节夹角=" + (minAng as string) + "度"
                    cmsg = cmsg + " 连线长=" + ((length childVec) as string)
                    if minAng > MMD2MAX_DIRTOL do
                    (
                        cmsg = cmsg + "  **断开：盒子不指向自己的子关节**"
                        chainBad = chainBad + 1
                    )
                    mmd2maxLog cmsg
                    mmd2maxReport "foot_chain" "chain_angle_deg" (fName + " -> " + cName) minAng
                )
                /* #41 的护栏：**两个盒子之间那段看得见的空气**。链向 只证明「父盒的轴
                   指得到子关节」，量不到父盒**前端面**与子盒**起点**之间的距离 ——
                   第十六轮 链向 读 0.0 度全绿，用户看到的仍是「一根牙签，前方悬着
                   一块趾骨盒」（两张对照图）。这是「恒等式校验看不见偏航」的第三次。
                   判据 = 沿父骨长度轴（子关节坐标 − 父盒远端面坐标），正=中间有空气、
                   负=盒子盖过子关节；再补一条「子关节落不落在父盒端面的矩形里」——
                   平轴时子节点在轴下方 5.6cm，端面不够高就是两块盒子错开而非接上。
                   全部来自回读到的包围盒与 pivot，不碰 mmd2maxBoxWantDir 给的方向。 */
                local gbb = undefined
                try ( gbb = nodeGetBoundingBox fnode fnode.transform ) catch ( gbb = undefined )
                local gAx = mmd2maxBipedAxis fnode (normalize childVec)
                if gbb != undefined and gAx != undefined do
                (
                    local gi = gAx[1]
                    local galong = dot childVec gAx[2]
                    local gmn = gbb[1]
                    local gmx = gbb[2]
                    local gfar = if galong >= 0.0 then gmx[gi] else gmn[gi]
                    local cover = true
                    for k = 1 to 3 do
                    (
                        if k != gi do
                        (
                            local offk = dot childVec fAxes[k]
                            if offk < gmn[k] - 0.05 or offk > gmx[k] + 0.05 do cover = false
                        )
                    )
                    local ggap = galong - gfar
                    local gmsg = "  盒隙 " + fName + " -> " + cName
                    gmsg = gmsg + " 沿轴=" + (ggap as string) + "cm"
                    gmsg = gmsg + " 子沿轴=" + (galong as string)
                    gmsg = gmsg + " 盒远端=" + (gfar as string)
                    gmsg = gmsg + " 端面覆盖=" + (cover as string)
                    if abs ggap > MMD2MAX_GAPTOL or not cover then
                    (
                        gmsg = gmsg + "  **没接上**"
                        gapBad = gapBad + 1
                    )
                    mmd2maxLog gmsg
                    mmd2maxReport "foot_chain" "gap_cm" (fName + " -> " + cName) ggap
                    mmd2maxReport "foot_chain" "gap_covered" \
                        (fName + " -> " + cName) cover
                )
            )
        )
    )
    /* 一句话总结：#40 这类回归不允许靠截图才发现。
       注意这两行量的是**两件不同的事**：链向 = 轴的箭头指没指向孩子（平轴口径下
       脚骨必然读 ~30 度，那是 #39 换来的代价，见 README 第 22/23 条）；
       盒隙 = 两块盒子在屏幕上接没接上 —— 用户两张图判的是后者。 */
    /* 门槛读 MMD2MAX_DIRTOL，不写死：链向 断开 这个数现在是 Python 侧的一条判据
       （foot_chain/broken_roots），阈值再留一份字面量就等于「尺在两边各刻一遍」。 */
    mmd2maxLog ("  链向结论 断开=" + (chainBad as string) \
        + " 根（门槛 " + (MMD2MAX_DIRTOL as string) + " 度）")
    mmd2maxLog ("  盒隙结论 没接上=" + (gapBad as string) + " 根（门槛 " \
        + (MMD2MAX_GAPTOL as string) + " 厘米，且子关节须落在父盒端面矩形内）")
    mmd2maxReport "foot_chain" "broken_roots" "-" chainBad
    mmd2maxReport "foot_chain" "gap_unconnected" "-" gapBad
    mmd2maxReport "pass" "ran" "foot_chain_check" 1

    /* 肢段长度复核：摆位之后重新量每节实际长度，与肢段表的目标值对比。
       这是判断「骨骼网格是否真的被拉到位」的唯一硬指标——位置对了不等于长度对了，
       上一版就是位置校验全绿而骨骼长度全错。 */
    for item in limbs do
    (
        local pnode = getNodeByName (root.name + (substring item[1] 7 -1)) exact:true
        local cnode = getNodeByName (root.name + (substring item[2] 7 -1)) exact:true
        if pnode != undefined and cnode != undefined do
        (
            local got = mmd2maxSegLen pnode cnode
            local err = abs (got - item[3])
            local smsg = "  段校验 " + item[1] + " -> " + item[2]
            smsg = smsg + " 目标=" + (item[3] as string)
            smsg = smsg + " 实际=" + (got as string)
            smsg = smsg + " 误差=" + (err as string)
            mmd2maxLog smsg
            /* 报「实际量到的长度」而不是「我自己算的误差」：期望值由 Python 从 limbs
               的坐标列算（骨盆三段按髋中点，其余就是 desired_len），连 Max 这一侧
               目标值算错没有都被一起管住。见 verdicts.segment_expectation。 */
            mmd2maxReport "segment" "len_cm" (item[1] + " -> " + item[2]) got
        )
    )

    mmd2maxReport "pass" "ran" "segment_check" 1
    /* 「骨盒到底有多大、在不在鞋里」别想用回读解决：第十二轮实测 node.min / node.max
       在 Biped_Object 上退化成同一个点（四只脚骨全部 尺寸=0.0x0.0x0.0），量不到显示盒；
       nodeGetBoundingBox 的矩阵参数口径又没验过。这一项只能靠藏网格特写截图 + 下面的
       结构向夹角（用 mmd2maxBipedAxes 取骨局部轴，已证明可用）判。 */

    /* 摆位校验：对比关键关节「实际位置 vs PMX 目标位置」。
       偏差接近 0 说明摆位生效；若某段偏差很大，说明 Biped 内部比例
       把它拉回了默认位置，需要按段缩放骨骼。
       注意：必须包含肘/膝等中间关节（之前漏检，导致肢段长度错配没被发现）。 */
    for targetName in #("Bip001 Head", "Bip001 Neck", "Bip001 Spine2", \
        "Bip001 Spine", "Bip001 Pelvis", \
        "Bip001 L Clavicle", "Bip001 L UpperArm", "Bip001 L Forearm", "Bip001 L Hand", \
        "Bip001 R Clavicle", "Bip001 R UpperArm", "Bip001 R Forearm", "Bip001 R Hand", \
        "Bip001 L Thigh", "Bip001 L Calf", "Bip001 L Foot", \
        "Bip001 R Thigh", "Bip001 R Calf", "Bip001 R Foot") do
    (
        local srcIdx = findItem targetNames targetName
        if srcIdx > 0 do
        (
            local row = rowIds[srcIdx]
            if row <= rows.count do
            (
                local p = rows[row]
                if p.count >= cTailX do
                (
                    local want = [(p[cHeadX] as float), (p[cHeadY] as float), (p[cHeadZ] as float)]
                    local actual = root.name + (substring targetName 7 -1)
                    local node = getNodeByName actual exact:true
                    if node != undefined do
                    (
                        local got = node.transform.translationpart
                        local dd = distance got want
                        local vmsg = "  校验 " + targetName + " 目标=" + (want as string)
                        vmsg = vmsg + " 实际=" + (got as string)
                        vmsg = vmsg + " 偏差=" + (dd as string)
                        /* b 方案（#29）：UpperArm 的落位目标本来就是「锁骨骨盒射线点」而不是
                           PMX 肩点 —— 不并排打出来的话这一行的 1.9 会被读成「还没修」。
                           真判据是 `对射线偏差` 要回到 1e-5，那说明肩点不再跟 Biped 抢，
                           肘/腕才摆得准。 */
                        if mmd2maxRayFromParent targetName do
                        (
                            local clavName = substituteString actual "UpperArm" "Clavicle"
                            local clavNode = getNodeByName clavName exact:true
                            local rayT = mmd2maxShoulderRayTarget clavNode want
                            if rayT != undefined do
                            (
                                vmsg = vmsg + " 射线目标=" + (rayT as string) \
                                    + " 对射线偏差=" + ((distance got rayT) as string) + "（b 方案认这个）"
                                mmd2maxReport "placement" "ray_err_cm" targetName \
                                    (distance got rayT)
                            )
                        )
                        mmd2maxLog vmsg
                        mmd2maxReport "placement" "pos_err_cm" targetName dd
                    )
                )
            )
        )
    )


    mmd2maxReport "pass" "ran" "placement_check" 1
    /* ===== 结构方向复核（#27）=====
       上面那份 校验 名单是写死的 19 个节点：Bip001 Spine1 和 30 根手指骨都不在里面，
       「没打印」被当成了「没问题」（用户 #27：Spine2 没后仰、颈没前倾、手指不贴合）。
       而且位置校验比的是「pivot vs PMX head」，可 PMX 每根骨的**显示方向**是 tail - head，
       网格就是照着它做的 —— 我们只搬 pivot，画出来的段是 head -> 下一个 head，
       两者是不是同一个方向，只有量局轴才知道（手指链离线算过：PMX 的 中指１ tail
       就是 中指２ head，所以盒若随 pivot 走就该读 0 度；读出大夹角 = 盒不随 pivot 转，
       和 #26 脚骨同一个机制）。这里遍历全部映射节点，把这两个方向直接对量。 */
    local structTotal = 0
    local structBad = 0
    local structStub = 0
    for k = 1 to targetNames.count do
    (
        local t = targetNames[k]
        if t != "Bip001" do
        (
            local row = rowIds[k]
            if row <= rows.count do
            (
                local p = rows[row]
                if p.count >= cTailZ do
                (
                    local headPos = [(p[cHeadX] as float), (p[cHeadY] as float), (p[cHeadZ] as float)]
                    local tailPos = [(p[cTailX] as float), (p[cTailY] as float), (p[cTailZ] as float)]
                    /* 判据必须和转角 pass 用同一个方向：脚链的盒本来就规定不平齐
                       PMX tail - head（见 mmd2maxBoxWantDir），拿三维 tail 复核会把
                       修好的脚又报成超标 */
                    local want = mmd2maxBoxWantDir t headPos tailPos (mmd2maxShoeTip limbs t)
                    local node = getNodeByName (root.name + (substring t 7 -1)) exact:true
                    if node != undefined do
                    (
                        local a = mmd2maxLogStructDir t (mmd2maxBipedAxes node) \
                            headPos want node.transform.translationpart node.name
                        if a >= 0.0 do
                        (
                            local stubBone = (distance headPos tailPos) < 1.0
                            if stubBone do structStub += 1
                            if (not stubBone) do
                            (
                                structTotal += 1
                                if a > MMD2MAX_DIRTOL do structBad += 1
                            )
                        )
                    )
                )
            )
        )
    )
    /* 阈值打在日志里，不写死成 「夹角>5度」：这个数换了轮次之间还能直接对，
       否则下一轮读到 `夹角>3度=7` 会以为是容差变严了。 */
    mmd2maxLog ("结构向 复核：可测=" + (structTotal as string) \
        + " 夹角>" + (MMD2MAX_DIRTOL as string) + "度=" + (structBad as string) \
        + " 短骨不计=" + (structStub as string))
    mmd2maxReport "struct_dir" "measurable" "-" structTotal
    mmd2maxReport "struct_dir" "over_tol" "-" structBad
    mmd2maxReport "struct_dir" "short_skipped" "-" structStub
    mmd2maxReport "pass" "ran" "struct_dir_check" 1
    /* ===== 骨盒回读（M4c）：四道终局复核之后，最后量一次骨盒自己 =====
       这一段只读不写（一个 setTransform 都没有），所以它不改任何几何：日志里多出来的
       只有 `骨盒回读 …` 那些行。放在这里的两个前提是注册表写的：box_verify
       requires struct_dir_check，而 accessory_build 声明自己不动 Biped 的 #scale ——
       哪天附属骨那段开始改骨盒，这一组读数就得跟着往后挪，否则 STALE 会把它抓出来。
       ran 行排在两条聚合读数之后：ran 是收工不是开工（M3）。 */
    local boxRb = mmd2maxVerifyBoxes root limbs widths
    mmd2maxReport "box_rb" "nodes_measured" "-" boxRb[1]
    mmd2maxReport "box_rb" "read_fail" "-" boxRb[2]
    mmd2maxLog ("骨盒回读：量到=" + (boxRb[1] as string) \
        + " 读不到=" + (boxRb[2] as string) \
        + "（期望来自 limbs 脚段与 widths 表，判定在 Python 侧）")
    mmd2maxReport "pass" "ran" "box_verify" 1

    /* 把 Biped 节点写回 boneNodes 的对应位置 */
    for k = 1 to targetNames.count do
    (
        local row = rowIds[k]
        local actual = root.name + (substring targetNames[k] 7 -1)
        local node = getNodeByName actual exact:true
        if node != undefined and row <= bipedNodes.count do bipedNodes[row] = node
    )
    root
)

fn mmd2maxLogAccessoryStats nodes =
(
    local childOfBiped = 0
    local childOfBone = 0
    local noParent = 0
    for i = 1 to nodes.count do
    (
        if (classOf nodes[i]) != BoneGeometry do continue
        local pr = nodes[i].parent
        if pr == undefined then noParent += 1
        else if (classOf pr) == BoneGeometry then childOfBone += 1
        else childOfBiped += 1
    )
    local statMsg = "附属骨父级统计：挂 Biped=" + (childOfBiped as string)
    statMsg = statMsg + "，挂 Bone=" + (childOfBone as string)
    statMsg = statMsg + "，无父级=" + (noParent as string)
    mmd2maxLog statMsg
    mmd2maxReport "accessory" "on_biped" "-" childOfBiped
    mmd2maxReport "accessory" "on_bone" "-" childOfBone
    mmd2maxReport "accessory" "no_parent" "-" noParent
)

fn mmd2maxCreateBones =
(
    /* lines = bones.tsv 的表对象 #(列名, 列号, 数据行)，rows = 其中的数据行。
       行数一律用 rows.count：表对象自己的 .count 是 3，拿它当骨骼数会把 555 根读成 3 根。 */
    local lines = mmd2maxReadTable MMD2MAX_BONES
    local rows = mmd2maxRows lines
    local cName = mmd2maxColPos lines "bone_name"
    local cParent = mmd2maxColPos lines "parent"
    local cHeadX = mmd2maxColPos lines "head_x"
    local cHeadY = mmd2maxColPos lines "head_y"
    local cHeadZ = mmd2maxColPos lines "head_z"
    local cTailX = mmd2maxColPos lines "tail_x"
    local cTailY = mmd2maxColPos lines "tail_y"
    local cTailZ = mmd2maxColPos lines "tail_z"
    local cMaxParent = mmd2maxColPos lines "max_parent"
    mmd2maxLog ("读取骨骼数：" + (rows.count as string))
    /* 先创建 Biped，人形骨的位置预留给 Biped 节点 */
    local bipedNodes = #()
    local bipedRoot = undefined
    for i = 1 to rows.count do append bipedNodes undefined
    local mapData = mmd2maxReadBipedMap MMD2MAX_MAP
    local rowIds = mapData[1]
    local targetNames = mapData[2]
    mmd2maxLog ("Biped 映射条目：" + (rowIds.count as string))
    if rowIds.count == 0 do throw "未读到有效的 Biped 映射条目，请检查 biped_map.tsv。"
    bipedRoot = mmd2maxBuildBiped lines rowIds targetNames bipedNodes

    local nodes = #()
    disableSceneRedraw()
    for p in rows do
    (
        if p.count >= cTailX do
        (
            local existing = bipedNodes[nodes.count + 1]
            if existing != undefined then
            (
                /* 人形骨：直接复用 Biped 节点，不新建 Bone */
                append nodes existing
            )
            else
            (
                local head = [(p[cHeadX] as float), (p[cHeadY] as float), (p[cHeadZ] as float)]
                local tail = [(p[cTailX] as float), (p[cTailY] as float), (p[cTailZ] as float)]
                local dir = tail - head
                local len = length dir
                if len < 0.0001 do
                (
                    dir = [0.0, 0.0, 0.001]
                    len = 0.001
                )
                /* 审查项 P1#21：BoneSys.createBone 第三参数是骨骼局部 Z 轴，
                   骨骼方向是局部 X 轴；不能把同一个方向向量同时当 X 和 Z，
                   否则 Max 会替你正交化，导致骨骼 roll 不稳。
                   这里取与骨骼方向垂直的参考向量做叉乘，得到真正垂直的局部 Z。 */
                local xaxis = normalize dir
                local ref = [0.0, 0.0, 1.0]
                if abs (dot xaxis ref) > 0.99 do ref = [0.0, 1.0, 0.0]
                local zaxis = normalize (cross xaxis ref)
                local bone = BoneSys.createBone head (head + xaxis * len) zaxis
                /* 不显式设置的话，Max 的骨骼显示尺寸会取默认值，
                   短骨骼会显示为巨大的锥体。按骨长比例设置宽高。 */
                local thickness = len * 0.2
                if thickness < 0.001 do thickness = 0.001
                try
                (
                    bone.width = thickness
                    bone.height = thickness
                    bone.taper = 90
                )
                catch ()
                append nodes bone
            )
        )
    )
    /* 第二遍设置名称与父级。
       注意：Biped 节点既不能改名也不能改父级（会破坏 Biped 系统的内部结构），
       因此只对 BoneGeometry 执行；人形骨保持 Biped 原始名称与层级。 */
    for i = 1 to nodes.count do
    (
        local p = rows[i]
        if p.count >= cTailX do
        (
            if (classOf nodes[i]) != BoneGeometry do continue
            local boneName = p[cName]
            try ( nodes[i].name = boneName ) catch ( nodes[i].name = ("mmdbone" + (i as string)) )
            local parentIndex = p[cParent] as integer
            /* max_parent 列：附属骨链根在 Max 里的改挂目标（-1 = 用 parent 列）。
               裙摆/头发在 MMD 里挂「下半身」，映射后是 Bip001 Spine；若用户希望改成
               几何最近的 Biped 节点，由 Python 侧算好写在这一列，脚本照做即可。 */
            if p.count >= cMaxParent do
            (
                local override = -1
                try ( override = p[cMaxParent] as integer ) catch ( override = -1 )
                if override >= 0 and override < nodes.count do
                (
                    if override != parentIndex do
                        mmd2maxLog ("  附属骨改挂：" + boneName + " 父 " + (parentIndex as string) \
                            + " -> " + (override as string))
                    parentIndex = override
                )
            )
            if parentIndex >= 0 and parentIndex < nodes.count then
            (
                /* 关键：Max 里设置 .parent 后，物体的变换会被当成「相对父级」重新解释，
                   世界位置会变。必须先记下世界变换，链接后再写回去，骨骼才不会飞散。 */
                local worldTM = nodes[i].transform
                try
                (
                    nodes[i].parent = nodes[parentIndex + 1]
                    nodes[i].transform = worldTM
                )
                catch ()
            )
            else
            (
                /* MMD 的根骨（如 全ての親 / 操作中心）没有对应
                   Biped 节点（ROOT 语义刻意不映射，避免两个源骨指向 Bip001）。
                   这里统一挂到 Biped 根下，避免它们游离在场景根层级。 */
                local worldTM = nodes[i].transform
                try
                (
                    nodes[i].parent = bipedRoot
                    nodes[i].transform = worldTM
                )
                catch ()
            )
        )
    )
    enableSceneRedraw()
    mmd2maxLog ("已创建骨骼：" + (nodes.count as string))

    /* 诊断：统计附属骨的父级构成，确认「附属骨挂到最近人形骨」确实生效，
       而不是散落在场景根下。 */
    mmd2maxLogAccessoryStats nodes
    mmd2maxReport "pass" "ran" "accessory_build" 1
    nodes
)

/* ================= M4b：Skin 回读验收的四个实测前提 =================
   探针在 probes/readback_m4*.ms（a/b/c/d/e/f 六轮），读数是同名的 .txt。下面每一句
   写法都有出处，没有实测过的假设不许进这段代码：
     P1 刚 addModifier 的 Skin **顶点表还没建**：任何 skinOps 读写都抛
        `Exceeded the vertex count`（M4e T0/T3/T4/T5）。点亮它靠的是「让修改器堆栈真求值
        一次」—— 取 mesh.numVerts（T1）或 redrawViews()（T2）；skinOps 自己的读不算求值
        （T3/T4 仍然抛）。所以写权重之前必须显式预热，而不是指望 select 顺手办好。
     P2 每个顶点**只能写一次**：第二次 ReplaceVertexWeights / SetVertexWeights 无论写什么，
        回读都是 0.0/0.0 且此后永久坍缩（M4e T1/T2/T6）。所以「写完再刷一遍」这条路不存在，
        闭环只能是「读出坏的 → 关掉归一化 → 重写一次」。
     P3 唯一的补救动作：`skinOps.unNormalizeVertex smod v true` 之后再写才落得进去（M4e T6）。
     P4 没写过的顶点回读是「某根骨 @ 1.0」而不是空（M4f A1/A2，骨号还会变），
        所以「读到非空」证明不了写进去过；能证明的是 IsVertexModified（写前 0、写后 1，
        M4f B2/D3）。回读因此既数权重、也数这个标记。
   另外两条只影响怎么读数（M4f）：每顶点骨数没有被裁到 4 的默认上限（B1 写 5 读 5）；
   权重大约小于 1e-8 会被存成 0.0（C2），所以链路计数按**骨号在不在**算，不按权重大小算。 */
fn mmd2maxSkinReadable skinMod =
(
    /* 拿第一个真实顶点号试探顶点表建好没有：建好了返回 #(骨号数组, 权重数组)，
       没建好就抛 —— catch 成 undefined，classOf 就不是 Array。 */
    /* 语句形式的 try/catch + 显式失败值：v4 契约 rule 10 禁止 `local x = try (...) catch (...)`
       （编译期就死、弹窗只给标题的那种）。探针里这形状跑出过读数，但边界没单独测过，
       不值得拿一整轮实跑去赌 —— 何况「读不到」写成 -1 比写成 undefined 更明白。 */
    local probe = -1
    try ( probe = skinOps.GetVertexBoneIDsAndWeights skinMod 1 ) catch ( probe = -1 )
    (classOf probe) == Array and probe.count >= 2
)

fn mmd2maxSkinPrime meshObj skinMod =
(
    /* 点亮新建 Skin 的顶点表：先走便宜的那条（读一次网格顶点数 = 逼堆栈求值），
       还不行再 redrawViews() 逼一次。返回值是「最终可不可读」，
       调用方拿它决定要不要中止 —— 绝不允许预热没成功还往下写。 */
    select meshObj
    modPanel.setCurrentObject skinMod
    local dummyVerts = 0
    try ( dummyVerts = meshObj.numVerts ) catch () -- 允许失败：要的是「读顶点数逼堆栈求值」这个动作，取没取到无所谓；能不能写由下一行 mmd2maxSkinReadable 真读一遍判定。
    if mmd2maxSkinReadable skinMod do return true
    try ( redrawViews() ) catch () -- 允许失败：备用预热（探针 M4e T2）；没点亮照样落到返回值，调用方会中止。
    mmd2maxSkinReadable skinMod
)

fn mmd2maxApplySkin meshes boneNodes =
(
    if meshes.count == 0 do throw "没有可蒙皮的网格"
    /* 审查项 P1#24：本脚本只处理单一网格对象。OBJ 导入产生多于一个网格说明
       导入设置或模型分组异常，必须显式失败，而不是默认取第一个。 */
    if meshes.count > 1 do
        throw ("OBJ 导入产生了 " + (meshes.count as string) + " 个网格对象，本脚本只支持单网格蒙皮；" \
            + "请检查 OBJ/MTL 是否被拆成多个对象（导出侧应在 Python 里合并成一个 model.obj）。")
    local meshObj = meshes[1]
    mmd2maxLog ("开始挂 Skin，网格=" + meshObj.name + "，骨骼总数=" + (boneNodes.count as string))

    /* 先统一转成 Editable_Mesh，并从网格对象直接取顶点数。
       不要用 skinOps.GetNumberVertices：它在 Skin 尚未初始化/未激活时会返回 0
       （实跑踩到：明明网格名为 default、骨骼也加进去了，却报「顶点数 0」）。
       这样取数同时也真实反映 OBJ 导入是否启用了 Weld（焊点会改变顶点数）。 */
    if (classOf meshObj) != Editable_Mesh do
        meshObj = convertTo meshObj Editable_Mesh
    local meshVertexCount = meshObj.numVerts
    mmd2maxLog ("网格顶点数（取自网格对象）：" + (meshVertexCount as string))
    mmd2maxReport "mesh" "vertex_count" "-" meshVertexCount

    local skinMod = Skin()
    addModifier meshObj skinMod

    /* 只把「参与蒙皮」（skin_flag=1）的骨骼加进 Skin。
       把全部 555 根都加进去会让 Max 的 Skin 修改器卡死。 */
    local lines = mmd2maxReadTable MMD2MAX_BONES
    local rows = mmd2maxRows lines
    local cSkinFlag = mmd2maxColPos lines "skin_flag"
    local skinFlags = #()
    local target = 0
    for i = 1 to rows.count do
    (
        local p = rows[i]
        local flag = 1
        /* skin_flag 是标记位（1=参与）；原先注释写成「第 10 列」，实际在表头里是第 12 列 ——
           这正是魔数下标会写错的地方，现在列号由表头名解析出来。 */
        if p.count >= cSkinFlag do
        (
            local raw = p[cSkinFlag]
            if raw == "0" do flag = 0
        )
        append skinFlags flag
        if flag == 1 do target += 1
    )
    /* skinFlags 按 bones.tsv 的**行号**建，下面却按 boneNodes 的**下标**去取 ——
       两边数量不等就是「骨表行」和「场景骨对象」错位，权重会整体绑错骨。
       以前不检查：行少的时候 skinFlags[i] 是 undefined，`undefined == 1` 为假，
       那根骨就被静默跳过（不报错，只是 Skin 里少一根骨、它名下的权重全落空）。 */
    if rows.count != boneNodes.count do
        throw ("骨骼表 " + (rows.count as string) + " 行与场景骨骼 " \
            + (boneNodes.count as string) + " 个数量不符：权重里的骨号无法一一对应，已中止。")
    mmd2maxLog ("加入 Skin 的骨骼数：" + (target as string) + " / 总数 " + (rows.count as string))

    /* 预建 全局骨骼序号 -> Skin 局部序号 的映射（审查项 P0#3 / P1#23）。
       AddBone 按 skinFlags 顺序加入，因此 skinId 即累加计数。
       这样权重写入时 O(1) 查表，且不会把全局索引误当成 Skin 局部索引。 */
    local globalToSkin = #()
    for i = 1 to boneNodes.count do append globalToSkin 0
    local skinNodeNames = #()
    local skinId = 0
    for i = 1 to boneNodes.count do
    (
        if skinFlags[i] == 1 do
        (
            skinId += 1
            globalToSkin[i] = skinId
            append skinNodeNames boneNodes[i].name
        )
    )

    /* 逐根添加骨骼，但用 update=0 延迟更新，最后统一 forceUpdate（审查项 P1#22）。 */
    disableSceneRedraw()
    local addStart = timeStamp()
    local failedBones = 0
    local failedBoneNames = #()
    for i = 1 to boneNodes.count do
    (
        if skinFlags[i] == 1 do
        (
            local added = false
            try ( skinOps.AddBone skinMod boneNodes[i] 0; added = true ) \
                catch ( failedBones += 1; append failedBoneNames boneNodes[i].name )
            if not added do ( failedBones += 1; append failedBoneNames boneNodes[i].name )
        )
        if (mod i 50) == 0.0 do mmd2maxLog ("已处理骨骼 " + (i as string) + "/" + (boneNodes.count as string))
    )
    /* 原先这里有一句 try ( skinOps.forceUpdate skinMod ) catch ()，整条是死的：
       skinOps 的 143 个成员里没有 forceUpdate（probes/skinops_members.txt），
       它每次都抛 Unknown property 再被 catch 吞掉 —— 一行永远不会成功的「保险」。
       真正需要的是让堆栈求值一次，见下面的 mmd2maxSkinPrime（M4e 探针 T1/T2）。 */
    local addElapsed = ((timeStamp() - addStart) / 1000.0) as string
    local boneTotal = (skinOps.GetNumberBones skinMod) as string
    mmd2maxLog ("添加骨骼耗时：" + addElapsed + " 秒，Skin 内骨骼数 " + boneTotal)
    mmd2maxReport "skin" "bones" "-" boneTotal
    if failedBones > 0 do
        throw ("有 " + (failedBones as string) + " 根骨骼加入 Skin 失败：" + (failedBoneNames as string))

    /* 写权重前必须先把 Skin 的顶点表点亮（M4e T0/T3/T4/T5：不点亮就是每发都抛
       Exceeded the vertex count）。原先这里只有 select + setCurrentObject，
       它治的是「第一个顶点写不进去」，真正起作用的是紧随其后的那次堆栈求值 ——
       本函数在前面读过 meshObj.numVerts，但那一次发生在 addModifier **之前**，
       对新建的 Skin 不算数。现在把这件事写成显式一步，并且读不出来就直接中止：
       「一个顶点都没写进去但返回值为 OK」的状态是实测存在的（M4c/M4e），
       绝不能靠后面的计数兜。 */
    if not (mmd2maxSkinPrime meshObj skinMod) do
        throw ("Skin 顶点表预热后仍读不出来（网格 " + meshObj.name \
            + "，顶点 " + (meshVertexCount as string) + "）：权重写不进去，已中止。")

    /* 读权重并写入。weights.tsv 只有前两列有名字：vertex_index + bone_weight_pairs，
       而 bone_weight_pairs 往右是**变长的** `骨序号:权重` 对（每行顶点绑的骨数不同），
       所以循环从该列的列号一路取到行尾，对内的 `:` 分隔与列无关，仍用 mmd2maxSplit。 */
    local wtbl = mmd2maxReadTable MMD2MAX_WEIGHTS
    local wlines = mmd2maxRows wtbl
    local cVert = mmd2maxColPos wtbl "vertex_index"
    local cPairs = mmd2maxColPos wtbl "bone_weight_pairs"
    mmd2maxLog ("读取权重行数：" + (wlines.count as string))
    -- 顶点数取自网格对象本身（见上方 meshVertexCount），不用 skinOps.GetNumberVertices
    local vertexCount = meshVertexCount
    /* 期望顶点数直接来自 CFG（mmd2maxCfgInt 已保证是正整数）。
       M1b 前这里是 `try ( MMD2MAX_VERTS as integer ) catch ( expected = 0 )` 再加
       `if expected > 0 and …`：配置没读到时 expected=0，整条顶点数校验被静默跳过，
       权重照样按序号写进去 —— 正是「日志绿、场景错」的那类写法。 */
    local expected = MMD2MAX_VERTS

    /* 审查项 P1#18：顶点数必须严格一致，否则权重按顶点序号写会整体错位。
       这里直接抛错，而不是只告警。 */
    if vertexCount != expected do
        throw ("网格顶点数 " + (vertexCount as string) + " 与期望 " + (expected as string) + " 不一致，权重可能错位，已中止。")
    if wlines.count != vertexCount do
        throw ("权重行数 " + (wlines.count as string) + " 与网格顶点数 " + (vertexCount as string) + " 不一致，已中止。")

    local weightStart = timeStamp()
    local applied = 0
    local failedVerts = 0
    /* #35 的 Max 端回读：每根骨实际拿到多少个顶点。Python 侧改绑Toe0之前是 0，
       光看 weights.tsv 只能证明「导出对了」，证明不了 Skin 里真挂着这些顶点。 */
    local boneVertHits = #()
    for k = 1 to skinNodeNames.count do append boneVertHits 0
    for i = 1 to wlines.count do
    (
        local p = wlines[i]
        if p.count >= cPairs do
        (
            local vi = (p[cVert] as integer) + 1
            if vi >= 1 and vi <= vertexCount do
            (
                local ids = #()
                local weights = #()
                local hadPairs = false
                for j = cPairs to p.count do
                (
                    local pair = mmd2maxSplit p[j] ":"
                    if pair.count >= 2 do
                    (
                        hadPairs = true
                        local g = (pair[1] as integer)   -- 0-based 全局骨骼序号
                        local w = pair[2] as float
                        if g >= 0 and g < boneNodes.count and globalToSkin[g + 1] > 0 do
                        (
                            local sid = globalToSkin[g + 1]
                            append ids sid
                            append weights w
                            /* 用显式赋值而不是复合赋值：数组下标做左值再叠复合赋值是
                               MAXScript 的边界情况，不值得拿一轮实跑去试
                               （标量累加 failedVerts += 1 是验证过好用的，不冲突）。 */
                            boneVertHits[sid] = boneVertHits[sid] + 1
                        )
                    )
                )
                /* 注意：MaxScript 的 if...do 形式不允许带 else；
                   要接 else 必须用 if...then。...else（本次实跑 Syntax error 的修复点）。 */
                if ids.count > 0 then
                (
                    local okWrite = false
                    try ( skinOps.ReplaceVertexWeights skinMod vi ids weights; okWrite = true ) \
                        catch ( failedVerts += 1; \
                                if failedVerts <= 5 do mmd2maxLog ("  写权重失败 vi=" + (vi as string) \
                                    + " ids=" + (ids as string) + " ws=" + (weights as string)) )
                    if okWrite do applied += 1
                )
                else if hadPairs do
                (
                    /* 有权重数据，但没有一根指向参与蒙皮的骨骼：视为失败，不能静默跳过 */
                    failedVerts += 1
                    if failedVerts <= 5 do mmd2maxLog ("  无可蒙皮骨骼 vi=" + (vi as string) \
                        + " 权重项数=" + ((p.count - 1) as string))
                )
            )
        )
        if (mod i 10000) == 0.0 do
            mmd2maxLog ("权重进度：" + (i as string) + "/" + (wlines.count as string))
    )

    /* M1b 删掉了「按 meta 的 has_sdef/has_qdef 开启 Skin 双四元数混合」这一段。
       理由不是 DQ 没用，而是这段从没被执行过：跑过的五个模型全是 has_sdef=0，
       于是 `dqBlend` / `useDQ` 这两个属性名到底存不存在、写了以后肘膝到底变不变，
       一次都没实测过；而它整段被 try/catch 兜住，真出错也只留一行日志。
       留着 = 一条「配置能打开、却没人验过」的分支，正是 v4 契约第 V 条要清掉的东西。
       SDEF/QDEF 的处理口径保持既有的事实：导出侧按近似 LBS 写权重，并在导出摘要里
       明确提示会丢旋转中心与体积保真（mmd2max/export_obj.py 的 warnings）。
       真要做 DQ，先写探针量一次属性名与效果，再作为一条有读数的 pass 加回来。 */

    enableSceneRedraw()
    local weightElapsed = ((timeStamp() - weightStart) / 1000.0) as string
    /* MaxScript 换行即语句结束，不能把 + 放在续行行首（会报 expected <factor>），
       这里改用局部变量分步拼接，保证每行都是完整语句。 */
    local wmsg = "权重写入耗时：" + weightElapsed + " 秒，已写入顶点 " + (applied as string)
    wmsg = wmsg + "，失败顶点 " + (failedVerts as string)
    mmd2maxLog wmsg
    mmd2maxReport "skin" "written_vertices" "-" applied
    mmd2maxReport "skin" "failed_vertices" "-" failedVerts
    /* #35 判据（离线算好的期望值，别拿「大概」当通过）：
       Toe0 从 0 变 74、Foot 从 377/380 降到 363/366 —— 两边一涨一落、总数不变
       （L:363+74=437 与改绑前的 377+60 同量级，差的是过渡带里被混权重的那批）。
       实际 Python 侧读数：左 顶点 363/74、右 366/74，权重合计 272.71/51.87 与 272.70/51.87。 */
    for k = 1 to skinNodeNames.count do
    (
        local nm = skinNodeNames[k]
        if matchPattern nm pattern:"* Toe0" or matchPattern nm pattern:"* Foot" do
            mmd2maxLog ("  蒙皮覆盖 " + nm + " 顶点=" + (boneVertHits[k] as string))
    )
    mmd2maxReport "pass" "ran" "skin_apply" 1
    if failedVerts > 0 do
        throw ("有 " + (failedVerts as string) + " 个顶点未能写入有效蒙皮权重，已中止（不写 success 标记）。")
)

/* ---------- 回读用的两个纯函数：一次对撞、一次全表扫描 ---------- */
fn mmd2maxSkinCmp expIds expWs rbIds rbWs =
(
    /* 一个顶点的「期望 vs 场景读数」，返回 #(最大权重误差, 缺失链路数, 期望主控骨, 回读主控骨)。
       期望权重必须先归一化：实测 Skin 总是归一化后才存（写 0.4/0.2 读回 0.667/0.333，M4b 探针），
       拿没除的那份去比除了的那份，每一行都会假 FAIL。
       主控骨取第一个最大值：M4b 实测 Skin 保留写入顺序（写 #(3,1) 读回 #(3,1)），
       所以并列时两边取到同一格，不需要额外的并列规则。 */
    local maxErr = 0.0
    local missing = 0
    for i = 1 to expIds.count do
    (
        local hit = 0
        for j = 1 to rbIds.count do
            if rbIds[j] == expIds[i] do hit = j
        if hit == 0 then
        (
            missing += 1
            if expWs[i] > maxErr do maxErr = expWs[i]
        )
        else if hit <= rbWs.count then
        (
            local diff = rbWs[hit] - expWs[i]
            if diff < 0.0 do diff = -diff
            if diff > maxErr do maxErr = diff
        )
    )
    local expDom = 0
    local best = -1.0
    for i = 1 to expWs.count do
        if expWs[i] > best do ( best = expWs[i]; expDom = expIds[i] )
    local rbDom = 0
    best = -1.0
    for i = 1 to rbWs.count do
        if rbWs[i] > best do ( best = rbWs[i]; rbDom = rbIds[i] )
    #(maxErr, missing, expDom, rbDom)
)

fn mmd2maxSkinScan smod expIdsArr expWsArr vertexCount boneCount =
(
    /* 全表回读一遍（实测 3752 顶点 0.007 秒、100 顶点 0.0 秒，18892 顶点同样不用采样）。
       返回的每一格都是**场景里量到的**，不是写入侧的自述：
       #(读到顶点数, 读失败数, 未被写入顶点数, 零权重顶点数, 主控骨不符数,
         缺失链路数, 场景链路总数, 最大权重误差, 最大权重和偏差, 每骨链路数, 坏顶点清单) */
    local okCount = 0
    local failCount = 0
    local unmodCount = 0
    local zeroCount = 0
    local domMiss = 0
    local missLinks = 0
    local linksRead = 0
    local wErrMax = 0.0
    local sumErrMax = 0.0
    local badList = #()
    local rbLinks = #()
    for i = 1 to boneCount do append rbLinks 0
    for vi = 1 to vertexCount do
    (
        /* 写没写过的证据（P4）：没写过的顶点照样能读出「一根骨 @ 1.0」，
           只有 IsVertexModified 分得开「真写过」和「Skin 的默认值」。 */
        local modFlag = 0
        try ( modFlag = skinOps.IsVertexModified smod vi ) catch ( modFlag = -1 )
        if modFlag != 1 do unmodCount += 1
        local rb = undefined
        try ( rb = skinOps.GetVertexBoneIDsAndWeights smod vi ) catch ( rb = "EX" )
        if (classOf rb) != Array or rb.count < 2 or (classOf rb[1]) != Array then
            failCount += 1
        else
        (
            okCount += 1
            local rbIds = rb[1]
            local rbWs = rb[2]
            local rbSum = 0.0
            for i = 1 to rbIds.count do
            (
                local bid = rbIds[i]
                if bid >= 1 and bid <= boneCount do rbLinks[bid] = rbLinks[bid] + 1
                linksRead += 1
                if i <= rbWs.count do rbSum += rbWs[i]
            )
            /* 链路按骨号计（P4 的另一半：权重会被存成 0.0，骨号不会）；
               零权重顶点按权重和判 —— 坍缩的顶点读回来是 0.0/0.0，和没写完全不同。 */
            if rbSum <= 1e-6 do
            (
                zeroCount += 1
                append badList vi
            )
            local sErr = rbSum - 1.0
            if sErr < 0.0 do sErr = -sErr
            if sErr > sumErrMax do sumErrMax = sErr
            local eIds = expIdsArr[vi]
            if eIds.count > 0 then
            (
                local cmp = mmd2maxSkinCmp eIds expWsArr[vi] rbIds rbWs
                if cmp[1] > wErrMax do wErrMax = cmp[1]
                missLinks += cmp[2]
                if cmp[3] != cmp[4] do domMiss += 1
            )
        )
        if (mod vi 5000) == 0.0 do
            mmd2maxLog ("  回读进度：" + (vi as string) + "/" + (vertexCount as string))
    )
    #(okCount, failCount, unmodCount, zeroCount, domMiss, missLinks, linksRead, wErrMax, sumErrMax, rbLinks, badList)
)

fn mmd2maxVerifySkin meshes boneNodes =
(
    /* M4b：蒙皮的**真回读**。期望值全部来自 weights.tsv / bones.tsv（Python 写下的输入），
       读数全部来自场景里那个 Skin 修改器，两边来自不同进程 —— 这才是验收。
       正式脚本 23 轮的 written_vertices=18892 数的是「ReplaceVertexWeights 返回没抛」，
       而 M4c/M4e 实测「返回 OK 而权重是 0.0」真实存在，那条计数证明不了场景里挂着权重。
       这一段**只报数不判定**（v4 契约：判在 Python），只有「量不了」才抛：
       没有 Skin 修改器、顶点表读不出来、行数和网格顶点数对不上、骨号映射不成立。 */
    local tStart = timeStamp()
    if meshes.count != 1 do
        throw ("蒙皮回读只支持单网格，实际 " + (meshes.count as string) + " 个")
    local meshObj = meshes[1]
    local smod = undefined
    for i = 1 to meshObj.modifiers.count do
        if smod == undefined and (classOf meshObj.modifiers[i]) == Skin do smod = meshObj.modifiers[i]
    if smod == undefined do
        throw ("网格 " + meshObj.name + " 上没有 Skin 修改器，但 skin_apply 报了 ran：回读无处可取")
    local vertexCount = meshObj.numVerts
    if vertexCount <= 0 do throw ("网格顶点数读成 " + (vertexCount as string) + "，回读无从对应")
    if not (mmd2maxSkinPrime meshObj smod) do
        throw "回读预热失败：Skin 顶点表仍然读不出来（前提 P1，见 mmd2maxSkinPrime 上方笔记）"

    /* ── 骨号映射：与写入侧同一条规则，但这里拿它去核对**场景回读到的骨名** ── */
    local lines = mmd2maxReadTable MMD2MAX_BONES
    local rows = mmd2maxRows lines
    local cSkinFlag = mmd2maxColPos lines "skin_flag"
    if rows.count != boneNodes.count do
        throw ("回读时骨骼表 " + (rows.count as string) + " 行与场景骨骼 " \
            + (boneNodes.count as string) + " 个不符：骨号对不上，读数全是垃圾")
    local skinFlags = #()
    for i = 1 to rows.count do
    (
        local flag = 1
        if rows[i].count >= cSkinFlag do
            if rows[i][cSkinFlag] == "0" do flag = 0
        append skinFlags flag
    )
    local globalToSkin = #()
    for i = 1 to boneNodes.count do append globalToSkin 0
    local skinNodeNames = #()
    local skinId = 0
    for i = 1 to boneNodes.count do
    (
        if skinFlags[i] == 1 do
        (
            skinId += 1
            globalToSkin[i] = skinId
            append skinNodeNames boneNodes[i].name
        )
    )
    local boneTotal = 0
    try ( boneTotal = skinOps.GetNumberBones smod ) catch ( boneTotal = 0 )
    if boneTotal != skinNodeNames.count do
        throw ("Skin 里回读到 " + (boneTotal as string) + " 根骨，映射期望 " \
            + (skinNodeNames.count as string) + " 根：骨号映射不成立，中止回读")
    /* 映射成立还不够，还得一根根对名字：globalToSkin 是按 skinFlags 顺序累加的，
       哪一天 AddBone 的顺序变了，这里先响，而不是让下面 18892 行权重全比错。 */
    local nameMismatch = 0
    for i = 1 to skinNodeNames.count do
    (
        local gotName = ""
        try ( gotName = skinOps.GetBoneName smod i 0 ) catch ( gotName = "读不到" )
        if (gotName as string) != skinNodeNames[i] do
        (
            nameMismatch += 1
            if nameMismatch <= 5 do
                mmd2maxLog ("  回读骨名不符 Skin#" + (i as string) + " 场景=" \
                    + (gotName as string) + " 期望=" + skinNodeNames[i])
        )
    )

    /* ── 期望值：weights.tsv 重读一遍，按列名取，归一化后存进按顶点号编址的数组 ── */
    local wtbl = mmd2maxReadTable MMD2MAX_WEIGHTS
    local wlines = mmd2maxRows wtbl
    local cVert = mmd2maxColPos wtbl "vertex_index"
    local cPairs = mmd2maxColPos wtbl "bone_weight_pairs"
    if wlines.count != vertexCount do
        throw ("权重行数 " + (wlines.count as string) + " 与网格顶点数 " \
            + (vertexCount as string) + " 不符，回读无从对应")
    local expIdsArr = #()
    local expWsArr = #()
    for i = 1 to vertexCount do ( append expIdsArr #(); append expWsArr #() )
    local expLinks = #()
    for i = 1 to skinNodeNames.count do append expLinks 0
    local indexSeqBad = 0
    local pairsDropped = 0
    local linksExpected = 0
    local vertsNoExp = 0
    for i = 1 to wlines.count do
    (
        local p = wlines[i]
        local ids = #()
        local ws = #()
        if p.count >= cPairs then
        (
            /* 权重行必须按 vertex_index 落位；这里既检查「序号 == 行号」，
               也用它当数组下标的正当理由（不检查就是拿错位的数据当判据）。 */
            local viRaw = (p[cVert] as integer)
            if viRaw + 1 != i do indexSeqBad += 1
            local wsum = 0.0
            for j = cPairs to p.count do
            (
                local pair = mmd2maxSplit p[j] ":"
                if pair.count >= 2 then
                (
                    local g = (pair[1] as integer)
                    local w = pair[2] as float
                    if g >= 0 and g < boneNodes.count and globalToSkin[g + 1] > 0 then
                    (
                        local sid = globalToSkin[g + 1]
                        append ids sid
                        append ws w
                        wsum += w
                        expLinks[sid] = expLinks[sid] + 1
                        linksExpected += 1
                    )
                    else pairsDropped += 1
                )
            )
            if ids.count > 0 and wsum > 0.0 do
            (
                for k = 1 to ws.count do ws[k] = ws[k] / wsum
            )
        )
        if ids.count == 0 do vertsNoExp += 1
        expIdsArr[i] = ids
        expWsArr[i] = ws
    )

    /* ── 扫一遍；有坍缩顶点就按 P3 补救一次，再扫一遍 ── */
    disableSceneRedraw()
    local scan = mmd2maxSkinScan smod expIdsArr expWsArr vertexCount skinNodeNames.count
    local zeroPre = scan[4]
    local repaired = 0
    if scan[11].count > 0 do
    (
        /* 顺序不能反：先 unNormalizeVertex 关掉归一化，再写第二次。
           反过来就是「在已经坍缩的顶点上再写一次」，M4e T6 实测读回来还是 0.0。 */
        for vi in scan[11] do
        (
            local eIds = expIdsArr[vi]
            if eIds.count > 0 then
            (
                try ( skinOps.unNormalizeVertex smod vi true ) catch () -- 允许失败：只是重写的前置动作（P3），没救回来的顶点留在 zero_verts 里变红，不会被静默咽掉。
                local okFix = false
                try ( skinOps.ReplaceVertexWeights smod vi eIds expWsArr[vi]; okFix = true ) catch () -- 允许失败：抛了就不计入 repaired，重扫的 zero_verts 才是最终读数。
                if okFix do repaired += 1
            )
        )
        scan = mmd2maxSkinScan smod expIdsArr expWsArr vertexCount skinNodeNames.count
    )
    enableSceneRedraw()

    local linkDiffMax = 0
    local linkMismatchBones = 0
    for i = 1 to skinNodeNames.count do
    (
        local d = expLinks[i] - scan[10][i]
        if d < 0 do d = -d
        if d > linkDiffMax do linkDiffMax = d
        if d > 0 do linkMismatchBones += 1
    )

    /* #35 那两个看得见的数：脚尖骨/足首骨各拿到多少顶点。以前只有写入侧的日志行，
       现在报场景回读的链路数；Python 那边拿同一个 weights.tsv 独立数一遍，
       两边对不上就是中间有人在丢链路。 */
    for i = 1 to skinNodeNames.count do
    (
        local nm = skinNodeNames[i]
        if matchPattern nm pattern:"* Toe0" or matchPattern nm pattern:"* Foot" do
            mmd2maxReport "skin_rb" "links" nm scan[10][i]
    )

    mmd2maxReport "skin_rb" "verts_read" "-" scan[1]
    mmd2maxReport "skin_rb" "read_fail" "-" scan[2]
    mmd2maxReport "skin_rb" "unmodified_verts" "-" scan[3]
    mmd2maxReport "skin_rb" "index_seq_bad" "-" indexSeqBad
    mmd2maxReport "skin_rb" "pairs_dropped" "-" pairsDropped
    mmd2maxReport "skin_rb" "verts_no_exp" "-" vertsNoExp
    mmd2maxReport "skin_rb" "links_expected" "-" linksExpected
    mmd2maxReport "skin_rb" "links_read" "-" scan[7]
    mmd2maxReport "skin_rb" "links_missing" "-" scan[6]
    mmd2maxReport "skin_rb" "name_mismatch" "-" nameMismatch
    mmd2maxReport "skin_rb" "zero_verts_pre" "-" zeroPre
    mmd2maxReport "skin_rb" "repaired" "-" repaired
    mmd2maxReport "skin_rb" "zero_verts" "-" scan[4]
    mmd2maxReport "skin_rb" "dom_mismatch" "-" scan[5]
    mmd2maxReport "skin_rb" "weight_err_max" "-" scan[8]
    mmd2maxReport "skin_rb" "sum_err_max" "-" scan[9]
    mmd2maxReport "skin_rb" "link_diff_max" "-" linkDiffMax
    mmd2maxReport "skin_rb" "link_mismatch_bones" "-" linkMismatchBones
    local elapsed = ((timeStamp() - tStart) / 1000.0)
    mmd2maxReport "skin_rb" "elapsed_s" "-" elapsed
    mmd2maxReport "pass" "ran" "skin_verify" 1

    local vmsg = "蒙皮回读：" + (scan[1] as string) + "/" + (vertexCount as string) + " 顶点"
    vmsg = vmsg + "，未写入=" + (scan[3] as string) + "，零权重=" + (scan[4] as string)
    vmsg = vmsg + "（补前 " + (zeroPre as string) + "，补救重写 " + (repaired as string) + "）"
    vmsg = vmsg + "，主控不符=" + (scan[5] as string) + "，缺失链路=" + (scan[6] as string)
    vmsg = vmsg + "，链路 期望/回读=" + (linksExpected as string) + "/" + (scan[7] as string)
    vmsg = vmsg + "，最大权重误差=" + (scan[8] as string) + "，耗时 " + (elapsed as string) + " 秒"
    mmd2maxLog vmsg
)


/* ================= 顶点变形（表情）→ Morpher =================
   MMD 表情（顶点 morph）在 Max 端最自然的落地方式是 Morpher 修改器通道。
   每个顶点 morph 克隆基准网格、施加偏移（同拓扑），再 WM3_MC_BuildFromNode 成通道。
   这样表情可滑杆驱动，且不破坏 Skin 蒙皮。 */
fn mmd2maxBuildMorpher meshObj morphFile =
(
    if meshObj == undefined do return false
    if not (doesFileExist morphFile) do
    (
        mmd2maxLog "未提供 morphs 文件，跳过 Morpher 构建。"
        return false
    )
    local lines = mmd2maxReadTable morphFile
    local rows = mmd2maxRows lines
    if rows.count == 0 do return false
    /* 列名见文件表头：主记录（MORPH）用 row_kind/morph_name/item_count_or_flag，
       第二记录（@V）用 vertex_index/offset_x~offset_z。
       表头缺任何一个名字，下面的 mmd2maxColPos 会直接抛 —— 老的无表头文件第 1 行
       会被当成语法行、于是这里必然报缺列，不会静默少一个表情。 */
    local cKind = mmd2maxColPos lines "row_kind"
    local cMorphName = mmd2maxColPos lines "morph_name"
    local cFlag = mmd2maxColPos lines "item_count_or_flag"
    local cVert = mmd2maxColPos lines "vertex_index"
    local cOffX = mmd2maxColPos lines "offset_x"
    local cOffY = mmd2maxColPos lines "offset_y"
    local cOffZ = mmd2maxColPos lines "offset_z"

    /* 把 MORPH / V 行聚合成块：每块 = #(名称, #( (顶点序号1based, 偏移vec3), ... )) */
    local blocks = #()
    local cur = undefined
    for p in rows do
    (
        if p.count >= cKind then
        (
            if p[cKind] == "MORPH" then
            (
                if cur != undefined do append blocks cur
                /* item_count_or_flag 列是「本 morph 的顶点偏移条数」；Python 侧对不可导出的
                   morph 写两个哨兵：SKIP（非顶点变形）和 CHANNEL_FULL
                   （顶点变形已超过 Morpher 的 100 通道上限，被截断）。
                   两种都不能建通道 —— CHANNEL_FULL 后面没有 V 行，建了会得到
                   一个和基准网格完全重合的假通道，还白占一个名额。 */
                if p.count >= cFlag and p[cFlag] != "SKIP" and p[cFlag] != "CHANNEL_FULL" then
                    cur = #(p[cMorphName], #())
                else
                (
                    cur = undefined
                    if p.count >= cFlag and p[cFlag] == "CHANNEL_FULL" do
                        mmd2maxLog ("  Morpher 通道已满，跳过表情：" + (p[cMorphName] as string))
                )
            )
            else if p[cKind] == "V" and cur != undefined and p.count >= cOffZ then
            (
                local vi = (p[cVert] as integer) + 1
                local offv = [(p[cOffX] as float), (p[cOffY] as float), (p[cOffZ] as float)]
                append cur[2] #(vi, offv)
            )
        )
    )
    if cur != undefined do append blocks cur
    if blocks.count == 0 do
    (
        mmd2maxLog "morphs 文件无有效顶点 morph，跳过 Morpher。"
        return false
    )

    /* 确保基准网格是 Editable_Mesh */
    if (classOf meshObj) != Editable_Mesh do meshObj = convertTo meshObj Editable_Mesh
    local morpherMod = Morpher()
    addModifier meshObj morpherMod
    setCommandPanelTaskMode #modify
    select meshObj
    modPanel.setCurrentObject morpherMod

    /* 在 -U MAXScript 批处理模式下，MorpherMXS.dlx 提供的 WM3_* 全局函数
       可能尚未初始化（报 undefined）。先做 best-effort 预加载，再判断可用性；
       不可用则明确跳过表情构建（非致命），避免刷一堆无意义报错、也绝不崩主流程。

       注意：早期版本用 `isKindOf WM3_MC_BuildFromNode MAXScriptFunction` 判断，
       这个 API 对函数值不适用（isKindOf 是给对象/值用的），恒为 false，
       导致表情每次都被误判为「未加载」而全部跳过。正确做法是直接与 undefined 比较。 */
    try ( pluginManager.loadClass Morpher ) catch ()
    try ( pluginManager.loadClass MorpherMXS ) catch ()
    local haveWM3 = false
    try ( haveWM3 = (WM3_MC_BuildFromNode != undefined) ) catch ( haveWM3 = false )
    /* 诊断：上一轮日志里 7 个通道全部报
       "Call needs function or class, got: undefined"，说明符号存在但不可调用。
       把它的实际类型打出来，下一轮就能判断该换成哪种调用形式。 */
    local wm3Type = "?"
    try ( wm3Type = (classOf WM3_MC_BuildFromNode) as string ) catch ( wm3Type = "undefined" )
    mmd2maxLog ("WM3_MC_BuildFromNode 类型：" + wm3Type + " 可用=" + (haveWM3 as string))
    if not haveWM3 do
    (
        mmd2maxLog "WM3_* Morpher 命令不可用，跳过表情(Morpher)构建。"
        return true
    )

    local built = 0
    local targets = #()
    for b in blocks do
    (
        local name = b[1]
        local offsets = b[2]
        local target = copy meshObj
        /* 通道名跟着「目标节点名」走：实测 BuildFromNode 之后 WM3_MC_GetName 1..8 号
           回来的是 default001..default008 —— 就是 meshObj（OBJ 里那个组叫 default）的
           副本自动编号名。所以先把副本改名成表情名，通道名自然就对了：08:20 那轮
           [1]あ/Y [2]い/Y … 有名=61/61。（WM3_SetChannelName 实测 61/61 抛异常，
           别再往回加。） */
        try ( target.name = name ) catch ( mmd2maxLog ("  表情副本改名失败：" + (name as string)) )
        /* 藏起来必须在「通道构建成功」之前做：早期版本建失败时 7 个复制网格全部留在
           场景里可见 —— 用户看到「多出来一个和自己重叠、选不中、也不受 Biped 控制的
           模型」就是这个（7 个完全重叠，看着像 1 个）。 */
        try ( hide target ) catch ()
        /* copy 会把 Skin / Morpher 一并复制过来：必须清空修改器栈，
           否则目标网格会跟着 Biped 变形，morph 偏移就套在变形后的顶点上。 */
        try ( while target.modifiers.count > 0 do deleteModifier target 1 ) catch ()
        if (classOf target) != Editable_Mesh do target = convertTo target Editable_Mesh
        for o in offsets do
        (
            local vi = o[1]
            local offv = o[2]
            if vi >= 1 and vi <= target.numVerts do
            (
                local v = getVert target vi
                setVert target vi (v + offv)
            )
        )
        update target
        /* 通道号必须是 1,2,3... 的连续编号，不能用「已有通道数 + 1」推。
           WM3_NumberOfChannels 返回的是容量而不是已用数（实测空 Morpher 就报 100，
           建 7 个表情后 800、60 个 6100、61 个 6200，每次正好 +100），所以旧写法
           把 61 个通道全写到 101/201/.../6101 上，1..8 号通道读回来是 "- empty -"。 */
        local chIdx = built + 1
        local okBuild = false
        local errMsg = ""
        try ( WM3_MC_BuildFromNode morpherMod chIdx target; okBuild = true )
        catch ( errMsg = getCurrentException() )
        if not okBuild do
        (
            /* 另一种官方写法：直接给通道设目标节点（部分版本只有这个能用） */
            try ( WM3_MC_SetTarget morpherMod chIdx target; okBuild = true )
            catch ( errMsg = errMsg + " / " + (getCurrentException()) )
        )
        if okBuild then
        (
            /* 这里绝不再调 WM3_SetChannelName：08:20 那轮实测它 61/61 全部抛异常
               （日志 改名接口抛异常=61），通道名靠上面的 target.name 就已经对了，
               留着只是每次白抛 61 回。 */
            append targets target
            built += 1
        )
        else
        (
            /* 建不出来就把临时网格删掉，绝不留可见残骸 */
            try ( delete target ) catch ()
            mmd2maxLog ("Morpher 通道构建失败：" + (name as string) + " " + errMsg)
        )
    )
    /* 关键自检：WM3_MC_SetTarget 只「设置已有通道的目标」，若它并不新建通道，
       上面几次都会写到同一个通道（built 照样等于表情数，但实际只有 1 个表情）。
       WM3_NumberOfChannels 不能用来判断成败 —— 它报的是容量（100 的整数倍）。
       真正要看的是 1..blocks.count 这几个通道里有多少「读得回目标 / 名字」。 */
    local chTotal = 0
    try ( chTotal = (WM3_NumberOfChannels morpherMod) ) catch ( chTotal = -1 )
    local chUsed = 0
    local chNamed = 0
    for k = 1 to blocks.count do
    (
        try ( if (WM3_MC_GetTarget morpherMod k) != undefined do chUsed += 1 ) catch ()
        local nm = ""
        try ( nm = (WM3_MC_GetName morpherMod k) as string ) catch ( nm = "" )
        /* 「有名」必须等于「名字就是那个表情名」：上一轮它读回来的是副本的自动编号名
           default001，非空但不等于表情名，按非空判就等于自欺欺人。 */
        local blk = blocks[k]
        local want = blk[1] as string
        if nm == want do chNamed += 1
    )
    /* 诊断：把前 8 个通道的「名字 / 是否有目标」打出来，通道号修对之后
       这一行应该逐个显示表情名，而不是整排 "- empty -/N"。 */
    local chInfo = ""
    for k = 1 to 8 do
    (
        local nm = "?"
        try ( nm = (WM3_MC_GetName morpherMod k) ) catch ( nm = "?" )
        local tgt = "?"
        try ( tgt = (if (WM3_MC_GetTarget morpherMod k) != undefined then "Y" else "N") ) catch ( tgt = "?" )
        chInfo = chInfo + " [" + (k as string) + "]" + nm + "/" + tgt
    )
    mmd2maxLog ("Morpher 通道明细：" + chInfo)
    mmd2maxLog ("Morpher 通道：" + (built as string) + " / " + (blocks.count as string) \
        + "，有目标=" + (chUsed as string) + " 有名=" + (chNamed as string) \
        + "，通道容量=" + (chTotal as string))
    /* 四个数分别管四件事，所以分别报：blocks = Max 到底从表情表解析出几条（文件被
       截断/表头读错时这里就少），built = 通道真建了几条，有目标/有名 = 回读能不能
       拿到内容与表情名（#24 空索引、#25 default001 两个坑各管一条）。
       通道容量故意**不**报：WM3_NumberOfChannels 是已分配容量，不是已用数量。 */
    mmd2maxReport "morpher" "blocks" "-" blocks.count
    mmd2maxReport "morpher" "built" "-" built
    mmd2maxReport "morpher" "with_target" "-" chUsed
    mmd2maxReport "morpher" "with_name" "-" chNamed
    /* ran 排在这四行读数之后、下面的 built==0 回滚之前：回滚那条路 return false
       就走不到函数末尾，边界若排在末尾就会出现「有读数、没有段」的孤儿行。 */
    mmd2maxReport "pass" "ran" "morpher_build" 1
    /* 自检分两级，绝不一上来就拆修改器：
       built == 0 —— 两个 WM3 接口逐个抛异常，确实一个通道都没建，清掉修改器；
       临时网格只 append 到 targets 才存在，这里 targets 必为空，不会误删。
       built > 0 但读不回目标/名字 —— 要么「不抛异常但也不建通道」（就是本轮
       61/61 + 前 8 通道全空那个状态），要么读接口本身不通。后者没探过，
       所以只警告、保留现场，让用户在 Max 里打开通道列表人工确认。 */
    if built == 0 do
    (
        for t in targets do ( try ( delete t ) catch () )
        try ( deleteModifier meshObj morpherMod ) catch ()
        mmd2maxLog ("Morpher 不可用（" + (blocks.count as string) \
            + " 个表情全部建通道失败），已移除修改器并清理临时网格。")
        return false
    )
    if chUsed == 0 and chNamed == 0 do
        mmd2maxLog ("Morpher 警告：" + (built as string) \
            + " 次建通道都没抛异常，但 1.." + (blocks.count as string) \
            + " 号通道读不回任何目标/名字 —— 请在 Max 里打开 Morpher 通道列表人工确认。")
    if chUsed > 0 and chNamed < chUsed do
        mmd2maxLog ("Morpher 通道名对不上：" + (chNamed as string) + "/" + (chUsed as string) \
            + " 个通道的名字不是表情名 —— 见上面「通道明细」那一行。")
    true
)

/* ================= 物理刚体 & 关节 =================
   PMX 物理（刚体+关节）在 Max 端 best-effort 落地：每根刚体生成一个可见代理
   （Box/Sphere/Cylinder，按形状与尺寸），挂到对应骨架骨；关节用 Link_Constraint
   把两刚体约束在一起，并把限位/弹簧记到注释属性。真实动力学需用户再开启 MassFX
   （这些代理已带正确位置/朝向，可直接作为 MassFX 的刚体源）。 */
fn mmd2maxCoordMatrix =
(
    /* convert_vec(v) = (v.x, v.z, v.y)，忽略缩放后的 3x3 线性部分（det = -1，含镜像）。
       矩阵按「第 i 行 = 基向量 e_i 的像」写，所以 M * [x,y,z] = x*r1 + y*r2 + z*r3。
       注意：这里必须是 Python 侧 mmd2max/coords.py 的同一个映射，改一处就要改另一处。 */
    matrix3 [1,0,0] [0,0,1] [0,1,0] [0,0,0]
)

fn mmd2maxRotToMax rx ry rz =
(
    /* 把 MMD 弧度欧拉（XYZ）旋转映射到 Max 坐标系：R' = M * R * M^T（M 正交）。
       M 含镜像时这个共轭仍然是正确的（M^T = M⁻¹），只是手性会反映在转向的顺逆上。
       这只是近似（PMX 未规定刚体欧拉顺序），用于物理代理朝向。 */
    local M = mmd2maxCoordMatrix()
    local R = (eulerAngles rx ry rz order:#xyz) as matrix3
    local Rmax = M * R * (transpose M)
    local eu = Rmax as eulerAngles
    eu.order = #xyz
    eu
)

fn mmd2maxBuildPhysics boneNodes physicsFile =
(
    if not (doesFileExist physicsFile) do
    (
        mmd2maxLog "未提供 physics 文件，跳过物理刚体构建。"
        return false
    )
    local lines = mmd2maxReadTable physicsFile
    local rows = mmd2maxRows lines
    if rows.count == 0 do return false
    /* 两种记录共用一张列名表：主记录（RIGID）的名字在第 1 行，@JOINT 行的名字
       由 mmd2maxReadTable 从第 2 个字段起并进来，两边都带 rigid_/joint_ 前缀，不会撞名。
       缺列（含「文件根本没有表头」这种老格式）= mmd2maxColPos 抛，绝不静默读错位。 */
    local cKind = mmd2maxColPos lines "row_kind"
    local cRIdx = mmd2maxColPos lines "rigid_index"
    local cRName = mmd2maxColPos lines "rigid_name"
    local cRBone = mmd2maxColPos lines "rigid_bone"
    local cRShape = mmd2maxColPos lines "rigid_shape"
    local cRSX = mmd2maxColPos lines "rigid_size_x"
    local cRSY = mmd2maxColPos lines "rigid_size_y"
    local cRSZ = mmd2maxColPos lines "rigid_size_z"
    local cRPX = mmd2maxColPos lines "rigid_pos_x"
    local cRPY = mmd2maxColPos lines "rigid_pos_y"
    local cRPZ = mmd2maxColPos lines "rigid_pos_z"
    local cRRX = mmd2maxColPos lines "rigid_rot_x"
    local cRRY = mmd2maxColPos lines "rigid_rot_y"
    local cRRZ = mmd2maxColPos lines "rigid_rot_z"
    local cRMode = mmd2maxColPos lines "rigid_physics_mode"
    local cJName = mmd2maxColPos lines "joint_name"
    local cJJA = mmd2maxColPos lines "joint_rigid_a"
    local cJJB = mmd2maxColPos lines "joint_rigid_b"
    local cJPX = mmd2maxColPos lines "joint_pos_x"
    local cJPMin = mmd2maxColPos lines "joint_pos_min_x"
    local cJPMax = mmd2maxColPos lines "joint_pos_max_x"
    local cJRMin = mmd2maxColPos lines "joint_rot_min_x"
    local cJRMax = mmd2maxColPos lines "joint_rot_max_x"
    local cJPSpring = mmd2maxColPos lines "joint_pos_spring_x"
    local cJRSpring = mmd2maxColPos lines "joint_rot_spring_x"
    local cJLast = mmd2maxColPos lines "joint_rot_spring_z"
    local rigids = #()
    for i = 1 to 4096 do append rigids undefined
    /* 物理对象单独分层：363 个刚体 + 700 个关节标记，混在主层里既没法看也没法选。
       joints 层默认隐藏（700 个 Dummy 会让视口非常卡），要看时在层管理器里打开即可。 */
    local layerRB = undefined
    local layerJT = undefined
    try ( layerRB = LayerManager.newLayerFromName "mmd2max_physics" ) catch ()
    try ( layerJT = LayerManager.newLayerFromName "mmd2max_joints" ) catch ()
    local created = 0
    local rigidFail = 0
    local rotFail = 0
    local minP = undefined
    local maxP = undefined
    for p in rows do
    (
        if p.count >= cRMode and p[cKind] == "RIGID" then
        (
            try
            (
                /* Python 写出的列名（见 physics.tsv 表头行）：
                   row_kind/rigid_index/rigid_name/rigid_name_en/rigid_bone/rigid_group/
                   rigid_nocollide_mask/rigid_shape/rigid_size_x~z/rigid_pos_x~z/rigid_rot_x~z/...
                   早期版本从 rigid_name_en 那格取骨骼、从 rigid_nocollide_mask 取形状，整体
                   错位一位：把 name_en 当成了骨骼、把 mask 当成了形状，且 pos/rot 也全错。
                   现在列号全部由表头名解析，Python 侧增删列也不会读错，缺列直接抛。 */
                local idx = p[cRIdx] as integer
                local name = p[cRName]
                local boneNew = p[cRBone] as integer
                local shapeIdx = p[cRShape] as integer
                local sx = p[cRSX] as float
                local sy = p[cRSY] as float
                local sz = p[cRSZ] as float
                local px = p[cRPX] as float
                local py = p[cRPY] as float
                local pz = p[cRPZ] as float
                local rx = p[cRRX] as float
                local ry = p[cRRY] as float
                local rz = p[cRRZ] as float
                local proxy = undefined
                /* 尺寸要和位置用同一套轴变换：MMD(x,y,z) -> Max(x,-z,y)。
                   所以三个半边长也要同样置换：Max.x=sx, Max.y=sz, Max.z=sy。
                   早期版本直接把 (sx,sy,sz) 当成 (宽,长,高)，盒子的高矮和长短是反的。 */
                local ex = sx          /* Max X 半宽 */
                local ey = sz          /* Max Y 半长 */
                local ez = sy          /* Max Z 半高 */
                if shapeIdx == 0 then
                (
                    /* 球：size = 半径 */
                    proxy = Sphere radius:(amax ex 0.001) pos:[px,py,pz] segs:8
                )
                else if shapeIdx == 2 then
                (
                    /* 胶囊：PMX 的 size = (半径, 半高, 0)，轴向是局部 Y（转成 Max 就是 Z）。
                       Max 的 Capsule 轴向也是 Z，height 是「含两端的整体高度」。 */
                    local radius = amax ex 0.001
                    local hgt = amax ((ey * 2.0) + (radius * 2.0)) 0.002
                    try ( proxy = Capsule radius:radius height:hgt pos:[px,py,pz] ) \
                        catch ( proxy = Cylinder radius:radius height:hgt pos:[px,py,pz] )
                )
                else
                (
                    /* 方块：size 是三个方向的半边长，Max 的 Box 用整长，
                       且参数名是 length/width/height（没有 depth）。 */
                    proxy = Box width:(amax (ex * 2.0) 0.001) \
                                length:(amax (ey * 2.0) 0.001) \
                                height:(amax (ez * 2.0) 0.001) pos:[px,py,pz]
                )
                if proxy != undefined do
                (
                    proxy.name = ("RB_" + (idx as string) + "_" + name)
                    proxy.wirecolor = color 255 128 0
                    /* Box 的 pivot 在底角，用 center 把它挪到刚体中心 */
                    if shapeIdx != 0 and shapeIdx != 2 do
                        try ( proxy.center = [px, py, pz] ) catch ()
                    /* 关键：`(eulerAngles) as quat` 这条转换在部分 Max 版本上不成立，
                       异常又被 try 吞掉 —— 结果 363 个代理**全部保持竖直**，
                       裙摆刚体竖着排成一块板，不顺着裙子的喇叭形（用户截图）。
                       改为经 matrix3 转四元数，并统计失败数。 */
                    local rotOk = false
                    try
                    (
                        local m3 = (mmd2maxRotToMax rx ry rz) as matrix3
                        proxy.rotation = (m3 as quat)
                        rotOk = true
                    )
                    catch ( rotOk = false )
                    if not rotOk do rotFail += 1
                    /* MAXScript 的 and **不短路**：boneNew = -1 时 `boneNodes[0]` 会抛
                       "array index must be positive number"（实跑 362/363 里那 1 个失败
                       就是它）。必须拆成嵌套 if 才安全。 */
                    if boneNew >= 0 do
                        if boneNew < boneNodes.count do
                            if boneNodes[boneNew + 1] != undefined do
                                proxy.parent = boneNodes[boneNew + 1]
                    if idx >= 0 and idx < rigids.count do rigids[idx] = proxy
                    try ( if layerRB != undefined do layerRB.addNode proxy ) catch ()
                    /* 记录整体包围盒，跑完用一行日志核对「代理是否落在角色身上」 */
                    local bb = undefined
                    /* 第二个参数是「在哪个坐标系里算」，传对象自身 transform 得到的是
                       局部包围盒（所以之前日志里 z 只到 25，完全误导）。要用单位矩阵 = 世界坐标。 */
                    try ( bb = nodeGetBoundingBox proxy (matrix3 1) ) catch ( bb = undefined )
                    if bb != undefined and bb[1] != undefined do
                    (
                        if minP == undefined then ( minP = bb[1]; maxP = bb[2] )
                        else
                        (
                            for k = 1 to 3 do
                            (
                                if bb[1][k] < minP[k] do minP[k] = bb[1][k]
                                if bb[2][k] > maxP[k] do maxP[k] = bb[2][k]
                            )
                        )
                    )
                    created += 1
                )
            )
            catch ( rigidFail += 1; \
                    if rigidFail <= 3 do mmd2maxLog ("刚体代理创建失败：" + (getCurrentException())) )
        )
    )
    /* 关节：在关节位置放一个 Dummy 标记，记录两刚体索引与限位/弹簧（注释属性）。
       刚体已各自挂到对应骨架骨（跟随动画），故这里不再改控制器，避免「父级 + 约束」
       冲突。真实动力学需在 MassFX 中按这些参数连接两刚体。

       JOINT 列名见 @JOINT 那行（joint_index / joint_name / joint_type / joint_rigid_a /
       joint_rigid_b / joint_pos_x~z / joint_rot_x~z / joint_pos_min_x~z / joint_pos_max_x~z /
       joint_rot_min_x~z / joint_rot_max_x~z / joint_pos_spring_x~z / joint_rot_spring_x~z，
       末列 joint_rot_spring_z 就是行宽判据）。
       早期版本整体少取 1 位（rigidA 取到 jointType、pos 取到 rigidB…），全部错位。 */
    local jointCount = 0
    local jointFail = 0
    for p in rows do
    (
        if p.count >= cJLast and p[cKind] == "JOINT" then
        (
            try
            (
                local name = p[cJName]
                local a = p[cJJA] as integer
                local b = p[cJJB] as integer
                local jx = p[cJPX] as float
                local jy = p[cJPX + 1] as float
                local jz = p[cJPX + 2] as float
                local jm = Dummy name:("JT_" + name)
                jm.pos = [jx, jy, jz]
                jm.wirecolor = color 0 180 255
                /* 把限位/弹簧记到 Dummy 的注释里，供后续接 MassFX 时读取。
                   每组限位是连续的三列（x/y/z），所以取首列的列号再加 1、2。 */
                local note = "joint " + name + " rigidA=" + (a as string) + " rigidB=" + (b as string)
                note = note + " pmin=(" + p[cJPMin] + "," + p[cJPMin + 1] + "," + p[cJPMin + 2] + ")"
                note = note + " pmax=(" + p[cJPMax] + "," + p[cJPMax + 1] + "," + p[cJPMax + 2] + ")"
                note = note + " rmin=(" + p[cJRMin] + "," + p[cJRMin + 1] + "," + p[cJRMin + 2] + ")"
                note = note + " rmax=(" + p[cJRMax] + "," + p[cJRMax + 1] + "," + p[cJRMax + 2] + ")"
                note = note + " pspring=(" + p[cJPSpring] + "," + p[cJPSpring + 1] + "," + p[cJPSpring + 2] + ")"
                note = note + " rspring=(" + p[cJRSpring] + "," + p[cJRSpring + 1] + "," + p[cJRSpring + 2] + ")"
                setUserProp jm "mmd2max_joint" note
                try ( if layerJT != undefined do layerJT.addNode jm ) catch ()
                jointCount += 1
            )
            catch ( jointFail += 1; \
                    if jointFail <= 3 do mmd2maxLog ("关节标记构建失败：" + (getCurrentException())) )
        )
    )
    if minP != undefined do
    (
        local bmsg = "刚体代理包围盒：x[" + (minP[1] as string) + "," + (maxP[1] as string) + "]"
        bmsg = bmsg + " y[" + (minP[2] as string) + "," + (maxP[2] as string) + "]"
        bmsg = bmsg + " z[" + (minP[3] as string) + "," + (maxP[3] as string) + "]"
        mmd2maxLog bmsg
    )
    /* 关节标记默认隐藏：700 个 Dummy 在视口里既看不见结构又特别卡 */
    try ( if layerJT != undefined do layerJT.isHidden = true ) catch ()
    local sumMsg = "物理刚体代理：" + (created as string) + "（失败 " + (rigidFail as string) + "）"
    sumMsg = sumMsg + " 关节标记：" + (jointCount as string) + "（失败 " + (jointFail as string) + "）"
    sumMsg = sumMsg + " 旋转失败=" + (rotFail as string)
    sumMsg = sumMsg + "；刚体在层 mmd2max_physics，关节在层 mmd2max_joints（已隐藏）"
    mmd2maxLog sumMsg
    true
)

fn mmd2maxMain =
(
    /* 配置必须第一个读：后面每一步（含日志落哪、.max 存哪）都依赖它。
       读不到就抛，整轮中止 —— 绝不带着默认值继续摆。 */
    mmd2maxLoadCfg()
    mmd2maxInitLog()
    /* 摘要必须在 InitLog 之后：InitLog 用 createFile 重建日志文件，
       之前写的每一行都会被截掉（第十九轮的日志里看不见「配置来自」就是这件事）。 */
    mmd2maxLog MMD2MAX_CFGSUMMARY
    /* 判据报告紧随其后重建：往后每一步只往里追加读数。放在 InitLog 之后是因为
       它也用 createFile —— 早于日志建立没坏处，但摘要先行能让日志里先有路径证据。 */
    mmd2maxReportInit()
    mmd2maxPrepareScene()
    mmd2maxReport "pass" "ran" "scene_prepare" 1
    mmd2maxImportObj()
    mmd2maxReport "pass" "ran" "mesh_import" 1
    local boneNodes = mmd2maxCreateBones()
    local meshes = mmd2maxMeshes()
    if mmd2maxSkinEnabled() then
    (
        mmd2maxApplySkin meshes boneNodes
        /* M4b：写完立刻从场景里读回来。验收不再看写入侧的计数 —— written_vertices
           数的是「ReplaceVertexWeights 返回没抛」，而探针实测「返回 OK 而权重 0.0」
           与「没写过也读得出 1.0」都真实存在，那条计数证明不了场景里挂着权重。 */
        mmd2maxVerifySkin meshes boneNodes
    )
    else
        mmd2maxLog "按 MMD2MAX_SKIN=false 跳过蒙皮（仅网格 + 骨架）。"

    /* v2 改进落地：表情 -> Morpher 通道；物理刚体/关节 -> 代理与约束。 */
    local meshObj = undefined
    if meshes.count > 0 do meshObj = meshes[1]
    if meshObj != undefined do
    (
        /* 直接读键，不再 try/catch 兜空串（#56）：MMD2MAX_MORPHS 是 CFG_KEYS 里的键，
           mmd2maxLoadCfg 缺键即抛，所以「读不到」根本到不了这里；而 catch 会把
           「配置丢了」压成「这一轮没有表情」——蒙皮照常、morph 静默不建、日志全绿。
           空串仍然是合法值（= 这一轮不做表情），判据留在 if 上。 */
        local morphFile = MMD2MAX_MORPHS as string
        if morphFile != "" do mmd2maxBuildMorpher meshObj morphFile
    )
    local physFile = MMD2MAX_PHYSICS as string
    if physFile != "" do
    (
        mmd2maxBuildPhysics boneNodes physFile
        /* 这一行必须在 if 的块里（M3）：没给 physics 文件时这段整段不发生。
           无条件报 ran 等于宣称跑过 —— 注册表里它是 optional，规格行退成 if_present，
           于是「没跑却报 ran」比「没报」难查得多：它会给 STALE 一个根本不存在的段。 */
        mmd2maxReport "pass" "ran" "physics_build" 1
    )

    clearSelection()
    for obj in meshes do selectMore obj
    mmd2maxZoomFit()

    mmd2maxLog ("保存：" + (MMD2MAX_OUTPUT as string))
    local saveStart = timeStamp()
    saveMaxFile MMD2MAX_OUTPUT useFileDialog:false quiet:true
    local saveElapsed = ((timeStamp() - saveStart) / 1000.0) as string
    mmd2maxLog ("保存耗时：" + saveElapsed + " 秒")

    /* 成功标记写在截图之前：.max 此时已经落盘，截图只是「额外证据」。
       上一版把截图排在标记之前，一个截图里的错误就把整轮装配判成失败
       （error.txt + 退出码 4），产物明明在却白跑一轮。 */
    local marker = createFile MMD2MAX_MARKER encoding:#utf8
    if marker == undefined do throw ("无法写入成功标记：" + (MMD2MAX_MARKER as string))
    format "{\n  \"success\": true,\n  \"output\": \"%\",\n  \"nodes\": %\n}\n" \
        (mmd2maxJsonStr (MMD2MAX_OUTPUT as string)) objects.count to:marker
    close marker
    mmd2maxLog ("完成，场景对象数：" + (objects.count as string))

    if mmd2maxScreenshotEnabled() do
    (
        mmd2maxLog "开始抓取视口截图（正/侧/顶 + 只留骨架 + 颈部/脚尖特写）..."
        /* 截图整段再包一层：函数内部还有一处没被自身 try 兜住的错误就会走到这里，
           绝不允许它再把已经成功的装配拖成失败。 */
        try
        (
            mmd2maxScreenshot()
        )
        catch
        (
            mmd2maxLog ("截图步骤异常（不影响产物）：" + (getCurrentException() as string))
        )
    )

    if mmd2maxIsInteractive() do
        messageBox ("已保存：\n" + (MMD2MAX_OUTPUT as string)) title:"mmd2max 完成"

    /* 非交互模式跑完就自己退出。
       为什么这条是硬要求而不是「顺手清理」：上层 tools/obj_run.py 以前一律
       `taskkill /F /IM 3dsmax.exe` 收尾，强杀让 Max 记下的「上次会话非正常结束」
       计数一路累积（实测 HKCU\Software\Autodesk\3dsMax\24.0\MiniDump\24.3.0.3404：
       SessionStartCount=167 / SessionCleanCloseCount=50），于是每次启动都弹
       "Startup Failure Detection" 模态框，把 -U MAXScript 的自动执行整个挡死 ——
       用户不在电脑前时这就是死锁。探针实测 quitMax() 可用，且它退出后
       SessionCleanCloseCount 会追平 SessionStartCount，下一轮启动干净。
       失败路径留在原地不退出：现场要给人看。 */
    if not mmd2maxIsInteractive() do
    (
        mmd2maxLog "非交互模式：quitMax() 正常退出（避免上层 taskkill /F 累积崩溃计数）。"
        /* quiet:true 不弹「保存更改？」——截图阶段改过 isHidden/EdgeFaces，场景是脏的，
           裸 quitMax 一旦被保存提示挡住就永远退不出去。写法沿用 tools/make_parse_probe.py
           里已经跑通的同一形式；真抛错再退回裸 quitMax。 */
        try ( quitMax quiet:true )
        catch
        (
            mmd2maxLog ("quitMax quiet 写法失败：" + (getCurrentException() as string))
            try ( quitMax )
            catch ( mmd2maxLog ("quitMax 失败：" + (getCurrentException() as string)) )
        )
    )
)

try
(
    mmd2maxMain()
)
catch
(
    local message = getCurrentException()
    mmd2maxLog ("错误：" + message)
    /* 这一段自己绝对不能再抛：它是失败路径上唯一的取证手段。
       mmd2maxErrorFilePath 在 CFG 都没读成时会退回 <CFG 目录>\mmd2max_cfg.error.txt，
       所以上层（tools/obj_run.py 的 CFG_ERROR_NAME）两种文件名都要查。 */
    try
    (
        local errorPath = mmd2maxErrorFilePath()
        if errorPath != undefined do
        (
            local errorFile = createFile errorPath encoding:#utf8
            format "%\n" message to:errorFile
            close errorFile
        )
    )
    catch ()
    if mmd2maxIsInteractiveNow() do
    (
        try ( messageBox ("转换失败：\n" + message) title:"mmd2max 失败" ) catch ()
    )
    /* 失败也要自己退。非交互模式下没人盯着屏幕，而错误全文已经落到 error.txt 和
       日志里；留在原地只会让上层等满 GRACEFUL_EXIT_SECONDS 再 taskkill /F ——
       那一下又给 Max 记一次异常终止，下次启动就弹崩溃对话框。
       真·报错弹窗（没被这里 catch 住的）走的是另一条路：obj_run 捕获到弹窗
       会保留 Max 现场，不杀掉。 */
    if not mmd2maxIsInteractiveNow() do
    (
        mmd2maxLog "失败：写完成 error.txt 后 quitMax 退出（现场已在日志与 error.txt 里）。"
        try ( quitMax quiet:true ) catch ( try ( quitMax ) catch () )
    )
)
