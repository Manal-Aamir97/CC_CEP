"""Statistics for paired study data, using only the Python standard library.

paired_t()     paired t-test, 95% CI of the mean difference, Cohen's d_z
wilcoxon()     Wilcoxon signed-rank test: exact p when there are no ties (n <= 60),
               normal approximation with tie and continuity correction otherwise;
               matched-pairs rank-biserial correlation as effect size
spearman()     Spearman rank correlation
analysis/test_analysis.py checks these against known values.
"""
import math
from statistics import mean, median, stdev

__all__ = ["mean", "median", "stdev", "t_cdf", "t_quantile", "paired_t", "wilcoxon", "spearman", "ranks"]


def _betacf(a, b, x):
    """Continued fraction for the incomplete beta function (Numerical Recipes, betacf)."""
    tiny, eps = 1e-300, 3e-16
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a, b, x):
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    log_bt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(log_bt) * _betacf(a, b, x) / a
    return 1.0 - math.exp(log_bt) * _betacf(b, a, 1 - x) / b


def t_cdf(t, df):
    x = df / (df + t * t)
    tail = 0.5 * _betai(df / 2.0, 0.5, x)
    return 1.0 - tail if t > 0 else tail


def t_quantile(p, df):
    lo, hi = -1000.0, 1000.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def normal_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def paired_t(diffs):
    n = len(diffs)
    if n < 2:
        return {"n": n, "error": "need at least 2 pairs"}
    m, s = mean(diffs), stdev(diffs)
    if s == 0:
        return {"n": n, "mean_diff": m, "sd_diff": 0.0, "error": "all differences are equal"}
    se = s / math.sqrt(n)
    t = m / se
    df = n - 1
    p = 2 * (1 - t_cdf(abs(t), df))
    crit = t_quantile(0.975, df)
    return {"n": n, "mean_diff": m, "sd_diff": s, "t": t, "df": df, "p": min(1.0, p),
            "ci95": (m - crit * se, m + crit * se), "cohen_dz": m / s}


def ranks(values):
    """Ranks starting at 1, ties given their average rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            result[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return result


def _exact_wilcoxon_cdf(n, w):
    """P(W+ <= w) for n untied, non-zero differences (counts subsets of 1..n by sum)."""
    counts = [1] + [0] * (n * (n + 1) // 2)
    for k in range(1, n + 1):
        for total in range(len(counts) - 1, k - 1, -1):
            counts[total] += counts[total - k]
    return sum(counts[:int(w) + 1]) / 2 ** n


def wilcoxon(diffs):
    """Two-sided Wilcoxon signed-rank test; zero differences are dropped (Wilcoxon's method)."""
    nonzero = [d for d in diffs if d != 0]
    n = len(nonzero)
    if n == 0:
        return {"n_used": 0, "n_zero": len(diffs), "W": 0.0, "p": 1.0, "method": "none (all differences zero)",
                "rank_biserial": 0.0}
    r = ranks([abs(d) for d in nonzero])
    w_plus = sum(rank for rank, d in zip(r, nonzero) if d > 0)
    w_minus = sum(rank for rank, d in zip(r, nonzero) if d < 0)
    w = min(w_plus, w_minus)
    tied = len(set(abs(d) for d in nonzero)) < n
    if not tied and n <= 60:
        p, method = min(1.0, 2 * _exact_wilcoxon_cdf(n, w)), "exact"
    else:
        mu = n * (n + 1) / 4
        tie_groups = {}
        for value in (abs(d) for d in nonzero):
            tie_groups[value] = tie_groups.get(value, 0) + 1
        sigma2 = n * (n + 1) * (2 * n + 1) / 24 - sum(t ** 3 - t for t in tie_groups.values()) / 48
        z = (abs(w - mu) - 0.5) / math.sqrt(sigma2) if sigma2 > 0 else 0.0
        p, method = min(1.0, 2 * (1 - normal_cdf(max(z, 0.0)))), "normal approximation (ties or n > 60)"
    return {"n_used": n, "n_zero": len(diffs) - n, "W_plus": w_plus, "W_minus": w_minus, "W": w,
            "p": p, "method": method, "rank_biserial": (w_plus - w_minus) / (w_plus + w_minus)}


def spearman(x, y):
    if len(x) != len(y) or len(x) < 3:
        return None
    rx, ry = ranks(list(x)), ranks(list(y))
    mx, my = mean(rx), mean(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return None if sx == 0 or sy == 0 else sxy / (sx * sy)
