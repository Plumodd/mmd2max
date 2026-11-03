"""M1 验收：改「表头 + 具名访问」之后，第十七轮的每一个数字必须逐位不变。

做法：拿 baselines/香菱_meta.tsv 里记的 scale 重新导出一次，把新的 6 个 TSV
和 baselines/ 里的旧文件逐行对比。M1 只允许每份文件**多出一行表头**，
数据行必须完全相同（字节级）。任何一处数字变化都是回归，不是「格式改进」。

例外只有下面两个**写死数值**的白名单（META_RENAMES / BONE_TAIL_OVERRIDES）：
每条都在运行时打印出来，且要求新值恰好等于白名单里的那几个数字 ——
估计算别的数照样判失败。想加新例外必须同时写清「为什么这是预期变化」，
不许写成「这一行随便变」。

为什么是独立脚本而不是 python - <<'PY' 内联：
    export_obj.export() 用 multiprocessing 进程池并行算顶点，spawn 模式要在
    子进程里重新导入 __main__；程序来自 stdin 时解释器没有可导入的源文件，
    直接 BrokenProcessPool（实测 OSError 22: ...\\<stdin>）。真实 .py 路径才能跑。

用法：
    python tools/check_baseline.py                 # 默认 香菱
    python tools/check_baseline.py 香菱-海灯节      # 换模型（baselines/ 里要有同名文件）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mmd2max import export_obj  # noqa: E402

# 基线里只有这 6 类文件；morphs/physics 第十七轮没落盘，不进回归范围。
BASELINE_GLOB = "{stem}_*.tsv"

# 已定价的**格式**变更白名单（只对 meta.tsv 生效，逐条打印出来，绝不静默放过）。
# M1b 把「脚的俯仰口径」从三重反转（导出侧 MMD2MAX_FOOT_FLATTEN → meta 的 foot_pitch
# 取反 → Max 端 MMD2MAX_FOOTPITCH 再 `if not`）压成一条直读 `foot_box_flat`：
# 键名换了、极性也换了，所以 `foot_pitch 0`（0 = 不俯仰 = 压平）与 `foot_box_flat true`
#（true = 压平）是**同一个几何**的两个写法。白名单只认这一对，别的任何变化照旧判失败。
META_RENAMES = {("foot_pitch", "0"): ("foot_box_flat", "true")}

# 已定价的**几何**变更白名单（只对 bones.tsv 生效，逐条打印，绝不静默放过）。
# v4 #66「胸部骨指向乳头」：PMX 里 左胸/右胸 的 tail_offset 是默认竖直短棒
# （head→tail = +Z 一整根 scale，7.6563cm，没有任何方向含义），导出侧现在改成
# 从网格里量出的乳头点（probes/breast_axis_probe.py 的估计器 H，两条独立算法读到
# 同一个点）。这是**用户 2026-11-03 截图判据要求的改动**，不是回归，所以进白名单。
# 白名单写死的是「新 tail 的三个数」而不是「这几行随便变」：估计器一旦被改得算出别的
# 点，这里对不上照样判失败 —— 保留它作为断言的意义。
BONE_TAIL_OVERRIDES = {
    "右胸": ("-4.915695", "-11.745141", "105.225345"),
    "左胸": ("4.915695", "-11.745141", "105.225345"),
}
TAIL_COLUMNS = (8, 9, 10)          # bones.tsv 里 tail_x/y/z 的列号（与表头一致）


def rewrite_bone_tails(lines: list[str]) -> tuple[list[str], list[str]]:
    """把基线 bones.tsv 中被白名单认下的尾坐标改写成新值，返回（行, 说明）。"""
    out, notes = [], []
    for line in lines:
        cols = line.split("\t")
        name = cols[1] if len(cols) > 1 else ""
        want = BONE_TAIL_OVERRIDES.get(name)
        if want is None or len(cols) <= max(TAIL_COLUMNS):
            out.append(line)
            continue
        old_tail = tuple(cols[i] for i in TAIL_COLUMNS)
        for i, value in zip(TAIL_COLUMNS, want):
            cols[i] = value
        out.append("\t".join(cols))
        notes.append(f"{name} tail {old_tail} -> {want}")
    return out, notes


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def rename_meta_keys(lines: list[str]) -> tuple[list[str], list[str]]:
    """把基线 meta 的旧键按白名单写成新键，返回（改写后的行, 改写了哪几行的说明）。"""
    out, notes = [], []
    for line in lines:
        key, _, value = line.partition("\t")
        hit = META_RENAMES.get((key.strip(), value.strip()))
        if hit is None:
            out.append(line)
            continue
        new_key, new_value = hit
        out.append(f"{new_key}\t{new_value}")
        notes.append(f"{key}={value} -> {new_key}={new_value}")
    return out, notes


def load_scale(baseline_dir: Path, stem: str) -> float:
    """旧 meta 无表头、新 meta 有表头 ⇒ 只认含 tab 的 key/value 行，别写死行号。"""
    for line in read_lines(baseline_dir / f"{stem}_meta.tsv"):
        parts = line.split("\t")
        if len(parts) == 2 and parts[0] == "scale":
            return float(parts[1])
    raise SystemExit(f"基线 meta 里找不到 scale：{baseline_dir / (stem + '_meta.tsv')}")


def find_model(stem: str) -> Path:
    candidates = [
        Path("C:/Users/Administrator/Desktop") / stem / f"{stem}.pmx",
        Path("C:/Users/Administrator/Desktop/test3") / f"{stem}.pmx",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise SystemExit("找不到模型源文件，试过的路径：\n  " +
                     "\n  ".join(str(c) for c in candidates))


def main(argv: list[str]) -> int:
    stem = argv[1] if len(argv) > 1 else "香菱"
    baseline_dir = ROOT / "baselines"
    outdir = Path("C:/Users/Administrator/AppData/Local/Temp/mmd2max_v4_check")
    pmx = find_model(stem)
    scale = load_scale(baseline_dir, stem)
    print(f"[1/2] 重新导出 {pmx.name}  scale={scale} -> {outdir}")
    plan = export_obj.build_plan(pmx, scale=scale)
    export_obj.export(pmx, outdir, plan)

    print("[2/2] 与基线逐行对比（只允许多一行表头）")
    old_files = sorted(baseline_dir.glob(BASELINE_GLOB.format(stem=stem)))
    if not old_files:
        raise SystemExit(f"基线目录里没有 {stem}_*.tsv：{baseline_dir}")
    bad = 0
    for old in old_files:
        new = outdir / old.name
        if not new.is_file():
            print(f"  [缺文件] {old.name}")
            bad += 1
            continue
        old_lines = read_lines(old)
        new_lines = read_lines(new)
        # M1 给这 6 个文件都加了首行表头，同时旧文件里那一行 `# 列名` 人读注释
        # 被表头取代 ⇒ 只比「非注释的数据行」，注释与表头各自打印出来给人看。
        # 不按「长得像不像表头」猜：猜错会把数据行当表头放过。
        header = new_lines[0]
        if not header.isascii():
            print(f"  [表头非 ASCII] {old.name}: {header}")
            bad += 1
        body = [ln for ln in new_lines[1:] if not ln.startswith("#")]
        data = [ln for ln in old_lines if not ln.startswith("#")]
        if old.name.endswith("_meta.tsv"):
            data, notes = rename_meta_keys(data)
            for note in notes:
                print(f"  [白名单改写] {old.name}: {note}")
        if old.name.endswith("_bones.tsv"):
            data, notes = rewrite_bone_tails(data)
            for note in notes:
                print(f"  [白名单改写] {old.name}: {note}")
        if body == data:
            print(f"  [数字不变] {old.name:<24} 数据 {len(body):>6} 行")
            print(f"      表头: {header}")
            continue
        bad += 1
        diff = next((i for i, (a, b) in enumerate(zip(data, body)) if a != b),
                    min(len(data), len(body)))
        print(f"  [数值变了] {old.name}：第 {diff + 1} 条数据行起不同，"
              f"行数 {len(data)} -> {len(body)}")
        print(f"      旧: {data[diff] if diff < len(data) else '<无此行>'}")
        print(f"      新: {body[diff] if diff < len(body) else '<无此行>'}")
    print("\n结论：" + ("M1/M1b 验收通过 —— 只有表头与白名单改写是新增的，数据逐位不变。"
                        if not bad else f"M1/M1b 未通过：{bad} 个文件的数据行发生变化。"))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
