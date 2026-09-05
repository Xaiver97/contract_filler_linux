# -*- coding: utf-8 -*-
"""
逐笔画扰动与超采样 —— 让单字产生"结构级"手写生涩感。

为什么需要这个模块：
    整字刚体渲染（一次 rotate + 平移）会让字内所有笔画整整齐齐地一起动，
    看起来"平滑、机械"。真人落笔时，同一个字里的每一笔是独立写出来的：
    彼此会轻微错动、开叉、不严格垂直/平行。
    本模块把单字先放大（超采样）画成灰度，再按 4-连通拆成若干"笔画块"，
    让每一块独立地随机平移 / 绕自身中心旋转，最后缩回目标字号。

    参考 Gsllchb/Handright 的逐笔画(连通域)扰动思想；这里额外做两点适配：
      1) 保留抗锯齿灰度（真人在扫描纸上写，必须带 AA 才能和纸融合）；
      2) 先放大到 S 倍再扰动、再缩回 —— 扰动发生在高分辨率，缩回后既平滑
         又保留笔画级错动，是小字号下依然看得出"手写感"的关键。
"""

import math

import numpy as np


def decompose(coverage: np.ndarray, solid_threshold: int = 40,
              min_blob: int = 6) -> list:
    """把单字的灰度覆盖图拆成若干"笔画块"。

    参数
    ----
    coverage : np.ndarray (H,W) float/uint8，0~255 笔画覆盖度（AA）。
    solid_threshold : int
        >= 该值的像素视为"实心笔画"，作为连通域种子。
    min_blob : int
        连通域像素数小于该值时并入背景（视为抗锯齿碎屑 / 点）。

    返回
    ----
    list of (list_of_xy, coverage_values)
        每项是一个笔画块：块内像素坐标 [(x,y),...] 与其覆盖度 [0..255,...]。
    """
    H, W = coverage.shape
    solid = coverage >= solid_threshold

    # 8-连通 flood-fill，找实心块
    label = np.zeros((H, W), dtype=np.int32)
    cur = 0
    blobs_px = []          # list of list[(x,y)]
    visited = np.zeros((H, W), dtype=bool)

    for y in range(H):
        row = solid[y]
        for x in range(W):
            if row[x] and not visited[y, x]:
                cur += 1
                # BFS
                stack = [(x, y)]
                visited[y, x] = True
                px = []
                while stack:
                    cx, cy = stack.pop()
                    label[cy, cx] = cur
                    px.append((cx, cy))
                    x0, x1 = max(0, cx - 1), min(W, cx + 2)
                    y0, y1 = max(0, cy - 1), min(H, cy + 2)
                    for yy in range(y0, y1):
                        rowy = solid[yy]
                        for xx in range(x0, x1):
                            if rowy[xx] and not visited[yy, xx]:
                                visited[yy, xx] = True
                                stack.append((xx, yy))
                blobs_px.append(px)

    # 把"非实心但仍有墨"(faint AA) 像素吸收到最近的实心块
    faint = (coverage > 0) & (~solid) & (~visited)
    fy, fx = np.nonzero(faint)
    for x, y in zip(fx.tolist(), fy.tolist()):
        best = None
        best_d = 1e18
        # 就近找标签：8 邻域内优先
        for yy in range(max(0, y - 1), min(H, y + 2)):
            for xx in range(max(0, x - 1), min(W, x + 2)):
                if label[yy, xx] > 0:
                    dd = (xx - x) ** 2 + (yy - y) ** 2
                    if dd < best_d:
                        best_d = dd
                        best = label[yy, xx]
        if best is None:
            cur += 1
            label[y, x] = cur
            blobs_px.append([(x, y)])
            best = cur
        else:
            label[y, x] = best

    # 按 label 汇总（含 faint 像素），并剔除过小碎块（归并到最邻近大块）
    buckets = {}
    for y in range(H):
        for x in range(W):
            if coverage[y, x] > 0 and label[y, x] > 0:
                buckets.setdefault(int(label[y, x]), []).append((x, y))

    # 剔除碎屑：像素太少又不是明显"点"(孤立小点)则丢弃
    result = []
    for lab, px in buckets.items():
        if len(px) < min_blob:
            continue
        vals = [coverage[y, x] for (x, y) in px]
        result.append((px, vals))
    return result


def _principal_axis(xs, ys):
    """返回笔画块沿主轴的单位向量 (ux,uy)。用 2x2 协方差最大特征向量近似。"""
    cx, cy = xs.mean(), ys.mean()
    dx = xs - cx
    dy = ys - cy
    a = float((dx * dx).sum())
    b = float((dx * dy).sum())
    c = float((dy * dy).sum())
    # 2x2 对称矩阵 [a b; b c] 的最大特征值对应特征向量
    trace = a + c
    disc = math.sqrt(max(0.0, (a - c) ** 2 + 4.0 * b * b))
    lam = (trace + disc) / 2.0
    if lam <= 1e-9:
        return 1.0, 0.0
    ux = a - c
    uy = 2.0 * b
    n = math.hypot(ux, uy)
    if n < 1e-9:
        # 退化（近似正圆），随便取水平方向
        return 1.0, 0.0
    ux /= n
    uy /= n
    # 保证主轴较长方向；若协方差接近零(单点)也回退水平
    return ux, uy


def perturb_strokes(coverage: np.ndarray,
                    dx_sigma: float, dy_sigma: float, theta_sigma: float,
                    rng,
                    growth_sigma: float = 0.0,
                    pressure: float = 0.0,
                    dry: float = 0.0) -> np.ndarray:
    """对单字灰度图做逐笔画扰动，返回扰动后的灰度图。

    思路
    ----
    1. decompose 拆成笔画块；
    2. 每个块独立做 4 件事：
       A/B) 绕块质心旋转 theta、整体平移 (dx, dy)；
       C)   沿主轴方向的**垂直(次要)轴**缩放 growth —— 只改笔画粗细、
            不改长度（起笔重收笔轻用 pressure 在 alpha 上做渐变）；
       D)   沿主轴做低幅"断墨/飞白"淡化（dry），制造墨量枯竭的断续感。
    3. 用"散点累积"(max) 把块的像素盖回新图 —— 旋转/平移都是小量，
       碰撞处取 max，不产生双倍墨色；
    4. 因为调用前已放大 S 倍、返回后再缩回，散点产生的锯齿会在 LANCZOS
       缩小阶段被平滑掉，肉眼只见"笔画错动 + 粗细不均 + 飞白"。

    参数
    ----
    coverage : np.ndarray (H,W) uint8 0~255。
    dx_sigma / dy_sigma / theta_sigma : 高斯扰动幅度（像素/弧度）。
    rng : numpy random.Generator
    growth_sigma : float
        C 阶段：每笔沿次要轴的粗细高斯方差(比例)。>0 时同一字不同笔画
        粗细不一；0 关闭。
    pressure : float
        C 阶段：起笔重/收笔轻幅度 0~0.6。每笔沿主轴两端做 alpha 渐变。
    dry : float
        D 阶段：断墨/飞白强度 0~1（建议 0.1~0.4）。每笔沿主轴随机 0~2 处
        墨色淡化。

    返回
    ----
    np.ndarray (H,W) uint8，扰动后的覆盖度。
    """
    # A+B 全关且 C/D 全关 → 原样返回（保留原始 AA）
    if (abs(dx_sigma) <= 0 and abs(dy_sigma) <= 0 and
            abs(theta_sigma) <= 0 and abs(growth_sigma) <= 0 and
            pressure <= 0 and dry <= 0):
        return coverage

    H, W = coverage.shape
    blobs = decompose(coverage)
    out = np.zeros((H, W), dtype=np.float32)
    if not blobs:
        return coverage

    for px, vals in blobs:
        xs = np.array([p[0] for p in px], dtype=np.float64)
        ys = np.array([p[1] for p in px], dtype=np.float64)
        vs = np.array(vals, dtype=np.float32).astype(np.float64)

        # 质心（作为旋转/缩放中心）
        cx = xs.mean()
        cy = ys.mean()

        # ---- 沿主轴坐标投影 t ∈[0,1]，供粗细/压感/断墨定位 ----
        ux, uy = _principal_axis(xs, ys)
        proj = (xs - cx) * ux + (ys - cy) * uy
        span = proj.max() - proj.min()
        if span > 1e-6:
            t = (proj - proj.min()) / span
        else:
            t = np.zeros_like(proj)

        # ---- 每笔独立随机量 ----
        theta = rng.normal(0.0, abs(theta_sigma))
        dx = rng.normal(0.0, abs(dx_sigma))
        dy = rng.normal(0.0, abs(dy_sigma))
        growth = 1.0 + rng.normal(0.0, abs(growth_sigma))
        # 次要轴（垂直主轴）单位向量
        mux, muy = -uy, ux

        # 旋转后坐标
        if abs(theta) > 1e-9:
            cth, sth = math.cos(theta), math.sin(theta)
            xr = (xs - cx) * cth - (ys - cy) * sth + cx
            yr = (xs - cx) * sth + (ys - cy) * cth + cy
        else:
            xr, yr = xs, ys

        # ---- C：沿次要轴缩放（只改粗细，不改长度）----
        if abs(growth - 1.0) > 1e-6:
            x0 = xr - cx
            y0 = yr - cy
            along = x0 * ux + y0 * uy          # 主轴分量
            minor = x0 * mux + y0 * muy        # 次要轴分量
            xr = cx + along * ux + growth * minor * mux
            yr = cy + along * uy + growth * minor * muy

        # ---- C 压感：起笔重 / 收笔轻（沿主轴 alpha 渐变）----
        if pressure > 0:
            # 随机决定哪端是"起笔"，让同一字里不同笔画轻重方向错落
            if rng.random() < 0.5:
                grad = 1.0 - pressure * t
            else:
                grad = pressure + (1.0 - pressure) * t
            vs = vs * grad

        # ---- D 断墨 / 飞白：沿主轴 0~2 处局部淡化 ----
        if dry > 0:
            n_dip = 0
            r_ = rng.random()
            if r_ < 0.55:
                n_dip = 1
            elif r_ < 0.75:
                n_dip = 2
            for _ in range(n_dip):
                # 淡化点位置与宽度（沿主轴的比例）
                tc = rng.uniform(0.12, 0.88)
                wd = rng.uniform(0.04, 0.12)
                depth = dry * rng.uniform(0.5, 1.0)
                dip = depth * np.exp(-((t - tc) ** 2) / (2.0 * wd * wd))
                vs = vs * (1.0 - dip)

        nx = xr + dx
        ny = yr + dy

        # 散点累积：只保留落图内像素，用 max 避免重叠双写
        nx_i = np.rint(nx).astype(np.int64)
        ny_i = np.rint(ny).astype(np.int64)
        inside = (nx_i >= 0) & (nx_i < W) & (ny_i >= 0) & (ny_i < H)
        if not inside.any():
            continue
        nnx = nx_i[inside]
        nny = ny_i[inside]
        vv = np.clip(vs[inside], 0, 255)
        np.maximum.at(out, (nny, nnx), vv)

    return np.clip(out, 0, 255).astype(np.uint8)
