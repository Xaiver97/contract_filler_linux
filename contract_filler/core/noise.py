# -*- coding: utf-8 -*-
"""
平滑噪声 —— 手写逼真度的第一块基石。

为什么不用纯 random：
    纯 random 是白噪声，每个字独立乱跳，视觉上呈现"高频抖动"，
    看起来像"抖动的印刷体"。真人手写是两层运动叠加：
      - 低频连续：手臂缓慢漂移、行越写越歪（疲劳）
      - 高频微弱：肌肉微颤（tremor）
    因此必须用连续噪声来驱动低频部分。
"""

import math
import random


class SmoothNoise1D:
    """一维 value noise：连续、低频、可复现（给定 seed 结果一致）。

    用法::

        n = SmoothNoise1D(seed=42)
        y = n(x / 45.0) * 2.5   # x 越大变化越缓，45.0 是"波长"
    """

    def __init__(self, seed=None, size=256):
        rng = random.Random(seed)
        self.size = size
        self.table = [rng.uniform(-1.0, 1.0) for _ in range(size)]

    def __call__(self, x: float) -> float:
        """返回 [-1, 1] 区间的连续平滑值。"""
        i = int(math.floor(x))
        f = x - i
        # smoothstep：让插值在整数点处一阶导为 0，避免折线感
        f = f * f * (3.0 - 2.0 * f)
        a = self.table[i % self.size]
        b = self.table[(i + 1) % self.size]
        return a + (b - a) * f

    def at(self, x: float, amp: float, scale: float = 45.0) -> float:
        """便捷调用：指定幅度与波长。"""
        return self(x / scale) * amp


class BaselineDrifter:
    """基线漂移合成器：把四种真实书写效应叠在一起。

    组成：
        1. 手臂漂移 (drift)      —— 低频连续噪声，行整体缓慢起伏
        2. 蛇形弯曲 (snake)      —— 中频正弦，行"永远不直"
        3. 疲劳下移 (fatigue)    —— 随字数线性累积，越写越往下/往右
        4. 肌肉微颤 (tremor)     —— 高频微弱随机
    """

    def __init__(self, seed=None):
        self.drift = SmoothNoise1D(seed=seed)
        self.rng = random.Random(seed)

    def offset_y(self, idx: int, total: int, x: float,
                 drift_amp: float, drift_scale: float,
                 snake_amp: float, snake_period: float,
                 fatigue: float, tremor: float,
                 chaos: float = 1.0) -> float:
        """计算第 idx 个字符相对基线的垂直偏移（像素）。"""
        if total <= 0:
            total = 1
        progress = idx / float(total)

        dy = 0.0
        dy += self.drift(x / max(1e-6, drift_scale)) * drift_amp
        dy += math.sin(idx / max(1e-6, snake_period) * math.tau) * snake_amp
        dy += progress * fatigue
        dy += self.rng.uniform(-tremor, tremor)

        return dy * chaos

    def offset_x(self, jitter: float, chaos: float = 1.0) -> float:
        """字符水平位置抖动。"""
        return self.rng.uniform(-jitter, jitter) * chaos

    def advance(self, width: float, spacing_jitter: float,
                chaos: float = 1.0) -> float:
        """返回该字符应推进的水平距离（含字间距随机）。"""
        return width + self.rng.uniform(-spacing_jitter, spacing_jitter) * chaos
