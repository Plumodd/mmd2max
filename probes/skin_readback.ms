/* v4 architecture probe (v6): do the skinOps weight READ-BACK names exist in Max 2022,
   and what do they actually return for weights we wrote?

   What each earlier iteration bought (all of these are v4 design constraints now):
     1) an undefined *global* in call position aborts the file (deleteAllObjects, and
        resetMaxFile in v1) -- a -U MAXScript batch launch already starts empty.
     2) openFile(path mode:"a") returns undefined when the file is missing; MAXScript does
        not create it. run_probe.ms createFile()s first or the log is silently empty.
     3) `local` is illegal at top level -> body lives inside one ( ) block.
     4) getCurrentException() must be read INSIDE the catch, else it is already cleared and
        you lose the diagnosis (v3 printed "undefined" instead of the real reason).
     5) addNewModifier() does not exist; v3's idiom is `Skin()` + addModifier (line 3925).
     6) string + Integer throws "Unable to convert: 0 to type: String" -- MAXScript does not
        coerce numbers into string concatenation, hence the S() helper below (v3 always
        writes `as string` for exactly this reason).
     7) getProperty skinOps <name> throws for every name: skinOps is a compiler-level
        interface, not a getProperty-able struct, so v2's "absent" column was noise. The
        valid probe is calling in function position inside try/catch. */

global PROBE_OUT = "C:/Users/Administrator/AppData/Local/Temp/mmd2max_probe/skin_readback.txt"

fn say s =
(
    local f = openFile PROBE_OUT mode:"a"
    if f != undefined do ( format "%\n" s to:f; close f )
)

fn why = try ( getCurrentException() as string ) catch "?"
fn S x = try ( x as string ) catch "<unprintable>"

(
    say "===== skin readback probe v6 start ====="

    local mesh = try ( Box name:"pBox" length:10.0 width:10.0 height:10.0 ) \
        catch ( say ("  Box FAILED: " + why()); undefined )
    if mesh == undefined do
        mesh = try ( Sphere name:"pSph" radius:5.0 segs:8 ) catch ( say ("  Sphere FAILED: " + why()); undefined )
    say ("  mesh = " + (if mesh == undefined then "NONE" else (S (classOf mesh))))

    local smod = undefined
    local sm = try ( Skin() ) catch ( say ("  Skin() FAILED: " + why()); undefined )
    if sm != undefined and mesh != undefined do
    (
        try ( sm.name = "pSkin"; addModifier mesh sm; smod = mesh.modifiers[1] ) \
            catch ( say ("  addModifier FAILED: " + why()) )
    )
    say ("  modifier = " + (if smod == undefined then "none" else (S (classOf smod))))

    local b1 = try ( BoneSys.createBone [-5,0,0] [0,0,0] [0,1,0] ) catch ( say ("  bone1 FAILED: " + why()); undefined )
    local b2 = try ( BoneSys.createBone [0,0,0] [5,0,0] [0,1,0] ) catch ( say ("  bone2 FAILED: " + why()); undefined )
    say ("  bones = " + (S (classOf b1)) + " / " + (S (classOf b2)))

    if smod != undefined and b1 != undefined and b2 != undefined do
    (
        say ("  GetNumberBones before = " + (S (try ( skinOps.GetNumberBones smod ) catch why())))
        say ("  AddBone b1 = " + (S (try ( skinOps.AddBone smod b1 0 ) catch why())))
        say ("  AddBone b2 = " + (S (try ( skinOps.AddBone smod b2 0 ) catch why())))
        say ("  GetNumberBones after = " + (S (try ( skinOps.GetNumberBones smod ) catch why())))
        say ("  GetBoneName 1 = " + (S (try ( skinOps.GetBoneName smod 1 0 ) catch why())))
        say ("  GetBoneName 2 = " + (S (try ( skinOps.GetBoneName smod 2 0 ) catch why())))

        /* v1: 0.7/0.3 as written. v5: bone2 listed first with 1.0 -> does the slot order
           survive? v9: 0.9/0.9 (sum > 1) -> does Skin renormalise, and do we SEE it.
           v13: a bone index that does not exist -> loud failure or silent drop?
           v3's coverage check could not answer any of these four questions. */
        say ("  RVW v1 0.7/0.3 = " + (S (try ( skinOps.ReplaceVertexWeights smod 1 #(1,2) #(0.7,0.3) ) catch why())))
        say ("  RVW v5 bone2-first = " + (S (try ( skinOps.ReplaceVertexWeights smod 5 #(2,1) #(1.0,0.0) ) catch why())))
        say ("  RVW v9 0.9/0.9 = " + (S (try ( skinOps.ReplaceVertexWeights smod 9 #(1,2) #(0.9,0.9) ) catch why())))
        say ("  RVW v13 bogus bone7 = " + (S (try ( skinOps.ReplaceVertexWeights smod 13 #(1,7) #(0.5,0.5) ) catch why())))
        say ("  forceUpdate = " + (S (try ( skinOps.forceUpdate smod; "ok" ) catch why())))

        say "-- read-back candidates: a number = exists, Unknown property = not there --"
        say ("  GetVertexWeightCount smod 1 = " + (S (try ( skinOps.GetVertexWeightCount smod 1 ) catch why())))
        say ("  GetVertexWeightTotal smod 1 = " + (S (try ( skinOps.GetVertexWeightTotal smod 1 ) catch why())))
        say ("  GetVertexWeightValue smod 1 1 = " + (S (try ( skinOps.GetVertexWeightValue smod 1 1 ) catch why())))
        say ("  GetVertexWeightValue smod 1 2 = " + (S (try ( skinOps.GetVertexWeightValue smod 1 2 ) catch why())))
        say ("  GetVertexWeight smod 1 1 = " + (S (try ( skinOps.GetVertexWeight smod 1 1 ) catch why())))
        say ("  GetBoneIndex smod 1 0 = " + (S (try ( skinOps.GetBoneIndex smod 1 0 ) catch why())))
        say ("  GetBoneIndex smod 1 1 = " + (S (try ( skinOps.GetBoneIndex smod 1 1 ) catch why())))
        say ("  GetVertexWeightBoneID smod 1 0 = " + (S (try ( skinOps.GetVertexWeightBoneID smod 1 0 ) catch why())))
        say ("  GetNumberWeights smod 1 = " + (S (try ( skinOps.GetNumberWeights smod 1 ) catch why())))
        say ("  GetWeightValueByIndex smod 1 0 = " + (S (try ( skinOps.GetWeightValueByIndex smod 1 0 ) catch why())))
        say ("  GetMaxWeightsNumber smod = " + (S (try ( skinOps.GetMaxWeightsNumber smod ) catch why())))
        say ("  GetNumberVertices smod = " + (S (try ( skinOps.GetNumberVertices smod ) catch why())))

        say "-- dump the probed vertices with whatever names survived --"
        for v in #(1,3,5,9,13) do
        (
            local cnt = try ( skinOps.GetVertexWeightCount smod v ) catch "EX"
            local line = "  vert " + (S v) + " count=" + (S cnt)
            if cnt != "EX" and cnt != 0 do
            (
                for k = 0 to ((cnt as integer) - 1) do
                (
                    local bi = try ( skinOps.GetBoneIndex smod v k ) catch "EX"
                    local wSlot = try ( skinOps.GetVertexWeightValue smod v (k + 1) ) catch "EX"
                    local wBone = "skip"
                    if bi != "EX" do wBone = (try ( skinOps.GetVertexWeightValue smod v (bi + 1) ) catch "EX")
                    local wBid = try ( skinOps.GetVertexWeightBoneID smod v k ) catch "EX"
                    line += " | slot" + (S k) + " bone(0b)=" + (S bi) + " wBySlot1=" + (S wSlot) \
                        + " wByBone=" + (S wBone) + " boneID=" + (S wBid)
                )
                line += " total=" + (S (try ( skinOps.GetVertexWeightTotal smod v ) catch "EX"))
            )
            say line
        )
    )

    say "===== skin readback probe v6 done ====="
    quitMax quiet:true
)
