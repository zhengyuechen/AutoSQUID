"""Auto S-tune: live-center the locked input-SQUID output near a target by stepping the SQUID-flux DAC.

Automates the manual bench procedure — "nudge S-flux until the continuously-acquired trace looks centered
around 0 mV" — as a secant search: each step reads a short live mean, then moves flux by
dx = (target - mean) / slope (the slope self-finds the sign + gain, which are unknown and per-cooldown).
Stops when the live mean is within tol_v of target (what the eye judges), or when the predicted step is
below one DAC LSB (can't do better). Reads are finite live views (like OpenSQUID's prepareDc/measureDc,
not continuous). No reset between steps — flux shifts the locked baseline directly (matches the manual
flow + OpenSQUID zeroStage1Output). If the live mean shows no response to the S-flux steps (flat slope), it
returns 'no_response' rather than a false centering — the SQUID is not locked, DAQ_AI is wrong, or the
S-flux DAC isn't reaching the SQUID. LOCK the SQUID first.
"""
import time

from .config import require_fields
from .serial_io import set_squid_flux, reset
from .daq import live_mean
from .analysis import log_action

FLUX_MAX = 100.0                 # SQUID-flux DAC full scale (sflux), code 0x000-0xFFF
FLUX_RES = FLUX_MAX / 0xFFF      # one DAC LSB in sflux (~0.024) -> "can't move finer" fallback


def auto_s_tune(cfg,
                start_sflux=10.0,
                target_V=0.0,
                tol_V=0.020,        # "centered" band on the live mean (~1.5 x noise std; SEM of a 1 s read is ~0.02 mV)
                read_s=1.0,         # length of each live-view read (s)
                max_step_uA=10.0,   # clamp per step so the lock can't slip a fluxon
                max_iter=15,
                settle_s=0.5,
                reset_first=True):
    """Live-center the LOCKED SQUID output near target_V by stepping the SQUID-flux DAC.
    Returns dict(status, flux_sflux, mean_V, std_V, n_iter); status in
    {'converged','dac_limited','no_response','max_iter'} ('dac_limited' = the next step is below one DAC LSB
    while still outside tol_V, i.e. as centered as the DAC allows, not a true centering)."""
    require_fields(cfg, ["daq_ai", "port", "data_root", "user"], "auto_s_tune")

    def read(flux):
        "Set flux (clamped to the DAC range), settle, and return a live-view dict; print a one-line summary."
        flux = max(0.0, min(FLUX_MAX, flux))
        set_squid_flux(cfg, flux); time.sleep(settle_s)
        m = live_mean(cfg, seconds=read_s)
        print(f"    flux={flux:6.2f} sflux  mean={m['mean']*1e3:+7.2f} mV  std={m['std']*1e3:5.2f} mV  "
              f"[{m['min']*1e3:+.1f},{m['max']*1e3:+.1f}] mV")
        return flux, m

    def result(status, flux, m):
        "Pack the structured outcome + print the verdict; log the tune action when an output folder is configured."
        print(f"  {status.upper()} -> flux={flux:.3f} sflux, mean={m['mean']*1e3:+.2f} mV")
        if cfg.data_root and cfg.user:                       # same action_log.txt as run_cycle; skipped in standalone bench use
            cfg.outdir.mkdir(parents=True, exist_ok=True)
            log_action(cfg.outdir / "action_log.txt", "S_FLUX",
                       f"{status}: flux={flux:.2f} uA, mean={m['mean']*1e3:+.2f} mV, n_iter={_it}")
        return {"status": status, "flux_sflux": flux, "mean_V": m["mean"], "std_V": m["std"], "n_iter": _it}

    if reset_first:
        reset(cfg); time.sleep(settle_s)
    _it = 0
    xo, mo = read(start_sflux)
    if abs(mo["mean"] - target_V) < tol_V:           # already centered
        return result("converged", xo, mo)
    dx = -0.1 * xo if xo else max_step_uA             # first probe = 10% BELOW the start current
    flipped = False                                   # one fallback: if 10% down is flat, try 10% up before giving up

    for _it in range(1, max_iter + 1):
        xn, mn = read(max(0.0, min(FLUX_MAX, xo + max(-max_step_uA, min(max_step_uA, dx)))))
        if abs(mn["mean"] - target_V) < tol_V:       # centered -> confirm with one more read (catches slow drift)
            xc, mc = read(xn)
            if abs(mc["mean"] - target_V) < tol_V:
                return result("converged", xc, mc)
            xn, mn = xc, mc                           # drifted; keep iterating from the confirmation read
        slope = (mn["mean"] - mo["mean"]) / (xn - xo) if xn != xo else 0.0   # local V/sflux (sign + gain self-found)
        if abs(slope) < 1e-6:
            if not flipped:                          # first probe flat -> try 10% the OTHER way before giving up
                flipped = True
                dx = -dx                             # 10% less -> 10% more, from the same start point
                continue                             # xo, mo unchanged: probe the other side of start
            return result("no_response", xn, mn)     # both directions flat: not locked / wrong DAQ_AI
        dx = (target_V - mn["mean"]) / slope         # secant step toward target
        if abs(dx) < FLUX_RES:                       # can't move the DAC any finer (still outside tol_V)
            return result("dac_limited", xn, mn)
        xo, mo = xn, mn

    return result("max_iter", xo, mo)
