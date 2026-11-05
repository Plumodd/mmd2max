"""M1 线格式的测试侧解析器：一份实现，所有测试共用。

格式约定（写的一侧是 `mmd2max/export_obj.py` 的 `_header_line` / `_kind_header`，
读的一侧是 `scripts/max_import_obj.ms` 的 `mmd2maxReadTable`，这里是第三侧）：

    第 1 行        = tab 分隔的列名，不是注释也不是数据
    `#` 开头的行    = 给人看的注释，跳过
    `@KIND\\t列名…` = 同一文件里**另一种记录**的列名；它的列名从第 2 个字段起对齐
                     （第 1 个字段是记录类型本身，所以数据行的第 1 列统一叫 `row_kind`）
    其余行         = 数据

为什么测试必须跟着改成按列名取：如果测试继续数 `r[2]`，那它就只在自己这一侧适配新格式
—— 列序一变，它读到隔壁列还照样绿。这正是 M1 要治的病，不能由测试先犯一遍。
"""
from __future__ import annotations


def join(lines) -> str:
    """`Plan.*_lines` 是「不含结尾换行的行列表」，落盘时才拼成文本。
    测试直接吃这个列表，免得每处都写一遍 join。"""
    return "\n".join(lines)


def parse(text: str):
    """返回 (主记录列名, {子记录类型: 列名}, 数据行)。"""
    cols: list = []
    kinds: dict = {}
    rows: list = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if not cols:
            cols = fields
            continue
        if fields[0].startswith("@"):
            kinds[fields[0][1:]] = fields[1:]
            continue
        if fields[0].startswith("#"):
            continue
        rows.append(fields)
    if not cols:
        raise AssertionError("表里没有表头行：M1 要求第 1 行就是列名")
    return cols, kinds, rows


def header(text: str) -> list:
    """主记录的列名清单（契约断言用）。"""
    return parse(text)[0]


def data_lines(text: str) -> list:
    """数据行的**原始字符串**（表头、注释、`@KIND` 行都不算）。
    只给「每行长度可变、按位置成对取」的表用（weights.tsv 的 `骨序号:权重` 对）；
    定宽表请改用 table()，别在这里数下标。"""
    return ["\t".join(fields) for fields in parse(text)[2]]


def key_value(text: str) -> dict:
    """两列的 key/value 表（meta.tsv）→ dict。列名仍从表头取，不许数下标。"""
    return {row["key"]: row["value"] for row in table(text)}


def table(text: str, kind: str | None = None, variable_width: bool = False) -> list:
    """数据行 -> `dict[列名] = 值`。`kind` 指定时只取该记录类型的行，并用它自己的列名。

    列名对不上（表头少了列、或 `@KIND` 行没写）直接抛，不静默补 None：
    静默容错是本项目最贵的一类 bug 的来源。

    窄行以前**静默截断**（`zip` 到行尾就停，后面的键根本不存在），只有宽行会抛 ——
    蜕变测试第一次读 `limbs.tsv` 的 `foot_len` 就撞在 `KeyError` 上。现在两边都拦，
    因为「少一列」比「多一列」更常见也更阴：宽行会读出隔壁列的值，窄行让下游
    拿到 KeyError 或者（更糟）拿到 `row.get(...)` 的 None。

    确实按设计就是变宽的表（`limbs.tsv` 的第 10~18 列只有 `mode=foot` 的行才写）
    要显式声明 `variable_width=True`：缺的列在 dict 里就是**不存在**，调用方必须
    自己判Presence，而不是让它悄悄等于空串。声明这个参数就是把「我知道这表有 ragged 行」
    写进调用点，读代码的人一眼看得见。
    """
    cols, kinds, rows = parse(text)
    if kind is None:
        names = cols
        # 主记录 = 第 1 列不是任何一种 `@KIND` 声明过的记录。不过滤的话，V / JOINT
        # 这些子记录行会被 zip 到主表头的名字上，字段少的静默截断、读出隔壁列的值 ——
        # 正是 M1 要消灭的那类静默。
        rows = [r for r in rows if r[0] not in kinds]
    else:
        if kind not in kinds:
            raise AssertionError(f"文件里没有 @{kind} 的列名行，只有 {sorted(kinds)}")
        names = ["row_kind"] + kinds[kind]
    result = []
    for fields in rows:
        if kind is not None and fields[0] != kind:
            continue
        if len(fields) > len(names):
            raise AssertionError(f"行比表头宽（{len(fields)} > {len(names)}）：{fields}")
        if len(fields) < len(names) and not variable_width:
            missing = names[len(fields):]
            raise AssertionError(
                f"行比表头窄（{len(fields)} < {len(names)}），少的列 {missing} 会被静默丢掉："
                f"{fields}\n这表真是变宽设计的话就传 variable_width=True。")
        result.append(dict(zip(names, fields)))
    return result


# ---------------------------------------------------------------- 蒙皮输入的写侧
#
# bones.tsv / weights.tsv 是 M4b 那批回读判据的**唯一**期望值来源，所以测试要么喂真文件、
# 要么用这两个函数造出「解析器认得的形状」。以前守卫测试往里塞 `"x\n"` 就够了 ——
# 那时 Python 侧根本不读这两张表；现在塞假内容会在装规格表的第一步就抛，
# 抛的是「列名不对」，看起来像 fixture 坏了，其实测不到了东西。
BONE_COLUMNS = ("idx", "bone_name", "parent", "role", "biped_target",
                "head_x", "head_y", "head_z", "tail_x", "tail_y", "tail_z",
                "skin_flag", "max_parent")

# 链路数会按骨名单独上报的几根（`verdicts.LINK_SUBJECT_SUFFIXES`）：造出来的表里
# 必须有它们，否则「每根脚骨一条 gate」这类行永远是空集，测试也就钉不住它。
FOOT_CHAIN_NAMES = ("Bip001 L Foot", "Bip001 L Toe0",
                    "Bip001 R Foot", "Bip001 R Toe0")


def bones_text(count: int, no_skin: int = 0) -> str:
    """`count` 根骨：前四根是脚链（名字能被链路判据认出来），末尾 `no_skin` 根 skin_flag=0。

    行序 == `idx`：`verdicts.skin_expectation` 会逐行核对这条，写错顺序就该抛。
    """
    lines = ["\t".join(BONE_COLUMNS)]
    for i in range(count):
        name = FOOT_CHAIN_NAMES[i] if i < len(FOOT_CHAIN_NAMES) else f"Bip001 Bone{i}"
        lines.append("\t".join((
            str(i), name, str(i - 1), "skeleton", name,
            "0.000000", "0.000000", "10.000000", "0.000000", "0.000000", "20.000000",
            "0" if i >= count - no_skin else "1", str(i - 1))))
    return "\n".join(lines) + "\n"


def weights_text(verts: int, bones: int) -> str:
    """每个顶点挂一根骨（权重 1.0），骨号按 `顶点号 % 骨数` 轮转 —— 每根都有链路。

    变长列（一对顶点挂两三个骨）由 `weights_rows()` 造；这里先用最规整的一骨一权重，
    让「行数 == 顶点数」「链路总数」这类期望值能口算出来当断言。
    """
    return weights_rows([[(i % bones, 1.0)] for i in range(verts)])


def weights_rows(per_vertex: list) -> str:
    """`[[(骨号, 权重), …], …]` → weights.tsv 正文（第 i 行的 vertex_index 就是 i）。"""
    lines = ["vertex_index\tbone_weight_pairs"]
    for i, pairs in enumerate(per_vertex):
        lines.append("\t".join([str(i)] + [f"{b}:{w:.6f}" for b, w in pairs]))
    return "\n".join(lines) + "\n"
