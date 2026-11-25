#!/usr/bin/env python3
"""汇报前的数据体检：只读不改，输出一份 Markdown 报告。

用法：python checkup.py <表格路径> [--date-col 列名] [--key-cols 列1,列2] [--out 报告.md]
检查：重复行、空值、日期列漏月 / 重复月、数值列单位突变（相邻月数量级跳变）、异常值（IQR）、数字存成文本。
"""
import argparse
import sys
from pathlib import Path

import pandas as pd


def load(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(path)
    for enc in ("utf-8-sig", "gbk"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    sys.exit(f"读不了这个文件的编码：{path}")


def guess_date_col(df: pd.DataFrame):
    hints = ("日期", "月份", "月", "时间", "date", "month", "period")
    for c in df.columns:
        if any(h in str(c).lower() for h in hints):
            return c
    for c in df.columns:
        if df[c].dtype == object:
            parsed = pd.to_datetime(df[c], errors="coerce")
            if parsed.notna().mean() > 0.8:
                return c
    return None


def to_month(s: pd.Series) -> pd.Series:
    text = s.astype(str).str.strip()
    text = text.str.replace("年", "-", regex=False).str.replace("月", "", regex=False)
    return pd.to_datetime(text, errors="coerce").dt.to_period("M")


def numeric_like_text(s: pd.Series) -> bool:
    if s.dtype != object:
        return False
    cleaned = s.dropna().astype(str).str.replace(r"[,，¥￥%\s元万]", "", regex=True)
    if cleaned.empty:
        return False
    return pd.to_numeric(cleaned, errors="coerce").notna().mean() > 0.8


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--date-col")
    ap.add_argument("--key-cols", help="判断重复用的列，逗号分隔；不填则整行完全相同才算重复")
    ap.add_argument("--out")
    a = ap.parse_args()

    path = Path(a.path)
    df = load(path)
    issues = []  # (严重程度, 问题, 在哪里, 建议)
    lines = [f"# 体检报告：{path.name}", "", f"共 {len(df)} 行、{len(df.columns)} 列。列名：{('、'.join(map(str, df.columns)))}", ""]

    # 1. 重复行（Excel 行号 = 索引 + 2，因为第 1 行是表头）
    keys = [k.strip() for k in a.key_cols.split(",")] if a.key_cols else None
    dup = df[df.duplicated(subset=keys, keep=False)]
    if not dup.empty:
        rows = "、".join(str(i + 2) for i in dup.index)
        issues.append(("高：影响合计", f"{len(dup)} 行重复（{len(dup) - dup.duplicated(subset=keys).sum()} 组）", f"第 {rows} 行", "确认是否真重复；是的话每组保留一行"))

    # 2. 日期列：漏月、重复月
    dcol = a.date_col or guess_date_col(df)
    if dcol is not None and dcol in df.columns:
        months = to_month(df[dcol])
        bad = months.isna().sum()
        valid = months.dropna()
        if bad:
            issues.append(("中", f"日期列「{dcol}」有 {bad} 个值读不出日期", f"列「{dcol}」", "统一成 2026-01 这样的格式"))
        if not valid.empty:
            full = pd.period_range(valid.min(), valid.max(), freq="M")
            missing = sorted(set(full) - set(valid))
            if missing:
                issues.append(("高：影响合计", f"漏了 {len(missing)} 个月：{'、'.join(str(m) for m in missing)}", f"列「{dcol}」", "问清是没有业务（填 0 并说明）还是导出漏了（补数据）"))
            lines.append(f"日期列：「{dcol}」，范围 {valid.min()} 到 {valid.max()}。")
    else:
        lines.append("没找到日期列；如果有，用 --date-col 指定。")

    # 3. 空值
    for c in df.columns:
        n = df[c].isna().sum()
        if n:
            rows = "、".join(str(i + 2) for i in df.index[df[c].isna()][:10])
            issues.append(("中", f"列「{c}」有 {n} 个空值", f"第 {rows} 行" + (" 等" if n > 10 else ""), "问清是 0 还是缺数据，两者算法不同"))

    # 4. 数字存成了文本
    for c in df.columns:
        if numeric_like_text(df[c]):
            issues.append(("中", f"列「{c}」看起来是数字，但存成了文本（带逗号、单位或符号）", f"列「{c}」", "去掉单位和逗号后转成数字再算"))

    # 5. 数值列：单位突变 + 异常值
    num = df.select_dtypes("number")
    order = df.index
    if dcol is not None and dcol in df.columns:
        order = to_month(df[dcol]).sort_values(na_position="last").index
    for c in num.columns:
        s = num[c].loc[order].dropna()
        s = s[~s.index.duplicated()]
        flagged = set()
        # 单位突变：和前后相比差了 8 倍以上。孤立的一行（前后都正常）只报一次，算作那一行写错了单位
        pos = s[s > 0]
        if len(pos) >= 4:
            med = pos.median()
            for idx, v in pos.items():
                r = v / med
                if r > 8 or r < 1 / 8:
                    flagged.add(idx)
                    times = r if r > 1 else 1 / r
                    issues.append(("高：影响合计", f"列「{c}」第 {idx + 2} 行是 {v:,.0f}，约是其他月份的 {times:,.0f} 倍", f"第 {idx + 2} 行", "多半是单位不同（元 / 万元）或多打了零；核对后统一单位"))
        col = s.drop(index=list(flagged))
        # 异常值（IQR）
        if len(col) >= 6:
            q1, q3 = col.quantile(0.25), col.quantile(0.75)
            iqr = q3 - q1
            out = col[(col < q1 - 3 * iqr) | (col > q3 + 3 * iqr)]
            for idx, v in out.items():
                flagged.add(idx)
                issues.append(("低：问清原因", f"列「{c}」第 {idx + 2} 行的 {v:,.2f} 明显偏离其他值", f"第 {idx + 2} 行", "问原因；有原因的异常常常是汇报里的转折点"))
        # 相邻月份大起大落（排除已经报过的行）：不是错误，但最可能被老板问到
        if dcol is not None and dcol in df.columns and len(col) >= 4:
            chg = col.pct_change()
            for idx, p in chg.items():
                if pd.notna(p) and abs(p) >= 0.3 and idx not in flagged:
                    prev = col.index[col.index.get_loc(idx) - 1]
                    if prev in flagged:
                        continue
                    flagged.add(idx)  # 紧跟着的反弹不再重复报
                    issues.append(("低：问清原因", f"列「{c}」第 {idx + 2} 行比上一期{'涨' if p > 0 else '跌'}了 {abs(p):.0%}", f"第 {idx + 2} 行", "不一定是错，但汇报时最可能被问到原因，提前准备"))

    order_key = {"高：影响合计": 0, "高：影响增长率": 1, "中": 2, "低：问清原因": 3}
    issues.sort(key=lambda x: order_key.get(x[0], 9))
    serious = sum(1 for i in issues if i[0].startswith("高"))
    lines += ["", f"## 体检结论：发现 {len(issues)} 个问题，其中 {serious} 个会影响合计或增长率", ""]
    if issues:
        lines += ["| 严重程度 | 问题 | 在哪里 | 建议怎么处理 |", "| --- | --- | --- | --- |"]
        lines += [f"| {s} | {p} | {w} | {h} |" for s, p, w, h in issues]
    else:
        lines.append("没发现明显问题。仍建议核对一下单位和统计口径。")

    if not num.empty:
        lines += ["", "## 数值列概况", "", "| 列 | 合计 | 平均 | 最小 | 最大 |", "| --- | --- | --- | --- | --- |"]
        for c in num.columns:
            col = num[c]
            lines.append(f"| {c} | {col.sum():,.2f} | {col.mean():,.2f} | {col.min():,.2f} | {col.max():,.2f} |")
        lines.append("")
        lines.append("注：合计和平均是按原表直接算的，问题修好之前不要放进汇报。")

    report = "\n".join(lines)
    if a.out:
        Path(a.out).write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
