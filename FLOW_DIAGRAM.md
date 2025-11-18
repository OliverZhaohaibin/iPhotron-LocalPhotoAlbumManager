# Perspective Crop Box Interaction Flow Diagram

## Signal Flow

```
User Action: Press Perspective Slider
    ↓
BWSlider.mousePressEvent()
    ↓
BWSlider.sliderPressed.emit()
    ↓
_PerspectiveSliderRow.sliderPressed.emit()
    ↓
PerspectiveControls.interactionStarted.emit()
    ↓
DetailPageWidget: lambda connected signal
    ↓
CropInteractionController.set_perspective_interaction(True)
    ↓
Capture current crop state as baseline:
    - _interaction_base_size = (current_width, current_height)
    - _interaction_aspect_ratio = current_width / current_height
    - _perspective_interaction_active = True
```

## Adjustment Flow (During Drag)

```
User Action: Drag Perspective Slider
    ↓
EditSession.set_value("Perspective_Vertical", value)
    ↓
GLImageViewer._update_crop_perspective_state()
    ↓
CropInteractionController.update_perspective(vertical, horizontal)
    ↓
Check: _perspective_interaction_active?
    ↓
    YES (Interactive Mode)
    ↓
    _adjust_crop_interactive()
        ↓
        1. Compute perspective quad from new v/h values
        2. Calculate quad centroid: cx, cy
        3. Build candidate rect:
           - center = (cx, cy)
           - width = base_width
           - height = base_height
        4. Check fit: scale = calculate_min_zoom_to_fit(rect, quad)
        5. Apply scale:
           - final_width = base_width / scale
           - final_height = base_height / scale
        6. Update crop state:
           - crop.cx = cx
           - crop.cy = cy
           - crop.width = final_width
           - crop.height = final_height
        ↓
    Emit crop changed signal
    ↓
    Request UI update
```

## Release Flow

```
User Action: Release Perspective Slider
    ↓
BWSlider.mouseReleaseEvent()
    ↓
BWSlider.sliderReleased.emit()
    ↓
_PerspectiveSliderRow.sliderReleased.emit()
    ↓
PerspectiveControls.interactionEnded.emit()
    ↓
DetailPageWidget: lambda connected signal
    ↓
CropInteractionController.set_perspective_interaction(False)
    ↓
Clear interaction state:
    - _perspective_interaction_active = False
```

## State Diagram

```
┌─────────────────────────────────────────────────┐
│                                                 │
│  Non-Interactive Mode (Default)                │
│                                                 │
│  Behavior:                                      │
│  - Shrink crop if outside quad                 │
│  - Never expand crop                           │
│  - Passive constraint checking                 │
│                                                 │
└────────────┬────────────────────────────────────┘
             │
             │ User presses slider
             │ (interactionStarted)
             ↓
┌─────────────────────────────────────────────────┐
│                                                 │
│  Interactive Mode                               │
│                                                 │
│  State Captured:                                │
│  - Base size (width, height)                   │
│  - Aspect ratio                                │
│                                                 │
│  Behavior per perspective change:              │
│  1. Calculate quad centroid                    │
│  2. Try to place base-sized rect at centroid   │
│  3. Scale down if needed (scale > 1)           │
│  4. Keep base size if fits (scale ≈ 1)         │
│                                                 │
│  Result:                                        │
│  - Crop auto-expands when possible             │
│  - Crop shrinks when necessary                 │
│  - Always centered at optimal position         │
│                                                 │
└────────────┬────────────────────────────────────┘
             │
             │ User releases slider
             │ (interactionEnded)
             ↓
┌─────────────────────────────────────────────────┐
│                                                 │
│  Back to Non-Interactive Mode                   │
│                                                 │
└─────────────────────────────────────────────────┘
```

## Mathematical Flow

```
Given:
- base_width, base_height (captured at interaction start)
- perspective_quad (current distorted quad)

Step 1: Calculate Centroid
    cx = (quad[0].x + quad[1].x + quad[2].x + quad[3].x) / 4
    cy = (quad[0].y + quad[1].y + quad[2].y + quad[3].y) / 4

Step 2: Build Candidate Rectangle
    candidate = Rectangle(
        left = cx - base_width/2,
        top = cy - base_height/2,
        right = cx + base_width/2,
        bottom = cy + base_height/2
    )

Step 3: Check if Candidate Fits
    For each corner of candidate:
        Cast ray from center to corner
        Find intersection with quad edges
        Calculate scale needed = 1 / hit_distance
    scale = max(all corner scales)

Step 4: Apply Scale
    final_width = base_width / scale
    final_height = base_height / scale

Result:
    - If scale = 1.0: candidate fits perfectly, use base size
    - If scale > 1.0: candidate too large, shrink proportionally
    - Aspect ratio preserved: final_width/final_height = base_width/base_height
```

## Example Scenarios

### Scenario 1: User increases vertical perspective

```
Before:
    base_width = 0.8, base_height = 0.6
    quad = unit_quad (no distortion)
    
During drag to v=0.3:
    quad becomes trapezoid (wider at bottom)
    centroid ≈ (0.5, 0.52)  # slightly lower
    candidate rect at centroid with base size
    scale ≈ 1.0 (fits)
    → Crop stays at base size, moves to new centroid

During drag to v=0.8:
    quad becomes narrow trapezoid
    centroid ≈ (0.5, 0.55)
    candidate rect doesn't fit
    scale ≈ 1.4
    → Crop shrinks: width=0.57, height=0.43

On release:
    Exit interactive mode
    Crop stays at current size/position
```

### Scenario 2: User releases then presses again

```
After first adjustment:
    current crop: width=0.57, height=0.43
    
User presses slider again:
    NEW base captured: (0.57, 0.43)
    
During second adjustment:
    Uses NEW base size as maximum
    Can expand up to 0.57 x 0.43
    Won't expand beyond this new baseline
```

## Key Insights

1. **Baseline Recapture**: Each press-release cycle is independent
2. **Centroid Strategy**: Maximizes available space by optimal positioning
3. **Proportional Scaling**: Maintains aspect ratio at all times
4. **Passive vs Active**: Clear separation of constraint checking modes
5. **User Control**: User's initial size choice is respected during each interaction
