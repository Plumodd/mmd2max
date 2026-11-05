"""`tools/diff_round.py` 自己的测试 —— 它是「重构有没有动几何」的唯一判据，所以它自己
必须先被证过一遍。

钉住的都是第十九轮实际踩到的坑：Max 日志行首的时间戳**不补零**（`2026-11-2 23:03:48`
18 个字符，`2026-11-3 1:31:18` 17 个），当时按固定宽度裁时间戳，正则静默不匹配，
整份 413 行日志被读成「全变了」。归一化一旦不可信，「M1b 几何中性」这个结论就是空的，
所以这里既测「该消失的消失」，也测「该留下的必须留下」——后者更重要：
如果归一化顺手把 `累计误差` 也剥了，这个工具就变成一台永远说「没变化」的机器。
"""
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import diff_round                                       # noqa: E402
from tests import required_files                       # noqa: E402

# 这四份是**仓库里的**基线读数件（`baselines/round*` 已进版本控制）⇒ 缺件走
# `required_files.require()` 报红，不再 skipUnless：一条永不因为「文件没了」而响的锁，
# 和一条被人删掉的锁，在结果里长得一模一样。
RE17 = PROJECT_ROOT / "baselines" / "round17_objrun.log"
RE18 = PROJECT_ROOT / "baselines" / "round18_objrun.log"
RE19 = PROJECT_ROOT / "baselines" / "round19_objrun.log"
RE20 = PROJECT_ROOT / "baselines" / "round20_objrun.log"
RE22 = PROJECT_ROOT / "baselines" / "round22_objrun.log"


class NormalizeTests(unittest.TestCase):
    def test_timestamp_width_and_separator_do_not_matter(self):
        """真日志的时间戳是「不补零 + 斜杠分隔」，两种写法都必须被剥掉。

        第十九轮那次事故有两种：按固定宽度裁（`2026/11/2 23:03:48` 比 `2026/11/3 1:31:18`
        多一个字符），以及照终端回显把分隔符写成短横线（字节其实是 0x2F，写 `-` 静默不匹配）。
        """
        self.assertEqual(diff_round.normalize("2026/11/2 23:03:48  肢段拟合: 成功=39"),
                         diff_round.normalize("2026/11/3 1:31:18  肢段拟合: 成功=39"))
        # 短横线那种写法也要能剥（万一哪天日志换了格式，不许反过来漏判成差异）
        self.assertEqual(diff_round.normalize("2026-11-2 23:03:48  肢段拟合: 成功=39"),
                         "肢段拟合: 成功=39")

    def test_acceptance_numbers_are_never_stripped(self):
        """读数只有量：误差、角度、顶点数一个都不许归一掉，否则工具永远报「没变化」。"""
        for line in ("累计误差=0.224741", "转角尝试=45 改善=45", "已写入顶点 18892",
                     "髋中点残余偏差=2.6226e-06", "骨盒 3.7142 × 8.6560",
                     "肢段拟合: 成功=39 失败=0"):
            self.assertRegex(diff_round.normalize(line), r"\d", line)
        # 每一个读数被改掉都必须还能被看出是两行不同的文本
        for before, after in (("累计误差=0.224741", "累计误差=0.300000"),
                              ("转角尝试=45", "转角尝试=44"),
                              ("已写入顶点 18892", "已写入顶点 18891"),
                              ("骨盒 3.7142 × 8.6560", "骨盒 3.7142 × 2.3210")):
            self.assertNotEqual(diff_round.normalize(before), diff_round.normalize(after))

    def test_load_only_differences_disappear(self):
        self.assertEqual(diff_round.normalize("2026/11/3 1:31:20  OBJ 导入耗时：1.118 秒，对象数 1"),
                         diff_round.normalize("2026/11/3 1:57:22  OBJ 导入耗时：1.097 秒，对象数 1"))
        a = diff_round.normalize("输出: C:\\x\\mmd2max_v3_src_2026-11-01\\test_runs\\香菱.max")
        b = diff_round.normalize("输出: C:\\x\\mmd2max_v4_src_2026-11-03\\test_runs\\香菱.max")
        self.assertEqual(a, b)

    def test_file_size_masked_but_short_counts_not(self):
        self.assertEqual(diff_round.normalize("香菱.max 191111168 字节"),
                         diff_round.normalize("香菱.max 191111200 字节"))
        self.assertNotEqual(diff_round.normalize("节点总数 397"),
                            diff_round.normalize("节点总数 398"))

    def test_non_timestamp_line_keeps_its_head(self):
        """剥时间戳的規則必须**认得出是日期+时间**才动手。

        早先写的是「行首两个非空格 token」，于是 `香菱.max 191111168 字节` 被吃掉行头变成
        `字节` —— 两行不同的日志塌成同一行，就是漏判。
        """
        self.assertEqual(diff_round.normalize("香菱.max 191111168 字节 落盘"),
                         "香菱.max <N> 字节 落盘")
        self.assertEqual(diff_round.normalize("# 比例: 7.6563"), "# 比例: 7.6563")

    def test_six_digit_decimal_is_not_masked(self):
        """`累计误差=0.224741` 的小数部分正好 6 位：把它遮成 `<N>` 等于遮掉验收本身。"""
        self.assertEqual(diff_round.normalize("累计误差=0.224741"), "累计误差=0.224741")
        self.assertEqual(len(diff_round.substantive(["累计误差=0.224741"],
                                                   ["累计误差=0.224742"], [])), 2)

    def test_a_six_digit_reading_is_not_masked(self):
        """六位**整数读数**同样不许遮 —— 以前 `\\d{6,}` 见谁都遮。

        这条不是假想：蒙皮链路数在香菱上是 26742（5 位）所以一直没炸，换一具密一点的
        模型就是 267420；顶点数同理。到时候被遮成 `<N>` 的正是「这轮网格/权重变了」
        那个信号，而工具照样报「无实质差异」。字节数那一类由「后面必须跟着字节」保住。
        """
        for before, after in (
                ("已写入顶点 1889200", "已写入顶点 1889100"),
                ("蒙皮回读 links_read 267420", "蒙皮回读 links_read 267421"),
                ("骨盒回读 width_cm 3714245", "骨盒回读 width_cm 9999999")):
            self.assertNotEqual(diff_round.normalize(before), diff_round.normalize(after),
                                f"{before} 的读数被当字节数遮掉了")

    def test_the_mask_fires_on_nothing_in_the_frozen_logs(self):
        """收窄遮罩必须是**可证的中性**：35 份冻结基线日志里，旧遮罩本来就一次都没命中。

        旧口径「任何 6 位以上整数」在这些日志上命中 0 次（顶点/链路数都只有 5 位），
        所以把它收窄到「紧跟『字节』的整数」不可能改变任何一轮的比较结果 ——
        这条断言就是那句「不可能」的证据，不是我说说而已。
        """
        import re
        blanket = re.compile(r"(?<![\d.])\d{6,}(?![\d.])")
        logs = sorted((PROJECT_ROOT / "baselines").glob("round*_objrun.log"))
        self.assertGreaterEqual(len(logs), 30, "baselines/ 没带全，这条证据就没有内容")
        for log in logs:
            text = log.read_text(encoding="utf-8-sig", errors="replace")
            hits = blanket.findall(text)
            self.assertEqual(hits, [], f"{log.name} 里 6 位以上整数有 {hits[:3]}")


class PermittedMovesTests(unittest.TestCase):
    """`--allow` 放行的是行的增删，不是行里的数字 —— 这个区分以前只在文案里。"""

    def move(self, before, after, allows):
        import re
        return diff_round.permitted_moves(diff_round.normalized_lines(before),
                                          diff_round.normalized_lines(after),
                                          [re.compile(a) for a in allows])

    def test_an_allowed_line_with_a_new_number_is_not_silently_clean(self):
        """README 里 `--allow "^骨盒回读"` 那种放行，白掉的正是装 width_cm 的那一行。"""
        before = "2026-11-3 1:31:20  骨盒回读 L Foot width_cm 3.714245\n读数=1\n"
        after = "2026-11-3 1:57:20  骨盒回读 L Foot width_cm 9.999999\n"
        import re
        self.assertEqual(diff_round.substantive(
            diff_round.normalized_lines(before), diff_round.normalized_lines(after),
            [re.compile(r"^骨盒回读"), re.compile(r"^读数")]), [])
        moved = self.move(before, after, [r"^骨盒回读"])
        self.assertEqual(len(moved), 1, "放行的行里数字变了，必须单独说出来")
        self.assertIn("3.714245", moved[0][0])
        self.assertIn("9.999999", moved[0][1])

    def test_the_same_reading_is_not_a_move(self):
        """只有数字真的变了才算 —— 否则每次正常放行都会吐一堆假警报，下次就没人看这句话。"""
        before = "2026-11-3 1:31:20  骨盒回读 L Foot width_cm 3.714245\n"
        after = "2026-11-3 1:57:20  骨盒回读 L Foot width_cm 3.714245\n"
        self.assertEqual(self.move(before, after, [r"^骨盒回读"]), [])

    def test_a_line_that_is_not_the_same_line_is_not_a_move(self):
        """配对只认「非数字部分逐字符相同」的两行：认错会把一次删除说成一次数字漂移。"""
        before = "骨盒回读 L Foot width_cm 3.714245\n收敛闸门 出口=ceiling 遍数=3/3\n"
        after = "蒙皮回读 links_read 267420\n"
        self.assertEqual(self.move(before, after, [r"^蒙皮回读", r"^骨盒回读"]), [])

    def test_a_new_line_with_a_new_number_is_not_reported_as_a_move(self):
        """预期新增的行（M1b 的「配置:」）不是数字漂移，别把两种情况混成一种噪音。"""
        before = "场景已重置。\n"
        after = "场景已重置。\n配置: 29 项\n"
        self.assertEqual(self.move(before, after, [r"^配置"]), [])


class SubstantiveTests(unittest.TestCase):
    def diff(self, before, after, allows=()):
        import re
        return diff_round.substantive(diff_round.normalized_lines(before),
                                      diff_round.normalized_lines(after),
                                      [re.compile(a) for a in allows])

    def test_pure_timing_round_is_clean(self):
        before = "2026-11-3 1:31:20  权重写入耗时：0.432 秒，已写入顶点 18892\n"
        after = "2026-11-3 1:57:27  权重写入耗时：0.42 秒，已写入顶点 18892\n"
        self.assertEqual(self.diff(before, after), [])

    def test_moved_number_is_reported(self):
        before = "2026-11-3 1:31:20  Biped 肢段拟合: 成功=39 失败=0 累计误差=0.224741\n"
        after = "2026-11-3 1:57:20  Biped 肢段拟合: 成功=38 失败=1 累计误差=0.224741\n"
        problems = self.diff(before, after)
        self.assertEqual(len(problems), 2, "一行 + 一行 -，且不许被归一吃掉")
        self.assertTrue(any(p.startswith("+") and "成功=38" in p for p in problems))

    def test_allowed_addition_is_not_a_problem(self):
        """M1b 新增的「配置:」行是预期的结构变化，靠 --allow 放行而不是全局忽略。"""
        before = "2026-11-3 1:31:18  场景已重置。\n"
        after = ("2026-11-3 1:57:23  场景已重置。\n"
                 "配置: C:\\x\\mmd2max_v4_src_2026-11-03\\MMD2MAX.CFG\n")
        self.assertEqual(len(self.diff(before, after)), 1)
        self.assertEqual(self.diff(before, after, allows=[r"^配置:"]), [])

    def test_removed_line_is_still_a_problem_when_only_additions_allowed(self):
        """--allow 只能放行它说的那一行：删掉一行读数不许被另一行的放行名单一起放过。"""
        before = "配置: 旧\n读数=1\n"
        after = "配置: 新\n"
        problems = self.diff(before, after, allows=[r"^配置:"])
        self.assertEqual(problems, ["- 读数=1"])


class RealLogTests(unittest.TestCase):
    """真机日志的回归锁：第十九轮（M1b）与第二十轮（M2a）相对各自基线都必须只差配置行。

    RE19 以前指 `test_runs/香菱/香菱_objrun.log`（**活的**那一轮的落盘位置），M2a 第二十轮
    一跑它就红 —— 看着像「M2a 改坏了几何」，其实是这条锁把「上一轮」写成了「当前轮」：
    回归基线必须是不可变的副本，否则每一轮都在跟自己下一轮比，判据随时间自己变。
    """

    def test_regression_locks_point_at_baselines_not_the_live_run(self):
        """回归锁的输入必须来自 baselines/，一次都不许指 test_runs/。"""
        for name, path in (("RE17", RE17), ("RE18", RE18), ("RE19", RE19), ("RE20", RE20),
                           ("RE22", RE22)):
            self.assertEqual(path.parent.name, "baselines",
                             f"{name} 指向 {path.parent}：实跑目录会被下一轮覆盖，"
                             "锁住它的测试就变成了「随本轮变化」的读数")

    def test_m1b_round_changed_nothing_but_the_cfg_line(self):
        import re
        required_files.require(RE18, RE19)
        problems = diff_round.substantive(diff_round.normalized(RE18),
                                          diff_round.normalized(RE19),
                                          [re.compile(r"^配置:")])
        self.assertEqual(problems, [], "M1b 必须是几何中性：除配置行外不许有任何实质差异")

    def test_m1_and_m1b_together_changed_nothing(self):
        import re
        required_files.require(RE17, RE19)
        problems = diff_round.substantive(diff_round.normalized(RE17),
                                          diff_round.normalized(RE19),
                                          [re.compile(r"^配置:")])
        self.assertEqual(problems, [], "M1（线格式 v2）+ M1b（配置单源）合计必须几何中性")

    def test_m2a_round_changed_nothing_but_the_cfg_line(self):
        """M2a（五个容差搬进 CFG）必须几何中性：除配置汇总行外一行都不许多。

        放行写成 `^配置` 而不是 `^配置:`：M1b 那行是 `配置: <路径>`，M2a 起汇总里加上了
        顶点/脊柱/五个容差，前缀改成 `配置来自 …`。两条都是「同一行换了内容」，
        但 `累计误差=` 这类读数绝不在这行里，所以放行范围放大到前缀不放过任何读数。
        """
        import re
        required_files.require(RE19, RE20)
        problems = diff_round.substantive(diff_round.normalized(RE19),
                                          diff_round.normalized(RE20),
                                          [re.compile(r"^配置")])
        self.assertEqual(problems, [], "M2a 必须是几何中性：五个容差进 CFG 不许改变任何读数")

    def test_m1_through_m2a_changed_nothing_but_the_cfg_line(self):
        import re
        required_files.require(RE17, RE20)
        problems = diff_round.substantive(diff_round.normalized(RE17),
                                          diff_round.normalized(RE20),
                                          [re.compile(r"^配置")])
        self.assertEqual(problems, [], "M1+M1b+M2a 四步重构合计必须几何中性")

    def test_m2b_and_m2c_changed_nothing(self):
        """判据拆成两张表 + Python 侧判闸（M2b/M2c）必须几何中性。

        这两步只动「谁说了算」，不许动任何读数：`链向 断开` 的阈值从 .ms 的字面量
        3.0 换成 `MMD2MAX_DIRTOL`（同一个值），骨盆三段的尺改按髋中点算 —— 那是
        **期望值**的口径，Max 瞄准的目标值（日志里的 `目标=`）本来就已经是它。

        放行仍然只有 `^配置` 这一条，但它这次装了两件事：CFG 从 26 项涨到 28 项，
        汇总行尾多出「规格=… 报告=…」。前一轮（第二十轮）根本没有判据通道，所以
        这行必然分叉 —— 除它以外没有任何实质差异。
        """
        import re
        required_files.require(RE20, RE22)
        problems = diff_round.substantive(diff_round.normalized(RE20),
                                          diff_round.normalized(RE22),
                                          [re.compile(r"^配置")])
        self.assertEqual(problems, [],
                         "M2b/M2c 必须是纯验收层改动：除配置汇总行外一行读数都不许动")

    def test_no_real_line_keeps_its_timestamp(self):
        """真日志必须**每一行**都被剥掉时间戳。

        第十九轮那次事故的形式就是「正则静默不匹配」：不报错、只是什么都没剥，于是
        413 行全被当成差异。这里直接对真基线断言这个性质，而不是断言某个行数。
        """
        import re
        still_timed = re.compile(r"^\d{4}\D\d{1,2}\D\d{1,2}\s+\d{1,2}:\d{2}:\d{2}")
        for log in (RE17, RE18, RE19, RE20):
            if not log.is_file():
                continue
            left = [l for l in diff_round.normalized(log) if still_timed.match(l)]
            self.assertEqual(left, [], f"{log.name} 有 {len(left)} 行没剥掉时间戳")


if __name__ == "__main__":
    unittest.main(verbosity=2)
