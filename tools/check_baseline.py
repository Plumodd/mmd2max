"""M1 验收：拿基线那一代记的 scale 重新导出，数据行必须**逐字节**不变。

做法：拿 baselines/香菱_meta.tsv 里记的 scale 重新导出一次，把新的 6 个 TSV
和 baselines/ 里的旧文件逐行对比。M1 只允许每份文件**多出一行表头**，
数据行必须完全相同（字节级）。任何一处数字变化都是回归，不是「格式改进」。

基线是**会换代的**：换代 = 一次「已经实跑背书的数值变更」把参考冻结到新状态，
旧一代整份留在 baselines/gen17/（第十七轮那一代，含 #66 之前的 bones.tsv）。
换代必须在这里写下理由与背书，否则这条闸就退化成「红了就改基线」。

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
# v4 #66「胸部骨指向乳头」与本轮的「占位尾改钉导出厘米」都已经在基线里了，所以这里
# 清空 —— 留着会在每次运行时打印一条「old -> new」而两边其实是同一个数，那是假警报。
# 上一代基线（第十七轮 + #66 之前）没有删：整套复制在 baselines/gen17/ 里 ——
# 版本控制只放行 `baselines/round*` 那批读数件（见 .gitignore 的 `baselines/*` + `!baselines/round*`），
# `gen17/` 与金标准 tsv 仍在 ignore 里（不随克隆走），就地覆盖等于永久丢掉那条血缘。
#   换代对撞实测（gen17 → 本轮，去掉表头后逐行比）：只有 `香菱_bones.tsv` 变 **37 行**，
#   其余 5 张（biped_map 53 / limbs 39 / meta 41 / weights 18892 / widths 12）**逐行相同**。
#   这 37 行分两笔账，别混成一笔：
#   ①35 行是本轮「占位尾改钉导出厘米」：M5 单位协变抓到占位尾写成「0.1 / 0.05 个 PMX
#     单位」（香菱 0.76563cm、克雷薇 0.8763cm —— 同一根无名骨在两具模型上长得不一样），
#     改成导出厘米 0.8 / 0.4 之后 33 行无名附属骨 tail_z +0.03437、
#     `Bip001` 与 `Bip001 R Hand` 两行 +0.017185。
#   ②2 行是 #66「胸部骨指向乳头」（左胸/右胸 tail_x/y/z），换代前一直由下面的
#     BONE_TAIL_OVERRIDES 白名单代管，现在冻进基线，所以白名单清空。
#   背书 = 2026-11-04 20:48~20:49 第七十四轮（香菱 `--physics --shot`，退出码 0）：
#   判据 530 通过 / 0 失败 / 21 未跑（其中 27 条 lock），物理代理 254 根 0 失败、
#   关节 330 条 0 失败，21 张截图取景「投不出=0」、最小纵向填充 54.3342%。
BONE_TAIL_OVERRIDES: dict = {}
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
