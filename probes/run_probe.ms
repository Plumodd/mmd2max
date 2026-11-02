/* wrapper: run a probe .ms through fileIn so a COMPILE error is catchable and lands in
   the text file instead of a modal dialog. Also pre-creates the output file, because
   MAXScript's openFile(mode:"a") returns undefined for a missing file -- that alone
   silently swallowed the whole first probe run. */
global P_SRC = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/skin_readback.ms"
global P_OUT = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/skin_readback.txt"
(
    local f = createFile P_OUT
    if f != undefined do ( format "===== wrapper: fileIn % =====\n" P_SRC to:f; close f )
    try ( fileIn P_SRC )
    catch (
        local g = openFile P_OUT mode:"a"
        if g != undefined do
        (
            format "ABORTED: %\n" (getCurrentException() as string) to:g
            close g
        )
        quitMax quiet:true
    )
)
