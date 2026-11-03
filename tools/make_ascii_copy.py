"""把导出产物复制成全 ASCII 路径副本，排除 Max 2022 按 GBK 解析导致的找不到文件问题。

用法: python tools/make_ascii_copy.py <源目录> <模型stem> <目标目录> <ascii stem>
"""
import shutil
import sys
from pathlib import Path

TEX_MAP = {
    "體.png": "tex00.png",
    "顏.png": "tex01.png",
    "髮.png": "tex02.png",
    "飾.png": "tex03.png",
    "skin.bmp": "tex04.bmp",
}


def main():
    src = Path(sys.argv[1]).resolve()
    stem = sys.argv[2]
    dst = Path(sys.argv[3]).resolve()
    ascii_stem = sys.argv[4]

    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)

    tex_src = src / f"{stem}_tex"
    tex_dst = dst / f"{ascii_stem}_tex"
    # 贴图：复制并改成 ASCII 名，保留子目录结构
    path_map = {}
    if tex_src.is_dir():
        for old_file in sorted(tex_src.rglob("*")):
            if not old_file.is_file():
                continue
            rel = old_file.relative_to(tex_src)
            new_name = TEX_MAP.get(old_file.name)
            if new_name is None:
                # 未在映射表中的贴图：按序号兜底命名，避免中文名进入 Max
                new_name = f"tex{len(path_map):02d}{old_file.suffix}"
            target = tex_dst / rel.parent / new_name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old_file, target)
            path_map[str(old_file.resolve())] = str(target.resolve())

    # OBJ：改写 mtllib 行
    obj_text = (src / f"{stem}.obj").read_text(encoding="utf-8")
    obj_text = obj_text.replace(f"mtllib {stem}.mtl", f"mtllib {ascii_stem}.mtl")
    (dst / f"{ascii_stem}.obj").write_text(obj_text, encoding="utf-8")

    # MTL：map_Kd 改指向 ASCII 贴图
    mtl_src = src / f"{stem}.mtl"
    if mtl_src.is_file():
        mtl_text = mtl_src.read_text(encoding="utf-8")
        for old, new in path_map.items():
            mtl_text = mtl_text.replace(old.replace("\\", "/"), new.replace("\\", "/"))
            mtl_text = mtl_text.replace(old, new)
        (dst / f"{ascii_stem}.mtl").write_text(mtl_text, encoding="utf-8")

    # TSV：原样复制（数值列无编码问题；骨骼名可能显示为乱码但不影响装配）
    for suffix in ("_bones.tsv", "_weights.tsv", "_biped_map.tsv"):
        f = src / f"{stem}{suffix}"
        if f.is_file():
            shutil.copy2(f, dst / f"{ascii_stem}{suffix}")

    print(f"ASCII 副本已生成: {dst}")
    print(f"  贴图 {len(path_map)} 张 -> {tex_dst}")
    for k, v in path_map.items():
        print(f"    {Path(k).name} -> {Path(v).name}")


if __name__ == "__main__":
    main()
