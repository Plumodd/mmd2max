"""obj_run 的三条「不许骗人」契约，全部在不启动 3ds Max 的前提下核对。

  1. 报错弹窗探测：插件浮动面板（BsKeyTools 等，窗口类 MAXScriptDialog、标题为空）
     不许被当成 MAXScript 报错弹窗 —— 实跑踩到它让每轮都白喊「出现报错」，
     还把 Max 现场保留下来不杀；同样不许把**别的 Max 实例**留下的脚本窗口
     当成本轮报错（21:16 那一轮就是这么退 3 的）；
  2. 退出码：没有 .max / 没有成功标记就绝不许返回 0，否则 GUI 的横幅会写「完成」。
"""
import contextlib
import hashlib
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import obj_run                                            # noqa: E402
from mmd2max import verdicts                              # noqa: E402
from tests import spec_table                              # noqa: E402

NAME = "model"

# 这套守卫测试用的「假香菱」规模：顶点数 / 骨数 / 表情数三者自洽，因为 M4b 之后
# 装规格表时要交叉核对（权重行数 == CFG 的顶点数、骨骼行数 == meta 的 kept_bones）。
# 对不上就是「两份导出产物分叉」，obj_run 会直接中止 —— 所以这里改一个数得三个一起改。
VERTS = 18892
BONES = 321
MORPHS = 61


def win(title, cls="", pid=None):
    return {"hwnd": 1, "title": title, "cls": cls, "pid": pid,
            "rect": (0, 0, 300, 120)}


class DialogDetectTests(unittest.TestCase):
    def test_plugin_floater_is_not_an_error_dialog(self):
        # 实跑误命中的就是这一个：类名含 MAXScript，标题为空
        self.assertFalse(obj_run.is_maxscript_error_dialog(
            win("", "MAXScriptDialog")))
        # 有的浮动面板确实有标题，但类名仍是 MAXScriptDialog（脚本化面板）
        self.assertFalse(obj_run.is_maxscript_error_dialog(
            win("BsKeyTools", "MAXScriptDialog")))

    def test_listener_and_main_window_are_not_error_dialogs(self):
        self.assertFalse(obj_run.is_maxscript_error_dialog(win("MAXScript Listener")))
        self.assertFalse(obj_run.is_maxscript_error_dialog(
            win("Autodesk 3ds Max 2022 - [Metric]")))

    def test_real_error_dialog_is_detected(self):
        self.assertTrue(obj_run.is_maxscript_error_dialog(
            win("MAXScript Error", "#32770")))

    def test_stale_script_document_window_is_not_an_error_dialog(self):
        """21:16 那轮退 3 的元凶：我自己探针留下的脚本窗口，标题带 .ms 路径。

        报错弹窗标题只有错误种类（「MAXScript Error」等），绝不含文件路径；
        带路径的是 MAXScript 监听器/编辑器文档窗口，误判代价是「停轮询 +
        保留 Max + 本轮无产物」。
        """
        stale = (r"C:\Users\Administrator\AppData\Local\Temp\mmd2max_probe"
                 r"\micro\probe_syntax.ms - MAXScript")
        self.assertFalse(obj_run.is_maxscript_error_dialog(win(stale, "#32770")))
        self.assertFalse(obj_run.is_maxscript_error_dialog(
            win("untitled.ms - MAXScript", "MAXScriptBrowser")))

    def test_foreign_pid_is_not_this_runs_error_dialog(self):
        """owner_pid 给了就只认本轮 Max 进程自己的窗口，别家实例的窗口不算。"""
        foreign = win("MAXScript Error", "#32770", pid=999)
        self.assertFalse(obj_run.is_maxscript_error_dialog(foreign, 123))
        self.assertTrue(obj_run.is_maxscript_error_dialog(
            win("MAXScript Error", "#32770", pid=123), 123))
        # 拿不到 pid 时退化成旧行为，宁可截图也别漏掉真报错
        self.assertTrue(obj_run.is_maxscript_error_dialog(
            win("MAXScript Error", "#32770"), 123))

    def test_only_floaters_captures_nothing(self):
        """误报的代价是「停轮询 + 保留 Max」，所以这里连截图都不该发生。"""
        shots = []
        original_find, original_capture = obj_run.find_windows, obj_run.capture_region
        obj_run.find_windows = lambda needle: [win("", "MAXScriptDialog"),
                                               win("BsKeyTools", "MAXScriptDialog")]
        obj_run.capture_region = lambda *a, **k: shots.append(a)
        try:
            with tempfile.TemporaryDirectory() as temp:
                self.assertIsNone(obj_run.handle_maxscript_dialog(Path(temp)))
                self.assertEqual(shots, [])
                self.assertFalse((Path(temp) / "maxscript_dialog.bmp").exists())
        finally:
            obj_run.find_windows, obj_run.capture_region = original_find, original_capture

    def test_error_dialog_still_captured(self):
        shots = []
        original_find, original_capture = obj_run.find_windows, obj_run.capture_region
        obj_run.find_windows = lambda needle: [win("", "MAXScriptDialog"),
                                               win("MAXScript Error", "#32770")]
        obj_run.capture_region = lambda *a, **k: shots.append(a)
        try:
            with tempfile.TemporaryDirectory() as temp:
                shot = obj_run.handle_maxscript_dialog(Path(temp))
                self.assertEqual(len(shots), 1)
                self.assertTrue(shot.endswith("maxscript_dialog.bmp"))
        finally:
            obj_run.find_windows, obj_run.capture_region = original_find, original_capture

    def test_foreign_windows_captures_nothing(self):
        """本轮之外的 Max 窗口（探针脚本窗口 / 别家实例报错）不许打断轮询。"""
        shots = []
        original_find, original_capture = obj_run.find_windows, obj_run.capture_region
        obj_run.find_windows = lambda needle: [
            win(r"C:\Temp\probe_syntax.ms - MAXScript", "#32770", pid=2368),
            win("MAXScript Error", "#32770", pid=2368)]
        obj_run.capture_region = lambda *a, **k: shots.append(a)
        try:
            with tempfile.TemporaryDirectory() as temp:
                self.assertIsNone(obj_run.handle_maxscript_dialog(Path(temp), 30712))
                self.assertEqual(shots, [])
        finally:
            obj_run.find_windows, obj_run.capture_region = original_find, original_capture


class ResultCodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.marker = self.base / f"{NAME}.success.json"
        self.max_file = self.base / f"{NAME}.max"
        self.error_file = Path(str(self.marker) + ".error.txt")

    def tearDown(self):
        self.temp.cleanup()

    def code(self, **kwargs):
        return obj_run.result_code(self.base, NAME, **kwargs)

    def test_nothing_written_is_failure(self):
        # 超时 / Max 自己崩了：什么都不存在
        self.assertNotEqual(self.code(), 0)

    def test_marker_without_max_file_is_failure(self):
        # saveMaxFile 没落盘，成功标记却写了 —— 正是 GUI 报「完成」却没有产物的那一例
        self.marker.write_text("{}", encoding="utf-8")
        self.assertNotEqual(self.code(), 0)

    def test_max_file_without_marker_is_failure(self):
        self.max_file.write_bytes(b"x")
        self.assertNotEqual(self.code(), 0)

    def test_only_marker_and_max_file_is_success(self):
        self.marker.write_text("{}", encoding="utf-8")
        self.max_file.write_bytes(b"x")
        self.assertEqual(self.code(), 0)

    def test_error_report_wins_over_dialog_and_timeout(self):
        self.error_file.write_text("Runtime error: 顶点数不符", encoding="utf-8")
        self.assertEqual(self.code(dialog_seen=True, dialog_path="x.bmp"), 4)
        self.assertEqual(self.code(), 4)

    def test_dialog_beats_bare_timeout(self):
        self.assertEqual(self.code(dialog_seen=True, dialog_path="x.bmp"), 3)
        self.assertEqual(self.code(), 5)

    def test_marker_present_but_max_missing_is_its_own_code(self):
        self.marker.write_text("{}", encoding="utf-8")
        self.assertEqual(self.code(), 6)


class CfgContractTests(unittest.TestCase):
    """M1b 配置单源：装配的每一项输入/开关只有一个来源 —— <目录>/MMD2MAX.CFG。

    v3 是在生成的 .ms 头部逐行注入 22 个 global。那带来一条查不出来的分裂：自动跑读
    注入头，人手 `fileIn scripts/max_import_obj.ms` 得自己记得这 22 个值 —— 漏一个
    flag（MMD2MAX_FOOTPITCH 就是现成的例子）就摆出**不同的几何**，两边日志还都全绿。
    现在两条路径都只有 `MMD2MAX_CFG` 一个起点。这里不启动 Max，靠「把 .ms 里实际
    取用的键名正则出来，与 Python 侧写出的键集对照」来验两端同源 —— 这正是本项目
    那条老教训（写的人和验收的人共用一份事实来源 ⇒ 恒等式看不见偏差）的反例做法。
    """

    META = ["key\tvalue", "scale\t7.6563", "spine_links\t3", "fingers\t5",
            "finger_links\t3", "foot_box_flat\ttrue",
            # 规格表（M2b）的期望值有两个只能来自 meta：Skin 该有几根骨、该建几条表情
            # 通道。缺任何一个 = 那两条判据没有尺，所以中止而不是当没这条判据。
            f"kept_bones\t{BONES}", f"morph_count\t{MORPHS}",
            # 两张特写的两份产物（#70 胸、#71 颈）：框（Max 真去 zoom 的那只三轴盒）与
            # 尺（导出量出来的 subject 骨端点极值）。上面那些是香菱那一轮的真数：
            # 胸骨 x ±4.9157、y -11.7451..-7.2398、z 105.2253..105.7325；颈链三节
            # （Spine2 / Neck / Head）x -3.842957..0.000088、y -6.766848..-0.036633、
            # z 100.760711..128.286294。框 = 极值各外扩「网格实测高」的比例
            # （z 6% ≈ 8.66cm，x/y 10% ≈ 14.43cm），两组用同一对比例。
            "chest_box_x_lo\t-19.349036", "chest_box_x_hi\t19.349036",
            "chest_box_y_lo\t-26.178482", "chest_box_y_hi\t7.193581",
            "chest_box_z_lo\t96.565340", "chest_box_z_hi\t114.392518",
            "chest_bone_x_min\t-4.9157", "chest_bone_x_max\t4.9157",
            "chest_bone_y_min\t-11.7451", "chest_bone_y_max\t-7.2398",
            "chest_bone_z_min\t105.2253", "chest_bone_z_max\t105.7325",
            "neck_box_x_lo\t-18.276298", "neck_box_x_hi\t14.433429",
            "neck_box_y_lo\t-21.200189", "neck_box_y_hi\t14.396708",
            "neck_box_z_lo\t92.100706", "neck_box_z_hi\t136.946299",
            "neck_bone_x_min\t-3.842957", "neck_bone_x_max\t0.000088",
            "neck_bone_y_min\t-6.766848", "neck_bone_y_max\t-0.036633",
            "neck_bone_z_min\t100.760711", "neck_bone_z_max\t128.286294"]
    # 最小的**真**肢段表：一行普通链 + 一行 leg + 一行 foot，让派生出来的判据行
    # （段校验 / 大腿扭转 / 脚趾复摆 / 脚链 / 骨盒回读）在这套守卫测试里真被走一遍，
    # 而不是靠空表「正好什么都没检查所以没失败」。
    # 表头按真导出的 18 列写：第 10~18 列只有 mode=foot 的行才有（骨盒回读 M4c 的尺
    # 就住在这九列里），其余行照 v2 的「行可以比列名短」留空。
    LIMBS = ["parent_target\tchild_target\tdesired_len\tmmd_parent\tmmd_child"
             "\tchild_x\tchild_y\tchild_z\tmode"
             "\tfoot_len\ttoe_len\ttip_x\ttip_y\ttip_z\ttoe_w\ttoe_h\tfoot_w\tfoot_h",
             "Bip001 Spine\tBip001 Spine1\t8.325677\t腰\t腰2\t0\t-4.9\t100.7\tlink",
             "Bip001 L Thigh\tBip001 L Calf\t36.382676\t左足\t左膝\t6.2\t-3.9\t45.2\tleg",
             "Bip001 L Foot\tBip001 L Toe0\t11.228109\t足首\tつま先\t5.3\t1.0\t7.5\tfoot"
             "\t14.097240\t3.569191\t7.049082\t-11.587054\t1.459004"
             "\t5.317024\t1.860916\t3.714245\t8.656011"]
    # 手部粗细表（M4c 的 hand_scale_cm 的尺）：两根就够长出两类通道，其余行不改变判据。
    WIDTHS = ["biped_target\twidth",
              "Bip001 L Hand\t3.6286",
              "Bip001 R Hand\t3.6953"]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.populate(self.base, "t")

    def populate(self, base: Path, stem: str) -> Path:
        """铺一个「够 build_script 跑完」的最小导出目录（同一套文件名的唯一来源）。"""
        base.mkdir(parents=True, exist_ok=True)
        (base / f"{stem}.obj").write_text("x\n", encoding="utf-8")
        (base / f"{stem}_biped_map.tsv").write_text("x\n", encoding="utf-8")
        # M4b 之后骨骼表与权重表**不再是占位**：蒙皮回读的期望值直接从这两张表算，
        # 而且装表时会交叉核对「骨骼行数 == meta 的 kept_bones」「权重行数 == CFG 的顶点数」。
        # 塞 "x\n" 会从第一步就抛「列名不对」，看起来像 fixture 坏了，其实是谁也没在测。
        (base / f"{stem}_bones.tsv").write_text(
            spec_table.bones_text(BONES), encoding="utf-8")
        (base / f"{stem}_weights.tsv").write_text(
            spec_table.weights_text(VERTS, BONES), encoding="utf-8")
        (base / f"{stem}_limbs.tsv").write_text("\n".join(self.LIMBS) + "\n",
                                                encoding="utf-8")
        (base / f"{stem}_widths.tsv").write_text("\n".join(self.WIDTHS) + "\n",
                                                 encoding="utf-8")
        (base / f"{stem}_meta.tsv").write_text("\n".join(self.META) + "\n",
                                               encoding="utf-8")
        return base

    def tearDown(self):
        self.temp.cleanup()

    def script(self, **kwargs):
        return obj_run.build_script(self.base, "t", VERTS, **kwargs)

    def cfg_lines(self):
        return (self.base / obj_run.CFG_NAME).read_text(encoding="utf-8").splitlines()

    def spec_rows(self):
        """读 build_script 落下的判据规格表（线格式 v2）的数据行。

        路径从 **CFG 的 MMD2MAX_VERDICTS** 取，不自己拼：Max 读的是 CFG 里那个文件，
        测试要是拼出另一个路径，就测不到「Python 把表交给了 Max 读不到的地方」。
        """
        text = Path(self.cfg_values()["MMD2MAX_VERDICTS"]).read_text(encoding="utf-8")
        header, rows = verdicts.parse_table(text)
        self.assertEqual(header, verdicts.SPEC_HEADER)
        return rows

    def spec(self, **kwargs):
        """先跑一次 build_script（规格表是它的副作用），再把那张表读回来。

        写成两步是因为规格表**必须**由 build_script 落盘：测试自己拼一份再读就测不到
        「Python 到底有没有把这张表交给 Max」。kwargs 一路透传给 build_script，
        所以 `self.spec(skin=False)` 量的就是 `--no-skin` 那一轮真正写出去的行。
        """
        self.script(**kwargs)
        return self.spec_rows()

    def header(self):
        """生成的 mmd2max_run.ms = 注入头 + 模板原文；这里只取注入头那一段。"""
        text = self.script()
        template = (PROJECT_ROOT / "scripts" / "max_import_obj.ms").read_text(encoding="utf-8")
        self.assertTrue(text.endswith(template), "注入头之后必须原样跟着模板，一个字都不改")
        return text[:len(text) - len(template)]

    def cfg_values(self):
        return dict(ln.split("\t", 1) for ln in self.cfg_lines()[1:])

    def test_generated_script_injects_only_the_cfg_path(self):
        head = self.header()
        assigned = re.findall(r"^global\s+(\S+)", head, flags=re.M)
        self.assertEqual(assigned, ["MMD2MAX_CFG"],
                         "注入头只许出现这一个全局名；每多一个就多一条能分叉的配置来源")
        self.assertIn("MMD2MAX.CFG", head)

    def test_cfg_covers_every_key_the_maxscript_reads(self):
        """两侧键集必须逐字相同 —— 少一个键 = Max 那侧「CFG 里没有键 X」直接中止。"""
        self.script()
        text = (PROJECT_ROOT / "scripts" / "max_import_obj.ms").read_text(encoding="utf-8")
        read = set(re.findall(r'mmd2maxCfg(?:Str|Bool|Int|NumOpt|Num)\s+"(MMD2MAX_\w+)"', text))
        written = set(ln.split("\t")[0] for ln in self.cfg_lines()[1:])
        self.assertEqual(read, written, "Max 取的键与 Python 写的键分叉")
        self.assertEqual(written, set(obj_run.CFG_KEYS))
        # 20（M1b 的路径与开关，#56 删掉恒为 true 的 QUIET 后）+ 7（M2a 的 targets）
        # + 2（M2b 的判据通道）+ 1（M3 的 CONVERGE_MAX）+ 2（#70 的胸部取景带）…
        # 这份加式只列到 M2b 那一代，之后每轮都在长（第 99 轮实测 43 项 = 上面这些
        # + 颈部带 + 脚盒带 + 回滚带 + STUBCM…），所以它只能当来历注记，别当等式核对。
        # 变小 = 有人把某项配齐检查删了。
        self.assertGreaterEqual(len(read), 30, "键集被人删小了自己还不知道")

    def test_cfg_fingerprint_goes_to_the_driver_stream(self):
        """CFG 会被下一次复跑覆盖，所以「这一轮读的是哪份配置」必须能事后算回来。

        第 77 轮就是这么踩上的：实跑（`--physics --shot`，非交互）之后我又跑了一次
        gen-only 复核，那一次把 `MMD2MAX.CFG` 覆盖成 `INTERACTIVE=true`、`香菱.max`
        （没有 `_phys` 后缀）、`MMD2MAX_PHYSICS` 空 —— 于是「冻结的 CFG」其实是上一代
        配置，而它看起来完全正常。日志里 `配置来自 …（43 项）` 只回声了 12 个值，
        认不出整份文件。现在 `build_script` 把**落盘那份 CFG 的字节**的 sha256 前 12 位
        打进驱动流（不是内存正文：`write_text` 在 Windows 上写的是 `\r\n`，两者不同，
        而复核的人手里只有盘上这份）：拿任何一份候选 CFG 重算一次，对上才是这一轮的。
        指纹**不进 CFG 文件本身** —— CFG 是 Max 逐键读的数据，往里加一条非键值的行
        等于给它加一条 Max 侧的读法规则（规则 IV 的「一个来源」也包括格式）。
        """
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.script(suffix="phys", shot=True, physics=True, interactive=False)
        out = buf.getvalue()
        found = re.search(r"配置指纹 sha256\[:12\]=([0-9a-f]{12}) 键数=(\d+)", out)
        self.assertTrue(found, f"驱动流里没有配置指纹那一行（前 200 字：{out[:200]}）")
        digest = hashlib.sha256((self.base / obj_run.CFG_NAME).read_bytes()).hexdigest()
        self.assertEqual(found.group(1), digest[:12],
                         "指纹算的不是落盘的那份 CFG —— 那它就没资格当这一轮的凭据")
        self.assertEqual(int(found.group(2)), len(obj_run.CFG_KEYS),
                         "指纹报的键数与 CFG_KEYS 分叉")

    def test_verdicts_channel_is_written_and_never_optional(self):
        """M2b：规格表随 CFG 一起落盘，且两个通道路径都不许是空串。

        「报告为空就跳过报告」是这条链上最坏的一种默认值：装配照样跑完、日志照样全绿，
        只是这一轮永远没法验收 —— 而没人会注意到「没证据」，因为缺证据不报错。
        """
        self.script()
        values = self.cfg_values()
        # CFG 键名 ↔ 文件名 是一对（M1b 的「一个词两边同名」在这层多一个分隔符）
        for key, file_name in (("MMD2MAX_VERDICTS", obj_run.SPEC_NAME),
                               ("MMD2MAX_REPORT", obj_run.REPORT_NAME)):
            self.assertNotIn(key, obj_run.CFG_EMPTY_OK,
                             "判据通道进了「允许空串」名单 = 验收可以被整段关掉")
            self.assertTrue(values[key], f"{key} 在 CFG 里是空值")
            self.assertEqual(Path(values[key]).name, file_name,
                             "键名与文件名必须同词，否则事后拼不出是谁家的文件")
        self.spec_rows()  # 顺手证明这张表能被 Python 侧的解析器读回来
        # 报告此刻**不该**存在：它由 Max 的 mmd2maxReportInit 建立。Python 预先写一个
        # 空壳的话，「Max 根本没跑起来」和「跑了但一行没报」就都是「只有表头」了。
        self.assertFalse(Path(values["MMD2MAX_REPORT"]).exists(),
                         "报告只能由被验收的那一侧写")

    def test_spec_expects_come_from_the_exported_tables(self):
        """规格表的期望值只能来自导出结果：顶点数、肢段行数、每段的 desired_len。

        这里是「尺」与「被量的东西」分家的证明 —— 哪天有人把 expected 改成从上一轮
        日志里读，这几条断言立刻红（fixture 的 limbs 与任何日志都无关）。
        """
        rows = {(r["phase"], r["check"], r["subject"]): r for r in self.spec()}
        self.assertEqual(rows["mesh", "vertex_count", "-"]["expected"], str(VERTS))
        self.assertEqual(rows["fit", "segment_count", "-"]["expected"], "3",
                         "肢段数期望 = limbs.tsv 的数据行数，不是 Max 报的「成功=」")
        for line in self.LIMBS[1:]:
            cells = line.split("\t")
            subject = f"{cells[0]} -> {cells[1]}"
            got = rows["segment", "len_cm", subject]
            self.assertEqual(got["expected"], cells[2], "段校验的尺必须逐字来自 limbs.tsv")
            self.assertEqual(got["kind"], "gate")
        # 脚/腿两类 mode 各自派生出的主体：导出里有、规格表里没有 = 判据漏装
        for key in (("thigh_roll", "knee_err_cm", "Bip001 L Thigh"),
                    ("toe_snap", "residual_after_roll_cm", "Bip001 L Toe0"),
                    ("foot_chain", "chain_angle_deg", "Bip001 L Foot -> Bip001 L Toe0"),
                    ("foot_chain", "gap_cm", "Bip001 L Foot -> Bip001 L Toe0"),
                    ("foot_chain", "gap_covered", "Bip001 L Foot -> Bip001 L Toe0")):
            self.assertIn(key, rows, f"limbs 里有 {key[2]}，规格表却没这一行 = 判据漏装")
        self.assertNotIn(("segment", "len_cm", "Bip001 R Foot -> Bip001 R Toe0"), rows,
                         "导出里没有的右脚，规格表不该凭空造出一行")
        # M4b：蒙皮回读的尺也只能来自导出表。fixture 的权重是「每顶点一根骨、骨号按顶点
        # 轮转」，所以链路总数 == 顶点数、每根骨的链路数能口算 —— 这两个数都不是 Max 给的。
        per_bone = -(-VERTS // BONES)                     # 向上取整：前 VERTS%BONES 根多一条
        self.assertEqual(rows["skin_rb", "links_expected", "-"]["expected"], str(VERTS))
        self.assertEqual(rows["skin_rb", "links_read", "-"]["expected"], str(VERTS),
                         "读回的链路总数与期望值必须是同一个数，否则两边量的不是同一件事")
        for nm in spec_table.FOOT_CHAIN_NAMES:
            self.assertEqual(rows["skin_rb", "links", nm]["expected"], str(per_bone),
                             f"{nm} 的链路尺没按骨骼表/权重表算")
        self.assertEqual(rows["skin", "bones", "-"]["expected"], str(BONES),
                         "Skin 里该有几根骨 = 骨骼表里 skin_flag≠0 的行数")
        # M4c：骨盒回读的尺来自 limbs 脚段第 10~18 列（foot_len/toe_len/tip_*/toe_w/
        # toe_h/foot_w/foot_h）与 widths.tsv 的 width 列。两处都是「导出文件说了什么」，
        # Max 那边量的是「最终场景里那个盒子多大」。
        cells = self.LIMBS[3].split("\t")
        self.assertEqual(rows["box_rb", "width_cm", "Bip001 L Foot"]["expected"], cells[16])
        self.assertEqual(rows["box_rb", "height_cm", "Bip001 L Foot"]["expected"], cells[17])
        self.assertEqual(rows["box_rb", "length_cm", "Bip001 L Toe0"]["expected"], cells[10])
        self.assertEqual(rows["box_rb", "hand_scale_cm", "Bip001 L Hand"]["expected"], "3.628600")

    def rewrite_meta(self, **overrides):
        """改 META 里那几行的值再落盘，其余行原样保留（含表头行）。"""
        lines = []
        for line in self.META:
            key, _, value = line.partition("\t")
            if key in overrides:
                value = overrides[key]
            lines.append(f"{key}\t{value}")
        (self.base / "t_meta.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def shot_rows(self):
        return {(r["phase"], r["check"], r["subject"]): r for r in self.spec()}

    def test_the_box_goes_to_cfg_and_the_bones_go_to_the_ruler(self):
        """#70/#71 的分工：框（Max 真去 zoom 的那只三轴盒）走 CFG，尺（骨自己的极值）走规格表。

        这两组数**必须不同**：规格表的 expected 一旦写成框，那条判据就退化成「CFG 里的数
        原样回来了没有」，而框里到底有没有那几根骨还是没人管 —— 第 35 轮就是这么全绿的。
        两张特写各走一遍：颈部那一组是 #71 新接上的，只测胸部就等于「同一套代码的另一半
        没被跑过」，而分叉恰恰长在没被跑过的那一半。
        """
        rows, cfg = self.shot_rows(), self.cfg_values()
        meta = {line.partition("\t")[0]: line.partition("\t")[2] for line in self.META}
        for prefix in obj_run.FRAMINGS:
            for axis in obj_run.FRAMING_AXES:
                cfg_key = f"MMD2MAX_{prefix.upper()}_{axis.upper()}_LO"
                self.assertEqual(cfg[cfg_key], meta[f"{prefix}_box_{axis}_lo"],
                                 "框的分量原样进 CFG：Max 用的就是它")
                lo = rows["shot_rb", f"{prefix}_box_lo", axis]
                hi = rows["shot_rb", f"{prefix}_box_hi", axis]
                i = obj_run.FRAMING_AXES.index(axis)
                self.assertEqual(lo["expected"], self.bones(prefix)[0][i])
                self.assertEqual(hi["expected"], self.bones(prefix)[1][i])
                self.assertEqual((lo["kind"], hi["kind"]), ("max", "min"))
                self.assertNotEqual(lo["expected"], cfg[cfg_key],
                                    "期望值变成了框自己 = 这条判据又成恒等式了")

    def bones(self, prefix: str = "chest"):
        """meta 里那六行极值，按 ((x,y,z)min, (x,y,z)max) 摆好 —— 就是尺该有的形状。"""
        values = {line.partition("\t")[0]: line.partition("\t")[2] for line in self.META}
        return (tuple(values[f"{prefix}_bone_{a}_min"] for a in obj_run.FRAMING_AXES),
                tuple(values[f"{prefix}_bone_{a}_max"] for a in obj_run.FRAMING_AXES))

    def test_the_ruler_does_not_follow_the_box(self):
        """把框的下沿推到骨头之上，尺纹丝不动 —— 那一轮的 `{prefix}_box_lo` 读数必须判红。

        y 轴是第 36 轮真正坏掉的那一根：z 已经量对了，y 还在拿场景包围盒凑，
        所以这条特意改 y，而不是重复第 35 轮那条 z 的事故。颈部（#71）同一件事各测一次：
        两条路径共用 `_ruler`，但它读的键不同，各钉一次才知道颈部真的接上了。
        """
        for prefix, expected in (("chest", "-11.7451"), ("neck", "-6.766848")):
            with self.subTest(prefix=prefix):
                self.rewrite_meta(**{f"{prefix}_box_y_lo": "-5.0000"})
                rows, cfg = self.shot_rows(), self.cfg_values()
                self.assertEqual(cfg[f"MMD2MAX_{prefix.upper()}_Y_LO"], "-5.0000",
                                 "框还是要原样进 CFG：Max 用的就是它")
                self.assertEqual(rows["shot_rb", f"{prefix}_box_lo", "y"]["expected"], expected,
                                 "规格表的尺跟着框跑了：这条判据已经看不见偏航")

    def test_each_axis_is_graded_against_its_own_axis(self):
        """三行各用各的极值：x 的尺不许拿去判 y —— 那是「顺序切片」最容易犯、也最难看的错。

        颈部的六个数与胸部没有一个相同（`-3.842957` vs `-4.9157` 这种贴近的除外），
        所以这两组各自钉死之后，「颈部行拿的是胸骨的数」这种错就无处藏。
        """
        rows = self.shot_rows()
        for prefix in obj_run.FRAMINGS:
            with self.subTest(prefix=prefix):
                self.assertEqual({rows["shot_rb", f"{prefix}_box_lo", a]["expected"]
                                  for a in obj_run.FRAMING_AXES},
                                 set(self.bones(prefix)[0]))
                self.assertEqual({rows["shot_rb", f"{prefix}_box_hi", a]["expected"]
                                  for a in obj_run.FRAMING_AXES},
                                 set(self.bones(prefix)[1]))

    def test_the_tight_span_is_graded_for_both_framings(self):
        """收框之后的那条水平跨度：六张 tight 图各按自己视图的水平轴、自己的尺判。

        这是 #70 的另一半 —— 框（收之前）罩得住不代表画面里有骨：第 36 轮的 y 就是被
        #69 朝框中心砍掉的那一段。轴按 mmd2max/verdicts.SHOT_H_AXIS 走：正视=x、左视=y。
        #71 之前颈部那两张只能 report（导出侧没有颈链的尺），现在两边的尺都在，
        一张特写也不许只报数不被判 —— 这里把「颈部的期望值 = 颈骨的极值」钉住。
        """
        rows = self.shot_rows()
        for tag in verdicts.SHOT_TIGHT_TAGS:
            prefix = next(p for p in obj_run.FRAMINGS if p in tag)
            axis = verdicts.SHOT_H_AXIS[tag]
            i = obj_run.FRAMING_AXES.index(axis)
            self.assertEqual(rows["shot_rb", "tight_h_lo", tag]["expected"],
                             self.bones(prefix)[0][i], f"{tag} 的水平轴不是 {axis}")
            self.assertEqual(rows["shot_rb", "tight_h_lo", tag]["kind"], "max")
            self.assertEqual(rows["shot_rb", "tight_h_hi", tag]["kind"], "min")

    def test_a_model_without_the_framing_bones_reports_instead_of_locking(self):
        """量不出 subject：CFG 十二个数写空串（Max 退回场景带），那 24 行全退成 report。"""
        blank = {k: "" for prefix in obj_run.FRAMINGS
                 for k in obj_run.box_keys(prefix)}
        blank.update({k: "" for k in obj_run.META_FOR_CHECK})
        self.rewrite_meta(**blank)
        rows, cfg = self.shot_rows(), self.cfg_values()
        for prefix in obj_run.FRAMINGS:
            for key in obj_run.framing_cfg_keys(prefix):
                self.assertEqual(cfg[key], "")
        checks = [(f"{prefix}_box_{edge}", axis) for prefix in obj_run.FRAMINGS
                  for edge in ("lo", "hi") for axis in obj_run.FRAMING_AXES]
        checks += [(c, t) for c in ("tight_h_lo", "tight_h_hi")
                   for t in verdicts.SHOT_TIGHT_TAGS]
        for check, subject in checks:
            row = rows["shot_rb", check, subject]
            self.assertEqual(row["kind"], "report", f"{check}/{subject}")
            self.assertEqual(row["expected"], "",
                             "report 行留了香菱的数字 = 拿一具模型的尺判另一具")

    def test_one_framing_can_be_missing_without_the_other(self):
        """只量得出胸骨的模型（没有映射到 Neck/Head 的骨）：颈部 12 行退 report，胸部照判。

        两把尺各归各的骨，就不该一损俱损 —— 反过来也一样。这条同时钉住「降级只降那一组」，
        换一具非 Biped 骨架的模型时不至于把已有判据整段丢掉。
        """
        self.rewrite_meta(**{k: "" for k in obj_run.box_keys("neck")},
                          **{k: "" for k in obj_run.bone_keys("neck")})
        rows = self.shot_rows()
        for axis in obj_run.FRAMING_AXES:
            self.assertEqual(rows["shot_rb", "neck_box_lo", axis]["kind"], "report")
            self.assertEqual(rows["shot_rb", "chest_box_lo", axis]["kind"], "max")
        for tag in verdicts.SHOT_TIGHT_TAGS:
            kind = "report" if "neck" in tag else "max"
            self.assertEqual(rows["shot_rb", "tight_h_lo", tag]["kind"], kind)

    def test_box_and_extremes_are_an_all_or_nothing_pair(self):
        """框与极值由同一段导出代码写出，只有一组在场就是两份产物分叉 —— 中止，不许降级。

        两个方向都查：有极值没框 → Max 会退回场景带而尺还在（判据注定红）；
        有框没极值 → 那 12 行悄悄退成 report，而「悄悄少一条判据」正是最难被发现的那类事故。
        """
        for overrides in ({k: "" for prefix in obj_run.FRAMINGS
                           for k in obj_run.box_keys(prefix)},
                          {k: "" for k in obj_run.META_FOR_CHECK}):
            with self.subTest(**overrides):
                self.rewrite_meta(**overrides)
                with self.assertRaises(SystemExit) as got:
                    self.spec()
                self.assertIn("分叉", str(got.exception),
                              "报错没说是分叉，重跑导出的人不知道该怀疑哪张表")

    def test_one_missing_axis_counts_as_a_fork(self):
        """半只框（五个数在、y 那一对空）就是第 36 轮的形状：不许按「有框」走下去。

        那一只框看起来是量出来的，日志里没人能看出来它缺一轴 —— 现在它整只退回场景带，
        而且摘要与规格表都会写明「没给框」。两张特写各测一次：分叉闸住在 `_ruler` 一处，
        两份键表都得从那儿过。
        """
        for prefix in obj_run.FRAMINGS:
            with self.subTest(prefix=prefix):
                self.rewrite_meta(**{f"{prefix}_box_y_lo": "", f"{prefix}_box_y_hi": ""})
                with self.assertRaises(SystemExit) as got:
                    self.spec()
                self.assertIn("分叉", str(got.exception))

    def test_self_inconsistent_extremes_are_fatal(self):
        """y_min > y_max 的 meta 已经不自洽：拿它当尺，下一轮无论怎么取景都是垃圾判定。"""
        self.rewrite_meta(chest_bone_y_min="-5.0000", chest_bone_y_max="-9.0000")
        with self.assertRaises(SystemExit) as got:
            self.spec()
        self.assertIn("大于", str(got.exception))
        self.assertIn("y", str(got.exception), "报错要说是哪一根轴，否则还得再猜一轮")

    def test_unreadable_extremes_are_fatal(self):
        self.rewrite_meta(neck_bone_z_min="颈")
        with self.assertRaises(SystemExit) as got:
            self.spec()
        self.assertIn("读不出数", str(got.exception))

    def test_box_scale_tolerance_is_the_writers_own_exit_criterion(self):
        """容差必须 == 写入侧 `mmd2maxFitBoneExtent` 的退出条件（2% 相对，0.02cm 地板）。

        尺比承诺严 ⇒ 每轮红在浮点分辨率上，判据就退化成噪音；尺比承诺松 ⇒ 装歪了也绿。
        这里拿 fixture 的足背宽 3.714245 当面，两个分支各钉一次。
        """
        rows = {(r["phase"], r["check"], r["subject"]): r for r in self.spec()}
        got = rows["box_rb", "width_cm", "Bip001 L Foot"]
        self.assertAlmostEqual(float(got["tol"]), 0.02 * 3.714245, places=6)
        # 脚尖高 1.860916 的 2% = 0.037cm，仍在地板之上；换成 0.5cm 这种小尺寸才轮到地板
        toe = rows["box_rb", "height_cm", "Bip001 L Toe0"]
        self.assertAlmostEqual(float(toe["tol"]), 0.02 * 1.860916, places=6)
        self.assertEqual(float(rows["box_rb", "read_fail", "-"]["tol"]), 0.0,
                         "读不到节点的容忍数必须是 0：量不到就是没验收")

    def test_foot_box_columns_missing_is_fatal(self):
        """脚段少了 foot_w/foot_h 这种列 ⇒ 中止，不许「那几行判据不装」。

        静默跳过等于把判据删掉，而删掉的判据不报错，只会让下一轮全绿（#24 那一类）。
        """
        lines = self.LIMBS[:]
        cells = lines[3].split("\t")
        lines[3] = "\t".join(cells[:16])          # 砍掉 foot_w/foot_h
        (self.base / "t_limbs.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as got:
            self.script()
        self.assertIn("foot_w", str(got.exception))

    def test_absent_widths_table_drops_only_the_hand_rows(self):
        """没有 widths.tsv 时，Max 跳过 apply_widths，规格表也不许留 hand_* 那几条尺。

        两边「一起没有」才对：Python 装了尺而 Max 那一段没跑，判据就永远红在缺席上。
        """
        (self.base / "t_widths.tsv").unlink()
        rows = {(r["phase"], r["check"], r["subject"]): r for r in self.spec()}
        self.assertFalse([k for k in rows if k[1].startswith("hand_")],
                         "粗细表没了还装 hand_scale_cm = 判一条不可能被报的读数")
        self.assertIn(("box_rb", "width_cm", "Bip001 L Foot"), rows,
                      "脚段的尺来自 limbs.tsv，与 widths 无关，不许跟着一起消失")

    def test_unpriced_model_gets_reports_not_locks(self):
        """锁（已定价的读数）只属于被定价的那具模型；换模型时退化成 report。

        5.6995 厘米是香菱的身高折出来的；拿它去判一具 80 厘米的角色，得到的是一屏
        假 FAIL —— 而假 FAIL 的代价是「以后没人再看这个表」。这条同时钉住
        「退化 ≠ 消失」：行还在、仍然要求出现，只是不卡数字。
        """
        other = self.populate(self.base / "别的模型", "t")
        obj_run.build_script(other, "t", VERTS)
        priced = self.populate(self.base / "香菱", "香菱")
        obj_run.build_script(priced, "香菱", VERTS)
        plain = verdicts.parse_table(
            (other / obj_run.SPEC_NAME).read_text(encoding="utf-8"))[1]
        locked = verdicts.parse_table(
            (priced / obj_run.SPEC_NAME).read_text(encoding="utf-8"))[1]
        self.assertNotIn("lock", {r["kind"] for r in plain},
                         "fixture 不是被定价的那具模型，一条锁都不许有")
        self.assertIn("lock", {r["kind"] for r in locked})
        self.assertEqual(len(plain), len(locked),
                         "两个版本必须**同样多**的行：锁退化成人必须还量，不是删掉")
        self.assertEqual({(r["phase"], r["check"], r["subject"]) for r in plain},
                         {(r["phase"], r["check"], r["subject"]) for r in locked})

    def test_footing_the_reach_switch_off_relabels_the_rows(self):
        """FOOTREACH=false 时那一遍整段不跑：对应判据行必须退成 if_present。

        写成「删掉那些行」的话，「整段跳过」和「跑挂了没报」在表里就长得一样。
        开关只从 CFG 走，所以这里改的是 targets 的来源，而不是 build_spec 的参数。
        """
        on = {(r["phase"], r["check"], r["subject"]): r["when"] for r in self.spec()}
        original = obj_run.target_cfg_values
        obj_run.target_cfg_values = lambda: {
            **original(), "MMD2MAX_FOOTREACH": "false"}
        try:
            off = {(r["phase"], r["check"], r["subject"]): r["when"]
                   for r in self.spec()}
        finally:
            obj_run.target_cfg_values = original
        self.assertEqual(set(on), set(off), "关掉开关不该少行，只该少「必须出现」")
        changed = {k for k in on if on[k] != off[k]}
        # 守卫（M4d）整段住在接趾里：基准拍在接趾动手之前，退回之后复摆量的才是退完的
        # 场景。所以 FOOTREACH=false 时它没有东西可守，那六行跟着松——这是**同一段代码**
        # 的开关，不是串了门控。只有本来就是「必须出现」的行才需要松，所以名单从 `on`
        # 推（ROLLBACK=false 的默认轮里守卫行本来就是 if_present，一行都不该动）。
        guard = {("rollback", check, "-") for check in
                 ("guard_saved", "foot_before_cm", "foot_after_cm",
                  "guard_fired", "foot_worse_bones")}
        guard |= {("pass", "ran", "foot_reach_guard")}
        expected = {("toe_snap", "residual_after_reach_cm", "Bip001 L Toe0"),
                    ("pass", "ran", "foot_reach"),
                    ("pass", "ran", "toe_snap_reach")}
        expected |= {k for k in guard if on[k] == "always"}
        self.assertEqual(changed, expected,
                         "只有接趾那一遍的读数行和它自己的收工边界（外加住在同一段里的"
                         "守卫）会变，别的行跟着变就是串了门控")
        self.assertEqual(off["toe_snap", "residual_after_reach_cm", "Bip001 L Toe0"],
                         "if_present")
        back = {(r["phase"], r["check"], r["subject"]): r["when"] for r in self.spec()}
        self.assertEqual(back, on, "还原开关后必须逐行回到原样（patch 漏还原也是事故）")

    def test_no_skin_marks_the_skin_and_mesh_rows_optional(self):
        """--no-skin 冒烟轮：Skin 与顶点数那四行整段不会有，退成 if_present 而不是删掉。"""
        on = {(r["phase"], r["check"], r["subject"]): r["when"] for r in self.spec()}
        off = {(r["phase"], r["check"], r["subject"]): r["when"]
               for r in self.spec(skin=False)}
        self.assertEqual(set(on), set(off))
        core = {k for k in on if k[0] in ("skin", "mesh")}
        self.assertEqual(core,
                         {("mesh", "vertex_count", "-"), ("skin", "bones", "-"),
                          ("skin", "failed_vertices", "-"),
                          ("skin", "written_vertices", "-")})
        # M4b：回读那一整段（聚合读数 + 每根脚骨的链路）与写入计数同生同死 ——
        # 蒙皮没跑，Skin 修改器就不存在，这些行一条都量不出来。漏一条在 skin=False
        # 时仍写 always，冒烟轮会因为「读数缺席」而整轮判红。
        readback = {k for k in on if k[0] == "skin_rb"}
        self.assertEqual(len(readback), 24,
                         "回读判据行数变了：20 条聚合（#76 起多一条 zero_links）"
                         "+ 每根脚链骨 1 条链路")
        skin_keys = core | readback
        for key in skin_keys:
            self.assertEqual((on[key], off[key]), ("always", "if_present"))
        # 蒙皮那一遍的收工边界和它的读数一起松：`--no-skin` 冒烟轮没跑那段，
        # 时间轴审计要能认出「这段按设计没跑」而不是「这段跑挂了没人报」。
        changed = {k for k in on if on[k] != off[k]}
        self.assertEqual(changed,
                         skin_keys | {("pass", "ran", "skin_apply"),
                                      ("pass", "ran", "skin_verify")},
                         "与蒙皮无关的行不许跟着变 —— 否则 --no-skin 顺手把别的判据也放了")

    def test_missing_spec_source_in_meta_is_fatal(self):
        """kept_bones / morph_count 缺一个就中止：没有尺的判据行比没有判据更坏。"""
        lines = [ln for ln in self.META if not ln.startswith("kept_bones\t")]
        (self.base / "t_meta.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as got:
            self.script()
        self.assertIn("kept_bones", str(got.exception),
                      "报错要指名缺哪个键，否则重跑导出的人无从下手")

    def test_diverged_bone_table_is_fatal(self):
        """骨骼表的行数与 meta 的 kept_bones 对不上 → 中止，不许「按哪份算都行」。

        M4b 之前 kept_bones 直接顶在 `skin/bones` 的期望值上，所以这两份分叉没人看得见的
        机会是 100%。现在 kept_bones 只做交叉核对，期望值来自骨骼表的 skin_flag ——
        核对失败说明**输入本身**已经不是同一份导出了，装出来的判据全是垃圾，不如不跑。
        """
        text = (self.base / "t_bones.tsv").read_text(encoding="utf-8")
        lines = text.rstrip("\n").split("\n")
        (self.base / "t_bones.tsv").write_text("\n".join(lines[:-1]) + "\n",
                                               encoding="utf-8")
        with self.assertRaises(SystemExit) as got:
            self.script()
        self.assertIn("分叉", str(got.exception))

    def test_weights_row_count_must_equal_vertex_count(self):
        """权重表行数 ≠ CFG 的顶点数 → 中止：权重按顶点序号写入，错一行 = 全表错位（#13）。"""
        path = self.base / "t_weights.tsv"
        path.write_text(path.read_text(encoding="utf-8").rstrip("\n").rsplit("\n", 1)[0]
                        + "\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as got:
            self.script()
        self.assertIn("错位", str(got.exception),
                      "报错要说清后果（整表错位），只喊「行数不对」没人知道严重性")

    def test_target_values_reach_the_cfg(self):
        """M2：判据数值也走同一条单源路 —— targets.py → CFG → Max 读。

        只钉「键在不在」不够：值格式写错（Python 的 True、或者 3 而不是 3.0）在 Max 侧
        报的是「值不是 true/false / 不是数字」，看起来像手改坏了 CFG，不像接线错。
        """
        self.script()
        values = self.cfg_values()
        from mmd2max import targets
        for key, text in targets.cfg_values().items():
            self.assertEqual(values[key], text, f"{key} 落进 CFG 的值与 targets.py 不同")

    def test_cfg_is_self_describing_wire_format_v2(self):
        self.script()
        lines = self.cfg_lines()
        self.assertEqual(lines[0], "key\tvalue", "第 1 行必须是列名（线格式 v2）")
        self.assertEqual([ln.split("\t")[0] for ln in lines[1:]], list(obj_run.CFG_KEYS),
                         "顺序即 CFG_KEYS，重跑之间逐行可比")

    def test_vertex_count_and_switches_land_in_cfg(self):
        self.script(skin=False, shot=True, physics=False)
        values = self.cfg_values()
        self.assertEqual(values["MMD2MAX_VERTS"], str(VERTS))
        self.assertEqual(values["MMD2MAX_SKIN"], "false")
        self.assertEqual(values["MMD2MAX_SHOT"], "true")
        self.assertEqual(values["MMD2MAX_INTERACTIVE"], "false",
                         "自动跑永远非交互：跑完自己 quitMax，不留模态框等人点")

    def test_manual_and_gen_only_are_the_only_interactive_paths(self):
        """交互位必须真的跟着 --manual / --gen-only 走（#56）。

        这个键以前恒为 false，于是 .ms 里三段「给人留现场」的分支一次也没跑到过：
        --manual 的提示语写着「请手动在 Max 中查看 / 截图 / 调试」，脚本其实跑完就
        quitMax；--gen-only 更是在用户自己的 Max 里一句不问就 resetMaxFile ——
        那一声 queryBox 是唯一能挡住「没存的活儿被清空」的东西。
        """
        for kwargs in ({"interactive": True}, {"interactive": False}, {}):
            self.script(**kwargs)
            got = self.cfg_values()["MMD2MAX_INTERACTIVE"]
            self.assertEqual(got, "true" if kwargs.get("interactive") else "false",
                             f"{kwargs} -> MMD2MAX_INTERACTIVE={got}")

    def test_no_cfg_bool_is_written_as_a_constant(self):
        """开关若只能取一个值，它就不是开关，是把某段代码永久短路（#56 的 QUIET）。

        MMD2MAX_QUIET 当时恒写 "true"，唯一用处是让 mmd2maxPrepareScene 的
        「要重置你的场景，问一句」永远不弹 —— 而 --gen-only 恰恰是在用户自己的 Max
        里跑。这类「看着可配、实际不可配」的键比没有键更坏：读代码的人以为翻得动它。
        判据写成通用规则：build_cfg 里任何 bool 值都必须来自条件表达式。
        """
        text = (PROJECT_ROOT / "tools" / "obj_run.py").read_text(encoding="utf-8")
        const = re.findall(r'"(MMD2MAX_\w+)":\s*"(true|false)",', text)
        self.assertEqual(const, [], f"CFG 里出现恒值布尔开关：{const}")
        # 恒值键与被删的读取者都不许回来
        self.assertNotIn("MMD2MAX_QUIET", obj_run.CFG_KEYS)
        template = (PROJECT_ROOT / "scripts" / "max_import_obj.ms").read_text(encoding="utf-8")
        self.assertNotIn("mmd2maxIsQuiet", template, ".ms 侧的读取者要一起删干净")
        self.assertIn('if (mmd2maxIsInteractive()) and objects.count > 0 do', template,
                      "重置确认必须由「有没有人盯着」这一个条件决定，别再叠恒值条件")

    def test_paths_are_written_raw_and_escaped_only_in_the_script(self):
        """CFG 是数据文件 ⇒ 原始反斜杠；转义只发生在 .ms 那一行赋值上。
        混在同一层里就会「转义两次」，Max 打开一个不存在的文件。"""
        self.script()
        head = self.header()
        values = self.cfg_values()
        self.assertIn('\\\\', head, "注入头里的 CFG 路径必须是转义过的")
        self.assertNotIn('\\\\', values["MMD2MAX_OBJ"], "数据文件里不许出现转义")
        self.assertTrue(Path(values["MMD2MAX_OBJ"]).is_file(),
                        f"CFG 写的路径必须真存在：{values['MMD2MAX_OBJ']}")

    def test_missing_limbs_file_is_fatal(self):
        """肢段表是闭环缩放的唯一事实来源：读不到就抛，不许退回「Biped 默认比例 + 日志绿」。
        而表情/物理/粗细这三项允许空串 = 整段跳过（名单与 .ms 的 CFG_EMPTY_OK 一致）。"""
        (self.base / "t_limbs.tsv").unlink()
        with self.assertRaises(SystemExit) as got:
            self.script()
        self.assertIn("MMD2MAX_LIMBS", str(got.exception))
        (self.base / "t_limbs.tsv").write_text("x\n", encoding="utf-8")
        self.script()
        values = self.cfg_values()
        self.assertEqual(values["MMD2MAX_MORPHS"], "", "没导出表情就该是空串")
        # 粗细表在这套 fixture 里是**真导出**的（M4c 的 hand_scale_cm 就靠它装尺），
        # 所以先验它落的是路径；删掉之后必须退成空串 = Max 那侧整段跳过，
        # 而不是抛「打不开文件」——名单 CFG_EMPTY_OK 与 .ms 的 widths.count == 0 分支
        # 是同一件事的两种写法，这里把它钉在一起。
        self.assertTrue(Path(values["MMD2MAX_WIDTHS"]).is_file())
        (self.base / "t_widths.tsv").unlink()
        self.script()
        self.assertEqual(self.cfg_values()["MMD2MAX_WIDTHS"], "",
                         "没导出粗细表就该是空串，让 apply_widths 整段跳过")

    def test_missing_meta_key_is_fatal_not_defaulted(self):
        lines = [ln for ln in self.META if not ln.startswith("spine_links\t")]
        (self.base / "t_meta.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as got:
            self.script()
        self.assertIn("spine_links", str(got.exception),
                      "报错要指名缺哪个键，否则重跑导出的人无从下手")

    def test_cfg_error_file_alone_is_a_failure_code(self):
        """CFG 没读成时 MaxScript 连 MARKER 都还没拿到，只能落到定名文件。
        这条路径若不算失败，配置错误就退化成「超时 + 一句看不懂的日志」。"""
        (self.base / obj_run.CFG_ERROR_NAME).write_text("Runtime error: 配置不存在",
                                                        encoding="utf-8")
        self.assertEqual(obj_run.result_code(self.base, "t"), 4)

    def test_error_file_outranks_a_late_marker(self):
        self.write_marker_pair()
        (self.base / "t.success.json.error.txt").write_text("Runtime error: 截图炸了",
                                                            encoding="utf-8")
        self.assertEqual(obj_run.result_code(self.base, "t"), 4,
                         "后置步骤抛错的轮次不许报 0（.max 可用，但不是全绿）")

    def write_marker_pair(self):
        (self.base / "t.success.json").write_text("{}", encoding="utf-8")
        (self.base / "t.max").write_bytes(b"x")

    def test_full_success_still_returns_zero(self):
        self.script()
        self.write_marker_pair()
        self.assertEqual(obj_run.result_code(self.base, "t"), 0)


class StartupDialogTests(unittest.TestCase):
    """启动弹窗只许按「确切按钮名」点，绝不再猜坐标。

    "Startup Failure Detection" 是 Qt 窗口（实测 0 个子 HWND），旧写法截图找
    最右侧色块再按坐标点 —— 那个位置是 "Restore Factory Settings…"，点下去
    把用户的 Max 设置清零。现在只认白名单按钮名，名字不在就什么都不做。
    """

    def setUp(self):
        self._find = obj_run.find_windows
        self._capture = obj_run.capture_region
        self._invoke = obj_run.invoke_safe_button
        self._sleep = obj_run.time.sleep
        obj_run.time.sleep = lambda *_a, **_k: None
        self.calls = []
        self.captures = []

    def tearDown(self):
        obj_run.find_windows = self._find
        obj_run.capture_region = self._capture
        obj_run.invoke_safe_button = self._invoke
        obj_run.time.sleep = self._sleep

    def install(self, titles, ok, names, shown=True):
        obj_run.find_windows = lambda needle: [
            win(t) for t in titles if needle.lower() in t.lower()]
        obj_run.capture_region = lambda *a, **k: self.captures.append(a)

        def fake_invoke(win_name, button):
            self.calls.append((win_name, button))
            return ok, {"windows": [{"name": win_name, "class": "Qt5QWindowIcon"}]
                        if shown else [],
                        "buttons": [{"name": n, "enabled": True} for n in names],
                        "invoked": button if ok else None, "error": None}
        obj_run.invoke_safe_button = fake_invoke

    def test_whitelist_has_no_destructive_button(self):
        values = " ".join(obj_run.SAFE_STARTUP_BUTTONS.values()).lower()
        self.assertNotIn("restore factory", values)
        self.assertNotIn("cancel", values)
        self.assertEqual(obj_run.SAFE_STARTUP_BUTTONS["Startup Failure"],
                         "Continue Without Restore",
                         "这个框里唯一「什么都不改」的按钮，实测名就是这个")

    def test_clicks_only_the_named_safe_button(self):
        self.install(["Startup Failure Detection"], True,
                     ["Continue Without Restore", "Restore Factory Settings…", "Cancel"])
        self.assertTrue(obj_run.handle_startup_dialog())
        # 两个白名单框各查一次（判定权在 UIA，不再靠 Win32 标题有没有匹配上）
        self.assertEqual(self.calls, [("Startup Failure", "Continue Without Restore"),
                                      ("Heads Up", "Continue")])

    def test_guesses_nothing_when_name_missing(self):
        """名字对不上（比如换了语言版/新版本按钮改名）就只记日志，不许有第二次尝试。"""
        self.install(["Startup Failure Detection"], False, ["继续但不恢复", "恢复出厂设置"])
        self.assertFalse(obj_run.handle_startup_dialog())
        self.assertEqual(len(self.calls), 2, "每个白名单条目各查一次，不许再退回去猜坐标")

    def test_heads_up_dialog_is_handled(self):
        """实测第二道拦路的是 "Heads Up! The 3ds Max default settings have changed"。"""
        self.install(["Heads Up! The 3ds Max default settings have changed"], True,
                     ["Continue", "Restore"])
        self.assertTrue(obj_run.handle_startup_dialog())
        self.assertIn(("Heads Up", "Continue"), self.calls)

    def test_no_dialog_is_silent(self):
        """UIA 说没有这个窗口 = 常态：不许点任何东西，也不许截图。"""
        self.install([], True, [], shown=False)
        self.assertFalse(obj_run.handle_startup_dialog())
        self.assertEqual(self.captures, [])

    def test_startup_dialog_is_probed_on_a_throttle(self):
        """一次 UIA 查询要起一个 PowerShell，轮询里必须按 STARTUP_DIALOG_EVERY 节流。"""
        source = Path(obj_run.__file__).read_text(encoding="utf-8")
        self.assertIn("ticks % STARTUP_DIALOG_EVERY", source)
        self.assertGreater(obj_run.STARTUP_DIALOG_EVERY, 1)


class GracefulExitTests(unittest.TestCase):
    """taskkill /F 会被 Max 记成异常终止，攒够就每次启动弹崩溃对话框，
    把 -U MAXScript 的自动执行整个挡死（用户不在电脑前=死锁）。
    所以它只能是「Max 没自己退」时的兜底。"""

    class FakeProc:
        def __init__(self, exit_after):
            self.exit_after = exit_after
            self.ticks = 0

        def poll(self):
            self.ticks += 1
            return 0 if self.ticks > self.exit_after else None

    def test_returns_true_once_process_is_gone(self):
        proc = self.FakeProc(exit_after=2)
        self.assertTrue(obj_run.wait_max_exit(proc, timeout=30))

    def test_returns_false_on_timeout_and_kills_nobody_itself(self):
        proc = self.FakeProc(exit_after=10 ** 6)
        self.assertFalse(obj_run.wait_max_exit(proc, timeout=0.05))

    def test_fallback_kill_targets_the_pid_not_the_image_name(self):
        """兜底强杀只许杀自己起的那棵树。

        `/IM 3dsmax.exe` 是按镜像名杀：用户自己开着的 3ds Max（未保存的场景）会被
        一起强杀，而且写死的镜像名绕过了 §7 的发现层 —— 装在别处或换年份时连本轮那个
        进程都杀不到。argv 单独成函数就是为了这条能被断言。
        """
        self.assertEqual(obj_run.kill_max_tree(4321),
                         ["taskkill", "/F", "/T", "/PID", "4321"])
        self.assertNotIn("/IM", obj_run.kill_max_tree(4321))
        source = Path(obj_run.__file__).read_text(encoding="utf-8")
        self.assertIn("subprocess.run(kill_max_tree(proc.pid)", source,
                      "兜底强杀必须走 kill_max_tree，不许在调用点另拼一份 argv")


class StaleRoundCleanupTests(unittest.TestCase):
    """上一轮的读数文件删不掉 ⇒ 不开跑。

    `obj_run` 每轮开头删 REPORT/CHECK/标记，靠的是「这一轮的读数一定不是上一轮的」。
    上一轮走 `dialog_seen` 分支时 Max 是**故意留着**的（保留报错现场），它占着那些文件时
    `unlink` 抛 PermissionError —— 以前是一条裸 traceback 顶掉整轮，改窄一点就变成
    「删失败了但照样开跑」，那比崩溃更坏：校验器会把上一轮的绿读成这一轮的绿。
    """

    def make(self, names):
        return [Path(self.tmp.name) / name for name in names]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_absent_files_are_not_a_failure(self):
        """missing_ok 的语义要保住：正常情况下这些文件本来就不全在。"""
        self.assertIsNone(obj_run.clear_stale_round(self.make(["MMD2MAX.REPORT"])))

    def test_deletes_everything_it_can(self):
        paths = self.make(["a.success.json", "MMD2MAX.REPORT", "MMD2MAX.CHECK"])
        for path in paths:
            path.write_text("x", encoding="utf-8")
        self.assertIsNone(obj_run.clear_stale_round(paths))
        for path in paths:
            self.assertFalse(path.exists())

    def test_a_locked_file_is_reported_not_swallowed(self):
        """占用 ⇒ 返回「是谁卡的 + 为什么」，调用方据此中止，不许静默继续。"""
        paths = self.make(["a.success.json", "MMD2MAX.REPORT"])
        paths[0].write_text("x", encoding="utf-8")
        # 目录占了 REPORT 的名字：unlink 必抛 OSError（IsADirectoryError），
        # 这正是「上一轮的 Max 还占着文件」在测试里的等价物。
        paths[1].mkdir()
        stuck = obj_run.clear_stale_round(paths)
        self.assertIsNotNone(stuck, "删不掉必须报出来，不许当没事")
        path, error = stuck
        self.assertEqual(path.name, "MMD2MAX.REPORT")
        self.assertIsInstance(error, OSError)
        self.assertFalse(paths[0].exists(), "卡住之前能删的还是要删")


class BaselineWhitelistTests(unittest.TestCase):
    """`check_baseline.py` 的改名白名单只许认得那一对旧键，别的都必须判失败。

    任何「白名单」都是让判据闭嘴的机制，所以它自己必须被钉住：M1b 把三重反转压成直读，
    于是 `foot_pitch=0` 与 `foot_box_flat=true` 是同一个几何的两种写法（旧值取反、新值取正），
    这一对可以放过；`spine_links` 少一节、`vertex_count` 差一个都必须照旧报「数值变了」。
    """

    def setUp(self):
        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        import check_baseline
        self.mod = check_baseline

    def rename(self, *lines):
        return self.mod.rename_meta_keys(list(lines))

    def test_the_one_priced_rename_is_recognised(self):
        out, notes = self.rename("foot_pitch\t0")
        self.assertEqual(out, ["foot_box_flat\ttrue"])
        self.assertEqual(notes, ["foot_pitch=0 -> foot_box_flat=true"],
                         "改写必须逐条打印，不许静默放过")

    def test_the_flattening_semantics_match(self):
        """旧键是「俯仰」= 压平的反值，新键是「压平」本身；两边指向同一根轴。"""
        old_pitch = False            # foot_pitch 0 ⇒ 不俯仰 ⇒ 压平
        self.assertEqual(str(not old_pitch).lower(),
                         self.mod.META_RENAMES[("foot_pitch", "0")][1])

    def test_any_other_meta_change_survives(self):
        for line in ("spine_links\t2", "vertex_count\t18893", "foot_box_flat\tfalse",
                     "foot_pitch\t1", "fingers\t4"):
            out, notes = self.rename(line)
            self.assertEqual(out, [line], f"{line} 不该被白名单改写")
            self.assertEqual(notes, [])

    def test_whitelist_is_not_a_blanket_meta_pass(self):
        """rename 之后仍然走原来的逐行相等判定：改了一个数就得出差异。"""
        out, _ = self.rename("foot_pitch\t0", "spine_links\t3")
        self.assertNotEqual(out, ["foot_box_flat\ttrue", "spine_links\t2"])


class MaxDiscoveryTests(unittest.TestCase):
    """装 Max 在哪是机器的事，不许写死在代码里。

    原来 tools/obj_run.py 里是 `C:\\Program Files\\Autodesk\\3ds Max 2022\\3dsmax.exe`
    一个字面量。换一台装在别的盘的机器（用户给的样子是装在 D 盘的那个 Programs 根下）
    就整条链路起不来，而报的错只是「找不到文件」——使用者分不清是自己机器没装好、
    还是脚本坏了。这一组盯的是发现顺序本身：环境变量 -> 注册表 -> 常见目录。
    """

    def test_no_source_hardcodes_an_install_path(self):
        for rel in ("tools/obj_run.py", "tools/gui.py", "启动GUI.bat"):
            text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn(
                "Program Files\\Autodesk\\3ds Max", text,
                f"{rel} 里又出现了写死的装目录：那正是换机器就起不来的那一条")

    def test_launch_uses_the_discovered_exe(self):
        """两条启动路径都只能吃发现结果，不许再引用常量。"""
        text = (PROJECT_ROOT / "tools" / "obj_run.py").read_text(encoding="utf-8")
        self.assertIn('proc = subprocess.Popen([str(max_exe)', text)
        self.assertIn('cmd /c start "" "{max_exe}"', text)

    def test_env_override_wins_but_only_for_a_real_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "3dsmax.exe"
            exe.write_text("not really max", encoding="utf-8")
            import os
            old = os.environ.get(obj_run.MAX_EXE_ENV)
            try:
                os.environ[obj_run.MAX_EXE_ENV] = str(exe)
                self.assertEqual(obj_run.find_max_exe(), exe)
                # 指到不存在的路径就返回 None：宁可不启动，也不悄悄忽略用户明写的
                # 那条路径、换成注册表里的另一台 Max。
                os.environ[obj_run.MAX_EXE_ENV] = str(Path(tmp) / "nope.exe")
                self.assertIsNone(obj_run.find_max_exe())
            finally:
                if old is None:
                    os.environ.pop(obj_run.MAX_EXE_ENV, None)
                else:
                    os.environ[obj_run.MAX_EXE_ENV] = old

    def test_hint_names_the_env_var(self):
        self.assertIn(obj_run.MAX_EXE_ENV, obj_run.MAX_EXE_HINT)

    def test_verified_version_beats_a_newer_one(self):
        """判据全在 2022 上量的；找到 2024 也不许当成「更好的那个」默默启动。"""
        reg, glob = obj_run._reg_max_exes, obj_run._glob_max_exes
        try:
            obj_run._reg_max_exes = lambda: [(2024, Path("D:/new/3dsmax.exe"))]
            obj_run._glob_max_exes = lambda: [(2022, Path("D:/old/3dsmax.exe"))]
            self.assertEqual(obj_run.find_max_exe(), Path("D:/old/3dsmax.exe"))
            obj_run._reg_max_exes = lambda: [(2019, Path("D:/a/3dsmax.exe"))]
            obj_run._glob_max_exes = lambda: [(2021, Path("D:/b/3dsmax.exe")),
                                              (2018, Path("D:/c/3dsmax.exe"))]
            self.assertEqual(obj_run.find_max_exe(), Path("D:/b/3dsmax.exe"),
                             "没有已验证版本时按新到旧取，且每次结果要稳定")
        finally:
            obj_run._reg_max_exes, obj_run._glob_max_exes = reg, glob

    def test_version_reads_from_the_folder_name(self):
        self.assertEqual(
            obj_run.max_year_of_exe(Path("D:/Programs/Autodesk/3ds Max 2022/3dsmax.exe")),
            2022)
        self.assertIsNone(obj_run.max_year_of_exe(Path("D:/Max/3dsmax.exe")),
                          "读不出就不猜 —— 猜一个年份会连带把启动目录也指错")

    def test_startup_dirs_follow_the_found_version(self):
        """启动目录名带版本和语言（`<年> - 64bit\\ENU`），只能按实际存在的扫。"""
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Autodesk" / "3dsMax"
            for name in ("2022 - 64bit/ENU", "2022 - 64bit/CHS", "2024 - 64bit/ENU"):
                p = root / name / "scripts" / "startup"
                p.mkdir(parents=True)
            (root / "2022 - 64bit/no_scripts").mkdir()
            old = os.environ.get("LOCALAPPDATA")
            try:
                os.environ["LOCALAPPDATA"] = tmp
                got = obj_run.max_startup_dirs(
                    Path("D:/Programs/Autodesk/3ds Max 2022/3dsmax.exe"))
                self.assertEqual([str(p.relative_to(root)) for p in got],
                                 ["2022 - 64bit\\CHS\\scripts\\startup",
                                  "2022 - 64bit\\ENU\\scripts\\startup"])
                self.assertEqual(
                    obj_run.max_startup_dirs(
                        Path("D:/Programs/Autodesk/3ds Max 2024/3dsmax.exe")),
                    [root / "2024 - 64bit" / "ENU" / "scripts" / "startup"])
            finally:
                if old is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old


class UiaParserTests(unittest.TestCase):
    def test_parse_lines_maps_channels(self):
        import uia_dialog
        info = uia_dialog.parse_lines([
            "WIN|Startup Failure Detection|Qt5QWindowIcon",
            "BTN|Continue Without Restore|enabled=True",
            "BTN|Restore Factory Settings…|enabled=True",
            "INVOKED|Continue Without Restore",
        ])
        self.assertEqual(info["windows"][0]["class"], "Qt5QWindowIcon")
        self.assertEqual([b["name"] for b in info["buttons"]],
                         ["Continue Without Restore", "Restore Factory Settings…"])
        self.assertTrue(info["buttons"][0]["enabled"])
        self.assertEqual(info["invoked"], "Continue Without Restore")
        self.assertIsNone(info["error"])

    def test_no_window_is_not_an_error(self):
        import uia_dialog
        info = uia_dialog.parse_lines(["NO_WINDOW"])
        self.assertEqual(info["windows"], [])
        self.assertIsNone(info["invoked"])
        self.assertIsNone(info["error"], "没弹窗是正常状态，不许报错误导日志")

    def test_invoke_failure_is_surfaced(self):
        import uia_dialog
        info = uia_dialog.parse_lines(["INVOKE_FAILED|Continue|boom"])
        self.assertIn("boom", info["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
