/* wrapper（M4 探针第二轮）：与 run_readback_m4.ms 同一套路 ——
   先 createFile 建出输出（openFile mode:"a" 对不存在的文件返回 undefined，
   整轮读数会静默丢掉），再用 try 包住 fileIn，编译错误落成一行文本而不是弹窗。 */
global W_SRC = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/readback_m4b.ms"
global W_OUT = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/readback_m4b.txt"
(
    local f = createFile W_OUT
    if f != undefined do ( format "===== wrapper: fileIn % =====\n" W_SRC to:f; close f )
    try ( fileIn W_SRC )
    catch
    (
        local g = openFile W_OUT mode:"a" encoding:#utf8
        if g != undefined do
        (
            format "ABORTED: %\n" (getCurrentException() as string) to:g
            close g
        )
        try ( quitMax quiet:true ) catch ( try ( quitMax ) catch () )
    )
)
