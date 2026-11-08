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
   另外 MMD2MAX_{CHEST,NECK}_{X,Y,Z}_{LO,HI} 是「允许空的数值键」（#70、#71）：空串读成
   undefined，取景那一段自己决定退回什么 —— 与「写了个读不出来的数」分开，后者照抛。
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
global MMD2MAX_REFRESHCALL
global MMD2MAX_REPORT
global MMD2MAX_SHOTDIR
global MMD2MAX_VERTS
global MMD2MAX_SPINE_LINKS
global MMD2MAX_FINGERS
global MMD2MAX_FINGER_LINKS
global MMD2MAX_FOOTBOXFLAT
/* 特写的取景框（#70 胸、#71 颈）：导出侧按量出来的 subject 骨端点极值 + 网格高比例余量
   算好的六个绝对坐标（三根轴各一对），两张特写各一组、形状一模一样。
   空串 = 这具模型认不出那组骨，下面读成 undefined，取景那一段退回场景包围盒 + 高度比例带。
   以前这里是本文件里的两件事，各坏过一轮：
     · `b.min.z + h * 0.58 / 0.72`（胸）与 `h * 0.55 / 0.98`（颈）—— 分母是整场景高度，
       量出来的却是带的位置：第 35 轮实测整只带比胸骨低 4cm，chest_front 里一根骨都没有。
     · x/y 拿场景包围盒 ±10cm 凑 —— 第 36 轮补了 z 却漏了 x/y，换成侧视后框的前后中心被
       身后的裙摆拽到 y=+9.8，收框只剩 y -0.4..20.0，骨在 -11.75..-7.24 照样被裁出去）。
   账见 mmd2max/export_obj._chest_band 与 _neck_band（同一把尺：_framing_band）。 */
global MMD2MAX_CHEST_X_LO
global MMD2MAX_CHEST_X_HI
global MMD2MAX_CHEST_Y_LO
global MMD2MAX_CHEST_Y_HI
global MMD2MAX_CHEST_Z_LO
global MMD2MAX_CHEST_Z_HI
global MMD2MAX_NECK_X_LO
global MMD2MAX_NECK_X_HI
global MMD2MAX_NECK_Y_LO
global MMD2MAX_NECK_Y_HI
global MMD2MAX_NECK_Z_LO
global MMD2MAX_NECK_Z_HI
/* 收敛循环的遍数上限。以前它是这一份脚本里的字面量 `for passNo = 1 to 3`，Python 侧
   只能用契约测试去钉一个自己看不见的数字；现在数值住在 mmd2max/passes.py 的注册表里，
   经 MMD2MAX.CFG 下发，校验器拿同一个数核对「报 ceiling 却只跑了两遍」这种自相矛盾。 */
global MMD2MAX_CONVERGE_MAX
global MMD2MAX_SKIN
global MMD2MAX_SHOT
global MMD2MAX_INTERACTIVE
/* 跑完是否把 3ds Max 留在屏幕上（#137）。与 INTERACTIVE 是两件事：后者管「能不能
   弹模态框问人」，自动跑必须 false；前者只管「要不要 quitMax」。以前两者捆在一起，
   于是想保留现场就只能开交互位，而交互位一开，queryBox / messageBox 会把无人值守
   的自动装配挂在半路。值只从 CFG 来，缺省也不许在脚本里给常量。 */
global MMD2MAX_KEEPOPEN
/* 装配目标值（M2 起由 mmd2max/targets.py 的 TARGETS 写进 CFG，这里只声明、不给默认值）。
   声明必须排在任何读取者之前：以前这几个数就写在声明行末尾，于是「这份脚本自己带着判据」，
   Python 侧根本不知道有哪些数 —— 现在缺键一律中止，和路径/开关同一待遇。 */
global MMD2MAX_DIRTOL
global MMD2MAX_ROLLTOL
global MMD2MAX_TOETOL
global MMD2MAX_GAPTOL
global MMD2MAX_FOOTREACH
/* 趾盒落地开关（#134 加、#135 翻默认 true）。和 FOOTREACH 一样只声明不给默认值：值来自 CFG → targets.TOE_GROUND。
   true（默认）=把 Toe0 pivot 搬到盒底落地（用户第 135 轮拍板的效果）；false=回滚贴鞋尖（#19/#78）。
   探针 probes/toe_ground.txt 已证对象偏移挪不动 Biped 画的盒，所以落地只能搬 pivot；
   代价（segment/foot_chain 的 Toe0 链偏离）在落地态由 Python 降 report 摆出，数字照打印。 */
global MMD2MAX_TOE_GROUND
/* 快照回滚开关（M4d）。和 FOOTREACH 一样只声明不给默认值：值来自 CFG，
   而 CFG 的值来自 mmd2max/targets.py 的 ROLLBACK。它决定的是「一刀写坏了场景里剩什么」，
   不是某个目标值，所以必须能整段关掉来自证它不改几何。 */
global MMD2MAX_ROLLBACK
/* 接趾守卫的触发阈值（厘米）：骨盒远端比动手前更远这么多才算「推过头」。
   它和 ROLLBACK 是两件事 —— ROLLBACK 决定「有没有这条退路」，这个数决定「什么时候走」。 */
global MMD2MAX_RBTRIG
/* 短骨门限（厘米）：|tail-head| 比它短的骨在 PMX 里只是画给 MMD 看的显示 stub
   （香菱 右手首 0.38cm），方向没意义 —— 写侧不转它、回读不量它。以前这个数在本文件里
   抄了三份、Python 另有一份，改一边要等一整轮才以「规格表多出一条没人报的行」的形式暴露。
   现在数值住在 mmd2max/targets.py 的 STUB_CM，经 CFG 下发，本文件里五处短骨闸都读这一个
   全局（写侧：mmd2maxRotatePass 转角、mmd2maxFitScore 收敛打分、mmd2maxBuildBiped 建骨时
   的 stub 判定；读侧：mmd2maxLogStructDir 的「短骨」标注、mmd2maxVerifyAccessoryDirs 回读）。 */
global MMD2MAX_STUBCM
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

/* 跑完是否保留 3ds Max 现场（#137）。CFG 自己没读成时按 **true** 处理：配置都读不到，
   唯一还能看的证据就是屏幕上这个场景，自动把它关掉等于把报错现场也一起丢掉。
   上层 tools/obj_run.py 在同一个开关下也不会再等退出 / taskkill /F，所以这里不退出
   不会攒「异常终止」计数。 */
fn mmd2maxKeepOpen =
(
    if MMD2MAX_KEEPOPEN == undefined do return true
    MMD2MAX_KEEPOPEN
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
    -- 这里不许调 mmd2maxLog 记失败：它自己就是 mmd2maxLog，写不进就会递归。
    -- 上一行的 format 已经把同一条打到监听器，所以失败出口是监听器 + 这条异常文字。
    catch ( format "[mmd2max] !! 日志落盘失败: %\n" (getCurrentException()) )
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
    -- 同上：这条也不能用 mmd2maxLog（日志文件正是这里要建的）。初始化没成 = 后面每一行
    -- 只剩监听器，实跑的 obj_run 收不到文件日志，所以失败文字必须自己打出去。
    catch ( format "[mmd2max] !! 日志初始化失败: %\n" (getCurrentException()) )
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
        try ( viewport.zoom 1.35 )
        catch ( mmd2maxLog ("取景余量拉伸失败：" + (getCurrentException() as string)) )
    )
    completeRedraw()
    true
)

/* 只挑真网格：Biped / BoneGeometry / Helper 都不算，
   否则「藏掉网格看骨架」会把骨架一起藏掉。 */
global MMD2MAX_SHOTMESH = #()

/* 每张图的取景填充率（#69），由 mmd2maxShotOne 逐个 append，抓完在 mmd2maxScreenshot
   末尾汇总成两条计数。为什么用全局而不是让 ShotOne 自己返回：每一只 ShotOne 都包在
   自己的 try 里（一张图坏不许拖垮整段），返回值会被那个 catch 吞成 undefined，
   而「哪几张真的量到了」必须是这一段的读数 —— 少一张就是红，不是静默少一行。 */
global MMD2MAX_SHOTFILL = #()

/* 每张图的**像素**读数（#115），由 mmd2maxReportShotPixels 逐个 append 一个
   #(采到, 去重, 最大同类)。为什么还要第二条通道：MMD2MAX_SHOTFILL 量的是骨的世界坐标
   投影，它只证明「取景框在画面上够大」，从不看盘上那张 PNG 里到底有什么 —— 一张全黑
   的图照样 63% 填充率、照样数进 tags。第 83 轮 #112 实拍到过这个形状（desktop.capture
   恒黑），当时 21 张全在、计数全绿。这一份是「图里真有东西」的唯一读数。
   和 SHOTFILL 一样用全局而不是返回值：每只 ShotOne 包在自己的 try 里，返回值会被吞。 */
global MMD2MAX_SHOTPIXEL = #()

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
    /* 每只网格各包一次：Max 对单个节点写 isHidden 会挑（上一版实测过 Biped 那类不接受），
       所以「一条抛错」不该把后面几十条一起带走。抛过的根数必须变成一行日志 —— 「藏网格」
       失败=骨和网格混在同一张图里，那是图不可判，不是截图段没跑。 */
    local failed = 0
    for o in MMD2MAX_SHOTMESH do ( try ( o.isHidden = flag ) catch ( failed += 1 ) )
    if failed > 0 do ( mmd2maxLog ("截图切网格可见性失败 " + (failed as string) + "/" + (MMD2MAX_SHOTMESH.count as string) + " 根 flag=" + (flag as string)) )
    completeRedraw()
    MMD2MAX_SHOTMESH.count
)

/* 这一张特写临时藏掉的骨，拍完原样还原（#72）。名单与 MMD2MAX_SHOTMESH 分开两份：
   网格那套是**整段**共用的（「藏网格」= bone_* 那一组各张同一个开关），骨这套是每张自己的
   —— 取景框不同，该藏的就不同，而还原必须按各自的名单来。 */
global MMD2MAX_SHOTCLIP = #()

fn mmd2maxBoxHit b lo hi =
(
    /* 一只 box3 与取景框有没有沾上：三根轴里任何一根整个错开就算没沾。
       六次比较自己写，不调 `intersects`：本文件对「没在 Max 2022 里实测过的 API」一律不用
       （mmd2maxZoomFit 那段记着 viewport.setViewport / setViewport 都是「按理该有」而实报
       Unknown property 的），而这条一旦抛就被外层 catch 吞掉 —— 吞掉的后果是「一张没裁」，
       图上看不出来，只有 clip_hidden 那条读数能看出来。 */
    if b.max.x < lo.x or b.min.x > hi.x do return false
    if b.max.y < lo.y or b.min.y > hi.y do return false
    if b.max.z < lo.z or b.min.z > hi.z do return false
    true
)

fn mmd2maxIsBoneNode o =
(
    /* 「这根节点是不是骨」= 两类类名，round41 的逐类计数定的：
       - 附属骨（衣服/头发/裙摆）是 BoneGeometry，268 根；
       - Biped 的骨盒是 Biped_Object，54 根（比 biped_map 的 53 行多 1 根没映射过的
         Biped 节点，它在场景里就会进画面，所以也该裁）。
       两条都是猜过来的：round39 只按 BoneGeometry 筛，foot_top 藏掉 268 根 = 附属骨总数，
       画面正中那一大坨还是骨盆盒；round40 补上 `classOf o.controller == Biped_Control`
       （本文件清探针残留那段用过的写法），分母**仍是 268** —— 那条一个都没认到。
       round41 报出 `认定为骨=322`，而 322 = 268 + 54 恰好是两类类名之和，这就是「控制器
       那条一个对象都没多认到」的算术证据，所以它删掉了。类名从哪来的：本文件自己的探针
       记录（biped_taper.ms 那段）写着「Biped 链接节点的 classof = Biped_Object」。
       漏掉 Biped 的后果不是「少藏几根」而是特写白拍：脚顶视那张画面正中还是骨盆盒。
       以后再有模型认不到，看 clip_bones_total 那句逐类计数，别再往这里加没证据的第三条。 */
    (classof o) == BoneGeometry or (classof o) == Biped_Object
)

fn mmd2maxBoneCensus =
(
    /* 场景里每一类节点各有几个 + 其中认定为骨的几个，拼成一串文本。
       为什么要有这一串：上面那个 predicate 连着两轮按猜的类名筛（round39 猜 BoneGeometry、
       round40 加控制器那条），每次都只留下一个光秃秃的数字 268 —— 而「268 根里附属骨和
       Biped 都认到了」和「268 = 附属骨总数、Biped 一根没认到」是同一个读数。
       有了 objects 总数与逐类计数，一轮就能看出：Biped 节点到底报什么类、它们在不在
       `objects` 这个集合里（不在的话就得换遍历方式，而不是继续加类名）。
       写进 clip_bones_total 那一行的读数列：它是 report 行，没有「该等于几」，装得下这段话，
       也就不用为逐类计数新开支格表行。 */
    local names = #()
    local counts = #()
    local total = 0
    local objN = objects.count
    for o in objects do
    (
        local nm = "?"
        try ( nm = (classOf o) as string ) catch ( nm = "?" )
        local idx = findItem names nm
        if idx == 0 do ( append names nm; append counts 1 )
        if idx > 0 do counts[idx] = counts[idx] + 1
        if (mmd2maxIsBoneNode o) do total += 1
    )
    local msg = "objects=" + (objN as string) + " 认定为骨=" + (total as string)
    for i = 1 to names.count do msg = msg + " " + names[i] + "=" + (counts[i] as string)
    #(total, msg)
)

fn mmd2maxClipBones lo hi =
(
    /* 把**整根都不在取景框里**的骨临时藏掉，返回藏掉几根（#72）。
       为什么必须裁：Max 的 zoom 只定视野、不裁深度。round38 的 bone_foot_top 就是证据 ——
       取景框只到 z=19.7，可画面正中那一大坨是头骨（z≈130），四周一圈是手指与辫子，
       两只鞋在画面下缘只剩几十像素；注释里那句「俯视那张已经判不了脚」就是这个形状。
       用户要的「顶视看外八」从来拍不到，不是视口坏了，是**框外的东西没藏掉**。
       判「在不在框里」用 pivot **或** bounds，两者都不沾才藏：
       - 只看 pivot 会把「关节在框外、骨身伸进框」的骨藏掉（大腿根在髋、骨身到膝）；
       - 只看 bounds 不可靠 —— mmd2maxSceneBox 的注释就写着 BoneGeometry 的 .bounds
         可能是零尺寸，那种 bounds 落在哪全看运气。
       只动骨（`mmd2maxIsBoneNode`：附属骨 + Biped 骨盒），**不动网格**：round41 的逐类计数
       给了场景的真实形状 —— 397 个对象里网格是 62 个 Editable_mesh（身体 1 个 + 61 个
       Morpher 目标，不是原先以为的「一个 Editable_Poly」），每一个的包围盒都盖住全身，
       按对象裁网格等于什么都没裁 —— 带网格那几张仍然只能靠取景框本身，这是既成事实，
       不是这一条没生效。
       也只动**当前可见**的那些：本来就藏着的骨不进名单，还原时才不会把用户自己藏的东西
       显示出来（同 mmd2maxRestoreShot 那条理由）。 */
    local hidden = 0
    MMD2MAX_SHOTCLIP = #()
    for o in objects do
    (
        if (mmd2maxIsBoneNode o) do
        (
            local hit = false
            local p = undefined
            try ( p = o.transform.translationpart ) catch ( p = undefined )
            if p != undefined do ( hit = mmd2maxBoxHit (box3 p p) lo hi )
            if not hit do
            (
                local b = undefined
                try ( b = o.bounds ) catch ( b = undefined )
                if b != undefined do ( hit = mmd2maxBoxHit b lo hi )
            )
            if (not hit) and ((try ( o.isHidden ) catch ( true )) == false) do
            (
                /* 写回读：藏没藏上只有 Max 自己知道，而「一张没裁」在图上看不出来，
                   只有 clip_hidden 这个数能看出来 —— 所以这个数必须是真藏成的根数。 */
                try ( o.isHidden = true ) catch () -- 允许失败：下一行立刻回读 o.isHidden，没藏上就不进名单，clip_hidden 不会把它数成藏了
                if (try ( o.isHidden ) catch ( false )) do
                (
                    append MMD2MAX_SHOTCLIP o
                    hidden += 1
                )
            )
        )
    )
    hidden
)

fn mmd2maxUnclipBones =
(
    /* 只还原这一张藏掉的那些（名单为空时什么都不做，所以整场景那五张是原样）。 */
    if MMD2MAX_SHOTCLIP.count == 0 do return 0
    local n = MMD2MAX_SHOTCLIP.count
    local failed = 0
    for o in MMD2MAX_SHOTCLIP do ( try ( o.isHidden = false ) catch ( failed += 1 ) )
    if failed > 0 do ( mmd2maxLog ("截图还原骨可见失败 " + (failed as string) + "/" + (n as string) + " 根：下一张会拍在「上一张的骨还藏着」这个现场上") )
    MMD2MAX_SHOTCLIP = #()
    completeRedraw()
    n
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
    try ( hide (getNodeByName "Grid") ) catch () -- 允许失败：场景里没有名为 Grid 的栅格时 hide 直接抛；画面上多一个栅格不影响任何读数，只影响好不好看
    try ( select #() ) catch () -- 允许失败：取消选择失败只让选中对象在图上带高亮色，落盘的 .max / 骨 / 权重都不看选中集
    MMD2MAX_SHOTMESH = mmd2maxShotMeshes()
    MMD2MAX_SHOTFILL = #()
    MMD2MAX_SHOTPIXEL = #()
    /* 场景里一共有几根「骨」—— 裁骨的分母，只报一次。
       为什么要有这一行：`clip_hidden` 单独看会骗人。round39 实测 foot_top 藏掉 268 根，
       而 268 恰好等于附属骨总数 —— 没有分母，「藏了 268 根」和「整副骨架 321 根里只藏了
       268 根（Biped 一根没动）」是同一个读数。有了分母，`321 - 268 = 53` 就是那 53 根
       Biped 骨盒还留在画面里，一眼能看出来漏在哪一类。
       round41 起这一行带的不再只是数字，而是 mmd2maxBoneCensus 那句逐类计数 —— 分母只能
       说「认少了」，逐类计数才说得出「少的是哪一类、它在不在 objects 里」。 */
    local census = mmd2maxBoneCensus()
    mmd2maxReport "shot_rb" "clip_bones_total" "-" census[2]
    mmd2maxLog ("截图裁骨分类：" + census[2])
    try ( viewport.SetShowEdgeFaces true )
    catch ( mmd2maxLog ("EdgeFaces 开关失败：" + (getCurrentException() as string)) )
    completeRedraw()
    MMD2MAX_SHOTMESH.count
)

fn mmd2maxRestoreShot =
(
    /* 只还原我们藏掉的那些：上一版对 objects 里每个对象都 isHidden = false，
       会把用户本来就藏着的对象一并显示出来。 */
    local failed = 0
    for o in MMD2MAX_SHOTMESH do ( try ( o.isHidden = false ) catch ( failed += 1 ) )
    if failed > 0 do ( mmd2maxLog ("截图还原网格可见失败 " + (failed as string) + "/" + (MMD2MAX_SHOTMESH.count as string) + " 根：用户接着看的场景里它们还是藏的") )
    MMD2MAX_SHOTMESH = #()
    /* 兜底再还原一次裁掉的骨（#72）：每张 ShotOne 自己会还，但那张包在外层 try 里 ——
       中途抛异常时名单还挂着，下一张就拍在「上一张的骨是藏的」这个现场上。
       名单空的时候这条什么都不做，所以它不是「再藏一遍的反面」，只是收尸。 */
    mmd2maxUnclipBones()
    try ( viewport.SetShowEdgeFaces false ) catch () -- 允许失败：只关用户视口的三角边叠层；下一段 PrepareShot 会自己设 true，落盘产物不读这个开关
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
    try ( ticks = ((dotNetClass "System.IO.File").GetLastWriteTime path).Ticks ) catch () -- 允许失败：取不到就是 -1，调用方拿 before/after 两个 -1 相等判「没落盘」，失败只会多报失败、不会报成成功
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

fn mmd2maxShotFill viewKind lo hi =
(
    /* 取景填充率：这只框的**纵向**跨度投影到屏幕上占视口高度的百分之几。
       #69 的验收就靠这个数：之前「特写」只能肉眼判，而肉眼判的是「图里有没有头」，
       不是「4.55cm 的骨有几个像素」。世界点用 gw.transPoint 投到屏幕（探针实测可用，
       返回屏幕坐标 [x, y, 深度]），纵向轴按视图取（正视/左视=Z，俯视=Y）。
       投不出来返回 -1.0：宁可报一个刺眼的负数，也不要 try/catch 吞成「没有这行」。 */
    local vAxis = 3
    if viewKind == #view_top do vAxis = 2
    local loA = #(lo.x, lo.y, lo.z)
    local hiA = #(hi.x, hi.y, hi.z)
    /* 取框的**纵向**那条棱的两个端点：另外两轴取中点，这样量到的是「这只框有多高」，
       不是对角线。用 #(x,y,z) 数组而不是直接改 Point3 的下标 —— Point3 的 p[1]= 写回
       在 Max 2022 里没验过，数组下标赋值是确定的。 */
    local midA = #((loA[1] + hiA[1]) / 2.0, (loA[2] + hiA[2]) / 2.0, (loA[3] + hiA[3]) / 2.0)
    midA[vAxis] = loA[vAxis]
    local pLo = [midA[1], midA[2], midA[3]]
    midA[vAxis] = hiA[vAxis]
    local pHi = [midA[1], midA[2], midA[3]]
    local sLo = undefined
    local sHi = undefined
    try ( sLo = gw.transPoint pLo; sHi = gw.transPoint pHi )
    catch ( mmd2maxLog ("取景投影失败：" + (getCurrentException() as string)) )
    if sLo == undefined or sHi == undefined do return -1.0
    local dPx = distance [sLo.x, sLo.y, 0.0] [sHi.x, sHi.y, 0.0]
    local winY = 0.0
    try ( winY = gw.getWinSizeY() as float ) catch () -- 允许失败：winY 留在 0.0 ⇒ 下面那条 `winY <= 0.0` 把这一张的填充率判成 -1.0（刺眼的负数），不会读成一个正常的百分比
    if winY <= 0.0 do return -1.0
    100.0 * dPx / winY
)

fn mmd2maxHAxis viewKind =
(
    /* 这张视图里「屏幕上的水平方向」对应世界哪一根轴：正视=X、左视=Y、俯视=X。
       mmd2maxAspectBox 收的就是这一根，mmd2maxShotOneTight 报回判据表的也必须是同一根 ——
       两处各写一遍迟早分叉，所以只留这一处。Python 侧 mmd2max/verdicts.SHOT_H_AXIS
       按同样的规则给每张 tight 图定轴（契约测试对撞这两份表）。 */
    local a = 1
    if viewKind == #view_left do a = 2
    a
)

fn mmd2maxComp p i =
(
    /* 按 mmd2maxHAxis 那个编号取 point3 的分量（1=x、2=y、3=z）。 */
    if i == 1 do return p.x
    if i == 2 do return p.y
    p.z
)

fn mmd2maxNumsGiven vals =
(
    /* CFG 有没有把这一组数都给出来：全都在才算给。缺一都当「没给」—— 让取景与摘要走同一条
       退回路径，比「用半只框」好：半只框（比如 z 是量的、y 是场景的）正是第 36 轮的形状，
       而那种框看起来是量出来的，日志里没人能看出来它缺一轴。
       写成遍历一个数组而不是六个 `and` 连成的长表达式：MAXScript 的换行是语句分隔符，
       跨行布尔表达式要单独包括号才不误判（本文件里那句「不用行首 + 的跨行表达式」同理）。
       胸与颈共用这一条判据（#71）：两组框的键形状一模一样，各写一遍就会各坏一遍。 */
    local given = true
    for v in vals do if v == undefined do given = false
    given
)

fn mmd2maxBoxSummary label vals =
(
    /* 一只特写的框进配置摘要长什么样：「 胸框=x-19.3..19.3 y-26.2..7.2 z96.6..114.4」，
       或者「 颈框=无(退回场景包围盒带)」。
       `无` 这一支必须写得出来：事后只看日志要能分辨「用的是量的框」和「用的是猜的带」，
       而第 35、36 轮当时用的框都没框住骨，日志却是绿的。三个轴都写：只写 z 就看不出 y
       有没有给全，而「缺一轴的半只框」正是第 36 轮的形状。 */
    if not (mmd2maxNumsGiven vals) do return " " + label + "=无(退回场景包围盒带)"
    " " + label + "=x" + (vals[1] as string) + ".." + (vals[4] as string) \
        + " y" + (vals[2] as string) + ".." + (vals[5] as string) \
        + " z" + (vals[3] as string) + ".." + (vals[6] as string)
)

fn mmd2maxAspectBox viewKind lo hi =
(
    /* 把框的**水平**跨度按视口宽高比收紧到刚好装得下纵向跨度，纵向不动。
       为什么：zoomToBounds 是拿框里最大的那一维去贴视口的（探针实测：香菱的胸带框
       119cm 宽 × 19.7cm 高，投出来横向占 63%、纵向只剩 18%），所以只要水平被张开
       的手臂撑宽，纵向放大率就被宽度绑架 —— 胸部骨 4.55cm 在图上只有 20px，
       「骨指着乳头」这件事在像素上根本不存在（#66 只能靠回读数字，用户没法用眼睛验收）。
       只由 mmd2maxShotOneTight 调用（颈部/胸部这种「按高度比例定义」的带），
       按节点算出来的框（脚/手）不做：那种框的水平跨度就是内容本身，收紧会裁掉内容。
       水平轴随视图变（正视=X、左视=Y、俯视=X），归 mmd2maxHAxis 一处说；纵向对应 Z/Z/Y。 */
    local hAxis = mmd2maxHAxis viewKind
    local vAxis = 3
    if viewKind == #view_top do vAxis = 2
    local winX = 0.0
    local winY = 0.0
    try ( winX = gw.getWinSizeX() as float; winY = gw.getWinSizeY() as float )
    catch ( mmd2maxLog ("视口尺寸取不到：" + (getCurrentException() as string)) )
    local loA = #(lo.x, lo.y, lo.z)
    local hiA = #(hi.x, hi.y, hi.z)
    if winX <= 0.0 or winY <= 0.0 do return #(lo, hi)
    local vSpan = hiA[vAxis] - loA[vAxis]
    local hSpan = hiA[hAxis] - loA[hAxis]
    if vSpan <= 0.0 or hSpan <= 0.0 do return #(lo, hi)
    local want = vSpan * (winX / winY)
    if want >= hSpan do return #(lo, hi)
    local mid = (loA[hAxis] + hiA[hAxis]) / 2.0
    local newLo = #(lo.x, lo.y, lo.z)
    local newHi = #(hi.x, hi.y, hi.z)
    newLo[hAxis] = mid - want / 2.0
    newHi[hAxis] = mid + want / 2.0
    #([newLo[1], newLo[2], newLo[3]], [newHi[1], newHi[2], newHi[3]])
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
    try ( viewport.zoom 1.35 )
    catch ( mmd2maxLog ("取景余量拉伸失败：" + (getCurrentException() as string)) )
    completeRedraw()
    fitted
)

/* ---- #115 截图内容回读：读数来自盘上那张 PNG 的像素 ----
   API 全部是探针实测（Temp/mmd2max_probe/shot_probe*.txt），不是照印象写的：
   · `getPixel`、`getPixelBuffer`、`createBitMap`、`freeBitmap`、`colorValue` 在
     Max 2022 里**根本不存在**（探针原文「-- Type error: Call needs function or class,
     got: undefined」）；能用的取像素是 `getPixels <BitMap> <point2> <count>`，
     返回 Array of Color，越界/负坐标/count=0/跨过行尾一律返回空 `#()` **不抛**。
   · `openBitMap <path>` 只认位置参数（写成 `filename:` 是「Argument count error:
     openBitMap wanted 1, got 0」），读不了（不存在、0 字节、不是图）返回 **undefined**，
     也不抛。所以「回读失败」不能靠 try/catch 发现，必须自己编成一个往错方向判红的读数。
   · 释放用 `close <BitMap>`（探针：OK）。 */
fn mmd2maxShotPixelCoord span idx grid =
(
    /* 网格第 idx 个采样点落在 [0, span-1] 的哪个像素上。走 float 再 floor：
       MAXScript 的 `/` 对整数不一定给整数，而 `as integer` 的取舍方向探针没量过，
       两个都不许信 —— 夹回区间是最后那道保险（越界不抛，只是悄悄少采一个点）。 */
    local v = ((span - 1) * (idx as float) / ((grid - 1) as float))
    local i = (floor v) as integer
    if i < 0 do i = 0
    if i >= span do i = span - 1
    i
)

fn mmd2maxPixelAt bmp x y =
(
    /* 语句形式 + 显式失败值（契约 rule 10）：`local a = try (...) catch (...)`
       在 Max 2022 里是编译期打死整轮装配的写法，探针里那样用没事是因为探针那份
       不跑正式脚本 —— 正式脚本一个字都不许照抄探针的这个形状。 */
    local a = undefined
    try ( a = getPixels bmp [x, y] 1 ) catch ( a = undefined )
    if a == undefined do return undefined
    if (classof a) != Array do return a
    if a.count < 1 do return undefined
    a[1]
)

fn mmd2maxSampleShot shotFile =
(
    /* 返回 #(采到, 去重, 最大同类)；任何一步读不到都返回 #(-1, -1, -1)。
       为什么刺眼的 -1 而不是 0：distinct_colors 那道闸是 `min`（越大越好），
       dominant_pct 是 `max`（越小越好）—— 0 在后者上是满分，等于「读不到」被读成
       「这张图干净」。-1 两个方向都判红，而且和真采到的数（576 里的 1..576）不会混。
       网格边长单开一行：Python 侧的 verdicts.SHOT_PIXEL_GRID 是同一个数，
       契约测试拿这一行对撞（改这边不改那边 = 采样点数与期望值分叉）。 */
    local grid = 24
    local bad = #(-1, -1, -1)
    local bmp = undefined
    try ( bmp = openBitMap shotFile ) catch ( bmp = undefined )
    if bmp == undefined do return bad
    local w = 0
    local h = 0
    try ( w = bmp.width as integer; h = bmp.height as integer )
    catch ( mmd2maxLog ("位图尺寸读不出：" + shotFile) )
    if (w < grid) or (h < grid) do
    (
        try ( close bmp ) catch ( mmd2maxLog ("位图关闭失败：" + shotFile) )
        return bad
    )
    local got = 0
    local uniq = #()
    local cnt = #()
    local dom = 0
    for iy = 0 to (grid - 1) do
    (
        local y = mmd2maxShotPixelCoord h iy grid
        for ix = 0 to (grid - 1) do
        (
            local x = mmd2maxShotPixelCoord w ix grid
            local col = mmd2maxPixelAt bmp x y
            if col != undefined do
            (
                local key = col as string
                local k = findItem uniq key
                /* 新 key 的初值是 0，不是 1：探针里 append 时写成 1 再去加，
                   纯黑那张的最大同类量成 577（其实只有 576 个点），差一个。 */
                if k == 0 do ( append uniq key; append cnt 0; k = uniq.count )
                got += 1
                cnt[k] = cnt[k] + 1
                if cnt[k] > dom do dom = cnt[k]
            )
        )
    )
    try ( close bmp ) catch ( mmd2maxLog ("位图关闭失败：" + shotFile) )
    if got < 1 do return bad
    #(got, uniq.count, dom)
)

fn mmd2maxReportShotPixels tag s =
(
    /* 每个调用点都必须写下这两行，一条都不许省：两张表的 `when` 是 if_present，
       「没有这一行」在判闸眼里等于合法缺席 —— 截图段整个没跑也能全绿。
       失败的那一路（取不到图、落盘没改、回读打不开）由调用点交进来了 #(-1,-1,-1)，
       所以这里不需要知道失败长什么样，只管报数。 */
    local got = s[1]
    local distinct = s[2]
    local dom = s[3]
    local domPct = 100.0
    if got > 0 do domPct = 100.0 * (dom as float) / (got as float)
    mmd2maxReport "shot_rb" "distinct_colors" tag distinct
    mmd2maxReport "shot_rb" "dominant_pct" tag domPct
    append MMD2MAX_SHOTPIXEL s
    mmd2maxLog ("截图内容 " + tag + " 采到=" + (got as string) \
        + " 去重=" + (distinct as string) \
        + " 最大同类=" + (dom as string) \
        + " 占比=" + (domPct as string) + "%")
    s
)

fn mmd2maxShotOne outDir baseName tag viewKind lo hi =
(
    local shotFile = outDir + "\\" + baseName + "_" + tag + ".png"
    mmd2maxLog ("截图开始 " + tag + " -> " + shotFile)
    /* 这里的 lo/hi 就是「这一张要取景的那只框」：要收紧的带由 mmd2maxShotOneTight
       在**调进来之前**收好，所以取景与填充率天然量同一只框，中间没有任何改框的机会。
       裁骨也吃同一只框（#72）：收完之后的水平跨度才是画面上真正的边界，按收之前的框
       裁会留下「被收掉的那半边」里的骨 —— 它们根本不在画面上，却照样占着名单。 */
    local clipN = mmd2maxClipBones lo hi
    mmd2maxReport "shot_rb" "clip_hidden" tag clipN
    mmd2maxFitView viewKind lo hi
    /* 填充率排在取图之前：取景是这一段自己的事，DIB 取不到也得留下这一行 ——
       否则「视口坏了」会伪装成「这段没跑」（when=if_present 就把它跳过了）。 */
    local fill = mmd2maxShotFill viewKind lo hi
    mmd2maxReport "shot_rb" "fill_pct" tag fill
    append MMD2MAX_SHOTFILL fill
    mmd2maxLog ("截图 " + tag + " 取景填充=" + (fill as string) + "% 框=" \
        + ((lo as string)) + ".." + ((hi as string)))
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
        try ( bmp = gw.getViewportDib() ) catch () -- 允许失败：再拿不到时 bmp 仍是 undefined，下面那条 `if bmp == undefined` 已经把「视口取不到图」打成一行失败读数；第一次尝试的异常文字在上面那个 catch 里已经记过
    )
    /* 还原排在**取图之后、落盘之前**：DIB 已经在手里，藏没藏回都不影响这张图，
       而这一段两条出口（取图为空 / 正常）都必须走过还原 —— 排在分支之前只写一次，
       排在分支之后就要写两遍，漏一遍下一张就带着上一张的隐藏名单。 */
    mmd2maxUnclipBones()
    if bmp == undefined then
    (
        mmd2maxLog ("截图取图为空（视口不可用）：" + shotFile)
        mmd2maxReportShotPixels tag #(-1, -1, -1)
        false
    )
    else
    (
        /* 调用一律用「空格分隔」形式：MAXScript 根本没有 f(a, b) 这种逗号实参表，
           写成那样是**编译期**语法错误（实测：-- Syntax error: at ,, expected <factor>），
           整份脚本一行都不跑。单参数的 f(x) 合法，因为那对括号只是分组。 */
        local okShot = mmd2maxSaveDib bmp shotFile
        mmd2maxLog ("截图 " + tag + " 落盘=" + (okShot as string) + " -> " + shotFile)
        /* 回读只在 okShot 为真时做：mmd2maxSaveDib 的 okShot 是「这个文件的时间戳被
           本轮改过」（#21 就是这么治上一轮同名旧图的），假 = 盘上那张还是上一轮的，
           拿它算内容就是把上一轮的图盖章在这一轮的读数上。失败照样交 #(-1,-1,-1)，
           两行读数一条都不能少 —— when=if_present 只认「有没有这一行」。 */
        if okShot then ( mmd2maxReportShotPixels tag (mmd2maxSampleShot shotFile) )
        else ( mmd2maxReportShotPixels tag #(-1, -1, -1) )
        okShot
    )
)

fn mmd2maxShotOneTight outDir baseName tag viewKind lo hi =
(
    /* 和 mmd2maxShotOne 唯一的差别：先把框的**水平**跨度按视口宽高比收紧，再交给他拍。
       收完之后框的长宽比 = 视口的长宽比，zoomToBounds 无论贴哪一维，纵向放大率都一样
       ⇒ 带高不再被手臂宽度绑架（为什么必须这样，见 mmd2maxAspectBox 那段）。
       为什么不写成 ShotOne 的可选参数（`fn f a b /tight:false =`）：Max 2022 的
       MAXScript 解析器**不认这个写法**，第 34 轮实跑在编译期就报
       「-- Syntax error: at /, expected =」，整份脚本一行都不跑 —— 日志、error.txt、
       报告全是空的，只剩一个读不到文字的 Auto-load 弹窗。单开一个函数除了绕开这条，
       还把「哪几张紧」变成调用点上的名字：Python 侧的 verdicts.SHOT_TIGHT_TAGS
       就是照着这个名字对账的。 */
    local tightBox = mmd2maxAspectBox viewKind lo hi
    local fitLo = tightBox[1]
    local fitHi = tightBox[2]
    mmd2maxLog ("截图 " + tag + " 紧框：" + ((lo as string)) + ".." + ((hi as string)) \
        + " -> " + ((fitLo as string)) + ".." + ((fitHi as string)))
    /* 收完之后**真正拿去 zoom 的那条水平跨度**两头进判据表（#70 的另一半）：
       取景框那六行读的是收之前的框，而收是朝框中心砍的 —— 第 36 轮胸框的中心被身后的
       裙摆拽到 y=+9.8，砍完只剩 y -0.4..20.0，骨在 -11.75..-7.24，收之前的框明明罩得住、
       图里却没有骨。报收之后的这两头，「画面上有没有」才第一次有读数。
       排在 mmd2maxShotOne 之前：抓图那一段抛了也得留下这两行，否则「视口坏了」和
       「取景没跑」在报告里长得一样。 */
    local hAxis = mmd2maxHAxis viewKind
    mmd2maxReport "shot_rb" "tight_h_lo" tag (mmd2maxComp fitLo hAxis)
    mmd2maxReport "shot_rb" "tight_h_hi" tag (mmd2maxComp fitHi hAxis)
    mmd2maxShotOne outDir baseName tag viewKind fitLo fitHi
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
    try ( makeDir outDir ) catch () -- 允许失败：目录在不在由下面 doesFileExist (outDir + "\\") 实测，绝不靠「makeDir 没报错」推断；目录已存在时 makeDir 也会抛
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
        /* 特写区域默认按包围盒高度取比例，换角色不用改：整场景一张图判不了细节 ——
           颈部落位（#17）与躯干倾角（#27）要看 0.55~0.98 这一段（从腰往上全覆盖，
           0.72 起框会把 Pelvis/Spine1 裁掉，用户说「Spine2 没后仰」时画面里没有它），
           Toe0 尺寸（#19）要看脚下 0~0.14 这一段。
           侧视是硬性要求：挺胸、脚尖前后向、躯干后仰都只有侧面看得出来。
           下面这对只是**兜底**：CFG 给了颈部框（#71）就换成量出来的框，见 neck 取景那一段。 */
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
        /* 胸部特写（#66）：用户那句「胸部骨骼应该指向乳头」是一句**看图**的要求，
           而 4.55cm 的骨在整场景那张 984px 的画面里只有二十几个像素，判不了。
           框从哪里来：**导出侧量**（MMD2MAX_CHEST_{X,Y,Z}_{LO,HI}，见 export_obj._chest_band）。
           这里原来写的是两件事，各坏过一轮：
             · z 用「场景高度 × 0.58~0.72」。那两个比例是对着 PMX 标称身高 160 定的，而 h 是
               场景实测高度 140.513 —— 分母一换整只带低了 4cm，第 35 轮的 chest_front /
               bone_chest_front 里一根胸骨都没有（骨实测 z 105.23~105.73，带上沿只到 101.17）。
             · x/y 用「场景包围盒 ±10cm」。第 36 轮 z 修好了，这两头还是凑的：框的前后中心
               被身后的裙摆与长发拽到 y=+9.8，而 #69 的收框是**朝框中心**砍水平跨度的，
               砍完只剩 y -0.4..20.0 —— 骨在 -11.75..-7.24，侧视那张里subject 整个在框外。
           而两轮 shot_rb 的 fill_pct 都是 63%、全绿：填充率量的是框自己，框里没骨也照样满。
           「换模型不许改数」现在交给导出侧的比例（余量 = 网格实测高 × 0.06/0.10），
           不再交给这里的字面量。
           认不出胸骨的模型（六个数没给全）退回旧的场景包围盒 + 高度比例带 —— 至少有图可看，
           而那一轮的规格表里胸部那 18 行退成 report，配置摘要与下面这句日志都写明「没给框」。
           这一帧**不许**按骨名去找那两根骨：PMX 的胸骨名从「右胸」到 bustR 都有，名字规则
           只住在 Python 一处（export_obj._is_breast_bone），两处各认一遍必然分叉。
           收水平跨度那一步照旧交给 mmd2maxShotOneTight（#69），它现在也报收完之后的读数。 */
        local chestLo = [b.min.x - 10.0, b.min.y - 10.0, b.min.z + h * 0.58]
        local chestHi = [b.max.x + 10.0, b.max.y + 10.0, b.min.z + h * 0.72]
        local chestVals = #(MMD2MAX_CHEST_X_LO, MMD2MAX_CHEST_Y_LO, MMD2MAX_CHEST_Z_LO,
                            MMD2MAX_CHEST_X_HI, MMD2MAX_CHEST_Y_HI, MMD2MAX_CHEST_Z_HI)
        if (mmd2maxNumsGiven chestVals) then (
            chestLo = [chestVals[1], chestVals[2], chestVals[3]]
            chestHi = [chestVals[4], chestVals[5], chestVals[6]]
        ) else (
            mmd2maxLog "chest 取景：CFG 没给全框（这具模型量不出胸骨），退回场景包围盒 + 高度比例带"
        )
        mmd2maxLog ("chest 取景：框=" + ((chestLo as string)) + ".." + ((chestHi as string)))
        /* 报的是**这两个角点**：判据要问的是「这一轮真拿来 zoom 的那只框罩得住胸骨吗」，
           三根轴都要 —— 只报 z 就是第 36 轮那个形状，那一轮的 y 把骨裁在框外而没人看得见。
           收框之后还有 tight_h_* 两行（mmd2maxShotOneTight 报），那才是画面上真正的边界。 */
        mmd2maxReport "shot_rb" "chest_box_lo" "x" chestLo.x
        mmd2maxReport "shot_rb" "chest_box_hi" "x" chestHi.x
        mmd2maxReport "shot_rb" "chest_box_lo" "y" chestLo.y
        mmd2maxReport "shot_rb" "chest_box_hi" "y" chestHi.y
        mmd2maxReport "shot_rb" "chest_box_lo" "z" chestLo.z
        mmd2maxReport "shot_rb" "chest_box_hi" "z" chestHi.z
        /* 颈部特写（#71）：上面那对 neckLo/neckHi 是「场景高度 × 0.55~0.98」猜出来的带，
           坏的是和胸部第 35 轮同一件事 —— 分母是整场景，量出来的却是颈链那三节骨。
           现在框由导出侧按 `Bip001 Spine2 / Neck / Head` 三节的端点极值算，余量用与胸部
           **同一对比例**（MMD2MAX_NECK_{X,Y,Z}_{LO,HI}，见 export_obj._neck_band）。
           香菱实测：猜的带 z 77.3..137.7（60.4cm 高），量的框 z 92.1..137.0（44.8cm）——
           上沿几乎没动（不新增裁头），下沿抬到锁骨以上，颈链整条在框内。
           认不出颈链（这具模型没有映射到那三节的骨）时六个数没给全 → 保持猜的带：至少有图
           可看，而那一轮的颈部 12 行退成 report，配置摘要与下面这句日志都写明「没给框」。
           这一帧**不许**按骨名找颈链：PMX 的颈部名从「首」到 neck 都有，而「左手首 / 右足首」
           同样含「首」字 —— 名字规则只住 Python 一处（export_obj._neck_band 按 **Biped 目标**
           认），两处各认一遍必然分叉。 */
        local neckVals = #(MMD2MAX_NECK_X_LO, MMD2MAX_NECK_Y_LO, MMD2MAX_NECK_Z_LO,
                           MMD2MAX_NECK_X_HI, MMD2MAX_NECK_Y_HI, MMD2MAX_NECK_Z_HI)
        if (mmd2maxNumsGiven neckVals) then (
            neckLo = [neckVals[1], neckVals[2], neckVals[3]]
            neckHi = [neckVals[4], neckVals[5], neckVals[6]]
        ) else (
            mmd2maxLog "neck 取景：CFG 没给全框（这具模型量不出颈链骨），退回场景包围盒 + 高度比例带"
        )
        mmd2maxLog ("neck 取景：框=" + ((neckLo as string)) + ".." + ((neckHi as string)))
        mmd2maxReport "shot_rb" "neck_box_lo" "x" neckLo.x
        mmd2maxReport "shot_rb" "neck_box_hi" "x" neckHi.x
        mmd2maxReport "shot_rb" "neck_box_lo" "y" neckLo.y
        mmd2maxReport "shot_rb" "neck_box_hi" "y" neckHi.y
        mmd2maxReport "shot_rb" "neck_box_lo" "z" neckLo.z
        mmd2maxReport "shot_rb" "neck_box_hi" "z" neckHi.z
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
        /* #66：胸部骨的指向只有在「只剩骨架」的近景里才读得清 —— 网格把 4.55cm 的
           骨整个盖住，带网格那张顶多看出胸前有两个鼓包。 */
        try ( mmd2maxShotOneTight outDir baseName "bone_chest_front" #view_front chestLo chestHi ) catch ( mmd2maxLog ("bone_chest_front 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOneTight outDir baseName "bone_chest_side" #view_left chestLo chestHi ) catch ( mmd2maxLog ("bone_chest_side 截图异常：" + (getCurrentException() as string)) )
        /* #73：「指向乳头」这句话里有两根轴 —— 向外（x）与前（−y），而正视图把 y 压成一条线、
           侧视图把 x 压成一条线，两张各缺一半（第 33 轮那张侧视只能证「前」，胸骨在 x 上张开
           成 80 度这件事在图上不存在）。顶视是唯一两根轴同时可见的一张；它原先拍不了不是因为
           视口坏，而是框外的东西没藏掉 —— #72 之后才轮得到它。 */
        try ( mmd2maxShotOneTight outDir baseName "bone_chest_top" #view_top chestLo chestHi ) catch ( mmd2maxLog ("bone_chest_top 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxSetMeshHidden false ) catch ( mmd2maxLog ("显网格异常：" + (getCurrentException() as string)) )
        /* #66 的带网格版：乳头点在网格里，骨尖落到点上才算「指向乳头」。
           与上面 bone_chest_* 配对看 —— 只有一张时「骨在胸前」和「骨指着乳头」分不出来。 */
        try ( mmd2maxShotOneTight outDir baseName "chest_front" #view_front chestLo chestHi ) catch ( mmd2maxLog ("chest_front 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOneTight outDir baseName "chest_side" #view_left chestLo chestHi ) catch ( mmd2maxLog ("chest_side 截图异常：" + (getCurrentException() as string)) )
        /* #74：bone_chest_top（#73）只证明了「那两根骨在 x 与 y 上同时张开」，张开的**终点**
           是不是乳头点只有带网格的顶视能判 —— 俯视是唯一同时看得见「向左/右偏」与
           「向前/后偏」两根分量的一张，而乳头在网格里恰好是这框里最靠前的两点。 */
        try ( mmd2maxShotOneTight outDir baseName "chest_top" #view_top chestLo chestHi ) catch ( mmd2maxLog ("chest_top 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOneTight outDir baseName "neck_side" #view_left neckLo neckHi ) catch ( mmd2maxLog ("neck_side 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOneTight outDir baseName "neck_front" #view_front neckLo neckHi ) catch ( mmd2maxLog ("neck_front 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "foot_side" #view_left footLo footHi ) catch ( mmd2maxLog ("foot_side 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "foot_front" #view_front footLo footHi ) catch ( mmd2maxLog ("foot_front 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "foot_top" #view_top topLo topHi ) catch ( mmd2maxLog ("foot_top 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "hand_front" #view_front handLo handHi ) catch ( mmd2maxLog ("hand_front 截图异常：" + (getCurrentException() as string)) )
        try ( mmd2maxShotOne outDir baseName "hand_side" #view_left handLo handHi ) catch ( mmd2maxLog ("hand_side 截图异常：" + (getCurrentException() as string)) )
        mmd2maxRestoreShot()
        /* 两条聚合读数（期望值由 Python 侧的 mmd2max.verdicts.SHOT_TAGS 定，这里只数自己
           量到几个）：`tags` 少于名单 = 有某张图连取景都没跑到（那张图不存在，而报告里
           只是少一行，看不出来）；`projection_fail` 是 gw.transPoint 没投出来的张数，
           投不出来时 fill_pct 报的是 -1.0（刺眼，但不如这句人话好读）。 */
        local fillFail = 0
        for v in MMD2MAX_SHOTFILL do if v < 0.0 do fillFail += 1
        mmd2maxReport "shot_rb" "tags" "-" MMD2MAX_SHOTFILL.count
        mmd2maxReport "shot_rb" "projection_fail" "-" fillFail
        /* 回读过内容的张数（#115）。这一行是「采样这段代码到底跑没跑」的闸：
           那 42 行逐张读数的 `when` 是 if_present，采样函数一次都没被调到 = 一个像素
           都没看过 + 42 行合法缺席 + 这一行数出 0。放在聚合这一段而不是每张各一条，
           是因为 ShotOne 各自包在 try 里，坏在第一张就把循环带走时，剩下的行本来就
           不会有 —— 只有聚合计数看得见「少了几张」。 */
        mmd2maxReport "shot_rb" "pixel_sampled" "-" MMD2MAX_SHOTPIXEL.count
        local minFill = -1.0
        if MMD2MAX_SHOTFILL.count > 0 do minFill = amin MMD2MAX_SHOTFILL
        local minDistinct = -1
        for s in MMD2MAX_SHOTPIXEL do
        (
            if s[2] >= 0 do ( if minDistinct < 0 or s[2] < minDistinct do minDistinct = s[2] )
        )
        mmd2maxLog ("截图取景：量到=" + (MMD2MAX_SHOTFILL.count as string) \
            + " 投不出=" + (fillFail as string) \
            + " 最小纵向填充=" + (minFill as string) + "%")
        mmd2maxLog ("截图内容：回读=" + (MMD2MAX_SHOTPIXEL.count as string) \
            + " 最小去重色数=" + (minDistinct as string))
        /* ran 行排在还原之后：截图这一段包含「改视口显示 → 抓图 → 还原」，
           还原没做完就不算收工。它也是整份报告的最后一段，所以 shot_rb 那些填充率
           天然落在这一段里（注册表里 shot_capture 是最后一个 pass）。 */
        mmd2maxReport "pass" "ran" "shot_capture" 1
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

fn mmd2maxCfgNumOpt key low high =
(
    /* 允许「这一轮没有这个数」的配置：空串 → undefined，由调用方决定退回什么。
       非空时走 mmd2maxCfgNum 那一套校验（非数字、越界照抛）—— 「CFG 里写了个读不出的数」
       和「没写」是两件事，前者必须中止，否则又是一个「日志绿、几何另一套」的默认值。
       用它的例子：MMD2MAX_CHEST_{X,Y,Z}_{LO,HI}（模型里没有叫「胸/bust」的骨时导出量不出框）。 */
    local s = mmd2maxCfgStr key
    if s == "" do return undefined
    mmd2maxCfgNum key low high
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
    /* 特写的取景框（#70 胸、#71 颈）：三根轴各一对，六个数一组。范围放到 ±100000：它们是
       **绝对坐标**，单位跟着 scale 走（导出单位 = 厘米 × scale），这一层只能验「像不像一个数」；
       「框到底罩不罩得住那几根骨」由 MMD2MAX.VERDICTS 用导出侧量的骨极值判，
       不写在这里（规则 II：Max 只报数）。 */
    MMD2MAX_CHEST_X_LO = mmd2maxCfgNumOpt "MMD2MAX_CHEST_X_LO" -100000.0 100000.0
    MMD2MAX_CHEST_X_HI = mmd2maxCfgNumOpt "MMD2MAX_CHEST_X_HI" -100000.0 100000.0
    MMD2MAX_CHEST_Y_LO = mmd2maxCfgNumOpt "MMD2MAX_CHEST_Y_LO" -100000.0 100000.0
    MMD2MAX_CHEST_Y_HI = mmd2maxCfgNumOpt "MMD2MAX_CHEST_Y_HI" -100000.0 100000.0
    MMD2MAX_CHEST_Z_LO = mmd2maxCfgNumOpt "MMD2MAX_CHEST_Z_LO" -100000.0 100000.0
    MMD2MAX_CHEST_Z_HI = mmd2maxCfgNumOpt "MMD2MAX_CHEST_Z_HI" -100000.0 100000.0
    MMD2MAX_NECK_X_LO = mmd2maxCfgNumOpt "MMD2MAX_NECK_X_LO" -100000.0 100000.0
    MMD2MAX_NECK_X_HI = mmd2maxCfgNumOpt "MMD2MAX_NECK_X_HI" -100000.0 100000.0
    MMD2MAX_NECK_Y_LO = mmd2maxCfgNumOpt "MMD2MAX_NECK_Y_LO" -100000.0 100000.0
    MMD2MAX_NECK_Y_HI = mmd2maxCfgNumOpt "MMD2MAX_NECK_Y_HI" -100000.0 100000.0
    MMD2MAX_NECK_Z_LO = mmd2maxCfgNumOpt "MMD2MAX_NECK_Z_LO" -100000.0 100000.0
    MMD2MAX_NECK_Z_HI = mmd2maxCfgNumOpt "MMD2MAX_NECK_Z_HI" -100000.0 100000.0
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
    MMD2MAX_TOE_GROUND = mmd2maxCfgBool "MMD2MAX_TOE_GROUND"
    MMD2MAX_ROLLBACK = mmd2maxCfgBool "MMD2MAX_ROLLBACK"
    MMD2MAX_RBTRIG = mmd2maxCfgNum "MMD2MAX_RBTRIG" 0.0 50.0
    MMD2MAX_STUBCM = mmd2maxCfgNum "MMD2MAX_STUBCM" 0.0 50.0
    MMD2MAX_SKIN = mmd2maxCfgBool "MMD2MAX_SKIN"
    MMD2MAX_SHOT = mmd2maxCfgBool "MMD2MAX_SHOT"
    MMD2MAX_INTERACTIVE = mmd2maxCfgBool "MMD2MAX_INTERACTIVE"
    MMD2MAX_KEEPOPEN = mmd2maxCfgBool "MMD2MAX_KEEPOPEN"
    /* 分步拼接，不用行首 `+` 的跨行表达式：MAXScript 会把这种续行当成新语句。 */
    local cfgMsg = "配置来自 " + (path as string)
    cfgMsg = cfgMsg + "（" + ((mmd2maxRows MMD2MAX_CFG_TBL).count as string) + " 项）"
    cfgMsg = cfgMsg + " 顶点 " + (MMD2MAX_VERTS as string)
    cfgMsg = cfgMsg + " 脊柱 " + (MMD2MAX_SPINE_LINKS as string)
    cfgMsg = cfgMsg + " 手指 " + (MMD2MAX_FINGERS as string) + "x" + (MMD2MAX_FINGER_LINKS as string)
    cfgMsg = cfgMsg + " 脚盒压平轴=" + (MMD2MAX_FOOTBOXFLAT as string)
    /* 两只特写的框都进摘要（#70 胸、#71 颈），文案由 mmd2maxBoxSummary 一处定：
       「无」和「量的框」必须是两种看得出来的形状，见那条函数。 */
    cfgMsg = cfgMsg + (mmd2maxBoxSummary "胸框" \
        #(MMD2MAX_CHEST_X_LO, MMD2MAX_CHEST_Y_LO, MMD2MAX_CHEST_Z_LO,
          MMD2MAX_CHEST_X_HI, MMD2MAX_CHEST_Y_HI, MMD2MAX_CHEST_Z_HI))
    cfgMsg = cfgMsg + (mmd2maxBoxSummary "颈框" \
        #(MMD2MAX_NECK_X_LO, MMD2MAX_NECK_Y_LO, MMD2MAX_NECK_Z_LO,
          MMD2MAX_NECK_X_HI, MMD2MAX_NECK_Y_HI, MMD2MAX_NECK_Z_HI))
    cfgMsg = cfgMsg + " 蒙皮=" + (MMD2MAX_SKIN as string)
    cfgMsg = cfgMsg + " 方向容差=" + (MMD2MAX_DIRTOL as string)
    cfgMsg = cfgMsg + " 扭转容差=" + (MMD2MAX_ROLLTOL as string)
    cfgMsg = cfgMsg + " 趾落位容差=" + (MMD2MAX_TOETOL as string)
    cfgMsg = cfgMsg + " 盒隙容差=" + (MMD2MAX_GAPTOL as string)
    cfgMsg = cfgMsg + " 脚链拉伸=" + (MMD2MAX_FOOTREACH as string)
    cfgMsg = cfgMsg + " 趾盒落地=" + (MMD2MAX_TOE_GROUND as string)
    cfgMsg = cfgMsg + " 快照回滚=" + (MMD2MAX_ROLLBACK as string)
    cfgMsg = cfgMsg + " 回滚触发=" + (MMD2MAX_RBTRIG as string)
    cfgMsg = cfgMsg + " 短骨门限=" + (MMD2MAX_STUBCM as string)
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
            catch ( mmd2maxLog ("贴图探查失败：" + (getCurrentException() as string)) )
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
        try ( if (classOf obj.controller) == Biped_Control do continue )
        catch ( mmd2maxLog ("Biped 控制器探测失败 " + (classOf obj as string) + "：" + (getCurrentException() as string)) )
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
    /* 回落 [1,1,1] 和「真 scale 就是 [1,1,1]」在数上长得一样，而这一份 scale 直接乘进
       骨盒的厘米数（段校验 父scale= / 骨盒回读 都吃它）—— 所以读不到必须留一行字，
       不能让「没量到」冒充「量到 1.0」。 */
    catch ( mmd2maxLog ("Biped scale 读不到 " + (try (node.name) catch ("?")) + "：回落 [1,1,1]，" + (getCurrentException() as string)) )
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
    /* 读 widths.tsv：biped_target <tab> width <tab> palm_len，列按表头名取。
       第 3 列是 #136 加的可选列（只有 Hand 两行有掌长目标）；老产物没有这一列时
       `mmd2maxColPos` 会抛，所以先问表头在不在，再决定读不读 —— 不静默补 0，
       也不让一条不存在的新尺混进规格表。返回 #(名字, 目标宽, 目标掌长或 0)。 */
    local result = #()
    if path == undefined or path == "" do return result
    if not (doesFileExist path) do return result
    local tbl = mmd2maxReadTable path
    local cTarget = mmd2maxColPos tbl "biped_target"
    local cWidth = mmd2maxColPos tbl "width"
    local hasPalm = (findItem tbl[1] "palm_len") > 0
    local cPalm = 0
    if hasPalm do cPalm = mmd2maxColPos tbl "palm_len"
    for row in mmd2maxRows tbl do
    (
        if row.count >= cWidth do
        (
            local w = 0.0
            try ( w = row[cWidth] as float ) catch ( w = 0.0 )
            if w > 0.001 do
            (
                local palm = 0.0
                if hasPalm and row.count >= cPalm do
                    try ( palm = row[cPalm] as float ) catch ( palm = 0.0 )
                if palm < 0.0 do palm = 0.0
                append result #(row[cTarget], w, palm)
            )
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

/* ===== 骨盒写在哪个轴上，是一次事实而不是每次现量（#75）=====
   第 44 轮换模型冒烟（香菱-海灯节）撞出来的：同一根 Foot 在同一轮里被四处代码各判一次
   长度轴 —— 肢段拟合（缩这根轴能把父→子距离改掉，操作定义）给 1，足背收截面给 2，
   接趾给 1，最后的骨盒回读也给 1。足背那句「长度轴=2」意味着它把「高=8.7534」写在了
   真正的长度轴上，而把 14.82 那根边当成长度留在原地没碰：脚盒于是成了一只横着伸出
   14.82cm 的团块（两脚中心才 ±4.06，它一定盖住另一只脚），box_rb 的 7 行与脚链的
   3 行全是这一件事的读数（三根边的数**全对**，只是贴错了轴名）。
   香菱不显形纯属巧合：那具模型的脚骨在摆位前后长度轴恰好都是 2。
   所以这里加一份账：写入侧（mmd2maxFitCrossSection）把「我沿哪根轴缩的」记下来，
   回读侧（mmd2maxBoxDims）先认这份账。为什么不用 MMD2MAX_LENAXIS_MAP 直接当账：
   那份只记「恰好一个子关节」的骨（Toe0 是叶子骨，进不去），而盒子写在哪儿这件事
   与「转角该转哪根轴」是两个问题，混成一份账就会把 #28 的转角判据一起带跑。 */
global MMD2MAX_BOXAXIS_MAP = #()

fn mmd2maxBoxAxisOf nm =
(
    /* 查这根骨的盒子当初沿哪根轴写（1/2/3）；没记账 ⇒ 0 = 退回现场判据。 */
    if nm == undefined do return 0
    for e in MMD2MAX_BOXAXIS_MAP do
    (
        if e[1] == nm do return e[2]
    )
    0
)

fn mmd2maxRememberBoxAxis nm ax =
(
    /* 记一次；同一根骨再写就覆盖 —— 收敛循环里同一只盒子可能被缩两遍，后一遍才是终态。 */
    if nm == undefined do return 0
    if ax == 0 do return 0
    for i = 1 to MMD2MAX_BOXAXIS_MAP.count do
    (
        if MMD2MAX_BOXAXIS_MAP[i][1] == nm do
        (
            MMD2MAX_BOXAXIS_MAP[i] = #(nm, ax)
            return ax
        )
    )
    append MMD2MAX_BOXAXIS_MAP #(nm, ax)
    ax
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
    /* tail 离 head 不到 `MMD2MAX_STUBCM` = 模型里的显示 stub（香菱 右手首 只有 0.38cm，
       而左手首 3.26cm），方向本身没意义，标出来让调用方别计入超标。数住在
       mmd2max/targets.STUB_CM，Python 判据侧算期望行数用的是同一个（verdicts 里那个
       ACCESSORY_STUB_CM 只是别名），所以这里再抄一份字面量就是两边分叉的起点。 */
    if (length want) < MMD2MAX_STUBCM do msg = msg + " 短骨"
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

fn mmd2maxBipedRefresh =
(
    /* 逼一次控制器重算。返回真跑通的那一句的名字，一种都不通返回 ""；结果缓存在
       全局里，所以每轮只打一行读数。

       这条是第 62 轮的日志自己报出来的：旧写法 `update 0` 抛
       `-- No "update" function for 0` —— Max 2022 没有全局 `update`，MAXScript 把
       `update 0` 读成「给整数 0 发 update 消息」。也就是说从 #4 起每一句
       「刷新后仍在 >= 15 度」量的其实都是「写完立刻读」。这和 v3 那句从来没生效过的
       `skinOps.forceUpdate` 是同一种死法，而规则 V 立项时举的就是后者的例子 ——
       清完空 catch 的第一个实证是它自己抓出来的。

       候选按「会不会真按时间重算控制器」排，不是按「名字看着像」排，并且赢家要带口径：
       ① `evalTime` 是唯一一类「把场景推到指定时刻重算」的名字。第 65 轮实测它在
          Max 2022 里也不存在：`-- Type error: Call needs function or class, got: undefined`。
       ② `redrawViews()` 只重画视口，跑通了也不构成「控制器重算过」，所以读数里必须带上
          这句 —— 否则下一轮看到一行 `redrawViews()` 又会被人当成「刷新做完了」。
       已实测死掉的名字不再重复探，只留字面记录：`update`（`No "update" function for 0`，
       第 62 轮）、`updateSceneTime`（`Call needs function or class, got: undefined`，第 64 轮）。
       真正逼 Biped 重算的那一步不在这个函数里，是调用方对同一根骨重发的
       `biped.setTransform node #pos p false` —— 那是 Biped 自己的 API，会重算它自己的节点。 */
    if MMD2MAX_REFRESHCALL == undefined do
    (
        local used = ""
        local usedKind = ""
        local why = ""
        if used == "" do
        (
            try ( evalTime 0; used = "evalTime 0"; usedKind = "按时间重算控制器" )
            catch ( why = why + " evalTime→" + (getCurrentException() as string) + "；" )
        )
        if used == "" do
        (
            try ( redrawViews(); used = "redrawViews()" )
            catch ( why = why + " redrawViews→" + (getCurrentException() as string) + "；" )
            if used == "redrawViews()" do usedKind = "只重画视口，不重算控制器"
        )
        MMD2MAX_REFRESHCALL = used
        /* 试掉的那几句必须一起报：第 63 轮就是这句「redrawViews」赢了，但没人知道
           updateSceneTime 是怎么死的 —— 「哪种写法真的会重算控制器」这件事，
           全靠这几个报错文字才能判，光一个赢家名字不够。
           赢家后面那个括号是口径：`redrawViews()` 跑通 ≠ 控制器重算过。 */
        mmd2maxLog ("Biped 刷新调用：" + (if used == "" then "没有一种跑得通" else used + "（" + usedKind + "）") \
            + (if why != "" then "（试掉：" + why + "）" else ""))
    )
    MMD2MAX_REFRESHCALL
)

fn mmd2maxRefreshedMoveDeg node axRef =
(
    /* 「转完再逼 Biped 刷新一次」之后的局轴转角（度），取不到返回 -1.0。
       为什么必须有这一步：10:17 直接写 .transform / rotate 命令都读到 30 度，但那可能
       只是 MAXScript 自己的缓存 —— Biped 的控制器完全可以在下一次重算时把 TM 按
       结构算回去，那 30 度就是假的。做法：对同一根骨再发一次一直好用的 #pos（原值写回，
       位置不动），再走一次刷新（见 mmd2maxBipedRefresh），然后重量局轴。

       口径要说准（第 62~65 轮实测出来的）：真正逼 Biped 重算的是那句 `#pos` 原值重发
       —— 它是 Biped 自己的 API，会重算它自己的节点；`mmd2maxBipedRefresh()` 在 Max 2022
       上只剩 `redrawViews()` 这一句，它只重画视口。第 62 轮之前这里写的是 `update 0`，
       那句从来没生效过，所以历史上所有「刷新后仍在」的字面意思其实是「重发 #pos 后仍在」。
       两者对 Biped 骨来说指向同一件事，但别再把它读成「全场景控制器都重算过了」。 */
    local p = undefined
    local refreshed = false
    try ( p = node.transform.translationpart )
    catch ( mmd2maxLog ("刷新前平移读不到 " + (try (node.name) catch ("?")) + "：" + (getCurrentException() as string)) )
    if p != undefined do
    (
        try ( biped.setTransform node #pos p false; refreshed = true )
        catch ( mmd2maxLog ("刷新用 #pos 原值重发失败 " + (try (node.name) catch ("?")) + "：" + (getCurrentException() as string)) )
    )
    mmd2maxBipedRefresh()
    /* 原值重发那条路没走通 ⇒ 下面量到的局轴仍是「没被 Biped 重算过的那份」，而本函数
       存在的理由就是排除这种假 30 度。报 -1.0（调用方按「读不到」处理），不把未经验证
       的读数当成验证过的 —— 这一句把三个 catch 的失败方向从「冒充成绩」改成「刺眼负数」。 */
    if not refreshed do return -1.0
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
        try ( m0 = node.transform ) catch () -- 允许失败：m0 留在 undefined，下面那条 if 直接 return「当前位姿读不到」，失败变成返回值不是静默
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
            try ( n = getMethodCount biped ) catch () -- 允许失败：n 留在 0 ⇒ 下面 `if n == 0 do return` 把 dumpErr/oldErr 原文报进日志，反射失败是读得出名字的
            if n > 0 do def = biped
        )
        if n == 0 do return (logTag + " 反射失败：dumpInterface=" + dumpErr \
            + " | getMethodCount=0 " + oldErr)
        how = "getMethodCount"
        for i = 1 to n do
        (
            local one = ""
            try ( one = ((getMethod def i) as string) )
            catch ( append rows ("!getMethod[" + (i as string) + "] 抛错：" + (getCurrentException() as string)) )
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
    try ( apiPath = (getFilenamePath MMD2MAX_LOG) + "biped_api.txt" ) catch () -- 允许失败：apiPath 留在 "" ⇒ 下面 `if apiPath != ""` 跳过写文件，wrote 报「未写文件」，命中行仍进日志
    local wrote = "未写文件"
    if apiPath != "" do
    (
        local fh = undefined
        try ( fh = createFile apiPath encoding:#utf8 ) catch () -- 允许失败：fh 留在 undefined ⇒ 不写文件，wrote 保持「未写文件」并随这一行的结论一起进日志
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
                                        /* 回滚转角之后要把踝的 #pos 再钉回目标：Biped 转父关节会甩子树，
                                           只回滚矩阵不重发踝位，踝就留在被甩开的那儿。写没写成只有 Max 知道。 */
                                        local posErr = ""
                                        try ( biped.setTransform fnode #pos ankleTgt false ) catch ( posErr = (getCurrentException() as string) )
                                        if posErr != "" do msg = msg + " 回滚后重发踝位失败=" + posErr
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
   所以写 999 等于整段复摆跳过。
   门控取值 = 段长闸的容差（targets.SEGMENT_TOL_CM），两边必须是同一个数：这里比它松，
   就出现「复摆放行、段长判红」的自相矛盾 —— #127 在 林尼 上实测到 0.489997 被放行、
   `段校验 Foot -> Toe0` 红在 0.38872（期望 15.987622 / 实报 15.5989）。 */

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
                        local posErr2 = ""
                        try ( biped.setTransform cnode #pos before false ) catch ( posErr2 = (getCurrentException() as string) )
                        local back1 = distance cnode.transform.translationpart ballPos
                        local backA = distance fnode.transform.translationpart ankleTgt
                        msg = msg + " 没改善->回滚 回读=" + (back1 as string) \
                            + " 踝回读=" + (backA as string)
                        /* 回读两个数说明「现在离目标多远」，说明不了「回滚这条写有没有做成」——
                           抛错原文必须另带一截，否则「回滚写了但没生效」和「回滚没执行」读起来一样。 */
                        if posErr2 != "" do msg = msg + " 回滚抛错=" + posErr2
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
       短于短骨门限（MMD2MAX_STUBCM）的显示 stub 不计方向，读不到轴（-1.0）也不计。
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
                        if (length sWant) >= MMD2MAX_STUBCM do
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
                    /* 短于短骨门限（MMD2MAX_STUBCM，数值住在 targets.STUB_CM）的是模型
                       自己的显示 stub（香菱 右手首 0.38cm），方向没意义。
                       分两层 if：`length undefined` 会抛，不赌 and 的短路 */
                    if want != undefined do
                    if prio != #pos do
                    if (not onlyDir or prio == #dir) do
                    if (length want) >= MMD2MAX_STUBCM do
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
    /* 返回值是「算出来的」那份 scale，不是回读：调用方紧接着还会自己量骨盒（box_rb 那三行
       宽/高/长就是它的独立尺）。所以写没写成必须在这一行留字，不能让「没写进去」冒充
       「写进去了、下一轮再看」。 */
    try ( biped.setTransform node #scale v false )
    catch ( mmd2maxLog ("单轴缩放写入失败 " + (try (node.name) catch ("?")) + " axis=" + (axisIdx as string) + " ×" + (factor as string) + "：" + (getCurrentException() as string)) )
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
    /* #75：有肢段那笔操作账（缩它能把父→子距离改掉）就认账，没账才认现场方向；
       认完把轴号记进盒账，最后的骨盒回读按同一笔账读 —— 否则写入与回读各自现场
       重挑，中间隔着摆位与转角，同一只盒子会被贴到别的轴名上（第 44 轮的 10 行 FAIL）。 */
    local want = mmd2maxLenAxisOf node.name
    if want > 0 do ai = want
    mmd2maxRememberBoxAxis node.name ai
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

fn mmd2maxLeafLengthAxis node =
(
    /* 叶子骨（第三节手指一类）没有子关节，mmd2maxLengthAxisIndex 恒返回 0，
       于是 widths.tsv 里给它的「长度轴目标」在写入侧会被整段跳过、在回读侧
       会被记成 read_fail（#139 用户截图：第三节手指盒比原结构长出一节）。
       借父骨的长度轴：Biped 手指链三节共用同一根局部轴（探针实测三节的
       ax1 都指向指尖）。父骨也读不到时，退回本地盒跨度最大的那根 —— 细长盒
       里那根必然是长度方向。只在 widths 表出现末节行时才会走到这里，
       有子关节的骨一行都不改。 */
    local p = undefined
    try ( p = node.parent ) catch ( p = undefined )
    if p != undefined do
    (
        local pa = mmd2maxLengthAxisIndex p
        if pa > 0 do return pa
    )
    local best = 0
    local bestLen = 0.0
    for ai = 1 to 3 do
    (
        local e = mmd2maxLocalExtent node ai
        if e > bestLen do ( bestLen = e; best = ai )
    )
    best
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
            if lax <= 0 do lax = mmd2maxLeafLengthAxis node
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
                        local wErr = ""
                        try ( biped.setTransform node #scale [comps[1], comps[2], comps[3]] false ) catch ( wErr = (getCurrentException() as string) )
                        local s2 = mmd2maxBipedScale node
                        mmd2maxLog ("  手部粗细 " + name + " 长轴=" + (lax as string) \
                            + " 目标宽=" + (target as string) \
                            + " scale: (" + (s.x as string) + "," + (s.y as string) + "," + (s.z as string) + ")" \
                            + " -> (" + (s2.x as string) + "," + (s2.y as string) + "," + (s2.z as string) + ")")
                        /* s2 是从 Max 读回来的：写失败时它等于 s，日志里那对「-> 」读数自己会露馅；
                           这一行把抛错原文也钉上，省得下次再靠肉眼比对两个三元组。 */
                        if wErr != "" do ( mmd2maxLog ("  手部粗细写入抛错 " + name + "：" + wErr) )
                        adjusted += 1
                    )
                )
                /* 掌长（#136）：只缩横截面会把「手掌骨盒比手掌大一圈」原样留下 ——
                   Biped 默认 Hand 是一块按身高给的桨形盒，林尼最终场景里长轴盒跨度
                   10.24cm，而手掌网格沿腕→指根轴只有 6.5cm。目标来自 widths.tsv
                   第 3 列（Python 侧量手掌网格 5%~95% 分位），这里按**本地盒跨度**
                   闭环缩长度轴，与 mmd2maxFitBoneExtent 同一套「量→倍率→写」口径。
                   长度轴写下去会把五根指根一起推走，但这一步排在初摆位之前，
                   摆位会把指根按 PMX 目标重新摆回去（apply_widths 已登记 moves=True）。 */
                local palm = 0.0
                if item.count >= 3 do palm = item[3]
                if palm > 0.5 do
                (
                    local lenBefore = mmd2maxLocalExtent node lax
                    if lenBefore > 0.001 then
                    (
                        local pf = palm / lenBefore
                        if pf < 0.20 do pf = 0.20
                        if pf > 1.20 do pf = 1.20
                        mmd2maxScaleBoneAxis node lax pf
                        local lenAfter = mmd2maxLocalExtent node lax
                        mmd2maxLog ("  掌长拟合 " + name + " 长轴=" + (lax as string) \
                            + " 目标=" + (palm as string) \
                            + " 盒长 " + (lenBefore as string) + " -> " + (lenAfter as string) \
                            + " 倍率=" + (pf as string))
                    )
                    else mmd2maxLog ("  掌长拟合 " + name + " 跳过：长度轴本地盒跨度读不到")
                )
            )
        )
    )
    adjusted
)

fn mmd2maxWorldSpanAlong node dir =
(
    /* #79：把骨盒的八个角点投到 dir 上，量**世界**跨度 —— 唯一不看「盒轴记录」的尺。
       mmd2maxBoxDims 那三行（宽/高/长）都按 #75 那笔盒账挑轴，所以「薄的那根边有没有
       沿着这根骨指向的方向」在它里面根本问不出来：把 3.4674 报成「长」也是自洽的，
       而 #78 就是这件事（趾长 0.5722 写在竖直轴上、3.4674 顺着趾方向戳出鞋尖，
       box_rb 全绿）。这里绕开轴号，直接量角点，所以记录说谎它照量。
       坐标系与单位：本地角点来自 `nodeGetBoundingBox node node.transform`，这一句在
       本项目里已经被当成「以 pivot 为原点、**已经乘过 #scale** 的厘米本地盒」用了
       几十轮（mmd2maxLocalExtent 与 mmd2maxBoxFarEnd 都靠它，报出的 长/宽/高 直接与
       limbs 的厘米期望对得上；第 44 轮起还打印过 本地 min/max，脚链的 y 就是 9.73629
       那个接趾投影值）。所以要把它搬到世界只许乘**旋转**，不许整块乘 node.transform：
       后者的行向量里还带着一份 #scale（脚骨读到 [8.87, 14.27, 7.33] 这种厘米数），
       再乘一遍等于把尺寸缩两次，跨度会变成几百。这里用转角那边一直在用的
       mmd2maxBipedAxes（行向量 normalize 后的三根单位世界轴）；世界 pivot 是平移项，
       在 hi-lo 里自动抵消，所以不必加。
       写法：八个角点投影后取 max−min，对「棱与自身局部轴平行」的盒子就等于
       `Σ 棱长_i × |cos(轴_i, dir)|`（每个角点在各轴上的贡献独立取极值），所以这里
       直接写这个闭式 —— 少三层循环，也少一处跨行嵌套 for 的 MAXScript 解析风险。
       dir 在这里**自己 normalize**，不要求调用方给单位向量：调用方传的是
       mmd2maxBoxWantDir，而它三条返回路径**没有一条是单位向量**（压平那条乘的是
       `length raw / horiz`，另两条直接给 tail-head 或 shoeTip-head 原差）。拿非单位
       向量做投影，量出来是「跨度 × |dir|」—— 趾骨那根的 |dir| 就是鞋尖到脚掌的距离，
       这个数会绿在尺子外面、且每具模型错的比例还不一样。dir 长度近零、轴读不到或
       包围盒读不到都返回 undefined，绝不猜。 */
    local bb = undefined
    try ( bb = nodeGetBoundingBox node node.transform ) catch ( bb = undefined )
    if bb == undefined do return undefined
    local len = (length dir)
    if len < 0.0001 do return undefined
    local u = dir / len
    local axes = mmd2maxBipedAxes node
    if axes == undefined do return undefined
    local mn = bb[1]
    local mx = bb[2]
    local a1 = dot axes[1] u
    local a2 = dot axes[2] u
    local a3 = dot axes[3] u
    /* 每根棱长与它的 |cos| 各自成一项，`(...)` 是必需的：MAXScript 在表达式里把函数
       调用之后的整段都当参数吞（`abs a1 * e1 + …` 会被读成 `abs (a1*e1 + …)`），
       那样符号能互相抵掉，量出来的就不再是跨度。 */
    (abs a1) * (mx.x - mn.x) + (abs a2) * (mx.y - mn.y) + (abs a3) * (mx.z - mn.z)
)

fn mmd2maxWorldBoxAABB node =
(
    /* #134：把骨盒八个角点搬到世界，取轴对齐包围盒 #(minPoint, maxPoint)（厘米）。
       与 mmd2maxWorldSpanAlong、与探针 probes/toe_ground.ms 的 worldBoxZ 同一口径：
       本地角点来自 nodeGetBoundingBox node node.transform（pivot 原点、已乘 #scale 的厘米盒），
       搬到世界只乘 mmd2maxBipedAxes 的三根单位世界轴（纯旋转）再加世界 pivot 平移。
       探针 D 段实证：biped.setTransform #pos 把 pivot 搬 -2cm，盒底世界 z 也跟着 -2cm
       （boxMinDelta==pivotDelta==-2），所以「盒底落地」只能搬 pivot，对象偏移挪不动盒（B 段=0）。
       穿鞋检测尺拿 [1].z 当盒底世界 z、拿 [1].x/y~[2].x/y 当水平足迹去网格里找鞋底。
       读不到包围盒或轴返回 undefined，绝不猜。 */
    local bb = undefined
    try ( bb = nodeGetBoundingBox node node.transform ) catch ( bb = undefined )
    if bb == undefined do return undefined
    local axes = mmd2maxBipedAxes node
    if axes == undefined do return undefined
    local p = node.transform.translationpart
    local mn = bb[1]
    local mx = bb[2]
    local lo = [1e9, 1e9, 1e9]
    local hi = [-1e9, -1e9, -1e9]
    for cx in #(mn.x, mx.x) do
        for cy in #(mn.y, mx.y) do
            for cz in #(mn.z, mx.z) do
            (
                local w = p + cx * axes[1] + cy * axes[2] + cz * axes[3]
                if w.x < lo.x do lo.x = w.x
                if w.y < lo.y do lo.y = w.y
                if w.z < lo.z do lo.z = w.z
                if w.x > hi.x do hi.x = w.x
                if w.y > hi.y do hi.y = w.y
                if w.z > hi.z do hi.z = w.z
            )
    #(lo, hi)
)

fn mmd2maxShoeSoleZ meshes boxMin boxMax =
(
    /* #134 穿鞋检测：趾盒水平足迹（世界 AABB 的 x/y 范围）内，鞋网格最低点的世界 z（厘米）。
       顶点用 snapshotAsMesh 取**当前 evaluated** 网格再 getVert（类无关：Editable_Mesh/Poly 都行），
       乘 node.transform 搬到世界。本尺排在 skin_apply 之前（注册表 toe_shoe requires figure_exit，
       执行位紧跟 box_verify），此刻网格还没蒙皮、没有 Morpher 副本，evaluated 就是导入的绑定姿态鞋，
       所以量到的是「鞋这只网格的底」而不是 Skin 变形后的面。
       足迹内一个顶点都没有返回 undefined（绝不猜 0 —— 那会被拼表当成「鞋底真的在 z0」判绿）。 */
    local sole = undefined
    for m in meshes do
    (
        local tmesh = undefined
        try ( tmesh = snapshotAsMesh m ) catch ( tmesh = undefined )
        if tmesh == undefined do continue
        local tm = m.transform
        local nv = tmesh.numVerts
        for vi = 1 to nv do
        (
            local v = undefined
            try ( v = (getVert tmesh vi) * tm ) catch ( v = undefined )
            if v != undefined do
            (
                if v.x >= boxMin.x and v.x <= boxMax.x \
                   and v.y >= boxMin.y and v.y <= boxMax.y do
                (
                    if sole == undefined or v.z < sole do sole = v.z
                )
            )
        )
    )
    sole
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
    /* #75：先认盒账（写入那一刻沿哪根轴缩的就按哪根读），认不到才退回现场方向。
       这一行是「数字全对、标签错位」唯一的解药：回读若自己重挑一根轴，量到的就是
       别的轴的数，而 gate 会红在尺子上 —— 读者只会去改几何。 */
    local pinned = mmd2maxBoxAxisOf node.name
    if pinned > 0 do ai = pinned
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
    /* 一根骨：量 + 落五行读数 + 一行日志。返回 1=量到 / 0=没量到，调用方只负责数数。
       这边**一个判定都不写**（规则 II）：宽高等待不等于几厘米由 MMD2MAX.VERDICTS 说，
       Max 只把量到的数交出去。
       第五行 `span_want_cm` 是 #79：前三行按盒账挑轴，所以「长」报的是**哪根边的长度**
       由记录说了算；这一行把盒角点投到参考方向上直接量，绕开记录。两者相等才说明
       「沿着骨指向的那根边就是报出去的长」。 */
    local d = mmd2maxBoxDims node dir
    if d == undefined do return 0
    local span = mmd2maxWorldSpanAlong node dir
    mmd2maxReport "box_rb" "width_cm" subject d[1]
    mmd2maxReport "box_rb" "height_cm" subject d[2]
    mmd2maxReport "box_rb" "length_cm" subject d[3]
    mmd2maxReport "box_rb" "len_axis" subject d[4]
    if span != undefined do mmd2maxReport "box_rb" "span_want_cm" subject span
    mmd2maxLog ("  骨盒回读 " + subject + " 宽=" + (d[1] as string) \
        + " 高=" + (d[2] as string) + " 长=" + (d[3] as string) \
        + " 长度轴=" + (d[4] as string) \
        + " 沿指向=" + ((if span != undefined then span else -1.0) as string))
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
       参考方向与写入同一条：足背 = 实测踝 pivot → PMX 脚掌，脚趾 = mmd2maxBoxWantDir
       （脚掌 → 鞋尖，压平口径与导出侧 FOOT_BOX_FLAT 同真同假 —— #78 之前写入用的是三维
       向量，而这只鞋的鞋尖比脚掌低 1.23cm，两个口径挑出来的不是同一根轴）。
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
                    /* 与 脚趾两段 / 趾盒收尾 同一个参考方向：盒账读不到时才会用到它，
                       而那正是「写入与回读各挑一根轴」最容易分叉的落点（#78）。 */
                    local wantC = mmd2maxBoxWantDir cName ballPos ballPos tipPos
                    if wantC == undefined do wantC = tv / tl
                    local okC = mmd2maxReportBoxDims cName cnode wantC
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
        if node != undefined and lax <= 0 do lax = mmd2maxLeafLengthAxis node
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
                /* 掌长回读（#136）：只在 widths.tsv 给了目标时才报。期望值由 Python
                   从同一张表的 palm_len 列装进规格，这里一个判定都不写（规则 II）。 */
                local palm = 0.0
                if item.count >= 3 do palm = item[3]
                local lenOk = true
                if palm > 0.5 do
                (
                    local palmLen = mmd2maxLocalExtent node lax
                    if palmLen <= 0.0001 then
                    (
                        lenOk = false
                        mmd2maxLog ("  骨盒回读 " + name + " 掌长盒跨度读到 0")
                    )
                    else
                    (
                        mmd2maxReport "box_rb" "hand_length_cm" name palmLen
                        mmd2maxLog ("  骨盒回读 " + name + " 掌长盒跨度=" + (palmLen as string) \
                            + " 目标=" + (palm as string))
                    )
                )
                if lenOk then measured += 1 else failed += 1
            )
        )
    )
    #(measured, failed)
)

fn mmd2maxVerifyToeShoe root limbs =
(
    /* ===== 穿鞋检测（#134）：趾盒底相对鞋底/地面的竖直位置，只读不写 =====
       两把尺（判定在 Python 侧 verdicts.toe_shoe，这里只报数）：
         penetration_cm = max(0, 鞋底z − 盒底z)：盒底戳穿鞋底下方多少（穿模量）
         ground_gap_cm  = 盒底世界 z：盒底离地面（z=0）多远
       鞋底 z 从网格量（mmd2maxShoeSoleZ：盒水平足迹内最低顶点），盒底 z 从骨盒世界 AABB 量
       （mmd2maxWorldBoxAABB）—— 两边都在最终场景里读，不是写入侧自述（恒等式看不见穿模）。
       排在 box_verify 之后、skin_apply 之前：网格还没蒙皮、没有 Morpher 副本，鞋底是绑定姿态鞋。
       读不到盒或鞋底就跳过该根并留痕（绝不猜 0 —— 那会被拼表当成「盒底真在 z0/真没穿模」判绿；
       缺行本身会让 Python 侧那条 always 规格红，测量故障不会被静默放行）。返回 #(量到, 读不到)。 */
    local meshes = mmd2maxMeshes()
    local measured = 0
    local failed = 0
    for item in limbs do
    (
        if item.count >= 10 and item[5] == "foot" do
        (
            local cName = item[2]
            local cnode = getNodeByName (root.name + (substring cName 7 -1)) exact:true
            local aabb = if cnode != undefined then mmd2maxWorldBoxAABB cnode else undefined
            if aabb != undefined then
            (
                local boxMinZ = aabb[1].z
                local sole = mmd2maxShoeSoleZ meshes aabb[1] aabb[2]
                if sole != undefined then
                (
                    local pen = sole - boxMinZ
                    if pen < 0.0 do pen = 0.0
                    mmd2maxReport "toe_shoe" "penetration_cm" cName pen
                    mmd2maxReport "toe_shoe" "ground_gap_cm" cName boxMinZ
                    mmd2maxLog ("  穿鞋检测 " + cName + " 盒底z=" + (boxMinZ as string) \
                        + " 鞋底z=" + (sole as string) + " 穿模=" + (pen as string))
                    measured += 1
                )
                else
                (
                    failed += 1
                    mmd2maxLog ("  穿鞋检测 " + cName + " 跳过：盒足迹内没量到鞋底顶点")
                )
            )
            else
            (
                failed += 1
                mmd2maxLog ("  穿鞋检测 " + cName + " 跳过：节点或盒世界包围盒读不到")
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
                /* 还原必须回读确认：探针把某一根轴乘了 1.5，没还原等于把整副 Biped 的
                   骨盒尺寸留在那个错误 scale 上，而后面每一节的厘米数都从它上面量。 */
                local rbErr = ""
                try ( biped.setTransform pnode #scale s0 false ) catch ( rbErr = (getCurrentException() as string) )
                local sBack = mmd2maxBipedScale pnode
                if rbErr != "" or ((abs (sBack.x - s0.x)) > 0.0001 or (abs (sBack.y - s0.y)) > 0.0001 or (abs (sBack.z - s0.z)) > 0.0001) do
                (
                    mmd2maxLog ("!! 缩放探针(biped#scale) 没还原 axis=" + (axis as string) \
                        + " s0=" + (s0 as string) + " 回读=" + (sBack as string) + " " + rbErr)
                )
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
                    /* 同方法一：还原没做成 = 这根骨留在 1.5 倍上，后面每一节的厘米数都从它上面量。 */
                    local rbErr2 = ""
                    local sBack2 = s0
                    try ( pnode.scale = s0; sBack2 = pnode.scale ) catch ( rbErr2 = (getCurrentException() as string) )
                    if rbErr2 != "" or ((abs (sBack2.x - s0.x)) > 0.0001 or (abs (sBack2.y - s0.y)) > 0.0001 or (abs (sBack2.z - s0.z)) > 0.0001) do
                    (
                        mmd2maxLog ("!! 缩放探针(node.scale) 没还原 axis=" + (axis as string) \
                            + " s0=" + (s0 as string) + " 回读=" + (sBack2 as string) + " " + rbErr2)
                    )
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
    /* 骨盒缩放的唯一写入口，5 个调用点都不看返回值（它们之后靠回读长度判）。所以失败必须
       自己留字：否则「scale 根本没写进去」只表现为下一节骨盒尺寸不对 —— 那不是缺陷的形状，
       那是查不出来源的红。 */
    if method == "node.scale" then
        try ( node.scale = s )
        catch ( mmd2maxLog ("缩放写入失败 node.scale " + (try (node.name) catch ("?")) + " s=" + (s as string) + "：" + (getCurrentException() as string)) )
    else
        try ( biped.setTransform node #scale s false )
        catch ( mmd2maxLog ("缩放写入失败 biped#scale " + (try (node.name) catch ("?")) + " s=" + (s as string) + "：" + (getCurrentException() as string)) )
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

fn mmd2maxCoupledAxis pnode cnode method =
(
    /* 长度轴 = 缩同样 5% 之后，把子关节推得最远的那根轴。这是**探针**，不是「试到收敛」：
       海灯节 `肢段 L Foot -> L Toe0 轴=1` 那行是 scale.x 8.86744->9.82177（+10.8%）只换来
       父→子距离 +4.2%，误差 0.0475 刚好压过 0.05 门槛就被收下；香菱同一段是 `轴=2`
       （默认轴 1 不收敛、重试才找到 2）。同一句话在两具模型上给出两个轴，说明「收敛」
       不是长度轴的判据 —— 弱耦合的宽轴在初始长度本来就接近目标时会蒙过门槛。
       蒙错之后 #75 那笔盒账把错轴传给 足背 / 接趾 / 回读，海灯节的 Foot 骨盒于是把
       带 23:77 脚跟不对称的长边（axis2，缩前 14.82cm）转成了竖直边：盒子沿踝上方伸
       6.74cm 进小腿、只向下 2.01cm，而脚掌 pivot 在踝下方 4.58cm（由 连线长 15.1039 与
       子沿轴 14.3926 反推，纯世界 −Z 分量）—— `端面覆盖=false` 那两行量的就是这件事，
       截图 香菱-海灯节_bone_foot_side.png 里那只朝上指的鳍也是它。
       返回 #(轴号, 最大增益, 轴1增益, 轴2增益, 轴3增益)；三根都推不动（缩放被锁）时
       轴号=0，调用方退回默认轴。增益就是「缩 5% 之后距离动了 5% 的百分之几」，
       长度轴应当接近 0.05，宽/高轴接近 0。 */
    local base = mmd2maxSegLen pnode cnode
    if base < 0.0001 do return #(0, 0.0, 0.0, 0.0, 0.0)
    local s0 = mmd2maxBipedScale pnode
    local gains = #(0.0, 0.0, 0.0)
    for ax = 1 to 3 do
    (
        if mmd2maxApplyScale pnode ax 1.05 method do
        (
            gains[ax] = abs ((mmd2maxSegLen pnode cnode) - base) / base
            mmd2maxSetScale pnode s0 method
        )
    )
    local best = 0
    local bestGain = 0.0
    for ax = 1 to 3 do
    (
        if gains[ax] > bestGain do ( bestGain = gains[ax]; best = ax )
    )
    #(best, bestGain, gains[1], gains[2], gains[3])
)

fn mmd2maxFitSegments root limbs axis method =
(
    /* 闭环 + 逐段找轴。
       为什么要逐段找轴：Biped 的 #scale 三个分量对不同节点含义不同——
       肢骨通常 X = link 长度；但 **Pelvis 的 X 是 link 长度、横向髋宽在 Y/Z 上**，
       拿 X 去缩骨盆，髋关节的横向距离根本不变（上一轮髋差 1.45 就是这个原因）。
       所以：先用**探针**量出「缩哪根轴最能改掉父→子距离」（#77，见 mmd2maxCoupledAxis），
       拿那根轴做闭环；探针读不到或那根轴不收敛，才恢复原值、逐轴重试（含「三轴等比例」），
       取第一个收敛的。
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
            /* #77：首试轴改用探针实测的耦合轴（见 mmd2maxCoupledAxis）——
               「默认轴试到收敛」在初始长度本来就接近目标时会收下宽轴。
               探针读不到（三轴都推不动）才退回原来的默认轴；探针自己会把 scale 复原，
               所以上面那行 默认= 仍是没被碰过的长度。 */
            local probe = mmd2maxCoupledAxis pnode cnode method
            local usedAxis = axis
            if probe[1] > 0 do usedAxis = probe[1]
            local best = mmd2maxFitOnePair pnode cnode desired usedAxis method
            if best[1] > 0.05 do
            (
                /* 探针轴没收敛：恢复，再逐轴试（含「三轴等比例」），取第一个收敛的 */
                mmd2maxSetScale pnode s0 method
                for ax = 1 to 4 do
                (
                    if best[1] > 0.05 and ax != usedAxis do
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
            /* 探针三根轴各自的增益（缩 5% 后距离变化占原距离的比例）：
               这一行是「轴=1 到底是长度轴还是被蒙过去的宽轴」唯一的现场证据。 */
            msg = msg + " 探针=" + (probe[1] as string) + " 增益=" + (probe[2] as string)
            msg = msg + "(" + (probe[3] as string) + "/" + (probe[4] as string) \
                + "/" + (probe[5] as string) + ")"
            /* 附上父骨的 scale 前后值：区分「link 长度」与「比例缩放」两种语义，
               同时能看出骨盆被缩放时是否把整副骨架带偏 */
            msg = msg + " scale: " + (s0 as string) + " -> " + ((mmd2maxBipedScale pnode) as string)
            mmd2maxLog msg
            /* 长度轴记账。三个条件缺一不可：
               1) 这段真的收敛（err<=0.05）—— 不收敛说明 usedAxis 是「试出来的假轴」；
               2) usedAxis 是 1/2/3 —— 4 是「三轴等比例」，那没指出长度在哪根轴上；
               3) 父骨**只有这一个子关节** —— Pelvis 有三个（Spine + 两腿）、Hand 有五个，
                  它们的「长度方向」不是一个方向，记进去会把躯干/骨盆一起带跑。
               #77 之后 usedAxis 来自探针（耦合最强的轴），条件 1 只是「缩到位」的账，
               不再承担「认轴」的职责 —— 第 44 轮它承担过，于是海灯节的脚盒朝上长。
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
    local probeErr = ""
    try
    (
        probe = biped.createNew height 0.0 [0.0, 0.0, 0.0] arms:true neckLinks:1 \
            spineLinks:(mmd2maxSpineLinks()) \
            legLinks:3 fingers:(mmd2maxFingerCount()) fingerLinks:(mmd2maxFingerLinks()) \
            toes:1 toeLinks:1 trianglePelvis:false \
            triangleNeck:true
    )
    catch ( probeErr = (getCurrentException() as string) )
    /* 探针建不起来 ⇒ 旋转接口 / 橡皮筋 / 锁骨射线三段自查这一轮全部缺席，而日志上只看得到
       「那几行没有」。缺席不是绿，所以失败自己必须留下一行字。 */
    if probeErr != "" do ( mmd2maxLog ("!! Biped 探针创建失败，旋转接口/橡皮筋/锁骨射线三段自查本轮没跑：" + probeErr) )
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
            local figErr = ""
            try ( figOn = mmd2maxFigureMode probe.controller true ) catch ( figErr = (getCurrentException() as string) )
            /* 10:34 最重要的一条：`eulerToQuat [0.0, 0.0, 30.0]` **自己就抛**
               `Unable to convert: [0,0,30] to type: EulerAngles`（旧代码只打 errP，
               这条一直藏在日志里）。也就是说 09:37/10:17 两轮看到的 `#rotation q=0.0度`
               根本不是「Biped 不接受转角」—— 四元数压根没构造出来，`setTransform #rotation`
               到今天一次都没被真正调用过。Max 自带 BsBipedTools.ms:1491 的写法是
               `eulerToQuat (eulerAngles 0 y z) order:1`，照抄这个形式。 */
            local q30 = undefined
            try ( q30 = eulerToQuat (eulerAngles 0.0 0.0 30.0) order:1 ) catch () -- 允许失败：q30 留在 undefined ⇒ 下一行往 rotMsg 写「quat构造失败」，且每条 #rotation 路都被 `if q30 != undefined` 跳过（10:34 就是靠这两个数抓到 quat 根本没构造出来的）
            local rotMsg = "  旋转接口探针：Figure模式=" + (figOn as string)
            /* Figure 模式进不去 ⇒ 下面每一组「度/留」都是模式外测的，而 rotWay 的采用条件写的是
               「Figure 模式内也留得住」。失败原文必须跟着这一行：figOn=false 不能同时表示
               「没进去」和「进去了但返回 false」。 */
            if figErr != "" do rotMsg = rotMsg + " 进Figure抛错=" + figErr
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
            local figErrOut = ""
            try ( mmd2maxFigureMode probe.controller false ) catch ( figErrOut = (getCurrentException() as string) )
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
            if figErrOut != "" do rotMsg = rotMsg + " 退出Figure抛错=" + figErrOut
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
            try ( mmd2maxFigureMode probe.controller true )
            catch ( mmd2maxLog ("!! 橡皮筋基准没进成 Figure 模式，下面那几组「子跟」是模式外量的：" + (getCurrentException() as string)) )
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
                    local rubErr = ""
                    try ( biped.setTransform pNode #pos (p0 + [0.0, 0.0, 5.0]) false ) catch ( rubErr = (getCurrentException() as string) )
                    local p1 = pNode.transform.translationpart
                    local c1 = cNode.transform.translationpart
                    local rubMsg = "  橡皮筋基准 " + pr[1] + "->" + pr[2] \
                        + " 父动=" + ((distance p0 p1) as string) \
                        + " 子跟=" + ((distance c0 c1) as string) + "cm"
                    /* 「父动=0.0」既可能是 Biped 不跟、也可能是这句 #pos 压根没写进去，而这两种
                       结论相反（前者=橡皮筋关不掉，后者=这段根本没测）⇒ 抛错原文进同一行。 */
                    if rubErr != "" do rubMsg = rubMsg + " 搬父抛错=" + rubErr
                    mmd2maxLog rubMsg
                    /* 复位：不然后一对的读数被前一对污染 */
                    try ( biped.setTransform pNode #pos p0 false )
                    catch ( mmd2maxLog ("  橡皮筋复位失败 " + pr[1] + "：下一对读数带着这一对的 5cm 偏移，" + (getCurrentException() as string)) )
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
            try ( mmd2maxFigureMode probe.controller false ) catch () -- 允许失败：这个探针骨架紧接着就 delete 掉，模式没退也只留在一个即将消失的对象上；真骨架那遍自己会重设 Figure 模式
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
            /* 认不出类型 = 这一根既没被算进 Biped 也没被算进「不是 Biped」，而漏掉的那根恰好
               就是会顶掉真骨架同名的那种。所以抛错必须留字，不能让它读成「场景是干净的」。 */
            try ( if (classOf o.controller) == Biped_Control do isBiped = true )
            catch ( mmd2maxLog ("  清理探针：认不出 controller 类型 " + (try (o.name) catch ("?")) + "：" + (getCurrentException() as string)) )
            try ( if (classOf o) == Biped_Object do isBiped = true )
            catch ( mmd2maxLog ("  清理探针：认不出对象类型 " + (try (o.name) catch ("?")) + "：" + (getCurrentException() as string)) )
            if isBiped do append toDelete o
        )
    )
    /* 先收集再删除：边遍历 objects 边 delete 会跳过对象 */
    for o in toDelete do
    (
        try ( delete o; leftover += 1 )
        catch ( mmd2maxLog ("!! 探针残留删不掉 " + (try (o.name) catch ("?")) + "：后面按名字取节点会取到旧的假骨架，" + (getCurrentException() as string)) )
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
                        local ankleErr = ""
                        try ( biped.setTransform fnode #pos want false ) catch ( ankleErr = (getCurrentException() as string) )
                        local afterDev = distance (fnode.transform.translationpart) want
                        local fmsg = "  踝关节复核 " + footName
                        fmsg = fmsg + " 偏差 " + (beforeDev as string) + " -> " + (afterDev as string)
                        /* 两个偏差相等既可能是「Biped 顶回去了」也可能是「这句根本没写」，
                           前者是收敛问题、后者是接口问题 ⇒ 抛错原文跟着这一行走。 */
                        if ankleErr != "" do fmsg = fmsg + " 写位抛错=" + ankleErr
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
                /* 参考方向与「画线/链向/转角」同一个函数（mmd2maxBoxWantDir），不再现场
                   用三维的 鞋尖−脚掌：第 48 轮实测海灯节的鞋尖比脚掌关节**低 1.23cm**、
                   水平只走 0.17cm，那条三维向量几乎是竖直的 ⇒ 挑出来的「长度轴」是骨
                   自己的竖轴，于是 0.5722 的趾长被写在竖直轴上、3.4674 那根边顺着趾方向
                   戳出鞋尖。导出侧量截面时用的就是压平后的那根轴（FOOT_BOX_FLAT），写入
                   用另一根，等于两端各按各的口径。undefined（没压平且斜过 45 度）才退回
                   三维向量。 */
                local dir = mmd2maxBoxWantDir cnode.name ballPos ballPos tipPos
                if dir == undefined do dir = normalize (tipPos - ballPos)
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

    /* ===== 趾盒收尾（#78）：截面必须收在**最终帧**上 =====
       第 48 轮的账：海灯节 `脚趾 … 长度轴=2 宽高轴=3/1` 是**写入那一刻**的帧量的，
       而终帧 `画线 Bip001 L/R Toe0 … 最近轴=1` —— 趾方向早帧落在局部 Y，收敛循环
       加两次复摆把这根骨转过约 90 度之后改落在局部 X。于是盒子的薄边 0.5722 写在
       了一根不再指向脚尖的轴上，3.4674 那根边顺着趾方向戳出鞋尖 3.47cm。
       `骨盒回读` 按 #75 那笔盒账读，所以它自洽、报不出问题：那一轮只有 R Toe0 的
       宽高互换两行红，而 L 恰好早帧与终帧的 |z| 顺序一致，把同一件事整个盖住了。
       香菱本来就一致（`脚趾 … 长度轴=1` == 终帧 `最近轴=1`），这道收尾对它是空跑：
       三个目标都已到位，闭环一次都不写。
       排位与 #41 同一条道理：会改骨盒的写必须排在「最后挪动这根骨的 pass」之后，
       否则写入的帧和被验收的帧不是同一帧。 */
    local toeBoxFinal = 0
    local toeBoxTried = 0
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
            /* #129：守卫与早帧那一道（脚趾两段）是**同一批条件**（书写顺序不同），
               只剩 `toeLen > 0.001`。原来这里还写着 `toeW > 0.05 and toeH > 0.05` ——
               那是第二把尺：「截面算不算一个正数」由导出侧 `_box_float`（> 0.02，读不到
               或非正就在写规格表那一步抛，Max 根本不会被启动）规定，而早帧那一道没有这两个
               门槛。于一根脚趾落在 0.02~0.05 之间时：早帧收、终帧按这里跳过，而下面那句
               `fit=false` 只能把这次**合法的**缺席报成红 —— 与 #127 那两把尺互相矛盾同源。 */
            local fitted = false
            if cnode != undefined and ballPos != undefined and tipPos != undefined \
               and toeLen > 0.001 then
            (
                /* 与早帧那一道、与画线/链向同一个参考方向（理由见 脚趾两段 里那段）。 */
                local dir = mmd2maxBoxWantDir cnode.name ballPos ballPos tipPos
                if dir == undefined do dir = normalize (tipPos - ballPos)
                /* 早帧那笔盒账先抄下来：FitCrossSection 会把同一根骨的行覆盖掉。 */
                local axEarly = mmd2maxBoxAxisOf cnode.name
                mmd2maxLogLocalBox ("趾盒终帧前 " + cName) cnode
                local cs = mmd2maxFitCrossSection cnode dir toeW toeH
                if cs == undefined then
                    mmd2maxLog ("  趾盒 " + cName + " 长度轴读不到，截面未收")
                else
                (
                    local ai = cs[7]
                    local before = mmd2maxLocalExtent cnode ai
                    local rL = mmd2maxFitBoneExtent cnode ai toeLen
                    local msg = "  趾盒 " + cName + " 趾长 " + (before as string)
                    msg = msg + " -> " + (rL[1] as string)
                    msg = msg + " 目标=" + (toeLen as string)
                    msg = msg + " 截面 " + (cs[5] as string) + "/" + (cs[6] as string)
                    msg = msg + " -> " + (cs[1] as string) + "/" + (cs[2] as string)
                    msg = msg + " 宽/高目标=" + (toeW as string) + "/" + (toeH as string)
                    msg = msg + " 宽高轴=" + (cs[3] as string) + "/" + (cs[4] as string)
                    msg = msg + " 长度轴=" + (ai as string)
                    msg = msg + " 早帧轴=" + (axEarly as string)
                    msg = msg + " 迭代=" + (rL[2] as string)
                    if rL[3] do msg = msg + "(长度对缩放没反应，已停)"
                    if cs[10] or cs[11] do msg = msg + "(宽高对缩放没反应，已停)"
                    mmd2maxLog msg
                    mmd2maxLogLocalBox ("趾盒终框 " + cName) cnode
                    fitted = true
                    toeBoxFinal += 1
                )
            )
            else
            (
                /* 走到这里一根也没动：这根趾骨的节点找不到，或者脚段的
                   脚掌位置 / 鞋尖 / toe_len 三样里读不出东西来（上面那道守卫）。
                   以前这一支**一句都不说**：一双脚跳过一根，日志只剩「最终帧重收 1 根」，
                   而那一句读起来和「本来就该收 1 根」一模一样。 */
                mmd2maxLog ("  趾盒 " + cName + " 终帧没重收：节点或脚段列读不出来")
            )
            /* 每根趾骨一行，跳过的报 false 而不是不出这一行：`pass/ran/toe_box_final 1`
               无论跳过几根都成立，所以「终帧这一刀没落下去」以前只在日志里看得见，
               而 `骨盒回读` 的期望值来自导出表、与哪一帧量的无关，抓不到「留的是早帧那次盒」。 */
            mmd2maxReport "toe_box_final" "fit" cName fitted
            toeBoxTried += 1
        )
    )
    mmd2maxLog ("趾盒收尾：最终帧重收 " + (toeBoxFinal as string) + "/" \
        + (toeBoxTried as string) + " 根趾盒截面")
    mmd2maxReport "pass" "ran" "toe_box_final" 1

    /* ===== 趾盒落地（#134 加、#135 翻默认开）=====
       MMD2MAX_TOE_GROUND=true（默认）时把每根 Toe0 的 pivot 竖直搬到「盒底世界 z=0」；
       false 回滚贴鞋、整段跳过。
       探针 probes/toe_ground.txt D 段实证：biped.setTransform #pos 搬 pivot -2cm，盒底世界 z
       也跟着 -2cm（boxMinDelta==pivotDelta），盒随 pivot 刚体走；而对象偏移挪不动盒（B 段=0）。
       所以「盒底落地」只能搬 pivot —— 任务原设想的「不动 pivot」被探针否掉了。
       代价摆出来：Toe0 pivot 离开 PMX つま先 ⇒ segment/foot_chain 的 Toe0 链偏离 PMX 靶
       （Toe0 是短骨，placement/struct_dir 的回读清单不含它；第 134 轮 true 态实测
       segment/len_cm +0.866cm 与 foot_chain/chain_angle_deg +6.5°，两只脚各一行）。
       #135 用户拍板落地为默认后，这两条在落地态由 Python 降 report（数字照打印、不藏），
       因为偏离是用户选择的落地形态的定义、不是缺陷；贴鞋态仍 gate/lock。
       排位：toe_box_final 之后（盒截面已收完，搬的是最终盒）、figure_exit 之前（仍在 Figure 模式，
       setTransform #pos 这一刻才改骨盒位置；探针 D 是退出 Figure 后读的，盒仍停在搬后位）。
       只搬竖直分量 newPivot = pivot - [0,0,盒底z]，水平不动。落地质量由后面的 toe_shoe 尺
       （ground_gap_cm gate 期望 0）回读判定，这里只搬 + 留痕，不自判（规则 II）。 */
    if MMD2MAX_TOE_GROUND do
    (
        local grounded = 0
        for item in limbs do
        (
            if item.count >= 10 and item[5] == "foot" do
            (
                local cName = item[2]
                local cnode = getNodeByName (root.name + (substring cName 7 -1)) exact:true
                if cnode != undefined do
                (
                    local aabb = mmd2maxWorldBoxAABB cnode
                    if aabb != undefined then
                    (
                        local boxMinZ = aabb[1].z
                        local p0 = cnode.transform.translationpart
                        local gErr = ""
                        try ( biped.setTransform cnode #pos (p0 - [0.0, 0.0, boxMinZ]) false ) \
                            catch ( gErr = (getCurrentException() as string) )
                        local aabb2 = mmd2maxWorldBoxAABB cnode
                        local afterZ = if aabb2 != undefined then aabb2[1].z else -1.0
                        local gmsg = "  趾盒落地 " + cName + " 盒底z " + (boxMinZ as string)
                        gmsg = gmsg + " -> " + (afterZ as string) + "（目标0）"
                        if gErr != "" do gmsg = gmsg + " 搬pivot抛错=" + gErr
                        mmd2maxLog gmsg
                        grounded += 1
                    )
                    else mmd2maxLog ("  趾盒落地 " + cName + " 跳过：盒世界包围盒读不到")
                )
            )
        )
        mmd2maxLog ("趾盒落地：搬了 " + (grounded as string) \
            + " 根 Toe0 pivot（MMD2MAX_TOE_GROUND=true 默认，落位代价由 segment/foot_chain 的 Toe0 链 report 摆出）")
        mmd2maxReport "pass" "ran" "toe_ground" 1
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
                /* 这两个数与转角 pass 逐字同源（比的就是 mmd2maxBoxWantDir 写进骨轴的
                   方向），所以它**只记日志、不报 verdict** —— 恒等式当成 gate 就是假验收：
                   MMD2MAX_FOOTBOXFLAT（true = 压成水平轴，与导出侧 FOOT_BOX_FLAT 同真同假）
                   #28 那版在这里压平是为了让「刚修平的脚」读出 0 度，代价是盒子被钉成
                   水平的板、前端离趾骨盒还差 5.6cm 高（第十二轮藏网格特写拍到）。
                   地面保护统一收在 mmd2maxBoxWantDir 的 45 度斜度上限里；Toe0 这一行
                   也必须走同一个函数，否则翻回滚开关时「写」变了、「读」没变 —— 读侧
                   留成日志正是为了翻开关时这一行还能和截图对时序。 */
                local yawF = mmd2maxLogBoneDir fName (mmd2maxBipedAxes fnode) \
                    (mmd2maxBoxWantDir fName ankleTgt toeTgt tipPos)
                local yawT = mmd2maxLogBoneDir cName (mmd2maxBipedAxes cnode) \
                    (mmd2maxBoxWantDir cName toeTgt toeTgt tipPos)
                /* 一行结论，方便和用户的截图对齐：正负号 = 朝哪边偏 */
                mmd2maxLog ("  脚骨偏航结论 " + fName + " 骨=" + (yawF as string) \
                    + "度 趾=" + (yawT as string) + "度")
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
                    /* 单向尺（#139）：ggap>0 才是**空气缝**（#41 那张「牙签 + 悬空
                       趾骨盒」截图），ggap<0 是父盒盖过子关节、两块盒子更连贯，不是断开。
                       旧写法用 abs(ggap) 把负值也判成「没接上」，与上面注释里写明的正负
                       含义自相矛盾：#135 的趾盒落地把 Toe0 pivot 抬 0.71cm，而 #139 把
                       Toe0 前移到鞋长 58.6% 后 Foot 盒长度轴翻到世界 -Z，于是落地那 0.71cm
                       第一次直接落在长度轴投影上 ⇒ 读到 ggap=-0.712906、端面覆盖=true。
                       那不是缺口，是盒盖过关节。正向空气缝的闸与 gap_covered 一概不动。 */
                    if ggap > MMD2MAX_GAPTOL or not cover then
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
            /* 带上父骨此刻的 #scale：`肢段 …  缩放后=` 是拟合那一刻的读数，这一行是最终
               读数。两者不一致时，这列数是唯一能分开「有人后来改了 scale」与「pivot 被
               别的骨带着走」（scale 没变但间距变了）的证据 —— 没有它只能猜。 */
            smsg = smsg + " 父scale=" + ((mmd2maxBipedScale pnode) as string)
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
                            local stubBone = (distance headPos tailPos) < MMD2MAX_STUBCM
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

    /* 穿鞋检测（#134）：紧跟骨盒回读，仍在 skin_apply 之前（网格是绑定姿态鞋）。
       只读不写，量趾盒底世界 z 与盒足迹内鞋底最低顶点，报 toe_shoe 两列；判定在 Python 侧。
       注册表 toe_shoe requires figure_exit/mesh_import、writes toe_shoe，排在 box_verify 之后。 */
    local toeShoe = mmd2maxVerifyToeShoe root limbs
    mmd2maxLog ("穿鞋检测：量到=" + (toeShoe[1] as string) \
        + " 读不到=" + (toeShoe[2] as string) \
        + "（盒底/鞋底世界 z，判定在 Python 侧）")
    mmd2maxReport "pass" "ran" "toe_shoe" 1

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

fn mmd2maxVerifyAccessoryDirs boneNodes =
(
    /* 附属骨方向回读（#66 的验收）。
       为什么必须回读而不是「写入时当然对」：bones.tsv 的 tail-head 是这根骨该指哪的
       唯一真值，胸部骨那一刀的 tail 更是**从网格量出来的乳头点**
       （mmd2max/export_obj.py 的 _aim_breast_bones）。但建完骨之后还有三道能挪它的刀：
       改挂（.parent 一设，Max 就把变换按父级重新解释，所以要写回 worldTM）、
       物理代理改父级、以及任何后来者「顺手」转一下附属骨。写入那一刻的方向不描述
       保存下来的场景 —— 和 M4b/M4c 把蒙皮与骨盒改成回读是同一件事。
       短骨（|tail-head| < MMD2MAX_STUBCM）不计：PMX 里那是显示 stub（香菱 右手首 0.38cm），
       方向本身没意义。这条门限与 结构向 复核、与 Python 侧的
       verdicts.ACCESSORY_STUB_CM 是**同一个数**（数值住在 targets.STUB_CM，经 CFG 下发），
       不再两侧各抄一份字面量。
       subject 用「行号|骨名」而不是骨名：4893 行改名重名时 Max 会自动加后缀，
       那时 nodes[i].name 已经不是 bones.tsv 里那个词，两边就对不上账了；行号是
       表的位置，唯一、两边同源，日志里也还能看见名字。
       期望值不在这段脚本里（M2b：Max 只报数，判定在 Python 侧）。 */
    local lines = mmd2maxReadTable MMD2MAX_BONES
    local rows = mmd2maxRows lines
    /* 行数必须与 boneNodes 一一对应：subject 里的行号就是从 boneNodes 的下标来的，
       两边错位就是拿 A 骨的方向去验 B 骨（mmd2maxApplySkin 里同一道检查的理由）。 */
    if rows.count != boneNodes.count do
        throw ("附属骨回读：bones.tsv 有 " + (rows.count as string) + " 行，boneNodes 有 " \
               + (boneNodes.count as string) + " 个，骨号对不上，读数全是垃圾")
    local cName = mmd2maxColPos lines "bone_name"
    local cHeadX = mmd2maxColPos lines "head_x"
    local cHeadY = mmd2maxColPos lines "head_y"
    local cHeadZ = mmd2maxColPos lines "head_z"
    local cTailX = mmd2maxColPos lines "tail_x"
    local cTailY = mmd2maxColPos lines "tail_y"
    local cTailZ = mmd2maxColPos lines "tail_z"
    local measured = 0
    local overTol = 0
    local readFail = 0
    local stubs = 0
    local worstAng = 0.0
    local worstSubj = "-"
    local pivotMax = 0.0
    local pivotSubj = "-"
    for i = 1 to boneNodes.count do
    (
        local nd = boneNodes[i]
        /* 复用 Biped 节点的人形骨不在这里量：它们的朝向由 struct_dir / box_rb 那两组
           终局复核管（Biped 骨盒的「方向」是 #dir/#scale 的事，不是 transform.row1）。 */
        if (classOf nd) != BoneGeometry do continue
        local p = rows[i]
        if p.count < cTailZ do
        (
            readFail += 1
            continue
        )
        local head = [(p[cHeadX] as float), (p[cHeadY] as float), (p[cHeadZ] as float)]
        local tail = [(p[cTailX] as float), (p[cTailY] as float), (p[cTailZ] as float)]
        local want = tail - head
        if (length want) < MMD2MAX_STUBCM do
        (
            stubs += 1
            continue
        )
        local tm = nd.transform
        /* 骨骼的**长度轴是局部 X**（4862 行建骨时传的就是 head -> head+x*len，
           第三参数 zaxis 只定滚转）。量错轴就是一条恒等式反过来的假红：
           垂直于骨的那根轴与 tail-head 恒读 90 度。 */
        local ang = mmd2maxVecAngleDeg tm.row1 want
        if ang < 0.0 do
        (
            readFail += 1
            continue
        )
        local subj = ((i - 1) as string) + "|" + (p[cName] as string)
        local err = distance tm.translationpart head
        measured += 1
        mmd2maxReport "bone_rb" "dir_deg" subj ang
        if ang > worstAng do
        (
            worstAng = ang
            worstSubj = subj
        )
        if err > pivotMax do
        (
            pivotMax = err
            pivotSubj = subj
        )
        if ang > MMD2MAX_DIRTOL do overTol += 1
    )
    /* 取景框之外也要能复盘：这行把「最大夹角在哪根骨」直接写进日志，
       超标时不用先把 500 行报告 sort 一遍才知道去看哪根。 */
    mmd2maxLog ("附属骨回读：量到=" + (measured as string) \
        + " 短骨不计=" + (stubs as string) \
        + " 读不到=" + (readFail as string) \
        + " 夹角>" + (MMD2MAX_DIRTOL as string) + "度=" + (overTol as string) \
        + " 最大夹角=" + (worstAng as string) + " 在=" + worstSubj \
        + " 最大pivot偏差=" + (pivotMax as string) + " 在=" + pivotSubj)
    mmd2maxReport "bone_rb" "measurable" "-" measured
    mmd2maxReport "bone_rb" "short_skipped" "-" stubs
    mmd2maxReport "bone_rb" "read_fail" "-" readFail
    mmd2maxReport "bone_rb" "over_tol" "-" overTol
    mmd2maxReport "bone_rb" "worst_deg" "-" worstAng
    mmd2maxReport "bone_rb" "worst_bone" "-" worstSubj
    mmd2maxReport "bone_rb" "pivot_max_cm" "-" pivotMax
    mmd2maxReport "bone_rb" "pivot_max_bone" "-" pivotSubj
    true
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
                catch () -- 允许失败：这三条是 BoneGeometry 的显示胖瘦/锥度，骨盒回读与 bone_rb 量的是世界变换与局部轴，不读显示属性；写不进去只是视口里那根骨按 Max 默认粗细显示
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
                catch ( mmd2maxLog ("!! 附属骨挂父这一对写没走完 " + boneName + "：父级可能已改而世界位置没写回（这一根会飞），" + (getCurrentException() as string)) )
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
                catch ( mmd2maxLog ("!! 根附属骨挂 Biped 根这一对写没走完 " + boneName + "：可能已改父级而位置没写回，" + (getCurrentException() as string)) )
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
            /* 一次失败只许计一次：旧写法 catch 里 `failedBones += 1` 之后，紧接着的
               `if not added` 又加一次、名字再 append 一遍 —— 抛出去那行「有 N 根骨骼加入
               Skin 失败」因此永远是真实数的一倍，名字表里每根出现两次。数字是错的，但
               判据本身没错（>0 就 throw），所以线上没有任何东西会发现它。 */
            try ( skinOps.AddBone skinMod boneNodes[i] 0; added = true ) \
                catch ( mmd2maxLog ("  AddBone 异常：" + boneNodes[i].name) )
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
                        /* 权重正好 0.0 的不写：实测（海灯节 332 条）ReplaceVertexWeights
                           收到 0.0 会把这条链路整条丢掉、回读连骨号都不留，写了等于没写，
                           还会让写入侧的 蒙皮覆盖 计数虚高。导出侧已挡，这里再挡一层，
                           两侧都不假设对方挡住了。 */
                        if w > 0.0 and g >= 0 and g < boneNodes.count and globalToSkin[g + 1] > 0 do
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
    local zeroLinks = 0
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
                        /* 权重正好 0.0 的对不进期望（与写入侧 mmd2maxApplySkin 同一条规则）：
                           Max 的 Skin 不持有这种链路，把它算进期望就等于要场景里去挂一条
                           挂不住的边 —— 海灯节就是这样的 332 条把 links_missing /
                           link_diff_max / link_mismatch_bones / links_read 四行一起拖红的。
                           单独计 zeroLinks 并报成 `zero_links`：这把尺盯着「权重表里又出现
                           了 Max 存不下的对」（陈旧文件/手改表），不然那种事只会伪装成
                           一次看不懂的链路缺失。 */
                        if w > 0.0 then
                        (
                            local sid = globalToSkin[g + 1]
                            append ids sid
                            append ws w
                            wsum += w
                            expLinks[sid] = expLinks[sid] + 1
                            linksExpected += 1
                        )
                        else zeroLinks += 1
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
    mmd2maxReport "skin_rb" "zero_links" "-" zeroLinks
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
    try ( pluginManager.loadClass Morpher ) catch () -- 允许失败：这一句是 best-effort 预热，插件早就加载好时它自己也抛；可用与否由下面 haveWM3 直接问 WM3_MC_BuildFromNode 在不在，那才是判据
    try ( pluginManager.loadClass MorpherMXS ) catch () -- 允许失败：同上一句，只是预热；可用与否的判据是下一行 haveWM3 的读数，不是「loadClass 没报错」
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
        try ( hide target ) catch ( mmd2maxLog ("  表情副本没藏成：用户会在场景里看到一个和自己重叠、选不中也不受 Biped 控制的网格（" + (name as string) + "）") )
        /* copy 会把 Skin / Morpher 一并复制过来：必须清空修改器栈，
           否则目标网格会跟着 Biped 变形，morph 偏移就套在变形后的顶点上。 */
        try ( while target.modifiers.count > 0 do deleteModifier target 1 ) catch ( mmd2maxLog ("  表情副本修改器没清空（偏移会套在变形后的顶点上）：" + (name as string) + " " + (getCurrentException() as string)) )
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
            try ( delete target ) catch ( mmd2maxLog ("Morpher 残骸删不掉（" + (name as string) + "）：这个临时网格留在场景里，" + (getCurrentException() as string)) )
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
        try ( if (WM3_MC_GetTarget morpherMod k) != undefined do chUsed += 1 ) catch () -- 允许失败：读接口通不通由下面「通道明细」那行的 ?/N 区分（抛错=?、没目标=N），而 chUsed 偏低本身会触发「建通道都没抛异常但读不回」那条警告
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
        for t in targets do ( try ( delete t ) catch ( mmd2maxLog ("Morpher 清理：临时网格删不掉，场景里会留下可见残骸 " + (try (t.name) catch ("?")) + "：" + (getCurrentException() as string)) ) )
        try ( deleteModifier meshObj morpherMod ) catch ( mmd2maxLog ("Morpher 清理：空修改器没删掉，.max 里会留一个坏 Morpher：" + (getCurrentException() as string)) )
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
    /* 通道建好后，临时副本网格已经「烘焙」进 Morpher 通道，源节点不再被引用，
       必须删掉 —— 否则场景里会留下几十个和本体完全重叠、点不中也不受 Biped 控制的
       网格（Scene Explorer 里一堆日文表情名，看着像模型坏了）。旧版本把删除只放在
       「建失败」分支，成功时一律留着，正是用户看到的 clutter。 */
    local cleaned = 0
    for t in targets do
    (
        try ( delete t; cleaned += 1 ) catch ( mmd2maxLog ("Morpher 残骸删不掉（" \
            + (try (t.name) catch ("?")) + "）：" + (getCurrentException() as string)) )
    )
    mmd2maxLog ("Morpher 临时副本已清理：" + (cleaned as string) + " / " + (targets.count as string) + " 个")
    true
)

/* ================= 物理刚体 & 关节 =================
   PMX 物理（刚体+关节）在 Max 端 best-effort 落地：每根刚体生成一个可见代理
   （Box/Sphere/Capsule，按形状与尺寸），挂到对应骨架骨；关节位置放 Dummy 标记，
   限位/弹簧记到注释属性。真实动力学需用户再开启 MassFX（这些代理已带正确的
   位置与朝向，可直接作为 MassFX 的刚体源）。

刚体朝向的数学不在这里做，全部在 Python（mmd2max/coords.py 的 rigid_rotation_max），
   MAXScript 这一侧只剩 `proxy.rotation = (eulerAngles a b c)` 一条（探针 87b 的 4c 行
   实测过它的写入与回读）。原因是「在 Max 里自己算共轭再拆回欧拉」要用的四条写法
   在本机 Max 2022 每一条都抛（探针 87 / 87b，test_runs/probe87/probe_api*.txt）：
       transpose <matrix3>             → No "transpose" function for (matrix3 ...)
       <Angle4>.order = #xyz           → Unknown property: "order"
       eulerAngles a b c order:#xyz    → Argument count error: EulerAngles wanted 3, got 6
       translate <point3>              → No "Translate" function for [1,2,3]
   再加一条单位坑：`eulerAngles` 吃**度**，PMX 的刚体欧拉是弧度（探针 87 的 F/G 行）。
   早期版本在这里放了 `fn mmd2maxCoordMatrix` + `fn mmd2maxRotToMax`（M*R*Mᵀ 共轭），
   第 64~66 轮连踩三条，读到 `旋转失败=254` —— 254 根代理一根都没转上，正是用户
   截图里「裙摆刚体竖着排成一块板」。这两个函数已删；口径与推导记在 coords.py。 */

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
    /* Python 已经把 PMX 弧度欧拉换成了「直接写给 node.rotation 的度数」（含手性共轭
       与 Max 节点口径反转）。原始弧度列仍然读：只为诊断行能把「PMX 给的是什么」
       和「我们写进去的是什么」并排报出来。 */
    local cRRMaxX = mmd2maxColPos lines "rigid_rot_max_x"
    local cRRMaxY = mmd2maxColPos lines "rigid_rot_max_y"
    local cRRMaxZ = mmd2maxColPos lines "rigid_rot_max_z"
    local cRMode = mmd2maxColPos lines "rigid_physics_mode"
    /* #126：这一格是 Python 按 `rigid_bone` 那个号**该对应到的场景节点名**
       （口径在 semantic.node_name，重名骨留空）。名字必须在导出侧算：让 Max 自己去
       bones.tsv 里查「号 → 名字」，等于用它想验的那条链的另一半来当尺。 */
    local cRBoneNode = mmd2maxColPos lines "rigid_bone_node"
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
    /* 分层失败只影响「363 个刚体能不能整层选中/隐藏」，不影响代理本身在不在；但最后那行
       汇总会说「刚体在层 mmd2max_physics」，所以这一句没做成必须留字，不能让汇总替它说谎。 */
    try ( layerRB = LayerManager.newLayerFromName "mmd2max_physics" ) catch ( mmd2maxLog ("!! 物理刚体层建不出来，下面那句「刚体在层 mmd2max_physics」不成立：" + (getCurrentException() as string)) )
    try ( layerJT = LayerManager.newLayerFromName "mmd2max_joints" ) catch ( mmd2maxLog ("!! 关节标记层建不出来，700 个 Dummy 会混在主层里：" + (getCurrentException() as string)) )
    local created = 0
    local rigidFail = 0
    local rotFail = 0
    local centerFail = 0
    /* 胶囊建不出来会**静默退成圆柱**（下面那个 catch 有体，check_ms 不拦）。
       探针 92 第 6 步实测：`Capsule radius:2 height:8` 世界盒 (4,4,12)，
       `Cylinder radius:2 height:8` 只有 (4,4,8) —— 退路天生短 2×半径。
       不数这一格，换一台机器（或某个 Max 版本）Capsule 类不存在时，
       72 根胶囊整体短一截而汇总行一个字都不动，看起来仍然「代理 254（失败 0）」。 */
    local capFalls = 0
    local offFail = 0
    local rotNonZero = 0
    local layerFail = 0
    /* #89/#90 的格子：`.center` 与 `.rotation` 都是「代理此刻自己的世界量」，挂父之后
       这块几何到底还在不在 PMX 那个点，它们不会替后面的事说话。第 70 轮量到的读数是
       `挂父后世界偏心=4 最大=94.6242`，而**探针 90 把「挂父」这一步本身洗掉了**：
       父骨平移+绕 Y 转 90 度（C1）与父骨 scale=[2,2,2]（C2）两种情况下，
       `只设 .parent` 之后的 center / tm[4] / 世界盒与写 center 之后**逐行相同**
       （d(形心,目标)=5.3e-06，世界盒尺寸一分没涨）。
       所以这一格现在分三层归属，谁搬的代理要说得出名字：
         linkShift   —— 挂父那一步把形心搬走多少（探针说该是 0，不是 0 就是新事实）
         worldFail   —— 挂完之后形心离 PMX 点多远（>1 厘米才数）
         afterMove   —— 整个循环跑完之后再读一次：循环外还有谁动了代理
       「父骨 scale 放大代理」这条**已被探针 90 否掉**，不再照它写注释。 */
    local worldFail = 0
    local worstWorld = 0.0
    local worstName = "—"
    local linkShift = 0
    /* #131 归因读数（第 104 轮林尼 RB_1：形心搬 0.174527cm，父骨=Bip001 Neck，
       而香菱 / 克雷薇同一格读 0）。探针 90 的 C1/C2 用的是 Box 父 + Box 子，
       两步都读不出搬动，所以真跑里搬的那一下到底是①`.parent=` 自己、②写回 transform、
       ③父骨 TM 在挂父那一刻变了、还是④代理朝向被转（形心离枢轴 9.7cm，转 1 度就搬 0.17cm）
       —— 四个候选得分开量，不能靠这一轮之后猜。只加读数，不改任何写。 */
    local dMidWorst = 0.0
    local dEndWorst = 0.0
    local rowWorst = 0.0
    local rowWorstName = "—"
    local parWorst = 0.0
    local parWorstName = "—"
    /* 收尾重发那两句（朝向 / 定心）抛错的根数：抛了就是「这一族写没做完」，
       与「拽回了但没拽到」是两种病，汇总行要分开说。 */
    local pinFail = 0
    /* #132 的两格。orientWorst 与上面那格 rowWorst 量的**不是同一件事**：
       rowWorst 比「挂父前的世界TM vs 挂父后的世界TM」，两端都是 Max 自己的读回，
       所以它看不见「早帧那一句自己就写歪了」；orientWorst 比「Python 给的三个度数
       造出的应当世界矩阵 vs 最终世界矩阵」，两端不同源，早帧写歪也照样红。
       靶的造法是实测的（探针 99 A，三轴同动 30/40/50）：节点 TM 的线性部分 =
       `(eulerAngles …) as matrix3` 的**转置**（弦长 1.73776e-07 对 1.7331）。
       `transpose` 是死调用，所以按行/列下标手转。
       rotCtrlNative 数的是修法本身（四元数原生控制器）。为什么机制也要一把尺：
       orientWorst 只在「这一具的父骨姿势正好把局部旋转顶到万向锁」时才响 ——
       香菱/克雷薇/海灯节 不撞死点，控制器退回去它们照样全绿，正是 #125/#126 那类绿盲。 */
    local orientWorst = 0.0
    local orientWorstName = "—"
    local rotCtrlNative = 0
    local rotCtrlFail = 0
    local afterFail = 0
    local afterWorst = 0.0
    local afterName = "—"
    local parentScaled = 0
    /* 挂父那三层条件（`boneNew >= 0` / `< boneNodes.count` / 节点非 undefined）任何一层落空，
       代理就静静钉在场景根上 —— 而这一族**现有的尺一根都抓不到**：创建计数照样 +1，
       `off_center` / `world_failed` / `after_failed` 量的是形心在不在 PMX 那个点上，
       而一个没挂父的代理在绑定姿态下正好就在那儿（那位置是我们自己按 PMX 摆的）。
       于是「254 根全绿」和「254 根一根都不跟骨动」在这几格里长得一模一样，
       差别只有把时间轴拖起来才看得见 —— 正是本项目认的那类绿盲。
       先例：附属骨那三条 `accessory/on_biped|on_bone|no_parent`（:5789），那边有、这边一直没有。
       口径：只认**回读** `proxy.parent`，不认「我那条 `.parent=` 有没有抛」——
       「写了但没生效」是不抛错的那一类，只有回读看得见。 */
    local rigidParented = 0
    local rigidRooted = 0
    /* #126 的格子：`rigid_parented` 说「挂上了一根骨」，不说「挂的是表里那一根」。
       期望名来自表（`rigid_bone_node`），实读名来自场景（`proxy.parent.name`），
       两边同源不了：一个在 Python 按行序算，一个在 Max 按父级关系读。
       `nameSkipped` = 挂了骨但表里那个名字是空的（重名骨，见导出侧 _bone_node_names），
       这几根**不计**，所以闸的期望值也少数它们 —— 两边同一个口径，别在这里偷偷放宽。 */
    local rigidNamed = 0
    local nameWrong = 0
    local nameSkipped = 0
    /* 循环后再读一次要用「这根代理本该在哪儿」，与 rigids 同下标存目标点。 */
    local rigidWant = #()
    for i = 1 to 4096 do append rigidWant undefined
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
                local ax = p[cRRMaxX] as float
                local ay = p[cRRMaxY] as float
                local az = p[cRRMaxZ] as float
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
                       探针 89 的 B1 实测 Max 的 `height:` **不含两端半球**
                       （radius:1 height:8 建在 z=30 → 世界盒 z[30,40]，总长 10 = 8 + 2×1），
                       所以圆柱段直接写 2×半高，两顶帽子由 Max 自己加，总长正好是 MMD 的
                       2×(半径+半高)。早期版本按「height 含两端」这个没测过的口径写
                       `ey*2 + radius*2`，而胶囊的 PMX size.z 恒为 0 ⇒ ey=0 ⇒
                       圆柱段实际被写成 2×半径，72 根胶囊每一根都短了半根。 */
                    local radius = amax ex 0.001
                    local hgt = amax (ez * 2.0) 0.002
                    try ( proxy = Capsule radius:radius height:hgt pos:[px,py,pz] ) \
                        catch (
                            proxy = Cylinder radius:radius height:hgt pos:[px,py,pz]
                            capFalls += 1
                            if capFalls <= 3 do
                                mmd2maxLog ("胶囊退成圆柱 RB_" + (idx as string) \
                                    + "：这块天生短 2×半径（探针 92 实测 (4,4,8) vs (4,4,12)）：" \
                                    + (getCurrentException() as string))
                        )
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
                    /* #132：旋转控制器换成四元数原生，而且必须在**任何写之前**。
                       默认的 Euler_XYZ 存不下万向锁上的旋转 —— 探针 97 的 X2 把正确的
                       局部 quat 直接写进它的 `.value`，读回来是**另一个** quat
                       （写 0.706299 -0.0337837 …、存 0.705466 -0.0481504 …），
                       弦长 0.0407014，比产品原来那 0.0183069 还差。所以这不是「写法不对」，
                       是那个控制器本身的表示能力不够，重发多少句都拽不回来。
                       死点也不是 PMX 数据带来的：探针 98 离线量了 6 具 1956 根刚体，
                       导出侧死点 0 根、欧拉往返最大误差 4.834e-10。死点是**父骨姿势**造出来的：
                       林尼那根 RB_1_首 挂在摆过姿势的 Bip001 Neck 下，局部矩阵第 1 行
                       = 父TM 第 1 列 = (1.365e-07,-2.634e-06,1) ⇒ 局部欧拉 Y = -89.9999，
                       正是 XYZ 死点（Max 是行矢量约定：世界 = 局部 × 父，探针 96 W1 逐字核对）。
                       换 Bezier_Rotation 之后探针 97 X1 走完整产品顺序**每一步**弦长都是 0.0，
                       连裸 `.parent=` 都不搬了（d=2.45756e-06）；TCB_Rotation 同样 0.0（X3）。
                       探针 99 B1/B2/B3 三种形状都换得上、写得进、形心不被搬走、世界盒照样读得出。
                       X4 还量了「已经歪了的那根」：只换控制器不重发仍是 0.018307（旧的坏 quat
                       被带过来了），所以顺序必须是建出来就换，不能留到收尾再补。 */
                    local rotCtrlOk = false
                    local rotCtrlErr = ""
                    try
                    (
                        proxy.rotation.controller = Bezier_Rotation()
                        rotCtrlOk = true
                    )
                    catch ( rotCtrlErr = (getCurrentException() as string) )
                    if not rotCtrlOk do
                    (
                        rotCtrlFail += 1
                        if rotCtrlFail <= 3 do
                            mmd2maxLog ("刚体的旋转控制器没换成四元数原生 RB_" + (idx as string) \
                                + "：这块代理留在 Euler_XYZ 上，父骨姿势一旦把局部旋转顶到万向锁就会歪约 1 度，" \
                                + rotCtrlErr)
                    )
                    /* PMX 里这根刚体到底有没有朝向。三根轴全是 0 的代理「转不上也无所谓」，
                       所以汇总行要能看出「旋转失败=0」是转好了还是根本没有朝向可转。 */
                    if (rx != 0.0) or (ry != 0.0) or (rz != 0.0) do rotNonZero += 1
                    /* 朝向：只写 Python 算好的那三个度数（见本节开头那段注释）。
                       第 64 轮第一次实跑读到 `旋转失败=254`，而代理一共 254 个 ——
                       也就是「每一根都没转上」，当时一个字都读不到，因为那个 catch
                       只写了 `rotOk = false`；补上报错文字之后第 65 轮立刻给出根因。
                       只数不记文字 = 下一轮又要重新猜一遍是谁死的。 */
                    local rotOk = false
                    local rotErr = ""
                    try
                    (
                        proxy.rotation = (eulerAngles ax ay az)
                        rotOk = true
                    )
                    catch ( rotErr = (getCurrentException() as string) )
                    if not rotOk do
                    (
                        rotFail += 1
                        if rotFail <= 3 do
                            mmd2maxLog ("刚体没转上（这块代理保持竖直）RB_" + (idx as string) + "：" + rotErr)
                    )
                    /* 前 3 根「PMX 确实给了朝向」的代理留一行三对照：
                       PMX 弧度 → 写进 node.rotation 的度数 → Max 读回的 quat。
                       手性/镜像这类错是看不见的（`旋转失败=0` 也可能是每一根都朝反），
                       必须留下能对截图的数字。 */
                    if rotOk and (rotNonZero <= 3) do
                        mmd2maxLog ("刚体朝向 RB_" + (idx as string) \
                            + " PMX弧度=(" + (rx as string) + "," + (ry as string) + "," + (rz as string) + ")" \
                            + " 写入度=(" + (ax as string) + "," + (ay as string) + "," + (az as string) + ")" \
                            + " 读回=" + (proxy.rotation as string))
                    /* 定心排在旋转之后，而且**三种形状都要写**。
                       旧注释（探针 89 的 C1「球对称 ⇒ 只有球不写」）在第 71 轮被探针 91 否掉：
                       球虽然 pivot 就在球心，但给它写 `.rotation` 会把**整个节点**绕
                       **世界原点**画一段弧 —— 球心、pivot、.pos 三个一起走（探针 91 的 A2/B2，
                       与第 70/71 轮的读数逐字相同）：
                         A2 半径 80.9 的球写 3.65 度 → 走 5.28 厘米（= 80.9 × 3.65° 的弧）
                         B2 半径 48.6 的球写 158.8 度 → 走 94.6 厘米
                       而 `.center` 是**世界**量（探针 89 A3 + 探针 91 B3）：转完再写一次
                       center = PMX 点，球就精确回到该在的位置（B3 量到 center=tm4=pos=期望，
                       |A-B|=0，半径与朝向都没被改写）。
                       方块与胶囊原本就靠这一步（它们的 pivot 在底角，探针 87b 第 7 行），
                       现在球也走同一步 —— 第 71 轮 `挂父后世界偏心=4` 那 4 根全是球，
                       就是被这条排除规则漏掉的。 */
                    (
                        try ( proxy.center = [px, py, pz] )
                        catch ( centerFail += 1; if centerFail <= 3 do mmd2maxLog ("刚体中心没挪到形心（转完这段弧没被拽回来，这块刚体不在 PMX 那个点上）RB_" + (idx as string) + "：" + (getCurrentException() as string)) )
                        /* 回读：抓住「center 写成功、但那一转把节点整块带离了刚体中心」这种不抛错的错，
                           并给「代理落在刚体中心」这句话一个出处。 */
                        local dOff = (distance proxy.center [px, py, pz])
                        if dOff > 0.01 do
                        (
                            offFail += 1
                            if offFail <= 3 do
                                mmd2maxLog ("转完之后形心不在刚体中心 RB_" + (idx as string) \
                                    + "：偏 " + (dOff as string) \
                                    + " 期望=(" + (px as string) + "," + (py as string) + "," + (pz as string) + ")")
                        )
                    )
                    /* MAXScript 的 and **不短路**：boneNew = -1 时 `boneNodes[0]` 会抛
                       "array index must be positive number"（实跑 362/363 里那 1 个失败
                       就是它）。必须拆成嵌套 if 才安全。 */
                    /* 挂父这一对写 + 收尾重发（#131 改的就是这段的出处）。
                       探针 90 的 C1（父骨=Box，平移 + 绕 Y 转 90 度）与 C2（父骨 scale=[2,2,2]）
                       说「只设 .parent」之后的 center / tm[4] / 世界盒与写 center 之后逐行相同，
                       于是那段把下面这条写回读成「恒等写，留着只为与附属骨那段口径一致」。
                       **第 105 轮在林尼上把这句推翻了**：父骨是 Biped 骨（Bip001 Neck）时
                       `.parent=` 自己就把代理搬走 0.174525cm、朝向转了 1.027 度（这根胶囊的形心
                       离枢轴 9.736cm = 半根胶囊长，转 1 度就搬 0.17cm），而写回那一下只改回
                       2.987e-05cm —— 它拦不住；父骨自己的 TM 在挂父前后逐字节相同（变化=0.0），
                       所以也不是「父骨 TM 陈旧」这一族。
                       香菱 / 克雷薇 / 海灯节 同一格读 0：**为什么它们不响本轮读不出来**，
                       新装的朝向弦长要下一轮才有那几具的数（别把「没响」当「恒 0」写进注释）。
                       修法不靠那条写回，用这一段里已经证活的两句重发，顺序照早帧：
                       先重发朝向（`.rotation` 会把整块绕世界原点画一段弧 —— 探针 91 的 A2/B2，
                       所以它必须排在定心之前），再重发定心（与 :7142 那一句同一个写法、同一个目标点）。
                       写回那行留着不动：附属骨那段（:6003 记 / :6007 写回）用的是同一对写法，
                       两处行为一致才不至于「一份探针解释了两份代码」。 */
                    local centerBefore = undefined
                    try ( centerBefore = proxy.center ) catch ( centerBefore = undefined )
                    local worldTM = proxy.transform
                    local rotBefore = "读不出"
                    try ( rotBefore = proxy.rotation as string ) catch ( rotBefore = "读不出" )
                    local rotAfter = "读不出"
                    local cMid = undefined
                    local cEnd = undefined
                    local parTmB = undefined
                    local parTmA = undefined
                    if boneNew >= 0 do
                        if boneNew < boneNodes.count do
                            if boneNodes[boneNew + 1] != undefined do
                            (
                                try ( parTmB = boneNodes[boneNew + 1].transform ) catch ( parTmB = undefined )
                                try
                                (
                                    proxy.parent = boneNodes[boneNew + 1]
                                    try ( cMid = proxy.center ) catch ( cMid = undefined )
                                    proxy.transform = worldTM
                                    try ( cEnd = proxy.center ) catch ( cEnd = undefined )
                                    /* 收尾重发（#131）：这两句抛错必须记数并记文字 ——
                                       抛了就等于这一族写没做完，下面的 `挂父搬走` 会替它红，
                                       但「红是因为抛错」和「红是因为拽不回」得能分开，
                                       所以两者各留一句（与 #81 的收法同一口径）。 */
                                    try ( proxy.rotation = (eulerAngles ax ay az) ) \
                                        catch ( pinFail += 1; if pinFail <= 3 do mmd2maxLog ("挂父收尾的朝向没重发 RB_" + (idx as string) + "：这块代理的朝向停在 .parent 给的那一度上，" + (getCurrentException() as string)) )
                                    try ( proxy.center = [px, py, pz] ) \
                                        catch ( pinFail += 1; if pinFail <= 3 do mmd2maxLog ("挂父收尾的定心没重发 RB_" + (idx as string) + "：这块代理不在 PMX 那个点上，" + (getCurrentException() as string)) )
                                )
                                catch ( mmd2maxLog ("!! 刚体挂父这一对写没走完 RB_" + (idx as string) \
                                    + "：可能已改父级而世界变换没写回，" + (getCurrentException() as string)) )
                                try ( parTmA = boneNodes[boneNew + 1].transform ) catch ( parTmA = undefined )
                            )
                    /* 挂父**之后**再读一次世界量（#89/#90）。`.center` 是**世界**量：
                       探针 89 的 A3（pivot 在 (10,20,30) 的 Box 写 center=[10,20,30]，世界盒
                       正好以 (10,20,30) 为中心）+ 探针 90 的 C1/C2（挂父之后仍然如此）。
                       探针 90 的 F1 顺手否掉了「球没有 center」这句旧注释 —— Sphere 读得到
                       center=[20,20,20]；下面那个 transform[4] 兜底只在 center 真读不出来时走。 */
                    local wC = undefined
                    try ( wC = proxy.center ) catch ( wC = undefined )
                    if wC == undefined do
                    (
                        try ( wC = proxy.transform[4] ) catch ( wC = undefined )
                    )
                    local pn = "无父骨"
                    local ps = "—"
                    /* 父骨缩放**不能**读 `.scale`：Biped 骨没有这个属性，第 69 轮那 42 条
                       「刚体代理创建失败」全是这一行读出来的 `Unknown property: "scale"`。
                       更要紧的是那条 `父骨带缩放=202` 数的是**读得到 .scale 的那批**（附属骨/Dummy），
                       254 根里挂在 Biped 上的全被 catch 成「父骨没有scale属性」—— 这一格当时是半盲的。
                       探针 90 的 F2 给了活的尺：缩放 = transform 三行的长度，
                       scale=2 的父骨量出 2.0/2.0/2.0，纯旋转的父骨量出 1.0，任何节点都有 .transform。
                       口径要说死：这一格量的是**世界矩阵的行长度**（会不会把挂上来的代理整块放大），
                       不是 Biped 的**段缩放** —— 后者我们为了凑骨长故意写成非 1（第 89 轮
                       `肢段 Bip001 L UpperArm -> L Forearm … [20.8675,…] -> [19.5224,…]`），
                       而同一轮那两根以 UpperArm 为父骨的代理行长度仍读 1.0。两回事，别去「修」。 */
                    local parN = undefined
                    try ( parN = proxy.parent ) catch ( parN = undefined )
                    if parN != undefined do rigidParented += 1
                    if parN == undefined do
                    (
                        rigidRooted += 1
                        /* 点名带表里那个骨号与骨骼总数：下一轮不用重跑就能分清
                           「表写坏了（号在范围外）」和「节点没建出来（号在范围内却读不到）」。
                           与同段别的计数一样封顶 3 条，后面的只数不写。 */
                        if rigidRooted <= 3 do
                            mmd2maxLog ("刚体代理没挂上任何骨 RB_" + (idx as string) \
                                + "：rigid_bone=" + (boneNew as string) \
                                + " 骨骼总数=" + (boneNodes.count as string) \
                                + "（这块代理不跟骨动，仿真时它留在原地）")
                    )
                    if parN != undefined do
                    (
                        try ( pn = parN.name ) catch ( pn = "父骨名字读不出" )
                        /* #126：上一格只说「挂上了一根骨」，这一格说「挂的是表里那一根」。
                           期望名是 Python 从 bones.tsv 那一行算出来后写进 physics.tsv 的，
                           实读名是 Max 从场景的父级关系上读的 —— 中间隔着「表行序 =
                           boneNodes 数组序」这句从来没有读数核过的假设（三处闸只核个数）。
                           表里名字为空 = 重名骨，Max 会给副本加后缀、谁拿裸名没量过，
                           这几根不计（导出侧与这里同一口径，期望值同样少数它们）。 */
                        local wantNode = p[cRBoneNode]
                        if wantNode == undefined do wantNode = ""
                        if wantNode == "" do nameSkipped += 1
                        if wantNode != "" and pn == wantNode do rigidNamed += 1
                        if wantNode != "" and pn != wantNode do
                        (
                            nameWrong += 1
                            /* 点名两个名字：`实挂` 是另一根骨的 PMX 名 = 数组序错位；
                               `实挂` 是期望名 + 后缀 = Max 给重名节点改名，是命名口径的事。
                               两种下一步不同，所以两个名都得打出来。 */
                            if nameWrong <= 3 do
                                mmd2maxLog ("刚体代理挂的不是表里那一根骨 RB_" + (idx as string) \
                                    + "：rigid_bone=" + (boneNew as string) \
                                    + " 期望节点=\"" + (wantNode as string) + "\"" \
                                    + " 实挂=\"" + (pn as string) + "\"")
                        )
                        local parT = undefined
                        try ( parT = parN.transform ) catch ( parT = undefined )
                        if parT != undefined do
                        (
                            /* 名字用 psx/psy/psz：这一段外层的 `sx/sy/sz` 已经是 PMX 的
                               刚体尺寸，就地重名会把「尺寸」换成「父骨缩放」，后面谁用错一个都不抛错。 */
                            local psx = (distance parT[1] [0, 0, 0])
                            local psy = (distance parT[2] [0, 0, 0])
                            local psz = (distance parT[3] [0, 0, 0])
                            ps = "(" + (psx as string) + "," + (psy as string) + "," + (psz as string) + ")"
                            if (abs (psx - 1.0) > 0.001) or (abs (psy - 1.0) > 0.001) or (abs (psz - 1.0) > 0.001) do (
                                parentScaled += 1
                                /* 这一格从 report 升成闸（#124）之后，红的时候必须点名：
                                   只报一个根数等于把「查是哪根」重新交回给一个读日志的人，
                                   而那正是 #122 治的病。前 3 根留字，与同段的别的计数一样。 */
                                if parentScaled <= 3 do
                                    mmd2maxLog ("父骨的世界矩阵带缩放 RB_" + (idx as string) \
                                        + "：父骨=" + pn + " 三行长度=" + ps \
                                        + "（这块代理的尺寸会被整块乘走）")
                            )
                        )
                    )
                    /* #131 归因：把「搬走」拆成四个候选，每个都自己一个数，全 452 根都过一遍。
                       dMidWorst = 只设 .parent 那一下；dEndWorst = 写回 transform 那一下；
                       rowWorst  = 代理自己的朝向（世界矩阵三个行矢量的最大弦长，
                                  1 度 = 0.01745，所以 0.018 就是转了约 1 度）；
                       parWorst  = 父骨自己的 TM 在挂父前后变了多少（行矢量 + 平移一起加）。 */
                    /* tmEnd 不套 try：`proxy.transform` 在探针 87/90/93~99 里每一次都读得到，
                       而套了 try 之后「读不出」会让 rowWorst 与 orientWorst 双双静默停在 0 ——
                       两把尺一起变绿盲。真死了就让外层 catch 数进 rigid_failed，那一格是闸。 */
                    local tmEnd = proxy.transform
                    try ( rotAfter = proxy.rotation as string ) catch ( rotAfter = "读不出" )
                    for k = 1 to 3 do
                    (
                        local dRow = (distance worldTM[k] tmEnd[k])
                        if dRow > rowWorst do
                        (
                            rowWorst = dRow
                            rowWorstName = "RB_" + (idx as string) + "_" + (name as string)
                        )
                    )
                    /* #132 的独立靶：从 Python 给的度数造应当的世界旋转矩阵。
                       转置那一步是探针 99 A 实测的口径（1.73776e-07 对 1.7331），不是推的；
                       第一版探针 99 B 直接拿 `as matrix3` 当靶，三种形状都读 0.193933
                       = 2·sin(5.5645 度)，那是坏仪器读数。`transpose` 是死调用，手转。 */
                    local em = (eulerAngles ax ay az) as matrix3
                    local wantTM = matrix3 [em[1][1], em[2][1], em[3][1]] \
                                         [em[1][2], em[2][2], em[3][2]] \
                                         [em[1][3], em[2][3], em[3][3]] [0, 0, 0]
                    local dOrient = 0.0
                    for k = 1 to 3 do
                    (
                        local dK = (distance wantTM[k] tmEnd[k])
                        if dK > dOrient do dOrient = dK
                    )
                    if dOrient > orientWorst do
                    (
                        orientWorst = dOrient
                        orientWorstName = "RB_" + (idx as string) + "_" + (name as string)
                    )
                    /* 控制器类排在**所有写之后**回读：「换上了但后来被谁换回去」也看得见。
                       classOf 印出来的大小写不一致（Euler_XYZ 混大小写、bezier_rotation 全小写，
                       探针 97/99 两份读数），所以用 matchPattern ignoreCase —— 探针 99 B
                       实测这个参数名活、三种形状都返回 true。 */
                    if matchPattern ((classOf proxy.rotation.controller) as string) \
                        pattern:"bezier_rotation" ignoreCase:true do rotCtrlNative += 1
                    if parTmB != undefined and parTmA != undefined do
                    (
                        local dPB = (distance parTmB[4] parTmA[4])
                        for k = 1 to 3 do dPB = dPB + (distance parTmB[k] parTmA[k])
                        if dPB > parWorst do
                        (
                            parWorst = dPB
                            parWorstName = pn
                        )
                    )
                    if centerBefore != undefined do
                    (
                        if cMid != undefined do
                        (
                            local dM = (distance centerBefore cMid)
                            if dM > dMidWorst do dMidWorst = dM
                        )
                        if cMid != undefined and cEnd != undefined do
                        (
                            local dE = (distance cMid cEnd)
                            if dE > dEndWorst do dEndWorst = dE
                        )
                    )
                    /* 归属第一层：挂父这一步自己搬了多少（centerBefore 与 wC 都是世界形心）。
                       探针 90 在 **Box 父骨**上读它是 0；#131 第 105 轮在 Biped 父骨上读到
                       0.174525cm，所以上面那两句重发才是这一步的修法。重发之后这一格该回到
                       浮点噪声量级；真跑里仍 > 0.01 就是「重发没拽回来」，必须留字。 */
                    if centerBefore != undefined and wC != undefined do
                    (
                        local dLink = (distance centerBefore wC)
                        if dLink > 0.01 do
                        (
                            linkShift += 1
                            if linkShift <= 3 do
                                mmd2maxLog ("挂父这一步把代理搬走了 RB_" + (idx as string) \
                                    + "：搬 " + (dLink as string) + " 之前=" + (centerBefore as string) \
                                    + " 之后=" + (wC as string) + " 父骨=" + pn + " 父骨行长度=" + ps \
                                    + " [#131 只设parent后=" + (if cMid == undefined then "读不出" else cMid as string) \
                                    + " 写回后=" + (if cEnd == undefined then "读不出" else cEnd as string) \
                                    + " 朝向前=" + rotBefore + " 朝向后=" + rotAfter \
                                    + " 父骨TM前=" + (if parTmB == undefined then "读不出" else parTmB as string) \
                                    + " 父骨TM后=" + (if parTmA == undefined then "读不出" else parTmA as string) + "]")
                        )
                    )
                    if wC != undefined do
                    (
                        local dW = (distance wC [px, py, pz])
                        if dW > worstWorld do ( worstWorld = dW; worstName = "RB_" + (idx as string) )
                        if dW > 1.0 do
                        (
                            worldFail += 1
                            if worldFail <= 3 do
                                mmd2maxLog ("挂上骨之后代理离开了 PMX 那个点 RB_" + (idx as string) \
                                    + "：偏 " + (dW as string) + " 中心=" + (wC as string) \
                                    + " 期望=(" + (px as string) + "," + (py as string) + "," + (pz as string) + ")" \
                                    + " 父骨=" + pn + " 父骨行长度=" + ps)
                        )
                    )
                    /* 前 4 根（球/胶囊/胶囊/方块）各留一行三值对照 + 自己的世界盒尺寸：
                       `.pos` / `transform[4]` 是 pivot 的世界位置（探针 90 的 F3：挂父之后
                       `.pos` 加 10 就是世界 x 加 10，父骨转 90 度也没把它折到 z 上），
                       `.center` 是几何形心的世界位置。世界盒尺寸对不上导出尺寸才是放大。 */
                    if created < 4 do
                    (
                        local wb = "读不出"
                        local bb0 = undefined
                        try ( bb0 = nodeGetBoundingBox proxy (matrix3 1) ) catch ( bb0 = undefined )
                        if bb0 != undefined and bb0[1] != undefined do
                            wb = "(" + ((bb0[2][1] - bb0[1][1]) as string) + "," \
                                 + ((bb0[2][2] - bb0[1][2]) as string) + "," \
                                 + ((bb0[2][3] - bb0[1][3]) as string) + ")"
                        mmd2maxLog ("刚体世界量三对照 RB_" + (idx as string) \
                            + " .pos=" + (proxy.pos as string) \
                            + " tm[4]=" + (proxy.transform[4] as string) \
                            + " 导出=(" + (px as string) + "," + (py as string) + "," + (pz as string) + ")" \
                            + " 父骨=" + pn + " 父骨行长度=" + ps + " 世界盒=" + wb)
                    )
                    /* PMX 的刚体索引是 0 基，Max 数组是 1 基 —— 写 `rigids[idx]` 时
                       第一根刚体（idx=0）必然抛 `array index must be positive number, got: 0`，
                       而这条异常被外层 try 当成「创建失败」数走了：第 64 轮那
                       `（失败 1）` 就是它，代理其实已经建好并挂上了父骨，死的是这本账。
                       这本账现在没人读（接 MassFX 时才用得上），但正因为它是唯一一处
                       0 基/1 基换算，留着就要写对。 */
                    if idx >= 0 and idx < rigids.count do rigids[idx + 1] = proxy
                    /* 与 rigids 同下标记下「这根代理本该在哪」，循环跑完后再读一遍用（#90 第三层归属）。 */
                    if idx >= 0 and idx < rigidWant.count do rigidWant[idx + 1] = [px, py, pz]
                    try ( if layerRB != undefined do layerRB.addNode proxy ) catch ( layerFail += 1; if layerFail <= 3 do mmd2maxLog ("!! 刚体没进 mmd2max_physics 层 RB_" + (idx as string) + "：汇总行「刚体在层 mmd2max_physics」对它不成立") )
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
    /* #90 第三层归属：整个刚体循环跑完之后再读一遍形心。循环里那一格只看到「挂父那一刻」，
       搬代理的如果是循环**后面**的事（Biped 刷新 / 关节段 / 分层），只有这里能看见。
       探针 90 的 C1/C2 说挂父这一步本身不搬（d 从 4.8e-07 变 5.3e-06），
       所以真跑里两边都非零时，比的是**哪一边大**：循环外大 = 后面有人动了代理。 */
    local pr = undefined
    local want = undefined
    local wc2 = undefined
    local d2 = 0.0
    for k = 1 to rigids.count do
    (
        pr = rigids[k]
        want = rigidWant[k]
        if pr != undefined and want != undefined do
        (
            wc2 = undefined
            try ( wc2 = pr.center ) catch ( wc2 = undefined )
            if wc2 != undefined do
            (
                d2 = (distance wc2 want)
                if d2 > afterWorst do ( afterWorst = d2; afterName = (pr.name as string) )
                if d2 > 1.0 do
                (
                    afterFail += 1
                    if afterFail <= 3 do
                        mmd2maxLog ("循环跑完代理还是不在 PMX 那个点 " + (pr.name as string) \
                            + "：偏 " + (d2 as string) + " 中心=" + (wc2 as string) \
                            + " 期望=" + (want as string))
                )
            )
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
                try ( if layerJT != undefined do layerJT.addNode jm ) catch ( layerFail += 1; if layerFail <= 3 do mmd2maxLog ("!! 关节标记没进 mmd2max_joints 层 JT_" + name + "：汇总行「关节在层 mmd2max_joints」对它不成立") )
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
    /* 关节标记默认隐藏：700 个 Dummy 在视口里既看不见结构又特别卡。隐藏本身是装饰
       ——错了只影响用户接着看的视口；但下一段汇总要写「关节层已隐藏」，那是个断言，
       所以写完回读一次，让那三个字有出处。 */
    local jtHide = "没建层"
    if layerJT != undefined do
    (
        try ( layerJT.isHidden = true; jtHide = (if layerJT.isHidden then "是" else "否写没生效") ) catch ( jtHide = "写抛错" )
    )
    if jtHide != "是" do mmd2maxLog ("!! 关节层没隐藏（" + jtHide + "）：700 个 Dummy 留在视口里挡结构")
    /* 刚体代理默认隐藏：与关节层同一形状（隐藏 + 回读 + 失败告警）。363 个橙格代理留在
       视口里会盖住角色，而截图裁剪只认骨节点、碰不到 Sphere/Capsule/Box，所以这里替用户藏掉；
       要看时在层管理器里打开 mmd2max_physics 即可。汇总那句「隐藏=」两层各报各的，不再只报关节。 */
    local rbHide = "没建层"
    if layerRB != undefined do
    (
        try ( layerRB.isHidden = true; rbHide = (if layerRB.isHidden then "是" else "否写没生效") ) catch ( rbHide = "写抛错" )
    )
    if rbHide != "是" do mmd2maxLog ("!! 刚体层没隐藏（" + rbHide + "）：橙格代理留在视口里盖住角色")
    /* #131 归因汇总（只进日志，不进判据）：把「挂父搬走」那一格拆成四个候选之后，
       哪一个是真的必须由这四个数说，而不是由下一轮读日志的人猜。 */
    mmd2maxLog ("挂父归因：只设parent最大=" + (dMidWorst as string) \
        + " 写回transform最大=" + (dEndWorst as string) \
        + " 朝向弦长最大=" + (rowWorst as string) + "@" + rowWorstName \
        + " 父骨TM变化最大=" + (parWorst as string) + "@" + parWorstName \
        + "（弦长 0.01745 = 1 度）")
    /* #132：上面那行的 `朝向弦长最大` 是**自比**（挂父前 vs 挂父后），这一行才是对独立靶。
       两个数并排放着，因为它们分得开两种病：自比大 = 挂父/收尾把它转走了；
       对靶大 = 最终朝向就不是 PMX 要的那个（早帧写歪也算）。修法落地后两个都该在噪声里。 */
    mmd2maxLog ("刚体朝向对靶：弦长最大=" + (orientWorst as string) + "@" + orientWorstName \
        + " 四元数原生控制器=" + (rotCtrlNative as string) + "/" + (created as string) \
        + " 没换上=" + (rotCtrlFail as string) + "（弦长 0.01745 = 1 度）")
    local sumMsg = "物理刚体代理：" + (created as string) + "（失败 " + (rigidFail as string) + "）"
    sumMsg = sumMsg + " 关节标记：" + (jointCount as string) + "（失败 " + (jointFail as string) + "）"
    sumMsg = sumMsg + " 旋转失败=" + (rotFail as string)
    /* 「有朝向的刚体」= PMX 里三根欧拉轴非零的根数。没有这一格，
       `旋转失败=0` 分不清「全转上了」和「根本没有朝向可转」——
       第 64~66 轮那种「254 根一根没转」反过来也可能变成「0 根其实什么都不用转」。 */
    sumMsg = sumMsg + " 有朝向的刚体=" + (rotNonZero as string)
    sumMsg = sumMsg + " 转后偏心=" + (offFail as string)
    /* 定心那一步（`proxy.center = [px,py,pz]`）抛错的根数：写不进去的代理**不在** PMX
       那个点上，而上面两格一个数的是创建、一个数的是转完之后的回读，都不覆盖这一步。
       它以前只进每条 `!!` 日志（且封顶 3 条），汇总行里没有 —— 判据行有了之后它必须
       一起报，否则第 4 根以后抛的错在报告表上读成 0。 */
    sumMsg = sumMsg + " 定心失败=" + (centerFail as string)
    /* 挂父之后那一格才是「代理在不在角色身上」的尺：前面几格都在节点自己的空间里量。
       `最大=` 带名字：第 70 轮 `挂父后世界偏心=4` 只留了前 3 条文字，第 4 根是谁、
       最偏的那根是不是第 4 根，日志里查不到 —— 计数封顶可以，名字不能封顶。 */
    sumMsg = sumMsg + " 挂父后世界偏心=" + (worldFail as string) + " 最大=" + (worstWorld as string) + "@" + worstName
    sumMsg = sumMsg + " 挂父搬走=" + (linkShift as string)
    /* 收尾重发（朝向 + 定心）抛错的根数：这两句是 #131 的修法本体，抛了就等于修法没落地。
       它不进判据是量过的决定，不是省事 —— `挂父搬走` 已经会在定心没重发时变红，
       这一格的作用是让「红了但没人重发」和「绿了但重发被抛掉」在日志里分得开。
       朝向单独没重发时 centroid 仍然对（定心排在朝向之后，它把点强行搬回去），
       所以那一半由 #132 的 `orient_worst` 闸负责（对独立靶，不是自比），这一格只留日志。 */
    sumMsg = sumMsg + " 挂父收尾抛错=" + (pinFail as string)
    sumMsg = sumMsg + " 朝向对靶最大=" + (orientWorst as string) + "@" + orientWorstName
    sumMsg = sumMsg + " 控制器没换上=" + (rotCtrlFail as string)
    /* 循环外那一次的读数（#90 第三层归属）：与上面那格成对照。 */
    sumMsg = sumMsg + " 循环后仍偏=" + (afterFail as string) + " 最大=" + (afterWorst as string) + "@" + afterName
    sumMsg = sumMsg + " 挂上骨=" + (rigidParented as string) + " 没挂上=" + (rigidRooted as string)
    /* #126：上一行走完，这一行问「挂对了吗」。三个数与 `挂上骨` 的关系是
       `名字对骨 + 挂错骨 + 名字不计 = 挂上骨`（这个式子在 Max 自己的计数里恒成立，
       所以它不是判据；判据是 `名字对骨` 对表里那个期望数 —— 见下面那行 report）。 */
    sumMsg = sumMsg + " 名字对骨=" + (rigidNamed as string) + " 挂错骨=" + (nameWrong as string) \
        + " 名字不计=" + (nameSkipped as string)
    /* 上面那格数的是「代理有没有落在某根骨下面」。它与 `刚体代理：N` 是两份数：
       创建成功不代表挂得上，挂不上的那些在绑定姿态下位置仍然全对。 */
    sumMsg = sumMsg + " 父骨带缩放=" + (parentScaled as string)
    /* 非 0 就说明这一轮的胶囊其实是圆柱：形状退化了，但创建计数仍然好看。 */
    sumMsg = sumMsg + " 胶囊退圆柱=" + (capFalls as string)
    sumMsg = sumMsg + " 层收纳失败=" + (layerFail as string)
    sumMsg = sumMsg + "；刚体在层 mmd2max_physics（隐藏=" + rbHide + "），关节在层 mmd2max_joints（隐藏=" + jtHide + "）"
    mmd2maxLog sumMsg
    /* ── 这串计数落到判据表（#122）────────────────────────────────
       上面那句汇总从前**只进日志**，于是「刚体少建了 1 个」（第 64/64b 轮）与
       「挂父后世界偏心=4 最大=94.6242」（第 70/71 轮）都是人一行行读日志读出来的，
       判据表上那些轮次全绿。现在每一格各报一行，check 名逐字对上 verdicts.py 的
       physics_rb（契约测试拿两边的字面量对撞）。
       五条「该有几根」的期望值来自导出的 physics.tsv（Python 那边逐行数出来的
       `PhysicsExpectation`），不是这里自己的计数 —— 所以 `rigid_created` 不是恒等式：
       它数的是**这个场景里真建出来的代理**。第 64 轮那种「代理建好了、记账那一步抛了」
       在这一格里就是 `created` 少数 1、`rigid_failed` 多 1，两行必须一起看。
       `rigid_parented`（#125）的期望 = 表里 `rigid_bone >= 0` 的行数：它数的是**回读到的
       `proxy.parent`**，所以「创建成功但挂父那三层条件落空」这一族在别处全绿的错，
       在这一格里少一根就红。日志里的「没挂上」不给行 —— 它恒等于
       `rigid_created - rigid_parented`，给一行等于把同一个数判两遍。
       `world_worst_cm` 是 report（不卡数）：它是最大偏心的厘米数，
       「有没有偏」已经由 off_center / world_failed / after_failed 三格按阈值卡了。
       `parent_scaled` 从前也算 report，理由写的是「202 根、父骨本来就可能带缩放」——
       那句是第 69 轮那把坏尺的读数（读 `parent.scale`，Biped 骨没这属性、逐根抛，
       只有附属骨/Dummy 进了分母），不是模型属性；换对尺之后每一轮都读 0。
       现在它卡 0，而且红的时候下面那段会点名是哪几根。
       #132 新增两行：`rot_ctrl_native` 的期望 = 表里的刚体行数（与 rigid_created 同源），
       它数的是**所有写做完之后**回读到的控制器类，所以「换上了又被谁换回去」也红；
       日志里的「没换上」不给行 —— 它恒等于 `rigid_created - rot_ctrl_native`。
       `orient_worst` 是**闸**而不是 report，这点与 world_worst_cm 相反：朝向此前
       没有任何一把尺（link_shifted 只量形心，而形心被收尾那句定心强行搬回去了，
       转歪 1 度它照样绿），所以这个最大弦长本身就是判据，阈值在 verdicts.py 那边。 */
    mmd2maxReport "physics_rb" "rigid_created" "-" created
    mmd2maxReport "physics_rb" "rigid_parented" "-" rigidParented
    mmd2maxReport "physics_rb" "rigid_parent_named" "-" rigidNamed
    mmd2maxReport "physics_rb" "joint_created" "-" jointCount
    mmd2maxReport "physics_rb" "rot_oriented" "-" rotNonZero
    mmd2maxReport "physics_rb" "rot_ctrl_native" "-" rotCtrlNative
    mmd2maxReport "physics_rb" "rigid_failed" "-" rigidFail
    mmd2maxReport "physics_rb" "joint_failed" "-" jointFail
    mmd2maxReport "physics_rb" "rot_failed" "-" rotFail
    mmd2maxReport "physics_rb" "center_failed" "-" centerFail
    mmd2maxReport "physics_rb" "off_center" "-" offFail
    mmd2maxReport "physics_rb" "link_shifted" "-" linkShift
    mmd2maxReport "physics_rb" "world_failed" "-" worldFail
    mmd2maxReport "physics_rb" "after_failed" "-" afterFail
    mmd2maxReport "physics_rb" "cap_cylinder" "-" capFalls
    mmd2maxReport "physics_rb" "layer_failed" "-" layerFail
    mmd2maxReport "physics_rb" "world_worst_cm" "-" worstWorld
    mmd2maxReport "physics_rb" "orient_worst" "-" orientWorst
    mmd2maxReport "physics_rb" "parent_scaled" "-" parentScaled
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

    /* 附属骨方向回读（#66）：排在保存之前、所有会挪骨的段落之后 —— 这一刻量到的就是
       存进 .max 的那个场景。这一段只读不写（一个 setTransform 都没有），所以它对几何
       是中性的；注册表里它是最后一道 pass。 */
    mmd2maxVerifyAccessoryDirs boneNodes
    mmd2maxReport "pass" "ran" "bone_dir_verify" 1

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

    /* 装配收尾哨兵（#137）：写在保存 / 判据报告 / 视口截图全部落盘之后、决定退不退之前。
       上层 tools/obj_run.py 在 KEEPOPEN=true 时靠这一行收尾 —— 只等 success.json 不够，
       成功标记刻意排在截图之前（截图出错不该毁掉产物），那时截图还没抓完、REPORT 还在写。 */
    mmd2maxLog "装配收尾完成 MMD2MAX_DONE（保存 / 判据报告 / 视口截图全部落盘）"

    /* 只有显式关掉 MMD2MAX_KEEPOPEN（obj_run 的 --quit）才自己退出。
       #137 之前这一刀挂在「非交互」上，于是自动装配跑完就把用户想看的场景关掉了；
       现在默认保留现场，交互位只管弹窗，两者互不牵连。
       回滚位仍要 clean quitMax：上层以前一律 `taskkill /F /IM 3dsmax.exe`，强杀让 Max
       记下的「上次会话非正常结束」计数一路累积（实测 SessionStartCount=167 /
       SessionCleanCloseCount=50），之后每次启动都弹 "Startup Failure Detection" 模态框，
       把 -U MAXScript 的自动执行整个挡死。quiet:true 不弹「保存更改？」——截图阶段
       改过 isHidden/EdgeFaces，场景是脏的，裸 quitMax 会被保存提示挡住。 */
    if not mmd2maxKeepOpen() do
    (
        mmd2maxLog "MMD2MAX_KEEPOPEN=false：quitMax() 正常退出（避免上层 taskkill /F 累积崩溃计数）。"
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
    catch ( format "[mmd2max] !! 写 error.txt 失败: %\n" (getCurrentException()) )
    if mmd2maxIsInteractiveNow() do
    (
        try ( messageBox ("转换失败：\n" + message) title:"mmd2max 失败" ) catch ( format "[mmd2max] !! 失败弹窗没弹出来（交互模式下屏幕上看不见这条错误）: %\n" (getCurrentException()) )
    )
    /* 失败默认也保留现场（#137）：错误全文已经落进 error.txt 和日志，屏幕上这个
       半成品场景才是排查时最有用的一份证据。只有 --quit（KEEPOPEN=false）才自己退出，
       那时上层在同一开关下会等 clean quitMax，不会触发 taskkill /F 的崩溃计数。
       真·报错弹窗（没被这里 catch 住的）走另一条路：obj_run 捕获到弹窗一律保留现场。 */
    if not mmd2maxKeepOpen() do
    (
        mmd2maxLog "失败且 MMD2MAX_KEEPOPEN=false：写完成 error.txt 后 quitMax 退出（现场已在日志与 error.txt 里）。"
        try ( quitMax quiet:true ) catch ( try ( quitMax ) catch () ) -- 允许失败：两条 quitMax 都不成时由上层 obj_run 的 GRACEFUL_EXIT_SECONDS 兜底 taskkill；这里再抛会把「错误已落 error.txt」的现场换成一次 Max 崩溃弹窗
    )
)
