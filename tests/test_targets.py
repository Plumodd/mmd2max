"""M2：装配目标值（容差/开关）的唯一来源是 mmd2max/targets.py。

v4 之前它们是 `scripts/max_import_obj.ms` 里的 `global MMD2MAX_DIRTOL = 3.0` 那一类：
判据住在装配脚本里，Python 侧看不见，也就没有任何一层能校验「这次跑用的容差是多少」。
M1b 把路径与开关收进 MMD2MAX.CFG，M2 把**数**也收进去 —— 因为下一步（verdicts 表）
要在 Python 里用同一份数算 PASS/FAIL，两边的数分叉就等于验收是假的。

这一组测试不跑 Max，钉的是三件事：
  1. 数值本身是定价过的，改动会让这里红（而不是等一轮实跑发现落位读数变了）；
  2. CFG 里确实带上这五项，且 .ms 里对每一项只许「从 CFG 赋值一次」；
  3. .ms 里那次的上下限和 targets.py 声明的范围一致 —— 否则 Python 能写出一个 Max 必抛的值。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import obj_run                                    # noqa: E402
from mmd2max import targets                       # noqa: E402

SCRIPT = (PROJECT_ROOT / "scripts" / "max_import_obj.ms").read_text(encoding="utf-8")


def _code(text: str) -> str:
    """剥掉注释只留代码（和 test_maxscript_contract 的 _code 同一件事）。

    这一组的判据是「某个形状在代码里只出现一次」，而注释里必然会提到旧写法
    （`MMD2MAX_FOOTREACH = false ⇒ 退回第十六轮` 这种就是人读的回滚说明）；
    不剥注释的话，文档会把测试打脸 —— 踩过一次，写在这里防止再犯。
    """
    out = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"--[^\n]*", " ", out)


CODE = _code(SCRIPT)


class TargetValueTests(unittest.TestCase):
    """targets.py 里那几个数是**结论**，不是参数：每个都对应一条已定价的取舍。"""

    def test_priced_values_are_pinned(self):
        self.assertEqual(targets.DIR_TOL_DEG, 3.0)     # 5.0→3.0：4.618 度的脚骨偏航必须落进门控
        self.assertEqual(targets.ROLL_TOL_DEG, 1.0)    # 1.0 度 ≈ 0.05cm，低于回滚阈值分辨下限
        self.assertEqual(targets.TOE_TOL_CM, 0.5)
        self.assertEqual(targets.GAP_TOL_CM, 0.5)
        self.assertTrue(targets.FOOT_REACH)            # false = 退回第十六轮那根牙签

    def test_cfg_text_format_is_maxscript_readable(self):
        """float 写成 `3.0`、bool 写成 `true`/`false`。

        Python 的 True/False 落到 CFG 里，Max 侧 mmd2maxCfgBool 会当非法值抛错；
        而 `3` 这种整写法读回来是 integer，容差比较 `abs ang <= 1` 照样成立，
        但日志里打印成 `1` 就和上一轮的 `1.0` 对不上 —— 归一对比会当成实质差异。
        """
        values = targets.cfg_values()
        self.assertEqual(values["MMD2MAX_DIRTOL"], "3.0")
        self.assertEqual(values["MMD2MAX_TOETOL"], "0.5")
        self.assertEqual(values["MMD2MAX_FOOTREACH"], "true")
        for key, (value, kind, _, _) in targets.TARGETS.items():
            text = values[key]
            self.assertEqual(text, text.strip(), f"{key} 的值不能带空格")
            self.assertNotIn("True", text)
            self.assertNotIn("False", text)
            if kind == "bool":
                self.assertIn(text, ("true", "false"))
            else:
                self.assertRegex(text, r"^-?\d+\.\d+$")

    def test_values_sit_inside_their_own_declared_range(self):
        for key, (value, kind, low, high) in targets.TARGETS.items():
            if kind == "bool":
                self.assertIsInstance(value, bool, key)
                continue
            self.assertIsInstance(value, float, f"{key} 必须是 float，写成 int 会落成 '3'")
            self.assertTrue(low <= value <= high, f"{key}={value} 不在自己声明的 [{low},{high}]")

    def test_rollback_switches_are_still_reachable(self):
        """DIRTOL/ROLLTOL/TOETOL 同时是「整段不动」的回滚开关（写 999 就跳过），
        上限被压到合理值就等于把回滚位删了。"""
        self.assertGreaterEqual(targets.TARGETS["MMD2MAX_DIRTOL"][3], 999.0)
        self.assertGreaterEqual(targets.TARGETS["MMD2MAX_ROLLTOL"][3], 999.0)
        self.assertGreaterEqual(targets.TARGETS["MMD2MAX_TOETOL"][3], 999.0)


class CfgWiringTests(unittest.TestCase):
    """Python 侧：这五个键真的会进 CFG。"""

    def test_keys_are_declared_once(self):
        self.assertEqual(list(obj_run.TARGET_CFG_KEYS), list(targets.TARGETS))
        self.assertEqual(len(set(obj_run.CFG_KEYS)), len(obj_run.CFG_KEYS), "CFG_KEYS 有重复键")

    def test_cfg_keys_contain_every_target(self):
        missing = [k for k in targets.TARGETS if k not in obj_run.CFG_KEYS]
        self.assertEqual(missing, [], f"targets 里的键没进 CFG_KEYS：{missing}")

    def test_no_target_is_allowed_empty(self):
        # CFG_EMPTY_OK 是「这个键可以是空串 = 整段跳过」的名单；数值键进来就等于
        # Max 侧 mmd2maxCfgNum("") → 「值不是数字」，报错点离根因很远。
        overlap = [k for k in obj_run.CFG_EMPTY_OK if k in targets.TARGETS]
        self.assertEqual(overlap, [])


class MaxScriptConsumerTests(unittest.TestCase):
    """MAXScript 侧：只有一个赋值点，且它读的就是 CFG 里那个键。"""

    def test_each_target_is_assigned_exactly_once(self):
        for key in targets.TARGETS:
            found = re.findall(rf"^\s*{key}\s*=", CODE, flags=re.M)
            self.assertEqual(len(found), 1, f"{key} 赋值出现 {len(found)} 次，只许 1 次")

    def test_assignment_reads_cfg_not_a_literal(self):
        for key, (_, kind, _, _) in targets.TARGETS.items():
            reader = "mmd2maxCfgBool" if kind == "bool" else "mmd2maxCfgNum"
            self.assertIn(f"{key} = {reader} \"{key}\"", CODE,
                          f"{key} 的唯一赋值必须是 {reader} 从 CFG 读")

    def test_no_literal_default_survives_anywhere(self):
        """写回 `global MMD2MAX_DIRTOL = 3.0`（哪怕加在文件末尾）都会让 CFG 的值被盖掉，
        而且是静默盖 —— 这一条是整个 M2 的锁。名单从 TARGETS 推，不手写：M4d 加
        ROLLBACK/RBTRIG 时，手写的那份会漏掉它们，而漏掉的那条正是「回滚位被写死」。"""
        names = "|".join(sorted(k[len("MMD2MAX_"):] for k in targets.TARGETS))
        offenders = re.findall(rf"global\s+MMD2MAX_({names})\s*=[^\n]*", CODE)
        self.assertEqual(offenders, [], f"目标值不许写在脚本里：{offenders}")

    def test_declared_without_default_at_the_top(self):
        for key in targets.TARGETS:
            self.assertIn(f"\nglobal {key}\n", CODE, f"{key} 顶部声明缺了")
        self.assertIn("\nglobal MMD2MAX_CFGSUMMARY\n", CODE)

    def test_bounds_match_the_python_declaration(self):
        """.ms 的 [low, high] 和 targets.py 的元组必须逐字相同：
        Python 写出一个超出 Max 范围的数，会在装配第一步就抛「超出允许范围」，
        而那句话看起来像「配置写错了」，不像「两边声明分叉了」。"""
        for key, (_, kind, low, high) in targets.TARGETS.items():
            if kind == "bool":
                continue
            m = re.search(rf'{key} = mmd2maxCfgNum "{key}" (\S+) (\S+)', CODE)
            self.assertIsNotNone(m, f"{key} 的读取行没找到")
            self.assertEqual(float(m.group(1)), low, f"{key} 下限两边不一致")
            self.assertEqual(float(m.group(2)), high, f"{key} 上限两边不一致")

    def test_summary_line_carries_every_target(self):
        """配置摘要里看不见判据 = 事后没法知道这轮用的是哪套数（验收只能靠猜）。"""
        load = CODE[CODE.index("fn mmd2maxLoadCfg"):CODE.index("fn mmd2maxImportObj")]
        for key, label in (("MMD2MAX_DIRTOL", "方向容差"), ("MMD2MAX_ROLLTOL", "扭转容差"),
                           ("MMD2MAX_TOETOL", "趾落位容差"), ("MMD2MAX_GAPTOL", "盒隙容差"),
                           ("MMD2MAX_FOOTREACH", "脚链拉伸"),
                           ("MMD2MAX_ROLLBACK", "快照回滚"), ("MMD2MAX_RBTRIG", "回滚触发")):
            self.assertIn(f'{label}=" + ({key} as string)', load,
                          f"摘要里少了「{label}」（{key}）")
        # 上面那份标签名单是人写的，会漏；这一圈是推导的，任何新目标值没进摘要就红。
        for key in targets.TARGETS:
            self.assertIn(f"({key} as string)", load,
                          f"{key} 没进配置摘要：这一轮用的这套数事后无从查证")
        self.assertIn("MMD2MAX_CFGSUMMARY = cfgMsg", load)
        main = CODE[CODE.index("fn mmd2maxMain ="):]
        self.assertLess(main.index("mmd2maxInitLog()"), main.index("mmd2maxLog MMD2MAX_CFGSUMMARY"),
                        "摘要必须在 InitLog 之后打：InitLog 用 createFile 重建日志，先写的会被截掉")

    def test_float_reader_rejects_non_numbers(self):
        """`"abc" as float` 在 Max 里给 0，不给错。0 度容差会让每根骨都报超标，
        看着像判据生效，其实是配置没读到 —— 所以解析器必须先验字面再转。"""
        fn = CODE[CODE.index("fn mmd2maxCfgNum"):CODE.index("fn mmd2maxLoadCfg")]
        self.assertIn("的值不是数字", fn)
        self.assertIn("dots > 1", fn, "1.2.3 这种必须抛，不能悄悄读成 1.2")
        self.assertIn("throw", fn)


if __name__ == "__main__":
    unittest.main()
