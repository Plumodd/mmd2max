"""一条命令跑完「新模型 → 可运行的 Max 脚本」。

用法：
    python tools/run_model.py "C:/Users/Administrator/Desktop/test3"
    python tools/run_model.py "D:/models/xxx" --height 160 --min-weight 0
    python tools/run_model.py "D:/models/xxx" --drop-guides   # 不导出辅助骨骼

它做四件事：
  1. 在给定目录里找 .pmx / .pmd（多个时报错让你指定）；
  2. 按身高自动算 scale（默认 160cm，可用 --height 改）；
  3. 导出到 test_runs/<模型名>/（OBJ / MTL / textures / bones / limbs / ...）；
  4. 生成 mmd2max_run.ms（只生成、不启动 Max），并打印一份体检摘要：
     骨骼数、Biped 映射、手指数量、脊柱节数、材质/贴图、裙骨/头发骨规模。

摘要里这几项是「换模型最容易翻车」的地方，先看它们再跑 Max：
  - 手指 3 指还是 5 指（决定 Biped 建几根指头）
  - 脊柱节数（决定 createNew 的 spineLinks）
  - 材质数与带贴图数（决定会不会一片灰）
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mmd2max import export_obj, semantic          # noqa: E402
from mmd2max.pmx import load_pmx                  # noqa: E402


def find_model(folder: Path) -> Path:
    if folder.is_file():
        return folder
    # Windows 文件系统大小写不敏感，*.pmx 与 *.PMX 会匹配到同一个文件 → 按解析路径去重
    seen: dict[Path, Path] = {}
    for pattern in ("*.pmx", "*.PMX", "*.pmd", "*.PMD"):
        for path in folder.glob(pattern):
            seen.setdefault(path.resolve(), path)
    models = [seen[k] for k in sorted(seen)]
    if not models:
        raise SystemExit(f"目录里没有找到 .pmx/.pmd：{folder}")
    if len(models) > 1:
        listing = "\n".join(f"  - {p.name}" for p in models)
        raise SystemExit(f"目录里有多个模型，请直接把要跑的那个文件传进来：\n{listing}")
    return models[0]


def summarize(pmx: Path, outdir: Path) -> None:
    model = load_pmx(pmx)
    mapping, missing, conflicts = semantic.biped_targets(model)
    fingers, finger_links = semantic.finger_layout(mapping)
    spine_links = semantic.spine_link_count(mapping)

    def count(pred) -> int:
        # 必须同时看译名：很多模型的日文名是无意义代号（wt_0_0），
        # 真正的「Skirt_0_0 / hair_01」写在 name_en 里。只查 name 会谎报 0 根
        # （香菱-海灯节实测：日文名 0 命中，name + name_en 命中 432 根）。
        return sum(1 for b in model.bones if pred(f"{b.name} {b.name_en}"))

    hair = count(lambda n: any(k in n.lower() for k in ("hair", "髪", "毛")))
    skirt = count(lambda n: "裙" in n or "skirt" in n.lower())
    tex = [t for t in model.textures if t]

    print("\n=== 模型体检 ===")
    print(f"  文件        : {pmx.name}（{pmx.stat().st_size / 1024 / 1024:.1f} MB）")
    print(f"  顶点/面     : {len(model.vertices)} / {len(model.faces)}")
    print(f"  骨骼        : {len(model.bones)} 根（映射到 Biped {len(mapping)} 根）")
    print(f"  手指        : {fingers} 指 × {finger_links} 节")
    print(f"  脊柱        : {spine_links} 节（Pelvis 之上）")
    print(f"  材质/贴图   : {len(model.materials)} / {len(tex)}")
    print(f"  附属骨      : 裙 {skirt} 根，头发类 {hair} 根")
    print(f"  刚体/关节   : {len(model.rigid_bodies)} / {len(model.joints)}")
    print(f"  表情        : {len(model.morphs)}")
    if missing:
        print(f"  [!] 缺少必需人形骨: {'、'.join(missing)}")
    if conflicts:
        print(f"  [!] 语义冲突: {'、'.join(conflicts)}")
    print(f"\n  导出目录    : {outdir}")
    print(f"  Max 脚本    : {outdir / 'mmd2max_run.ms'}")


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 1
    folder = Path(args[0]).resolve()
    height = 160.0
    min_weight = 0.0
    # --drop-guides：不导出「什么都不干的辅助骨」（センター2 / WeaponL·R / 十根指先…）。
    # 默认不传 = 保持历轮行为，基线数字不变。
    drop_guides = "--drop-guides" in argv
    for i, item in enumerate(argv):
        if item == "--height" and i + 1 < len(argv):
            height = float(argv[i + 1])
        if item == "--min-weight" and i + 1 < len(argv):
            min_weight = float(argv[i + 1])

    pmx = find_model(folder)
    outdir = ROOT / "test_runs" / pmx.stem
    # 目录交给 export() 自己建：在这里先 mkdir 会在 auto_scale/export 抛错时
    # 留下一堆空目录，看起来像「跑过了」。
    scale = export_obj.auto_scale(pmx, target_cm=height)
    print(f"[1/3] 导出：{pmx.name}  scale={scale}（目标身高 {height}cm）"
          + ("　[--drop-guides] 辅助骨骼不导出" if drop_guides else ""))
    plan = export_obj.build_plan(pmx, scale=scale, min_weight=min_weight,
                                 keep_guides=not drop_guides)
    info = export_obj.export(pmx, outdir, plan)
    print(f"      顶点 {info['vertex_count']}  面 {info['face_count']}  "
          f"骨骼 {info['bone_count']} -> {info['kept_bone_count']}")

    print("[2/3] 生成 Max 装配脚本（gen-only，不会启动 Max）")
    cmd = [sys.executable, str(ROOT / "tools" / "obj_run.py"), str(outdir),
           pmx.stem, "--shot", "--gen-only"]
    # 不 capture 子进程输出：obj_run 打的是 UTF-8 中文，而 subprocess 在 Windows 上
    # 按本地代码页(GBK)解码管道，真实踩过 UnicodeDecodeError → result.stdout 变 None
    # → write() 抛 TypeError。让子进程直接继承 stdio 既正确又能实时看到进度。
    if subprocess.call(cmd, cwd=ROOT) != 0:
        return 1

    print("[3/3] 体检")
    summarize(pmx, outdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
