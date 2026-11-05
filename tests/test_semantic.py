"""骨骼角色分类 / 配置驱动映射测试。

验证「无用骨剔除」这一核心改进（用户要求）：
  - IK 控制骨（足IK/つま先IK/腕IK…）分类为 control -> 不参与变形骨架
  - 物理/调试用哑骨（物理用ダミー/剛体親/おおよそ…）分类为 control
  - 表情驱动骨（表情/両目）分类为 morph -> 不建骨，转 Morpher
  - 其余（含扭转骨、足D、补助骨）分类为 skeleton -> 保留为 Bone
  - humanoid_kept_indices / biped_targets 必须跳过 control/morph，
    且 ROOT（全ての親）不映射到 Bip001（避免双源指向 COM）。
"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mmd2max.pmx import load_pmx              # noqa: E402
from mmd2max import semantic                  # noqa: E402
from tests.make_sample import create_sample    # noqa: E402


class SemanticClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.model = load_pmx(create_sample(Path(self.temp.name)))

    def tearDown(self):
        self.temp.cleanup()

    def test_ik_and_dummy_bones_are_control(self):
        roles = semantic.classify_bone_roles(self.model)
        by_name = {b.name: i for i, b in enumerate(self.model.bones)}
        self.assertEqual(roles[by_name["左足IK"]], "control")
        self.assertEqual(roles[by_name["物理用ダミー"]], "control")

    def test_standard_and_assist_bones_are_skeleton(self):
        roles = semantic.classify_bone_roles(self.model)
        for name in ("センター", "下半身", "左腕", "左足", "髪_辅助"):
            idx = next(i for i, b in enumerate(self.model.bones) if b.name == name)
            self.assertEqual(roles[idx], "skeleton", f"{name} 应保留为 skeleton")

    def test_humanoid_kept_excludes_control_bones(self):
        kept, old_to_new, missing, conflicts = semantic.humanoid_kept_indices(self.model)
        by_name = {b.name: i for i, b in enumerate(self.model.bones)}
        # 控制骨不能出现在保留骨架里
        self.assertNotIn(by_name["左足IK"], kept)
        self.assertNotIn(by_name["物理用ダミー"], kept)
        # 22 根标准人形骨被保留（25 总数 - 2 控制骨 - 1 未匹配辅助骨）
        self.assertEqual(len(kept), 22)
        self.assertEqual(missing, [])
        self.assertEqual(conflicts, [])

    def test_biped_targets_skip_control_and_root(self):
        mapping, missing, conflicts = semantic.biped_targets(self.model)
        by_name = {b.name: i for i, b in enumerate(self.model.bones)}
        # IK / 哑骨不进 Biped 映射
        self.assertNotIn(by_name["左足IK"], mapping)
        self.assertNotIn(by_name["物理用ダミー"], mapping)
        # 映射的规模合理（22 根人形骨里除 ROOT 外都应映射）
        self.assertGreaterEqual(len(mapping), 20)
        # 关键目标节点名正确
        self.assertEqual(mapping[by_name["左足"]], "Bip001 L Thigh")
        self.assertEqual(mapping[by_name["左足首"]], "Bip001 L Foot")
        self.assertEqual(mapping[by_name["左腕"]], "Bip001 L UpperArm")
        # 全ての親（ROOT）若存在应避免双源指向 COM（pmx_to_biped 已排除 ROOT）；
        # 样例没有 ROOT，而 センター 承担 Biped 根 COM。
        center_idx = by_name["センター"]
        self.assertEqual(mapping[center_idx], "Bip001")
        root_idx = next((i for i, b in enumerate(self.model.bones)
                         if b.name == "全ての親"), None)
        if root_idx is not None:
            self.assertNotIn(root_idx, mapping)

    def test_config_json_is_source_of_truth(self):
        # 验证运行时确实读到了带 biped/role 的配置
        defs = semantic.load_config_definitions()
        self.assertIn("ARM_UPPER_L", defs)
        self.assertEqual(defs["ARM_UPPER_L"].get("biped"), "Bip001 L UpperArm")
        self.assertEqual(defs["ARM_UPPER_L"].get("role"), "skeleton")


class ToeExDropTests(unittest.TestCase):
    """脚趾 EX 辅助骨整段剔除（用户反馈：它与撑满鞋头的 Biped Toe0 重叠，是多余骨）。

    样例模型里没有 足先EX，所以这里只造一根骨的名字/父链——该函数只读
    bone.name 与 bone.parent_index，不需要完整 PMX。
    """

    def _model(self, names, parents):
        bones = [types.SimpleNamespace(name=n, parent_index=p)
                 for n, p in zip(names, parents)]
        return types.SimpleNamespace(bones=bones)

    def test_drops_ex_bone_and_its_whole_chain(self):
        model = self._model(
            ["左足首", "左つま先", "左足先EX", "左足先EX先", "左足先EX先2",
             "右足首", "右つま先", "右足先EX", "髪01"],
            [-1, 0, 0, 2, 3, -1, 5, 5, 0])
        self.assertEqual(semantic.dropped_toe_ex_indices(model), {2, 3, 4, 7})

    def test_keeps_toe_and_unrelated_ex_bones(self):
        model = self._model(["左足首", "左つま先", "左足先EX", "左ひざEX"],
                            [-1, 0, 0, 0])
        dropped = semantic.dropped_toe_ex_indices(model)
        self.assertNotIn(1, dropped)          # つま先 是 Biped Toe0 的来源，必须保留
        self.assertNotIn(3, dropped)          # 膝盖 EX 不在鞋头里，不动
        self.assertEqual(dropped, {2})


class ConfigFallbackContract(unittest.TestCase):
    """配置文件「在而读不出」必须中止，不许静默回落到内置定义（#56 收尾）。

    缺文件可以回落：`export_configs()` 随时能重写，内置 DEFINITIONS 就是它的来源。
    文件在而读不出再回落，等于把用户改过的那张映射表当成不存在 —— 程序照常跑、
    数字照常报，没人看得出自己所那条改动没生效。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.path = self.dir / "pmx_definition.json"
        self._real_dir = semantic.CONFIG_DIR
        semantic.CONFIG_DIR = self.dir

    def tearDown(self):
        semantic.CONFIG_DIR = self._real_dir
        self.temp.cleanup()

    def _write(self, payload) -> None:
        self.path.write_text(payload if isinstance(payload, str)
                             else json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_missing_file_still_falls_back_to_builtins(self):
        """没有配置文件是正常状态（装完包没重写过也一样有，删掉则回到内置），不许抛。"""
        self.assertEqual(semantic.load_config_definitions(), semantic.DEFINITIONS)
        self.assertTrue(semantic.standard_pmx_names())

    def test_valid_file_is_honoured(self):
        self._write({"bones": {"ARM_UPPER_L": {"ja": "左腕", "cat": "required"},
                               "FAKE": {"ja": "只在配置里的骨", "cat": "optional"}}})
        defs = semantic.load_config_definitions()
        self.assertEqual(set(defs), {"ARM_UPPER_L", "FAKE"}, "配置在但没被当成事实来源")
        # 空 biped 字段由代码补齐：配置文件只承诺 ja/cat/role，节点名仍由 biped_name 派生。
        self.assertEqual(defs["ARM_UPPER_L"]["biped"], "Bip001 L UpperArm")
        self.assertEqual(defs["ARM_UPPER_L"]["role"], "skeleton")
        self.assertIn("只在配置里的骨", semantic.standard_pmx_names(),
                      "standard_pmx_names 仍读旧解析路径：两个消费者只修了一个")

    def test_unparseable_file_aborts_instead_of_reverting(self):
        self._write('{"bones": {"ARM_UPPER_L": {"ja": "左腕", "cat": "requi')  # 截断
        for call in (semantic.load_config_definitions, semantic.standard_pmx_names):
            with self.assertRaises(semantic.ConfigUnusable,
                                   msg=f"{call.__name__} 把坏配置当成了没配置"):
                call()

    def test_file_without_usable_bones_aborts(self):
        for payload in ({"metadata": {"name": "x"}}, {"bones": []}, {"bones": {}}):
            self._write(payload)
            self.assertRaises(semantic.ConfigUnusable, semantic.load_config_definitions)

    def test_entry_missing_ja_or_cat_aborts_and_says_how_many(self):
        """缺 ja 的条目匹配不上、缺 cat 的条目进不了分类 —— 静默丢掉就是一张变短的映射表。"""
        self._write({"bones": {"A": {"ja": "左腕", "cat": "required"},
                               "B": {"cat": "required"},          # 没有名字
                               "C": {"ja": "右腕"},               # 没有分类
                               "D": "不是对象"}})
        with self.assertRaises(semantic.ConfigUnusable) as ctx:
            semantic.load_config_definitions()
        text = str(ctx.exception)
        self.assertIn("3 条", text, f"报错没数出坏条目：{text}")
        for sid in ("B", "C", "D"):
            self.assertIn(sid, text, f"报错没点名 {sid}：改配置的人不知道该修哪一行")
        self.assertIn("pmx_definition.json", text, "报错没给文件名")

    def test_the_shipped_config_passes_the_new_gate(self):
        """打包里那份配置文件必须过这道闸：否则上面五条只是在保护一个已经跑不通的默认状态。"""
        semantic.CONFIG_DIR = self._real_dir
        self.assertEqual(len(semantic.load_config_definitions()), len(semantic.DEFINITIONS))

    def test_shipped_configs_are_byte_identical_to_export_configs(self):
        """打包的三份 JSON 必须与 `export_configs()` 现写出来的逐字相同。

        「配置是唯一事实来源」这句只对了一半：它同时是**代码生成的产物**。两边分叉有两种
        走法，都会骗人 —— 手改了打包件（下次谁跑一次 export 就无声覆盖掉他的改动），
        或改了代码没重写打包件（用户对着的还是旧表）。这条把第三种走法也钉住了：
        删掉一个只写不读的字段，必须连着把产物一起删，否则留下一份说假话的配置。
        """
        import shutil
        target = self.dir / "regen"
        written = semantic.export_configs(target)
        try:
            for name, path in sorted(written.items()):
                shipped = self._real_dir / name
                self.assertTrue(shipped.is_file(), f"打包件里没有 {name}")
                self.assertEqual(shipped.read_bytes(), path.read_bytes(),
                                 f"{name}：打包件与代码写出来的不是同一份")
        finally:
            shutil.rmtree(target, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
