#!/usr/bin/env python3
"""把 skills/ 下每个 skill 打成网页版可上传的 ZIP（ZIP 顶层是 skill 文件夹本身），输出到 dist/。"""
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
dist = root / "dist"
dist.mkdir(exist_ok=True)
for skill in sorted((root / "skills").iterdir()):
    if not (skill / "SKILL.md").exists():
        continue
    out = dist / f"{skill.name}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(skill.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, f.relative_to(skill.parent))
    print(out.relative_to(root))
