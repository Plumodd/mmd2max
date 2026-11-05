"""真实模型 Smoke 测试（默认桌面 test2/克雷薇.pmx，可用 MMD2MAX_TEST_PMX 指定）。

模型不存在时自动 skip，不阻塞常规测试；存在时校验导出路径的关键不变量。

存在意义：4 顶点的小样本覆盖不到真实数据的分支。本文件已实际抓到过一个
只有真实模型才会触发的 Bug —— `_remap_humanoid` 把 `_nearest_kept` 返回的
「新序号」又经 old_to_new 二次映射，导致 humanoid_only 抛 KeyError。
小样本里所有顶点权重恰好都直接命中保留骨，永远走不到那个分支。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mmd2max import export_obj  # noqa: E402
from mmd2max.pmx import load_pmx  # noqa: E402
from tests import spec_table  # noqa: E402

MODEL = Path(os.environ.get("MMD2MAX_TEST_PMX",
                            r"C:\Users\Administrator\Desktop\test2\克雷薇.pmx"))
SCALE = 0.08


def _scan(rows, bone_limit):
    """返回 (空行数, 越界行数, 未归一化行数, 出现过的骨序号集合)。

    bone_limit 语义：full/pruned 权重写「全局骨序号」→ 上限=总骨数；
    humanoid 权重写「kept 局部新序号」→ 上限=kept 数。
    """
    empty = outrange = unnorm = 0
    uniq = set()
    for line in rows:
        if not line.strip():
            empty += 1
            continue
        pairs = [p for p in line.split("\t")[1:] if p.strip()]
        if not pairs:
            empty += 1
            continue
        total = 0.0
        bad = False
        for pr in pairs:
            bone, weight = pr.split(":")
            idx = int(bone)
            uniq.add(idx)
            if not (0 <= idx < bone_limit):
                bad = True
            total += float(weight)
        if bad:
            outrange += 1
        if abs(total - 1.0) > 1e-3:
            unnorm += 1
    return empty, outrange, unnorm, uniq


def _export(source, outdir, *args, **kw):
    """两段式管线的测试快捷入口：build_plan（纯计算）-> export（落盘）。"""
    return export_obj.export(source, outdir, export_obj.build_plan(source, *args, **kw))


class RealModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not MODEL.is_file():
            raise unittest.SkipTest(f"真实模型不存在，跳过：{MODEL}")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.model = load_pmx(MODEL)

    @classmethod
    def tearDownClass(cls):
        tmp = getattr(cls, "tmp", None)
        if tmp is not None:
            tmp.cleanup()

    def _weights(self, subdir):
        """权重表的**数据行**：表头那一行不是顶点，注释也不算，行数必须等于 vertex_count。"""
        text = (self.out / subdir / f"{MODEL.stem}_weights.tsv").read_text(encoding="utf-8")
        return spec_table.data_lines(text)

    def test_full_export_consistent(self):
        info = _export(MODEL, self.out / "full", SCALE, min_weight=0.0)
        rows = self._weights("full")
        self.assertEqual(len(rows), info["vertex_count"])
        empty, outrange, unnorm, uniq = _scan(rows, len(self.model.bones))
        self.assertEqual((empty, outrange, unnorm), (0, 0, 0))
        self.assertLessEqual(len(uniq), info["skin_bone_count"])

    def test_prune_keeps_every_vertex(self):
        """P0#1：剪枝不得少行/空行，且非 Skin 骨的权重必须归并干净。"""
        info = _export(MODEL, self.out / "pruned", SCALE, min_weight=0.0001)
        rows = self._weights("pruned")
        self.assertEqual(len(rows), info["vertex_count"])
        empty, outrange, unnorm, uniq = _scan(rows, len(self.model.bones))
        self.assertEqual(empty, 0, "剪枝后出现空权重行")
        self.assertEqual(outrange, 0)
        self.assertEqual(unnorm, 0)
        self.assertLessEqual(
            len(uniq), info["skin_bone_count"],
            "出现了未参与 Skin 的骨骼权重（归并未生效）")

    def test_humanoid_no_empty_weights(self):
        """P0#2：humanoid_only 不得崩溃、不得产生空权重行。"""
        info = _export(MODEL, self.out / "hum", SCALE, humanoid_only=True)
        rows = self._weights("hum")
        self.assertEqual(len(rows), info["vertex_count"])
        # humanoid 权重是 kept 局部新序号
        empty, outrange, unnorm, _ = _scan(rows, info["skin_bone_count"])
        self.assertEqual((empty, outrange, unnorm), (0, 0, 0))

    def test_bone_morph_layout_on_real_file(self):
        """真实文件的骨 morph 按 vec4 读通：每项旋转必须是 4 元组。

        load_pmx 已强制「解析末尾 == 文件末尾」，能读通就说明宽度对；这里再抽查
        一项，防止将来有人把 vec4 改回 vec3 却刚好被某个小样本掩盖。
        """
        bone_morphs = [m for m in self.model.morphs if m.morph_type == 2]
        if not bone_morphs:
            self.skipTest("该模型没有骨变形，跳过布局抽查")
        for morph in bone_morphs:
            for item in morph.items:
                self.assertEqual(len(item[2]), 4,
                                 f"变形「{morph.name}」的骨旋转不是四元数")

    def test_toe0_is_never_silently_unbound(self):
        """#35 在真实文件上的不变量：Toe0 不许「安静地」拿 0 个顶点。

        换模型验收（克雷薇 / 香菱 / 海灯节…）时这一条比数值更有用：作者自己绑好了
        就该跳过、绑坏了就该改绑，两种都必须在 notes 里留话；什么都不说 = 配对没找到
        或者机制被谁改坏了，正是香菱那一轮「扳不动脚尖」查了很久的原因。
        """
        plan = export_obj.build_plan(MODEL, SCALE)
        hits: dict = {}
        for weights in plan.deform:
            for bone, w in weights:
                if w > 0.0:
                    hits[bone] = hits.get(bone, 0) + 1
            self.assertAlmostEqual(sum(w for _b, w in weights), 1.0, places=6)
        targets = [plan.biped_mapping.get(old) for old in plan.kept]
        toes = [t for t in targets if t and t.endswith("Toe0")]
        self.assertGreater(len(toes), 0, "这个模型没有 Toe0 映射，测试本身失效")
        for new_index, target in enumerate(targets):
            if target and target.endswith("Toe0") and hits.get(new_index, 0) == 0:
                self.assertTrue(any("脚尖改绑跳过" in n for n in plan.notes),
                                f"{target} 拿到 0 个顶点且 notes 里没有任何解释")


if __name__ == "__main__":
    unittest.main(verbosity=2)
