# Signal Processing

## Overview

Raw MediaPipe landmarks contain noise from two sources:
1. **RGB-only depth ambiguity** — MediaPipe estimates depth from a single camera, causing micro-jitter
2. **Tremor** — natural hand tremor, especially relevant for accessibility users

Three layers of signal processing address this:

```
Raw landmark position
       │
       ▼
1. Noise floor clamp     ← suppress micro-jitter below 12.5px
       │
       ▼
2. One Euro Filter       ← adaptive low-pass smoothing
       │
       ▼
3. Velocity FSM          ← detect intentional flick, ignore tremor
       │
       ▼
4. WTA Isolation Index   ← suppress crosstalk between fingers
```

---

## 1. Noise Floor

**Constant:** `NOISE_FLOOR_PX = 12.5`

If a finger's raw displacement from the wrist is below 12.5px, it is clamped to the calibrated resting anchor. This prevents MediaPipe's depth hallucinations from triggering false velocity spikes.

```python
if resting_anchor is not None and raw_dist <= NOISE_FLOOR_PX:
    raw_dist = resting_anchor
```

---

## 2. One Euro Filter

**Reference:** Géry et al., 2012 — *"1€ Filter: A Simple Speed-based Low-pass Filter for Noisy Input in Interactive Systems"*

An adaptive low-pass filter that automatically adjusts its cutoff frequency based on signal velocity:
- **Slow movement** → low cutoff → heavy smoothing (suppresses tremor)
- **Fast movement** → high cutoff → light smoothing (preserves flick sharpness)

### Parameters per FingerUnit

| Stream | min_cutoff | beta | Purpose |
|--------|-----------|------|---------|
| Position | 0.1 | 0.01 | Tight smoothing, tremor suppression |
| Velocity | 2.0 | 0.0 | Looser — velocity needs to respond fast |

### Formula

```
alpha = 1 / (1 + tau/te)        where tau = 1/(2π × cutoff), te = 1/freq
cutoff = min_cutoff + beta × |dx_hat|
x_hat = alpha × x + (1 - alpha) × x_prev
```

---

## 3. FingerUnit FSM

Each finger unit (Index, Middle, Ring/Pinky) has an independent 3-state FSM:

```
        velocity ≥ threshold
IDLE ──────────────────────▶ RISING
  ▲                              │
  │                              │ velocity ≤ 50% of peak
  │                              ▼
  │◀──────────────────────── DWELL
       dwell confirmed            │
       (200ms + 5 frames)         │ position jumps > 10px
                                  ▼
                               IDLE (aborted)
```

### State Transitions

**IDLE → RISING**
- Triggered when filtered velocity ≥ `vel_threshold`
- `vel_threshold` is personalised during calibration (30% of peak flick velocity)
- Latches the current velocity as `local_peak_vel` and position as `peak_pos`

**RISING → DWELL**
- Triggered when velocity drops to ≤ 50% of `local_peak_vel` (`VEL_PEAK_DROP_RATIO = 0.50`)
- Starts a dwell timer and position stability buffer

**DWELL → FIRE (returns True)**
- Requires ALL of:
  1. Position within `peak_pos_tol_px` of the latched peak (15% of flick range)
  2. Dwell timer ≥ 200ms (`DWELL_CONFIRM_MS`)
  3. Last 5 frames stable — spread ≤ 10px (`FLICK_CONFIRM_FRAMES = 5`)
- Fires exactly once per flick, then resets to IDLE

**DWELL → IDLE (aborted)**
- Frame-to-frame position jump > 10px (`JUMP_TOL_PX`) — landmark instability
- Position drifts too far from latched peak

### Why Dwell Confirmation?

Without dwell confirmation, a fast tremor spike could satisfy the velocity threshold and fire. The 200ms hold + 5-frame stability check ensures the finger has genuinely reached a peak position and settled — not just passed through it.

---

## 4. Ring/Pinky Merging

Ring and Pinky fingers are merged into a single `ring_pinky` unit because of **flexion synergy** — these fingers naturally move together and cannot be independently controlled by most users.

```python
# Displacement: max of ring and pinky
raw_dists["ring_pinky"] = max(raw_dists["ring"], raw_dists["pinky"])

# Velocity: max of ring and pinky (emergency flick detection)
vel_rp = max(vel_ring, vel_pinky)
fu.velocity = vel_rp
```

---

## 5. Winner-Takes-All (WTA)

**Isolation Index:** `ISOLATION_INDEX = 0.4`

When multiple fingers move simultaneously, WTA suppresses weaker fingers to prevent crosstalk. A finger is only considered "active" if its displacement is ≥ 40% of the dominant finger's displacement.

```
active_units = {
    name: unit_state[name]
    for name in names_for_wta
    if unit_state[name][0] >= ISOLATION_INDEX * max_disp
}
```

**Example:**
- Index: 200px → active (200/200 = 1.0 ≥ 0.4)
- Middle: 50px → gated out (50/200 = 0.25 < 0.4)
- Ring/Pinky: 30px → gated out (30/200 = 0.15 < 0.4)

### Locked Winner

Once any unit enters DWELL state, it is locked as the winner for the duration of the 200ms confirmation window. This prevents WTA from stealing the winner slot mid-confirmation if another finger starts moving.

```
Unit enters DWELL → _locked_winner = that unit
Unit leaves DWELL → _locked_winner = None
```

---

## 6. Pinch Layer (Advanced Mode)

**Threshold:** `PINCH_THRESHOLD_RATIO = 20mm / 90mm ≈ 0.222`

Thumb (landmark 4) and Index (landmark 8) distance is normalised by hand size (wrist to middle MCP, landmark 9) to be scale-invariant.

```
pinch_ratio = pinch_dist_px / hand_size_px
is_pinched = pinch_ratio < PINCH_THRESHOLD_RATIO
```

**Hysteresis:** 5 consecutive frames required to switch layers in either direction (`FLICK_CONFIRM_FRAMES = 5`). This prevents flickering on borderline pinch distances.

When Advanced layer is active:
- Index FSM is frozen (mechanically occupied by pinch)
- WTA only considers Middle and Ring/Pinky
- Command map switches to `{middle: AC, ring_pinky: TV}`

---

## Constants Reference

| Constant | Value | Description |
|----------|-------|-------------|
| `NOISE_FLOOR_PX` | 12.5px | Micro-jitter suppression threshold |
| `ISOLATION_INDEX` | 0.4 | WTA crosstalk gate ratio |
| `VEL_PEAK_DROP_RATIO` | 0.50 | RISING→DWELL velocity drop ratio |
| `DWELL_CONFIRM_MS` | 200ms | Minimum dwell hold time |
| `FLICK_CONFIRM_FRAMES` | 5 | Stability frame count for confirmation |
| `JUMP_TOL_PX` | 10px | Max frame-to-frame jump during dwell |
| `COOLDOWN_PERIOD` | 2.0s | Post-trigger lockout period |
| `PINCH_THRESHOLD_RATIO` | 0.222 | Normalised pinch distance threshold |

---

## Command Maps

| Layer | Index | Middle | Ring/Pinky |
|-------|-------|--------|------------|
| Normal | Light (toggle) | Fan (toggle) | Alarm (3s trigger) |
| Advanced (pinch ON) | — disabled | AC (toggle) | TV (toggle) |
