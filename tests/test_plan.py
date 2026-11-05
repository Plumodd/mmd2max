"""计算层（build_plan）的契约测试。

GUI 的「参数即时预览」建立在两条保证上，这里把它们钉死：
  1. build_plan 不碰磁盘 —— 用户没按「导出」之前，反复调参不会留下半个文件；
  2. plan 里的正文行就是最终写进 TSV 的内容 —— 预览显示的不会和产物不一致。
"""
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mmd2max import export_obj               # noqa: E402
from tests import spec_table                 # noqa: E402
from tests.make_sample import create_sample  # noqa: E402

SCALE = 8.763


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pmx = create_sample(self.root / "src")
        self.out = self.root / "out"

    def tearDown(self):
        self.temp.cleanup()

    def _files(self):
        return sorted(p.relative_to(self.root).as_posix()
                      for p in self.root.rglob("*") if p.is_file())

    def test_build_plan_touches_no_files(self):
        before = self._files()
        plan = export_obj.build_plan(self.pmx, scale=SCALE)
        self.assertEqual(self._files(), before)
        self.assertFalse(self.out.exists())
        # 数据确实算出来了，不是「什么都没做」才没写文件
        self.assertTrue(plan.bones_lines)
        self.assertTrue(plan.limb_lines)

    def test_plan_lines_are_exactly_what_gets_written(self):
        plan = export_obj.build_plan(self.pmx, scale=SCALE)
        export_obj.export(self.pmx, self.out, plan)
        tables = (("bones_lines", "bones.tsv"), ("biped_map_lines", "biped_map.tsv"),
                  ("morph_lines", "morphs.tsv"), ("physics_lines", "physics.tsv"),
                  ("limb_lines", "limbs.tsv"), ("width_lines", "widths.tsv"),
                  ("meta_lines", "meta.tsv"))
        for attr, name in tables:
            written = (self.out / f"{self.pmx.stem}_{name}").read_text(
                encoding="utf-8").splitlines()
            self.assertEqual(getattr(plan, attr), written, f"{name} 与 plan 不一致")

    def test_parameters_change_the_numbers_not_just_the_files(self):
        useful = export_obj.build_plan(self.pmx, scale=SCALE, filter_useless=True)
        full = export_obj.build_plan(self.pmx, scale=SCALE, filter_useless=False)
        self.assertEqual((len(useful.kept), len(full.kept)), (23, 25))
        self.assertEqual(useful.skin_count, 23)
        # 样例里只有 2 根骨真的分到权重（每骨总权重满值 2.0），阈值抬到 1.2 就该剪掉其余
        strict = export_obj.build_plan(self.pmx, scale=SCALE, min_weight=1.2)
        self.assertEqual(strict.skin_count, 2)
        # skin_flag 列（按列名取，不再数第 12 列）：预览数字与落盘数据同源
        rows = spec_table.table(spec_table.join(strict.bones_lines))
        self.assertEqual([r["skin_flag"] for r in rows],
                         [str(f) for f in strict.skin_flag])

    def test_plan_reuses_a_parsed_model(self):
        from mmd2max.pmx import load_pmx
        model = load_pmx(self.pmx)
        plan = export_obj.build_plan(self.pmx, scale=SCALE, model=model)
        self.assertIs(plan.model, model)
        self.assertEqual(plan.bone_names,
                         export_obj.build_plan(self.pmx, scale=SCALE).bone_names)

    def test_gui_metrics_match_plan_fields(self):
        """GUI 指标表直接取 Plan 字段：数字对不上就是预览在骗人。"""
        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        import gui
        plan = export_obj.build_plan(self.pmx, scale=SCALE, min_weight=1.2)
        shown = {name: value for _g, name, value, _t in gui.plan_rows(plan)}
        self.assertEqual(shown["保留进骨架"], len(plan.kept))
        self.assertEqual(shown["加入蒙皮"], plan.skin_count)
        self.assertEqual(shown["Morpher 通道"], plan.morph_count)
        self.assertEqual(shown["肢段表行数"], plan.limb_count)
        self.assertEqual(shown["改挂链根"], plan.reparented)
        self.assertEqual(shown["原骨骼"], len(plan.model.bones))
        self.assertEqual(shown["丢弃未引用顶点"], plan.dropped_verts)


class VertexCompactionTests(unittest.TestCase):
    """「不被任何面引用的顶点」必须在导出侧就丢弃并重编号。

    实跑踩到（香菱）：OBJ 写了 19212 行 `v`，其中 320 行没有任何面引用，
    Max 2022 的 OBJ 导入器只按面建顶点，网格顶点数 18892，权重表却仍按 PMX
    原序号写 —— 差 320 行且从第一个孤儿之后整体错位，蒙皮挂到别的顶点上。
    Max 端因此抛「网格顶点数与期望不一致」中止。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pmx = create_sample(self.root / "src")
        self.out = self.root / "out"

    def tearDown(self):
        self.temp.cleanup()

    def _model_with_orphans(self):
        """追加两个面引用不到的顶点（4、5），并让表情指向它们。"""
        from mmd2max.pmx import Vertex, load_pmx
        model = load_pmx(self.pmx)
        for i in (4, 5):
            model.vertices.append(Vertex(position=(9 + i, 9, 9), normal=(0, 0, 1),
                                         uv=(0.5, 0.5), weights=[(0, 1.0)],
                                         deform_type="BDEF1"))
        # 顶点 1 有面引用（保留，序号不变），顶点 5 是孤儿（该条偏移一并丢弃）
        model.morphs[0].items += [(1, (0.4, 0.5, 0.6)), (5, (0.7, 0.8, 0.9))]
        return model

    def test_nothing_dropped_gives_identity_remap(self):
        plan = export_obj.build_plan(self.pmx, scale=SCALE)
        self.assertEqual(plan.dropped_verts, 0)
        self.assertEqual(plan.vert_remap, list(range(len(plan.positions))))
        self.assertEqual(plan.faces, [tuple(f) for f in plan.model.faces])

    def test_orphans_dropped_and_faces_renumbered(self):
        plan = export_obj.build_plan(self.pmx, scale=SCALE,
                                     model=self._model_with_orphans())
        self.assertEqual(plan.dropped_verts, 2)
        self.assertEqual(plan.vert_remap, [0, 1, 2, 3, -1, -1])
        # 顶点四件套与权重表同步紧凑化，下游只有一套序号
        for items in (plan.positions, plan.normals, plan.uvs, plan.deform):
            self.assertEqual(len(items), 4)
        self.assertEqual({i for f in plan.faces for i in f}, {0, 1, 2, 3})
        # 提示语进 notes：export() 会打印，GUI 也看得到，不会静默吞掉顶点
        self.assertTrue(any("未被任何面引用" in n for n in plan.notes))

    def test_morph_offsets_follow_the_compaction(self):
        plan = export_obj.build_plan(self.pmx, scale=SCALE,
                                     model=self._model_with_orphans())
        morph_rows = spec_table.table(spec_table.join(plan.morph_lines))
        self.assertEqual(len([r for r in morph_rows if r["morph_index"] == "0"]), 1)
        v_rows = spec_table.table(spec_table.join(plan.morph_lines), kind="V")
        # 表头条数 = 真实写出的 V 行数（Max 端按行读，但数字骗人一样是 bug）
        row0 = [r for r in morph_rows if r["morph_index"] == "0"][0]
        self.assertEqual(int(row0["item_count_or_flag"]), len(v_rows))
        self.assertEqual(sorted(int(r["vertex_index"]) for r in v_rows), [0, 1])

    def test_obj_vertices_equal_weights_rows(self):
        """落盘后的三方一致：OBJ 的 v 行数 = 权重表行数 = meta 的 vertex_count。"""
        plan = export_obj.build_plan(self.pmx, scale=SCALE,
                                     model=self._model_with_orphans())
        export_obj.export(self.pmx, self.out, plan)
        stem = self.pmx.stem
        obj = (self.out / f"{stem}.obj").read_text(encoding="utf-8").splitlines()
        v_count = sum(1 for ln in obj if ln.startswith("v "))
        max_face_index = max(int(tok.split("/")[0])
                             for ln in obj if ln.startswith("f ")
                             for tok in ln.split()[1:])
        weights_text = (self.out / f"{stem}_weights.tsv").read_text(encoding="utf-8")
        # weights.tsv 的表头只给前两列命名（vertex_index + bone_weight_pairs），
        # 第 2 列往右是变长的 `骨序号:权重` 对 ⇒ 这里取**数据行**而不是整文件行数。
        _, _, weight_rows = spec_table.parse(weights_text)
        meta = spec_table.key_value(
            (self.out / f"{stem}_meta.tsv").read_text(encoding="utf-8"))
        self.assertEqual(v_count, len(weight_rows))
        self.assertEqual(int(meta["vertex_count"]), v_count)
        self.assertEqual(int(meta["dropped_verts"]), 2)
        # 面不许引用越界顶点：越界就是序号没重编完，Max 会丢顶点或报错
        self.assertLessEqual(max_face_index, v_count)
        # 权重首列就是行号，且严格 0..n-1
        self.assertEqual([int(r[0]) for r in weight_rows], list(range(v_count)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
