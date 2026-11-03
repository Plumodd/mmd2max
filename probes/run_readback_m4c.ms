/* wrapper（M4 探针第三轮）：同 run_readback_m4.ms —— 先 createFile 再 fileIn，
   编译/运行时错误落成一行文本而不是弹窗。 */
global W_SRC = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/readback_m4c.ms"
global W_OUT = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/readback_m4c.txt"
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
