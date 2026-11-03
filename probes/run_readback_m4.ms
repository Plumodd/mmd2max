/* wrapper（M4 回读探针）：先 createFile 把输出建出来，再 fileIn 探针本体。
   两条都是本机实测过的坑（见 probes/skin_readback.ms 头部笔记）：
     1) MAXScript 的 openFile(mode:"a") 对**不存在**的文件返回 undefined，
        探针里每一句 say 都静默丢掉，整轮跑完只剩一个空文件；
     2) 探针本体若有编译错误，裸跑会把错误扔进模态弹窗（读不到文字、还挡自动执行），
        经 wrapper 的 try 包住 fileIn，编译错误就落成一行文本。 */
global W_SRC = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/readback_m4.ms"
global W_OUT = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/readback_m4.txt"
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
