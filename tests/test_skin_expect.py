"""M4b 的尺从哪来：`parse_weights` / `skin_expectation` 的契约。

蒙皮回读那 20 多条判据的**期望值**全部由这两个函数从 `weights.tsv` + `bones.tsv` 算出。
它们一旦算错，红的是「Max 不合格」而真凶是尺 —— 这类 bug 在日志里看起来和真缺陷一模一样，
所以这里钉的全是「数错了会怎样」：

- 权重表按**行序**写入，`vertex_index` 只是自证；行序错了必须抛，不能默默照错位写下去（#13）。
- 骨号是**全局 0-based 行号**，Skin 里的骨号是**参与蒙皮后的紧凑号**；中间那层映射只有一份，
  漏一根 skin_flag=0 的骨就会让后面每一根错一号。
- 链路按**骨号出现次数**计，不按权重大小计（M4f C2：≲1e-8 的权重被存成 0.0，骨号还在）。
"""
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mmd2max import verdicts                               # noqa: E402
from tests import spec_table                               # noqa: E402


def bones_table(rows: list) -> str:
    """手写骨骼表：`rows` 是 `(idx, bone_name, biped_target, skin_flag)` 四元组。

    只填与蒙皮有关的列，其余留空位 —— 列名必须齐，缺列会让 `parse_table` 少读一半，
    而这里要的正是「列名对不上就报错」的行为，不是宽容。
    """
    lines = ["\t".join(spec_table.BONE_COLUMNS)]
    for idx, name, target, flag in rows:
        lines.append("\t".join((str(idx), name, str(idx - 1), "skeleton", target,
                                "0", "0", "0", "0", "0", "1", flag, str(idx - 1))))
    return "\n".join(lines) + "\n"


class ParseWeightsTests(unittest.TestCase):
    def test_round_trip_of_the_writer_shape(self):
        """蜕变测试：造出来 → 读回来 → 与造之前逐对相等（含变长行）。"""
        per_vertex = [[(0, 1.0)], [(3, 0.5), (4, 0.5)], [(7, 0.999), (8, 0.001)],
                      [(2, 1.0)]]
        got = verdicts.parse_weights(spec_table.weights_rows(per_vertex))
        self.assertEqual([[b for b, _ in pairs] for pairs in got],
                         [[b for b, _ in row] for row in per_vertex])
        self.assertAlmostEqual(got[2][1][1], 0.001, places=9)

    def test_wrong_header_is_refused_not_guessed(self):
        with self.assertRaises(ValueError) as got:
            verdicts.parse_weights("vertex\tweights\n0\t1:1.0\n")
        self.assertIn("vertex_index", str(got.exception))

    def test_empty_table_is_refused(self):
        with self.assertRaises(ValueError):
            verdicts.parse_weights("")

    def test_row_order_must_match_vertex_index(self):
        """权重按序号写入，行序 ≠ vertex_index 就是整表错位 —— 必须抛，不许「按列取值」。"""
        with self.assertRaises(ValueError) as got:
            verdicts.parse_weights("vertex_index\tbone_weight_pairs\n"
                                   "1\t1:1.0\n0\t2:1.0\n")
        self.assertIn("行序错位", str(got.exception))

    def test_cell_without_colon_is_refused(self):
        """`骨号:权重` 少冒号时静跳过 = 少数一条链路，而链路数是 gate。"""
        with self.assertRaises(ValueError) as got:
            verdicts.parse_weights("vertex_index\tbone_weight_pairs\n0\t1\n")
        self.assertIn("不含冒号", str(got.exception))

    def test_comment_blank_and_crlf_and_bom_tolerated(self):
        text = ("﻿vertex_index\tbone_weight_pairs\r\n"
                "# 给人看的图例\r\n"
                "\r\n"
                "0\t1:1.000000\t2:1.000000\r\n")
        self.assertEqual(len(verdicts.parse_weights(text)), 1)
        self.assertEqual(len(verdicts.parse_weights(text)[0]), 2)

    def test_trailing_tab_means_no_extra_pair(self):
        """行尾多一个制表符：Max 侧 `for j = cPairs to p.count` 同样当「没这一对」跳过。"""
        got = verdicts.parse_weights("vertex_index\tbone_weight_pairs\n"
                                     "0\t1:1.000000\t\n")
        self.assertEqual(got, [[(1, 1.0)]])


class SkinExpectationTests(unittest.TestCase):
    def test_non_skinned_bone_shifts_the_skin_id_by_one(self):
        """紧凑号映射只有一份：第 0 行不蒙皮时，第 1 行才是 Skin 里的 1 号骨。

        这条最容易写错成「骨号直接用行号」——那样每一根都会错一号，而链路总数照样对，
        因为总数不关心是哪根。所以断言看的是**每根骨各自的链路数**。
        """
        bones = verdicts.parse_table(bones_table([
            (0, "全ての親", "", "0"),
            (1, "左足", "Bip001 L Foot", "1"),
            (2, "左つま先", "Bip001 L Toe0", "1"),
        ]))[1]
        weights = verdicts.parse_weights(spec_table.weights_rows(
            [[(0, 1.0)], [(1, 1.0)], [(1, 1.0)], [(2, 1.0)]]))
        expect = verdicts.skin_expectation(bones, weights)
        self.assertEqual(expect.bones_skinned, 2)
        self.assertEqual(expect.links_expected, 3, "指向不蒙皮骨的那条不算链路")
        self.assertEqual(expect.verts_no_exp, 1, "只挂在不蒙皮骨上的顶点 = 没有期望")
        self.assertEqual(dict(expect.links_by_bone),
                         {"Bip001 L Foot": 2, "Bip001 L Toe0": 1})

    def test_biped_target_wins_over_bone_name(self):
        """复用 Biped 节点的骨，Skin 里挂的是 Biped 名；附属骨才是自己的名字。"""
        bones = verdicts.parse_table(bones_table([
            (0, "足首", "Bip001 L Foot", "1"),
            (1, "裙子A", "", "1"),
        ]))[1]
        expect = verdicts.skin_expectation(
            bones, verdicts.parse_weights(spec_table.weights_rows(
                [[(0, 1.0)], [(1, 1.0)]])))
        self.assertEqual(expect.links_expected, 2)
        self.assertEqual(expect.bones_skinned, 2)

    def test_tiny_nonzero_weight_is_a_link_but_a_zero_one_is_not(self):
        """两件事别混：M4f C2 说「极小但非零的权重会被存成 0.0，骨号还在」，#76 实测
        「**输入**就是 0.0 的对，Max 连骨号都不留」。

        海灯节那 332 条就是后者：PMX 权重 <5e-7，`%.6f` 印成 `0.000000` 交给 Skin，
        回读查不到那条链路，于是 links_missing / link_diff_max / link_mismatch_bones /
        links_read 四行一起为「对形变毫无影响」的数变红。所以期望链路只数非零对，
        零权单独记 `zero_links`（导出侧已不再发这种对，非 0 就是陈旧/手改的表）。
        """
        bones = verdicts.parse_table(bones_table([
            (0, "左足", "Bip001 L Foot", "1"),
            (1, "左辮", "", "1"),
        ]))[1]
        # 1e-6 量级的非零权重：印得出（0.000001）、Max 持有（海灯节实测这类链路全部回读到了）
        got = verdicts.skin_expectation(bones, verdicts.parse_weights(
            spec_table.weights_rows([[(0, 0.000001)]])))
        self.assertEqual((got.links_expected, got.zero_links), (1, 0),
                         "非零的极小权重仍然是链路：它会影响顶点跟哪几根骨走")
        # 输入正好 0.0：Max 存不下，期望里不许算它，否则这条 gate 永远红
        dropped = verdicts.skin_expectation(
            bones, verdicts.parse_weights(spec_table.weights_rows([[(0, 0.0)]])))
        self.assertEqual((dropped.links_expected, dropped.zero_links, dropped.verts_no_exp),
                         (0, 1, 1), "零权链路既不算期望、又要把数目说出来")
        # 混合一个顶点：非零那两根照数
        mixed = verdicts.skin_expectation(
            bones, verdicts.parse_weights(spec_table.weights_rows(
                [[(0, 0.999999), (1, 0.0)]])))
        self.assertEqual((mixed.links_expected, mixed.zero_links, mixed.verts_no_exp),
                         (1, 1, 0))
        self.assertEqual(dict(mixed.links_by_bone), {"Bip001 L Foot": 1})

    def test_out_of_range_bone_number_is_not_a_link(self):
        """骨号越界（指向不存在/已剔除的骨）不计链路：那由 `pairs_dropped` 那条 gate 兜。"""
        bones = verdicts.parse_table(bones_table([
            (0, "左足", "Bip001 L Foot", "1"),
        ]))[1]
        expect = verdicts.skin_expectation(
            bones, verdicts.parse_weights(spec_table.weights_rows([[(99, 1.0)]])))
        self.assertEqual((expect.links_expected, expect.verts_no_exp), (0, 1))

    def test_bone_row_order_must_match_idx(self):
        """骨骼表行序 = 权重表里的骨号，谁排第几错了就会蒙到隔壁骨。"""
        bones = verdicts.parse_table(bones_table([
            (1, "左足", "Bip001 L Foot", "1"),
        ]))[1]
        with self.assertRaises(ValueError) as got:
            verdicts.skin_expectation(
                bones, verdicts.parse_weights(spec_table.weights_rows([[(1, 1.0)]])))
        self.assertIn("行序不符", str(got.exception))

    def test_only_foot_chain_bones_get_a_per_bone_ruler(self):
        """每根骨一条 gate 太贵（香菱 321 根）：只给用户看得见的那几根，且按骨号顺序。"""
        bones = verdicts.parse_table(bones_table([
            (0, "右足", "Bip001 R Foot", "1"),
            (1, "左足", "Bip001 L Foot", "1"),
            (2, "左手", "Bip001 L Hand", "1"),
            (3, "右つま先", "Bip001 R Toe0", "1"),
        ]))[1]
        expect = verdicts.skin_expectation(
            bones, verdicts.parse_weights(spec_table.weights_rows(
                [[(0, 1.0)], [(1, 1.0)], [(2, 1.0)]])))
        self.assertEqual([nm for nm, _ in expect.links_by_bone],
                         ["Bip001 R Foot", "Bip001 L Foot", "Bip001 R Toe0"],
                         "顺序 = Skin 里的骨号顺序，不是名字排序")

    def test_fixture_expectation_is_self_consistent(self):
        """CHANNELS 的 fixture：一根不蒙皮 + 三行变长权重，必须真被解析成非空尺。

        这条看着像「测试测试」，但它挡的是「fixture 悄悄退化成空表」——
        那样 skin_rb 的通道集合就少一半，而通道契约测试照样全绿。
        """
        expect = verdicts.FIXTURE_SKIN_EXPECT
        self.assertEqual((expect.bones_skinned, expect.links_expected), (3, 4))
        self.assertEqual(expect.verts_no_exp, 0)
        self.assertEqual(dict(expect.links_by_bone),
                         {"Bip001 L Foot": 1, "Bip001 L Toe0": 2})


if __name__ == "__main__":
    unittest.main()
