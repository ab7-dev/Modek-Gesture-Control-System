# Calibration

## Why Calibration Is Needed

Every user has a different:
- **Hand size** — affects raw pixel displacements
- **Motor range** — how far they can flick
- **Flick speed** — peak velocity varies significantly

Without calibration, fixed thresholds would either miss slow flicks or trigger on tremor. Calibration personalises three key values to the individual user.

---

## What Gets Calibrated

| Value | Description | Used For |
|-------|-------------|---------|
| `resting_anchor` | Mean displacement at rest (px) | Noise floor reference, progress bar zero point |
| `max_flick` | Maximum displacement during flick (px) | Progress bar scale |
| `vel_threshold` | 30% of peak flick velocity (px/s) | FSM IDLE→RISING trigger |
| `peak_pos_tol_px` | 15% of flick range (px) | DWELL position stability window |

---

## Two-Phase Process

### Phase 1 — Resting (3 seconds)

**What to do:** Hold your hand completely still in front of the camera.

**What is recorded:** Filtered displacement of the dominant finger unit from the wrist, sampled every frame for 3 seconds (~90 samples at 30fps).

**What is computed:**
```python
resting_anchor = mean(calib_results)
```

This establishes the baseline — the "zero point" for all subsequent displacement measurements.

---

### Phase 2 — Flick (3 seconds)

**What to do:** Flick your fingers hard and fast repeatedly.

**What is recorded:**
- Peak displacement values → `calib_results`
- Absolute velocity values → `calib_vel_results`

**What is computed:**
```python
max_disp = max(calib_results)
peak_vel = max(calib_vel_results)

# Velocity threshold: 30% of peak flick velocity
if peak_vel > 1.0:
    vel_threshold = 0.30 * peak_vel
else:
    # Fallback: estimate from displacement range assuming 150ms flick
    span = max(max_disp - resting_anchor, 35.0)
    vel_threshold = span / 0.15 * 0.30

# Max flick: ensure at least 35px above resting
max_flick = max(max_disp, resting_anchor + 35.0)

# Peak position tolerance: 15% of flick range
flick_range = max_flick - resting_anchor
peak_pos_tol_px = 0.15 * flick_range
```

---

## Calibration Flow (Automatic)

Calibration is fully automatic — triggered by clicking **▶ Start Capture** on Service 1. No manual button press needed on Service 2.

```
Service 1 clicks Start
       │
       ▼
Connect to Service 2
       │
       ▼
Send {"type": "control", "cmd": "calib_hold"}
       │                    │
       │                    ▼ Service 2
       │               Reset all FingerUnits
       │               calib_step = 1
       │               Start recording resting samples
       │
       ▼ (3 seconds)
Send {"type": "control", "cmd": "calib_flick"}
       │                    │
       │                    ▼ Service 2
       │               resting_anchor = mean(calib_results)
       │               calib_step = 2
       │               Start recording flick samples
       │
       ▼ (3 seconds)
Send {"type": "control", "cmd": "calib_done"}
                            │
                            ▼ Service 2
                       Compute vel_threshold
                       Compute max_flick
                       Compute peak_pos_tol_px
                       Apply tol to all FingerUnits
                       calib_step = 0
                       Status: "Ready ✓ vel_thr=..."
```

---

## Calibration Tips

**For best results:**

| Phase | Tip |
|-------|-----|
| Hold still | Keep hand in the same position you'll use during operation |
| Flick | Use the same fingers you'll use for commands — flick each one |
| Flick | Flick as fast and hard as you naturally would |
| Both | Keep hand in frame — if MediaPipe loses tracking, samples are missed |

**If calibration produces poor results:**
- `vel_threshold` too high → flicks not detected → re-calibrate, flick harder
- `vel_threshold` too low → tremor triggers → re-calibrate, hold stiller in Phase 1
- Click **Calibrate** button on Service 2 to manually re-run at any time

---

## Recalibration

The **Calibrate** button on Service 2 can be used at any time to manually re-run calibration independently of Service 1. This is useful when:
- The user changes position
- Lighting changes affect MediaPipe tracking quality
- The user wants to adjust sensitivity

After manual calibration, the stats bar updates:
```
vel_threshold: 245.1  |  resting: 142.3px  |  max_flick: 198.7px
```

---

## Effect on Arduino

Calibration affects only the gesture detection thresholds in Service 2. The Arduino sketch does not need to be re-uploaded or reconfigured when calibration is re-run. Once a command fires, the same `LIGHT_ON`, `FAN_OFF` etc. strings are always sent regardless of calibration values.
