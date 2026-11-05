"""`tools/check_ms.py` 自己的测试 —— 它是「静态体检」这条路唯一的执行者，
它漏一条，那条就得靠一轮实跑去撞（一轮 = 启动 Max + 装配 + 回读，几分钟）。

钉住的都是**真死过**的写法，不是照文档想象出来的清单：
  * `shape`  —— 第一次 M4 探针（probes/readback_m4.txt）跑到那行才抛
    「Cannot assign to read-only variable: shape」，编译期一点动静没有；
  * `snapshot` —— M4g 探针（probes/figfile_m4g.txt）同一句死法，函数名撞内建。
所以 RESERVED 只收实测撞过的名字：抄一份「完整保留字表」会让人以为查得全，
而真正的名单里有一百多个词，查不全比查得假安全。
"""
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import check_ms                                          # noqa: E402


class ReservedNameTests(unittest.TestCase):
    def test_fn_name_colliding_with_builtin_is_flagged(self):
        problems = check_ms.check_reserved("fn snapshot sn =\n(\n    local a = 1\n)\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("行 1", problems[0])

    def test_local_and_global_and_param_forms_are_all_covered(self):
        src = ("local shape = 1\n"
               "global ok = 2\n"
               "fn f a mapped =\n(\n    a + mapped\n)\n")
        problems = check_ms.check_reserved(src)
        found = " ".join(problems)
        for name in ("shape", "ok", "mapped"):
            self.assertIn(name, found, f"三种声明形式里 {name} 必须被抓到：{problems}")

    def test_reserved_is_case_insensitive(self):
        """Max 的内建类叫 `Shape`，写 `shape` 一样撞 —— 判定必须忽略大小写。"""
        self.assertTrue(check_ms.check_reserved("fn Shape x = x"))
        self.assertTrue(check_ms.check_reserved("fn shape x = x"))

    def test_ordinary_names_are_not_flagged(self):
        src = ("fn mmd2maxFitBoneExtent node axis target =\n(\n    local totalLen = 0.0\n)\n")
        self.assertEqual(check_ms.check_reserved(src), [])

    def test_only_names_proven_by_a_real_death_are_in_the_list(self):
        """RESERVED 的准入条件：有探针或实跑的报错文字。新增必须能说出是哪一轮。"""
        for name, where in (("shape", "probes/readback_m4"),
                            ("snapshot", "probes/figfile_m4g"),
                            ("box", "probes/figfile_m4g")):
            self.assertIn(name, check_ms.RESERVED, f"{name} 是 {where} 实测撞到的")
        # 注释里写死了「只认实测」，这里就钉住它没被拿文档清单灌水：
        # 内建函数名成千上万，全抄进来会让体检报告变成噪音，真撞到的那条反而看不见。
        self.assertLess(len(check_ms.RESERVED), 70,
                        "保留字名单超过 70 个就该问一句：每个都有实测报错吗？")

    def test_box_shadows_the_builtin_in_a_local_declaration(self):
        """`local box = Box name:...` 这一类不报编译错，只在运行期把 `Box` 读成
        那个还没值的 local —— 读数假安全。探针 v2 实测，见 probes/figfile_m4g.txt 头部。"""
        problems = check_ms.check_reserved(
            'local box = try ( Box name:"W" ) catch undefined\n')
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("box", problems[0])

    def test_commented_out_collision_is_not_flagged(self):
        src = "-- fn snapshot x = x\nfn realName x = x\n"
        self.assertEqual(check_ms.check_reserved(src), [])


class EmptyCatchTests(unittest.TestCase):
    """规则 V：空 catch 必须留下痕迹，或显式声明「允许失败」。"""

    def test_bare_empty_catch_is_flagged(self):
        self.assertTrue(check_ms.check_empty_catch("try ( f a ) catch ()\n"))
        self.assertTrue(check_ms.check_empty_catch("try ( f a ) catch ( )\n"))

    def test_catch_that_records_is_allowed(self):
        src = 'try ( f a ) catch ( mmd2maxLog ("抛错：" + (getCurrentException())) )\n'
        self.assertEqual(check_ms.check_empty_catch(src), [])

    def test_explicit_allow_failure_comment_is_allowed(self):
        src = "try ( f a ) catch () -- 允许失败：这个 API 在 2022 不存在\n"
        self.assertEqual(check_ms.check_empty_catch(src), [])

    def test_the_mark_must_be_on_the_same_line(self):
        """声明写在上一行不算：grep「允许失败」时得能一眼看到那条 catch 本身。"""
        src = "-- 允许失败：探测\ntry ( f a ) catch ()\n"
        self.assertEqual(len(check_ms.check_empty_catch(src)), 1)


class LineNumberFidelityTests(unittest.TestCase):
    """报错行号是这个工具唯一的输出坐标；号不对等于没有。"""

    def test_block_comment_does_not_shift_line_numbers(self):
        src = "/* 一\n二\n三 */\nfn snapshot x = x\n"
        problems = check_ms.check_reserved(src)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("行 4", problems[0],
                      "块注释被压成一行会让后面的行号全体前移（实测真实第 720 行报成 553）")

    def test_string_content_does_not_count_as_code(self):
        src = 'local s = "catch ()"\ntry ( f a ) catch ()\n'
        self.assertEqual(len(check_ms.check_empty_catch(src)), 1,
                         "字符串里的 catch () 是文本，不是缺陷")


class IfDoElseTests(unittest.TestCase):
    """规则：`if ... do` 后面接不了 else（要接必须写 then）。

    这一条有两种形状，第二种是补的漏：
      A. `if X do` 换行写块体，块体结束后紧跟 else —— 老规则抓得到；
      B. **整条 if...do 连块体都写完在同一行**，下一行才是 else —— 老规则只认
         「行尾的 do」（IF_HEAD_DO），这一形状完全不进判断，探针 figfile_m4h
         第一发就死在这儿：MAXScript 报「Syntax error: at else, expected <factor>」，
         而 check_ms 报了 [OK]，白跑一次 Max。
    """

    def test_inline_complete_if_do_followed_by_else_is_flagged(self):
        src = ('fn detail tag name =\n'
               '(\n'
               '    if n == undefined do ( say "读不到" )\n'
               '    else\n'
               '    (\n'
               '        say "读到了"\n'
               '    )\n'
               ')\n')
        problems = check_ms.check_if_do_else(src)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("行 3", problems[0])

    def test_same_line_if_do_then_else_still_flagged(self):
        problems = check_ms.check_if_do_else("if a do ( f ) else ( g )\n")
        self.assertEqual(len(problems), 1, problems)

    def test_block_body_if_do_followed_by_else_is_flagged(self):
        src = ("if a do\n"
               "(\n"
               "    f()\n"
               ")\n"
               "else\n"
               "(\n"
               "    g()\n"
               ")\n")
        self.assertEqual(len(check_ms.check_if_do_else(src)), 1)

    def test_if_then_else_on_next_line_is_legal(self):
        """then 才许接 else。别把产品里那二十多处合法写法报成缺陷 ——
        假阳性的代价不是多改一行，是没人再信这个检查器。"""
        src = ("if a then\n"
               "(\n"
               "    f()\n"
               ")\n"
               "else\n"
               "(\n"
               "    g()\n"
               ")\n")
        self.assertEqual(check_ms.check_if_do_else(src), [])

    def test_inline_if_do_followed_by_ordinary_statement_is_not_flagged(self):
        src = ('if a do ( f() )\n'
               'if b do ( g() )\n'
               'say "done"\n')
        self.assertEqual(check_ms.check_if_do_else(src), [])


class BareFnReferenceTests(unittest.TestCase):
    """零参 `fn` 被裸引用 = 拿到函数对象，不是返回值（第 37 轮整轮就是这么废的）。

    `if (mmd2maxChestGiven) then (...)` 编译通过、运行到那句报
    `-- Type error: if-test requires BooleanClass, got: mmd2maxChestGiven()`，
    而它排在读 CFG 的摘要里 —— 于是一轮 Max 启动、导入、装配全部没跑，日志只有两行。
    这条能静态认出来，就不该拿一轮实跑去换这个信息。
    """

    def test_bare_reference_to_a_zero_arg_fn_is_flagged(self):
        src = ("fn mmd2maxChestGiven =\n(\n    true\n)\n"
               "if (mmd2maxChestGiven) then ( say \"y\" )\n")
        problems = check_ms.check_bare_fn_reference(src)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("行 5", problems[0])
        self.assertIn("mmd2maxChestGiven()", problems[0],
                      "报错要直接给出正确写法，让人不用再去查 MAXScript 的调用语法")

    def test_a_real_call_is_not_flagged(self):
        src = ("fn mmd2maxChestGiven =\n(\n    true\n)\n"
               "if (mmd2maxChestGiven()) then ( say \"y\" )\n")
        self.assertEqual(check_ms.check_bare_fn_reference(src), [])

    def test_fns_with_parameters_are_left_to_the_compiler(self):
        """带参函数裸引用是 `Call needs arguments` 那类**编译期**错误，Max 自己会报，
        这条规则只管零参那种 —— 那种唯一的表现形式就是「静默拿到一个函数值」。"""
        src = ("fn mmd2maxComp p i =\n(\n    p.x\n)\n"
               "local a = mmd2maxComp\n")
        self.assertEqual(check_ms.check_bare_fn_reference(src), [])

    def test_names_inside_comments_and_strings_are_not_references(self):
        """注释与 throw 文案里出现函数名是人话（「mmd2maxLoadCfg 必须排在…」）。
        把这两处当引用会让规则天天误报，而误报的规则三天后就被关掉了。"""
        src = ('fn mmd2maxLoadCfg =\n(\n'
               '    /* mmd2maxLoadCfg 必须排在所有装配步骤之前 */\n'
               '    throw "还没读 CFG 就取配置了（mmd2maxLoadCfg 排第一）"\n'
               ')\n')
        self.assertEqual(check_ms.check_bare_fn_reference(src), [])


class ProductScriptStillCleanTests(unittest.TestCase):
    """体检脚本自己别把产品判红：现在这份 .ms 必须 0 条拦路问题。"""

    def test_max_import_obj_has_no_blocking_problem(self):
        path = PROJECT_ROOT / "scripts" / "max_import_obj.ms"
        problems = check_ms.check_file(path)
        self.assertEqual(problems, [], "\n".join(problems))


# 空 catch 的历史债（#81）。这一行只许往下改，往上改必须先解释为什么要新增静默点。
# 66 → 51（第一批：截图段 15 条 —— 视口装饰/还原走「允许失败 + 理由」，会把
# 「图不可判」读成绿的三条（藏网格、还原骨、还原网格）改成计数 + 一行日志）；
# 51 → 0（第二批：物理段的层收纳/隐藏改成计数 + 回读，Morpher 预热那两条各写自己的
# 理由，失败路径上的 error.txt/弹窗/quitMax 三条改成打监听器）。
# 到 0 之后 `check_ms` 的 `--strict` 开关被删掉：规则 V/I 从此默认拦路，
# 再加一条 `catch ()` 就是在加一条「失败了我不想知道」，直接拦。
CATCH_DEBT = 0


class CatchDebtRatchetTests(unittest.TestCase):
    """把「先记账不拦路」那条债数钉成等式。

    为什么用相等而不是 `<=`：
      * `<=` 拦不住新增 —— 66 条债在场时，第 67 条 `catch ()` 照样绿，而这正是
        v3 那句 `try ( skinOps.forceUpdate skinMod ) catch ()` 藏了十几轮的形状；
      * `<=` 也读不出进展 —— 清掉 15 条不需要动这个文件，「债务在往下走」这件事
        在测试里就没有现场，清到 0 那天没人知道还剩几条。
    相等两个都拦住：加一条要改这里，清一条也要改这里，而改这里的代价是把那一条
    的判读理由写进 .ms（`-- 允许失败：<理由>` 或记进日志），这正是规则 V 想要的。
    """

    def test_empty_catch_debt_matches_the_pinned_number(self):
        src = (PROJECT_ROOT / "scripts" / "max_import_obj.ms").read_text(encoding="utf-8")
        problems = check_ms.check_v4_debt(src)
        rows = [p.split(":")[0] for p in problems]
        self.assertEqual(len(problems), CATCH_DEBT,
                         f"空 catch 债从 {CATCH_DEBT} 变成 {len(problems)}：\n  "
                         + "\n  ".join(rows)
                         + "\n清掉了就把 CATCH_DEBT 改成新数（并确认每条都换成了显式理由或真读数）；"
                           "变多了就把它判掉，别加回来。")

    def test_debt_is_all_the_empty_catch_kind(self):
        """债的种类也要能读出来：混进别 kinds 的债（规则 I 那种）是另一条账，别让它躲在同一个数里。"""
        src = (PROJECT_ROOT / "scripts" / "max_import_obj.ms").read_text(encoding="utf-8")
        for problem in check_ms.check_v4_debt(src):
            self.assertIn("`catch ()` 是空 catch", problem, problem)

    def test_debt_count_does_not_depend_on_the_exempt_marker(self):
        """`catch ( err = … )` 加不加 `-- 允许失败：` 都不该改变债数。

        这条是给规则 V 本身把门的：如果声明能改变**非空体**那条的计数，那它就成了
        可以互相抵的筹码（删掉 A 的声明去补 B 的债），「债数」不再等于「静默点个数」。
        """
        src = ('fn f a =\n'
               '(\n'
               '    try ( g a ) catch ()\n'
               '    try ( h a ) catch () -- 允许失败：探测用的，读不回就是没有\n'
               '    try ( i a ) catch ( err = (getCurrentException() as string) )\n'
               '    if err != "" do mmd2maxLog err\n'
               ')\n')
        self.assertEqual(len(check_ms.check_empty_catch(src)), 1)
        marked = src.replace(
            'try ( i a ) catch ( err = (getCurrentException() as string) )',
            'try ( i a ) catch ( err = (getCurrentException() as string) )'
            ' -- 允许失败：加了声明也不该多算一条')
        self.assertEqual(len(check_ms.check_empty_catch(marked)), 1)


class FnSpanTests(unittest.TestCase):
    """`_fn_spans` 切出来的跨度必须真覆盖函数体 —— 规则 I（判据不许同源）全靠它。

    这条测试是补的债：产品里 133 个函数有 132 个写成「声明一行、括号在下一行」，
    旧实现按「深度回到 0 就关段」处理，于是每个跨度只有声明那 1 行，规则 I 只看得到
    `fn 名字 …`，命中数永远是 0 —— 一条看着在跑、实际是死的闸。#81 把空 catch 清到
    0 之后重跑，才发现同一个开关下面还躺着这一条。
    """

    def _spans(self, src):
        return check_ms._fn_spans(check_ms._blank_strings(check_ms._strip_lines(src)))

    def test_block_body_opened_on_the_next_line_is_inside_the_span(self):
        src = 'fn mmd2maxLog m =\n(\n    format "%\\n" m\n)\n'
        self.assertEqual(self._spans(src), [("mmd2maxLog", 1, 4)])

    def test_one_line_body_closes_before_the_next_statement(self):
        src = ('fn mmd2maxA x = x + 1\n'
               'fn mmd2maxB y =\n'
               '(\n'
               '    mmd2maxA y\n'
               ')\n'
               'mmd2maxB 1\n')
        self.assertEqual(self._spans(src), [("mmd2maxA", 1, 1), ("mmd2maxB", 2, 5)])

    def test_rule_i_fires_inside_a_block_body(self):
        """死闸的对照：这条在修 _fn_spans 之前是 0 命中。"""
        src = ('fn mmd2maxBoxWantDir a b c d =\n(\n    a\n)\n'
               'fn mmd2maxCheckBox n =\n'
               '(\n'
               '    local dir = mmd2maxBoxWantDir n 1 2 3\n'
               '    dir\n'
               ')\n')
        problems = check_ms.check_oracle_independence(src)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("mmd2maxCheckBox", problems[0])
        self.assertIn("行 7", problems[0])

    def test_the_product_span_count_is_not_one_line_anymore(self):
        """产品自己也得量一次：133 个函数里「跨度 = 1 行」的必须是少数（真一行体）。

        只数跨度不数内容，是因为这条债的形状就是「看起来在工作」；把跨度量出来，
        哪天再退回去（比如有人改了 _paren_delta），这条会当场变红而不是静默 0 命中。
        """
        src = (PROJECT_ROOT / "scripts" / "max_import_obj.ms").read_text(encoding="utf-8")
        spans = self._spans(src)
        self.assertGreaterEqual(len(spans), 120, "顶格 fn 的跨度表都读不出来")
        one_line = [name for name, start, end in spans if start == end]
        self.assertLessEqual(len(one_line), 3,
                             f"只剩 1 行跨度的函数突然变多（{len(one_line)}）：{_safe_head(one_line)}"
                             f" —— _fn_spans 又退回「声明行即结束」了，规则 I 会跟着变哑")


def _safe_head(names):
    return ", ".join(names[:8])


class RuleIEvasionNeedleTests(unittest.TestCase):
    """规则 I 的八种规避，每一种一条针：写出来必须当场变红。

    为什么单独成一组：这条规则历史上「命中 0」的原因从来不是判据写得对，而是查法
    看不见 —— 锚在行首的名字、按函数而不是按调用点记的污点、跨度只有声明行的 `fn`，
    每一次都是「规则在跑，但它没在看」。那种死闸比没有闸坏，因为它让人以为有人管。
    所以这里钉的不是「现在的实现怎么工作」，是「每一条便宜的绕法都会被抓」。
    """

    _ORACLE = "fn mmd2maxBoxWantDir bone h t =\n(\n    h\n)\n"

    def _problems(self, src):
        return check_ms.check_oracle_independence(self._ORACLE + src)

    def _flagged(self, src, needle):
        problems = self._problems(src)
        self.assertTrue(problems, "这条规避没被抓到：%s\n%s" % (needle, src))
        self.assertIn(needle, "\n".join(problems), problems)
        return problems

    def test_checker_name_not_at_the_front_is_caught(self):
        """规避一：把语素挪到名字尾巴上（旧实现锚在行首，看不见它）。"""
        self._flagged(
            "fn mmd2maxBoneAudit bone =\n"
            "(\n"
            '    mmd2maxReport "p" "c" bone (mmd2maxBoxWantDir bone 1 2)\n'
            ")\n", "mmd2maxBoneAudit")

    def test_chinese_checker_name_is_caught(self):
        """规避二：判据用中文名。

        TOKEN 早就是 Unicode 的；要是名字表只认 ASCII，这支函数根本进不了 spans，
        规则对它彻底失明 —— 而失明的方向和「代码里没有这个问题」在日志里长得一样。
        """
        self._flagged(
            "fn mmd2max校验脚盒 bone =\n"
            "(\n"
            '    mmd2maxReport "p" "c" bone (mmd2maxBoxWantDir bone 1 2)\n'
            ")\n", "mmd2max校验脚盒")

    def test_want_passed_in_as_an_argument_is_caught(self):
        """规避三：求值器不在判据函数里调用，而是从外面当实参传进来。

        `mmd2maxReportDir` 的函数体里一个求值器都没有 —— 按名字查它永远干净，
        可它落的那行报告的最后一个实参就是写入侧给的方向。
        """
        self._flagged(
            "fn mmd2maxReportDir node dir =\n"
            "(\n"
            '    mmd2maxReport "p" "c" node dir\n'
            ")\n"
            "fn mmd2maxPlaceFeet =\n"
            "(\n"
            '    local want = mmd2maxBoxWantDir "Bip001 L Foot" 1 2\n'
            '    mmd2maxReportDir "Bip001 L Foot" want\n'
            ")\n", "mmd2maxReportDir")

    def test_want_on_a_backslash_continuation_is_caught(self):
        """规避四：用 `\\` 把一次调用劈成两行，按**物理行**做的污点在那里断掉。

        产品里脚骨偏航那两行（5355~5356）正是这个形状。
        """
        problems = self._flagged(
            "fn mmd2maxReportDir node dir =\n"
            "(\n"
            '    mmd2maxReport "p" "c" node dir\n'
            ")\n"
            "fn mmd2maxPlaceFeet =\n"
            "(\n"
            "    local want = mmd2maxBoxWantDir \"Bip001 L Foot\" \\\n"
            "        1 2\n"
            '    mmd2maxReportDir "Bip001 L Foot" want\n'
            ")\n", "mmd2maxReportDir")
        # 包装器自己那一行（行 7）也得红：污点是按**调用点实参**流进它的形参的，
        # 只报调用方等于默认「谁包装了报告就谁说了算」，那正好是产品里没作者的那一类。
        self.assertIn("行 7", "\n".join(problems), problems)

    def test_pure_ruler_is_not_a_permanent_taint_source(self):
        """规避五的反面 —— 不许误伤：尺（`mmd2maxVecAngleDeg`）不是期望值。

        按**函数**记污点的话，这支尺因为「某个调用点拿 want 喂过它」就永久变成污点源，
        于是它每一个正常调用都报成违规。误报的规则没人看，所以这条针钉的是：同一支尺，
        脏的那个调用点被抓、干净的那个一个都不许多报。
        """
        src = ("fn mmd2maxVecAngleDeg a b =\n"
               "(\n"
               "    0.0\n"
               ")\n"
               "fn mmd2maxLogAngle n a b =\n"
               "(\n"
               '    mmd2maxReport "p" "c" n (mmd2maxVecAngleDeg a b)\n'
               ")\n"
               "fn mmd2maxPlaceAll =\n"
               "(\n"
               '    local want = mmd2maxBoxWantDir "Bip001 L Foot" 1 2\n'
               '    mmd2maxLogAngle "脏的那一行" want want\n'
               '    mmd2maxLogAngle "干净的这一行" 1 2\n'
               ")\n")
        problems = self._problems(src)
        joined = "\n".join(problems)
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("行 11", joined, "包装器里那行报告没被抓到")
        self.assertIn("行 16", joined, "脏的那个调用点没被抓到")
        self.assertNotIn("行 17", joined,
                         "干净的调用点也被报了：尺（mmd2maxVecAngleDeg）又被当成永久污点源，"
                         "于是这条规则开始误伤每一个正常调用")

    def test_multi_line_empty_catch_is_caught(self):
        """规避六：`catch` 后面的空体跨行 —— 只扫本行的实现漏网。"""
        problems = check_ms.check_empty_catch(
            "try\n(\n    foo 1\n)\ncatch\n(\n)\n")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("catch", problems[0])

    def test_allow_failure_without_a_reason_is_caught(self):
        """规避七：写一句 `-- 允许失败：` 就当免死牌 —— 冒号后面必须有理由。"""
        problems = check_ms.check_empty_catch(
            "try ( foo 1 ) catch ( ) -- 允许失败：\n")
        self.assertEqual(len(problems), 1, problems)
        problems = check_ms.check_empty_catch(
            "try ( foo 1 ) catch ( ) -- 允许失败：这个 API 在 2022 上不存在\n")
        self.assertEqual(problems, [], problems)

    def test_literal_second_assignment_of_a_cfg_value_is_caught(self):
        """规避八（规则 IV）：CFG 读进来之后再被别处写死。

        这条让「改 MMD2MAX.CFG 就是改这个数」不再成立：.ms 里最终读到哪个值取决于
        执行顺序，下一轮 diff_round 对的就不是同一个口径。
        """
        problems = check_ms.check_numeric_single_source(
            "global MMD2MAX_DIRTOL = 0\n"
            "fn mmd2maxLoadCfg =\n"
            "(\n"
            '    MMD2MAX_DIRTOL = mmd2maxCfgNum "MMD2MAX_DIRTOL"\n'
            ")\n"
            "fn mmd2maxUse =\n"
            "(\n"
            "    MMD2MAX_DIRTOL = 3.0\n"
            ")\n", cfg_keys={"MMD2MAX_DIRTOL"}, target_keys=set())
        self.assertTrue(any("第二次赋值" in x for x in problems), problems)

    def test_diverged_cfg_key_name_is_caught(self):
        """规避八的另一半：名字与键分叉 + 读一个 CFG 里根本没有的键。

        分叉之后「一个词两边同名」这条约定就断了：CFG 里那个键没人读，.ms 里这个数
        没人写；而读不存在的键在 Max 侧是 `mmd2maxCfg*` 当场抛，整轮白跑。
        """
        problems = check_ms.check_numeric_single_source(
            "global MMD2MAX_DIRTOL = 0\n"
            "fn mmd2maxLoadCfg =\n"
            "(\n"
            '    MMD2MAX_DIRTOL = mmd2maxCfgNum "MMD2MAX_DIRTOL_DEG"\n'
            ")\n", cfg_keys={"MMD2MAX_DIRTOL"}, target_keys=set())
        self.assertTrue(any("分叉" in x for x in problems), problems)
        self.assertTrue(any("不在 Python 写出的那份 CFG 里" in x for x in problems),
                        problems)

    def test_a_check_name_keyed_exempt_does_not_silence_its_neighbours(self):
        """豁免表第三元素（check 名）必须真的窄：同源量落进**别的** check 还得红。

        钉这一条是因为 `mmd2maxBuildBiped` 是一千四百行的写入主体。按 (函数, 求值器)
        豁免它，等于给将来任何一条同源 gate 开后门 —— 那种「例外」一旦写下去，
        下一个想偷懒的人只需在同一支函数里再报一行，规则一声不响。
        """
        src = ("fn mmd2maxShoulderRayTarget clav want =\n"
               "(\n"
               "    want\n"
               ")\n"
               "fn mmd2maxBuildBiped =\n"
               "(\n"
               "    local t = mmd2maxShoulderRayTarget 1 2\n"
               '    mmd2maxReport "placement" "ray_err_cm" "Bip001 L UpperArm" t\n'
               '    mmd2maxReport "placement" "bone_yaw_deg" "Bip001 L UpperArm" t\n'
               ")\n")
        saved = check_ms.ORACLE_EXEMPT
        try:
            check_ms.ORACLE_EXEMPT = {("mmd2maxBuildBiped",
                                       "mmd2maxShoulderRayTarget", "ray_err_cm")}
            problems = check_ms.check_oracle_independence(src)
        finally:
            check_ms.ORACLE_EXEMPT = saved
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("行 9", problems[0], problems[0])
        self.assertIn("bone_yaw_deg", src.splitlines()[8],
                      "红的那一行不是 bone_yaw_deg —— 说明 ray_err_cm 没被放过")
        self.assertNotIn("ray_err_cm", problems[0])


class LockNameRecognitionTests(unittest.TestCase):
    """`verdicts.py` 里声明成 lock 的行，规则 I 必须认得出**check 名** —— 认不得就一片假红。

    这一条是真炸过的：第二组的正则没圈捕获组，`findall` 返回**整条调用文本**，于是
    名单里装的是 `row("placement", "ray_err_cm", … "lock"` 这种字符串，一个 check 名
    都没进去。表现出来就是规则 I 把已定价的锁也判成假验收，而「放宽规则」成了唯一
    能让它变绿的路 —— 那正是这条规则死掉的方式。
    """

    _SPELLINGS = (
        # 两种正写法（兜底文本扫）+ 一种扫不出来的（期望值现算，中间隔着右括号）
        ('rows.append(_lock("converge", "dir_over", g, v, 0, model))', "lock_a"),
        ('rows.append(row("segment", "len_cm", s, 1.0, 0.0, "lock"))', "lock_b"),
        ('LOCK_CHECKS = ("declared_one", "declared_two")', "declared"),
    )

    def _names_from(self, body):
        import tempfile
        root = Path(tempfile.mkdtemp())
        (root / "mmd2max").mkdir()
        (root / "mmd2max" / "verdicts.py").write_text(body, encoding="utf-8")
        saved = check_ms.ROOT
        try:
            check_ms.ROOT = root
            return check_ms._lock_check_names()
        finally:
            check_ms.ROOT = saved

    def test_the_declared_table_wins_over_the_text_scan(self):
        """有 LOCK_CHECKS 就按它：那张表由 test_verdicts 钉成「与真规格表相等」。

        文本扫是兜底，不是主口径 —— `_lock("converge", check, …)` 那种循环变量写法
        扫出来必然少几项，而少的那几项正是产品里真在落报告的行。
        """
        names = self._names_from(self._SPELLINGS[2][0])
        self.assertEqual(names, {"declared_one", "declared_two"}, names)

    def test_both_lock_spellings_land_their_check_name_in_the_set(self):
        """兜底那条路：没有 LOCK_CHECKS 时，两种字面写法都要认得出 check 名。"""
        names = self._names_from(self._SPELLINGS[0][0] + "\n" + self._SPELLINGS[1][0])
        self.assertIn("dir_over", names, names)
        self.assertIn("len_cm", names, names)
        self.assertFalse([n for n in names if "(" in n or " " in n],
                         "名单里是整条调用而不是 check 名：%s" % sorted(names))

    def test_an_unrecognizable_lock_is_reported_not_trusted(self):
        """兜底那条路的漏报方向：扫不出的拼法只能当 gate（多报），不许当锁放过。"""
        names = self._names_from(
            'rows.append(row("segment", "computed_cm", s, price[0](x), 0.0, "lock"))')
        self.assertNotIn("computed_cm", names, names)

    def test_the_real_verdicts_still_lands_recognizable_locks(self):
        names = check_ms._lock_check_names()
        self.assertTrue(names, "一个 lock 都没认出来：读不到 verdicts.py，还是正则又散了？")
        # dir_over 是「文本扫必然漏、声明表抓得住」的那一项
        for expected in ("dir_over", "chain_angle_deg", "pos_err_cm",
                         "place_max_cm", "place_sum_cm"):
            self.assertIn(expected, names, sorted(names))

class OracleExemptTests(unittest.TestCase):
    """例外表必须是「承重」的：逐条摘掉，产品必须当场变红。

    一条永不生效的例外表和一表假例外长得一样，所以这里不看内容、只看效果。
    反过来如果哪天判据真改了（不再同源），那条例外就变成空占位，也必须被这条测试
    发现 —— 空占位比没有例外坏：它让下一个想加例外的人相信「这里本来就该免检」。
    """

    _PRODUCT = None

    def _src(self):
        if self._PRODUCT is None:
            self._PRODUCT = (PROJECT_ROOT / "scripts" / "max_import_obj.ms"
                             ).read_text(encoding="utf-8")
        return self._PRODUCT

    def test_every_exempt_entry_still_buys_a_pass(self):
        """例外表逐条承重：把**其中一条**摘掉，产品必须当场多出一行问题。

        原来这条是「清空整表，命中数 == 表长」，那个等式从来不该成立 —— 一支函数
        可以落好几行报告（`mmd2maxReportBoxDims` 一处就是 5 行），它靠巧合绿着。
        而「靠巧合绿的测试」和「一条永不生效的例外」长得一样，都读起来像有人在管。
        """
        base = check_ms.check_oracle_independence(self._src())
        self.assertEqual(base, [], base)
        for entry in sorted(check_ms.ORACLE_EXEMPT):
            saved = check_ms.ORACLE_EXEMPT
            try:
                check_ms.ORACLE_EXEMPT = saved - {entry}
                problems = check_ms.check_oracle_independence(self._src())
            finally:
                check_ms.ORACLE_EXEMPT = saved
            self.assertGreater(len(problems), len(base),
                               f"例外 {entry} 已经不拦住任何东西：删掉它")
            joined = "\n".join(problems)
            self.assertIn(f"`{entry[0]}`", joined,
                          f"{entry} 摘掉之后红的是别的函数 —— 这条例外钉的名字过时了")
            if len(entry) == 3:
                self.assertIn(f'"{entry[2]}"', self._src(),
                              f"例外 {entry} 钉的 check 名在产品里已经找不到了")

    def test_the_product_has_no_oracle_that_is_not_spans_covered(self):
        """防「规则 I 又变哑」的另一半：判据函数的跨度必须真的覆盖到函数体。

        只量跨度不够，还得量「判据函数体内确实出现过标识符」——否则将来谁把判据写成
        一行体，规则 I 又是 0 命中，而这次 0 是真的 0。
        """
        src = self._src()
        spans = check_ms._fn_spans(check_ms._blank_strings(check_ms._strip_lines(src)))
        checkers = [(n, s, e) for n, s, e in spans if check_ms.CHECKER_NAME_RE.search(n)]
        self.assertGreaterEqual(len(checkers), 3,
                                "名字像判据的函数一个都没量到 —— CHECKER_NAME_RE 或 _fn_spans 变了")
        for name, start, end in checkers:
            self.assertGreater(end - start, 0, f"判据 `{name}` 的跨度又是 1 行")


if __name__ == "__main__":
    unittest.main()
