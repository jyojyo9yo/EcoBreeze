"""
pmv.py

Standard Fanger PMV calculation (ISO 7730 / ASHRAE 55, Fountain & Huizenga
1997 reference implementation) plus a solve for the indoor temperature (ta)
that hits a target PMV. Ported from the raspi/ teammate's comfort-band code
(raspi/pmv.py) so person_detect.py can compute the same "optimal temperature"
from a live BME280 humidity reading.
"""

import math
from dataclasses import dataclass


@dataclass
class PmvResult:
    pmv: float
    ppd: float


def calc_pmv(ta: float, tr: float, rh: float, vel: float, met: float, clo: float) -> PmvResult:
    """
    ta: indoor air temp (C), tr: mean radiant temp (C, assumed == ta indoors)
    rh: relative humidity (%), vel: air speed (m/s), met: metabolic rate (met),
    clo: clothing insulation (clo)
    """
    wme = 0.0  # external work (0 for a sleeping/resting scenario)

    pa = rh * 10.0 * math.exp(16.6536 - 4030.183 / (ta + 235.0))  # water vapor pressure (Pa)

    icl = 0.155 * clo
    m = met * 58.15
    w = wme * 58.15
    mw = m - w

    fcl = (1.0 + 1.29 * icl) if icl <= 0.078 else (1.05 + 0.645 * icl)

    hcf = 12.1 * math.sqrt(vel)
    taa = ta + 273.0
    tra = tr + 273.0
    tcla = taa + (35.5 - ta) / (3.5 * icl + 0.1)

    p1 = icl * fcl
    p2 = p1 * 3.96
    p3 = p1 * 100.0
    p4 = p1 * taa
    p5 = 308.7 - 0.028 * mw + p2 * (tra / 100.0) ** 4

    xn = tcla / 100.0
    xf = xn
    hc = 0.0
    eps = 0.00015
    n = 0

    while True:
        xf = (xf + xn) / 2.0
        hcn = 2.38 * abs(100.0 * xf - taa) ** 0.25
        hc = hcf if hcf > hcn else hcn
        xn = (p5 + p4 * hc - p2 * xf ** 4) / (100.0 + p3 * hc)
        n += 1
        if abs(xn - xf) <= eps or n >= 150:
            break

    tcl = 100.0 * xn - 273.0

    hl1 = 3.05 * 0.001 * (5733.0 - 6.99 * mw - pa)
    hl2 = 0.42 * (mw - 58.15) if mw > 58.15 else 0.0
    hl3 = 1.7 * 0.00001 * m * (5867.0 - pa)
    hl4 = 0.0014 * m * (34.0 - ta)
    hl5 = 3.96 * fcl * (xn ** 4 - (tra / 100.0) ** 4)
    hl6 = fcl * hc * (tcl - ta)

    ts = 0.303 * math.exp(-0.036 * m) + 0.028
    pmv = ts * (mw - hl1 - hl2 - hl3 - hl4 - hl5 - hl6)
    ppd = 100.0 - 95.0 * math.exp(-0.03353 * pmv ** 4 - 0.2179 * pmv ** 2)

    return PmvResult(pmv=pmv, ppd=ppd)


def solve_ta_for_target_pmv(target_pmv: float, rh: float, vel: float, met: float, clo: float,
                             ta_min: float, ta_max: float, eps: float) -> float:
    """Binary-searches [ta_min, ta_max] for the ta that hits target_pmv.
    PMV increases monotonically with ta, so this converges safely."""
    lo, hi = ta_min, ta_max

    pmv_lo = calc_pmv(lo, lo, rh, vel, met, clo).pmv
    pmv_hi = calc_pmv(hi, hi, rh, vel, met, clo).pmv
    if target_pmv <= pmv_lo:
        return lo
    if target_pmv >= pmv_hi:
        return hi

    for _ in range(40):
        mid = (lo + hi) / 2.0
        pmv_mid = calc_pmv(mid, mid, rh, vel, met, clo).pmv
        if abs(pmv_mid - target_pmv) < eps:
            return mid
        if pmv_mid < target_pmv:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0
