"""统一八维评分校验（S3-A5，固定维序与权重，不得手算）。

用法: python3 tools/score_check.py
  内置 S3-A4 宇宙 U1-U7 的八维，逐项重算并与文件中宣称值比对；
  同时校验定级门槛：P0 >=4.3、P1 >=3.6、P2 >=2.8、<2.8 ARCHIVE；
  未打开单篇正文的条目不得正式定级为 P2 以上，统一归 candidate_unverified。
维序：战略重要性, 屈臣氏相关度, 影响范围, 来源可信度, 数据丰富度, 可执行性, 时效性, 新颖性
权重：[0.18, 0.24, 0.12, 0.12, 0.10, 0.14, 0.06, 0.04]
"""
WEIGHTS = [0.18, 0.24, 0.12, 0.12, 0.10, 0.14, 0.06, 0.04]

def total(components):
    assert len(components) == 8, "components must be 8"
    return round(sum(c * w for c, w in zip(components, WEIGHTS)), 2)

def grade(t):
    if t >= 4.3:
        return "P0"
    if t >= 3.6:
        return "P1"
    if t >= 2.8:
        return "P2"
    return "ARCHIVE"

# S3-A4 文件宣称值（用于比对纠正）
CASES = [
    ("U1", [3, 3, 2, 3, 1, 2, 4, 2], 2.62),
    ("U2", [1, 1, 1, 3, 1, 1, 4, 1], 1.42),
    ("U3", [1, 1, 1, 3, 1, 1, 4, 1], 1.42),
    ("U4", [3, 4, 3, 3, 1, 3, 4, 3], 3.10),
    ("U5", [1, 1, 1, 3, 1, 1, 4, 1], 1.42),
    ("U6", [2, 1, 2, 3, 1, 1, 4, 2], 1.76),
    ("U7", [2, 2, 2, 3, 1, 2, 4, 2], 2.08),
]

def main():
    errors = []
    for label, comps, claimed in CASES:
        t = total(comps)
        g = grade(t)
        status = "OK" if t == claimed else f"CORRECTED claimed={claimed} -> actual={t}"
        if t != claimed:
            errors.append((label, claimed, t))
        print(f"{label} components={comps} total={t:.2f} grade={g} {status}")
    print(f"corrections={errors} (U1 and U7 corrected; grade unchanged for both, both remain ARCHIVE)")
    # 未核全文规则：U4 不得正式定级
    print("candidate_unverified: U1, U2, U3, U4 (rules, full text NOT verified) -> formal grade must be candidate_unverified, not P2, until full text is opened")
    print("verified_full_text: U5, U6, U7 (Meituan news articles opened in S3-A5) -> grades above apply as fact-checked grades, all ARCHIVE")

if __name__ == "__main__":
    main()
