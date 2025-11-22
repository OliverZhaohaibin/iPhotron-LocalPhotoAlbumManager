# iPhotos Crop Black Border Prevention Mechanism Analysis

## Overview

This document provides a detailed analysis of **how iPhotos ensures that the crop frame never contains black borders** during perspective transformations and rotations. The mechanism involves coordinated work across three coordinate systems, geometric validation algorithms, and interaction strategies.

## Core Problem

When a user applies **perspective transformation** or **rotation** to an image, the original rectangular image becomes a **convex quadrilateral** in projected space. If the crop box extends beyond this valid region, the final rendered image will contain black edges, which is unacceptable.

## Three-Coordinate-System Architecture

The key to preventing black borders lies in understanding and correctly using three coordinate systems:

### A. Texture Space (Original Image Pixels)
- **Definition**: The raw pixel space of the source image file
- **Range**: `[0, 0]` to `[W_src, H_src]` (in pixels)
- **Purpose**: **Input source** for perspective transformation
- **Example**: A 1920×1080 image has texture coordinates from `(0, 0)` to `(1920, 1080)`

### B. Projected/Distorted Space — **Core Calculation Space**
- **Definition**: The 2D space **after applying the perspective transformation matrix**
- **Shape**: The original rectangular boundary becomes a **convex quadrilateral** `Q_valid`
- **Crop Box**: Always remains an **axis-aligned bounding box (AABB)** `R_crop`
- **Coordinate Range**: Normalized to `[0, 1]` interval
- **Key Property**: **All black-border prevention validation must be performed in this space**

### C. Viewport/Screen Space
- **Definition**: The final pixel coordinates rendered on the screen widget
- **Purpose**: **Only used** for handling user interaction (mouse clicks, drags)
- **Transformation Requirement**: Must be **inverse-transformed** back to projected space for logic calculations

## Core Validation Algorithms

### 1. Point-in-Convex-Polygon Test (`point_in_convex_polygon`)

**File Location**: `src/iPhoto/gui/ui/widgets/perspective_math.py`

**Algorithm Principle**: Uses cross products to determine if a point has consistent orientation relative to all polygon edges

```python
def point_in_convex_polygon(point: tuple[float, float], polygon: Sequence[tuple[float, float]]) -> bool:
    """Return True if point lies inside the convex polygon."""
    if len(polygon) < 3:
        return False
    last_sign = 0.0
    for i in range(len(polygon)):
        a = polygon[i]
        b = polygon[(i + 1) % len(polygon)]
        orient = _point_orientation(a, b, point)
        if abs(orient) <= 1e-6:
            continue
        sign = 1.0 if orient > 0.0 else -1.0
        if last_sign == 0.0:
            last_sign = sign
        elif sign != last_sign:
            return False  # Inconsistent orientation, point is outside
    return True
```

**How It Works**:
1. Iterate through each edge of the polygon
2. Calculate the point's orientation relative to the edge (via cross product)
3. If all orientations are consistent, the point is inside; otherwise, it's outside

### 2. Rectangle-Inside-Quadrilateral Test (`rect_inside_quad`)

**File Location**: `src/iPhoto/gui/ui/widgets/perspective_math.py`

```python
def rect_inside_quad(rect: NormalisedRect, quad: Sequence[tuple[float, float]]) -> bool:
    """Return True when rect is fully contained inside quad."""
    corners = [
        (rect.left, rect.top),
        (rect.right, rect.top),
        (rect.right, rect.bottom),
        (rect.left, rect.bottom),
    ]
    return all(point_in_convex_polygon(corner, quad) for corner in corners)
```

**How It Works**:
- Checks if **all four corner points** of the rectangle are inside the quadrilateral
- **Only when all four corners satisfy the condition** is the rectangle considered fully contained
- This is the **core validation function** for black border prevention

### 3. Minimum Zoom-to-Fit Calculation (`calculate_min_zoom_to_fit`)

**File Location**: `src/iPhoto/gui/ui/widgets/perspective_math.py`

```python
def calculate_min_zoom_to_fit(rect: NormalisedRect, quad: Sequence[tuple[float, float]]) -> float:
    """Return the minimum uniform zoom needed so rect fits inside quad."""
    cx, cy = rect.center
    corners = [
        (rect.left, rect.top),
        (rect.right, rect.top),
        (rect.right, rect.bottom),
        (rect.left, rect.bottom),
    ]
    max_scale = 1.0
    for corner in corners:
        direction = (corner[0] - cx, corner[1] - cy)
        if abs(direction[0]) <= 1e-9 and abs(direction[1]) <= 1e-9:
            continue
        hit = _ray_polygon_hit((cx, cy), direction, quad)
        if hit is None or hit <= 1e-6:
            continue
        if hit >= 1.0:
            continue
        scale = 1.0 / max(hit, 1e-6)
        if scale > max_scale:
            max_scale = scale
    return max_scale
```

**How It Works**:
1. Cast rays from the rectangle's center towards each corner
2. Calculate where each ray intersects the quadrilateral boundary
3. If the intersection occurs before the corner, scaling is needed
4. Return the maximum scale factor required

## Black Border Prevention Strategies

### Strategy 1: Real-Time Validation and Rollback (`ensure_valid_or_revert`)

**File Location**: `src/iPhoto/gui/ui/widgets/gl_crop/model.py`

```python
def ensure_valid_or_revert(
    self,
    snapshot: tuple[float, float, float, float],
    *,
    allow_shrink: bool,
) -> bool:
    """Keep the crop within the perspective quad or restore snapshot."""
    if self.is_crop_inside_quad():
        return True  # Already inside, no action needed
    if allow_shrink and self.auto_scale_crop_to_quad():
        return True  # Auto-shrink to fit
    self.restore_snapshot(snapshot)
    return False  # Restore to safe state
```

**Use Cases**:
- When the user drags crop box edges
- When the user adjusts perspective parameters
- When the user rotates or flips the image

**Workflow**:
1. **Validate**: Check if the current crop box is fully inside the valid quadrilateral
2. **Auto-Correct**: If allowed, automatically shrink the crop box to fit
3. **Rollback**: If correction fails, restore to the pre-operation safe state

### Strategy 2: Edge Drag Constraints (`ResizeStrategy`)

**File Location**: `src/iPhoto/gui/ui/widgets/gl_crop/strategies/resize_strategy.py`

```python
def on_drag(self, delta_view: QPointF) -> None:
    """Handle resize drag movement, ensuring no boundary violations."""
    # 1. Create pre-operation snapshot
    snapshot = self._model.create_snapshot()
    
    # 2. Calculate new crop box position
    # (Apply drag delta, constrain within original image bounds)
    
    # 3. Validate new state, rollback if invalid
    if not self._model.ensure_valid_or_revert(snapshot, allow_shrink=False):
        return  # Operation rejected, maintain original state
    
    # 4. Notify UI to update
    self._on_crop_changed()
```

**Key Constraints**:
```python
# Ensure no overflow beyond original image bounds
new_left = max(new_left, img_bounds_world["left"])
new_right = min(new_right, img_bounds_world["right"])
new_top = min(new_top, img_bounds_world["top"])
new_bottom = max(new_bottom, img_bounds_world["bottom"])
```

### Strategy 3: Baseline Fit During Perspective Changes (`apply_baseline_perspective_fit`)

**File Location**: `src/iPhoto/gui/ui/widgets/gl_crop/model.py`

```python
def apply_baseline_perspective_fit(self) -> bool:
    """Fit the stored baseline crop into the current perspective quad."""
    if self._baseline_crop_state is None:
        return False
    
    # 1. Get baseline state
    base_cx, base_cy, base_width, base_height = self._baseline_crop_state
    
    # 2. Check if center is inside new quadrilateral
    center = (float(base_cx), float(base_cy))
    if not point_in_convex_polygon(center, quad):
        # Center is outside, use quadrilateral centroid as new center
        centroid = quad_centroid(quad)
        center = (max(0.0, min(1.0, float(centroid[0]))),
                  max(0.0, min(1.0, float(centroid[1]))))
    
    # 3. Calculate fit scale
    rect = NormalisedRect(
        center[0] - half_w,
        center[1] - half_h,
        center[0] + half_w,
        center[1] + half_h,
    )
    scale = calculate_min_zoom_to_fit(rect, quad)
    
    # 4. Apply scale
    new_width = max(self._crop_state.min_width, float(base_width) / scale)
    new_height = max(self._crop_state.min_height, float(base_height) / scale)
    
    # 5. Update state
    self._crop_state.width = min(1.0, new_width)
    self._crop_state.height = min(1.0, new_height)
    self._crop_state.cx = center[0]
    self._crop_state.cy = center[1]
    self._crop_state.clamp()
    
    return True
```

**How It Works**:
1. Save the crop box state before the user adjusts perspective (baseline)
2. When perspective parameters change, calculate the new valid quadrilateral
3. If the baseline crop box exceeds the new quadrilateral, auto-shrink and reposition
4. Ensures no black borders throughout the perspective adjustment process

## Interaction Flow Examples

### Scenario 1: User Drags Crop Box Right Edge

```
1. User presses mouse → Create snapshot = (cx, cy, w, h)
2. User drags → Calculate new right boundary position new_right
3. Constraint check:
   - new_right <= img_bounds["right"] ✓
   - rect_inside_quad(new_rect, perspective_quad) ?
     - ✓ Accept new state, update UI
     - ✗ restore_snapshot(snapshot), reject operation
4. User releases mouse → Operation complete
```

### Scenario 2: User Adjusts Perspective Vertical Parameter

```
1. User moves slider → vertical = 0.5
2. Recalculate projected quadrilateral:
   - matrix = build_perspective_matrix(vertical, horizontal, ...)
   - quad = compute_projected_quad(matrix)
3. Apply baseline fit:
   - Check if current crop box is inside new quadrilateral
   - If not, calculate scale = calculate_min_zoom_to_fit(...)
   - Shrink crop box: width /= scale, height /= scale
4. Validate and update UI
```

### Scenario 3: User Rotates Image 90 Degrees

```
1. User clicks rotate button → rotate_steps = 1
2. Update perspective matrix (including rotation)
3. Calculate new projected quadrilateral
4. Rotation changes valid region, auto-trigger baseline fit
5. Crop box is automatically adjusted to avoid black borders
6. UI smoothly transitions to new state
```

## Key Files Index

| File Path | Responsibility | Key Functions |
|---------|------|---------|
| `src/iPhoto/gui/ui/widgets/perspective_math.py` | Core geometric algorithms | `point_in_convex_polygon`<br>`rect_inside_quad`<br>`calculate_min_zoom_to_fit`<br>`compute_projected_quad` |
| `src/iPhoto/gui/ui/widgets/gl_crop/model.py` | Crop session model | `is_crop_inside_quad`<br>`ensure_valid_or_revert`<br>`apply_baseline_perspective_fit`<br>`auto_scale_crop_to_quad` |
| `src/iPhoto/gui/ui/widgets/gl_crop/utils.py` | Crop box state | `CropBoxState.clamp`<br>`CropBoxState.drag_edge_pixels` |
| `src/iPhoto/gui/ui/widgets/gl_crop/strategies/resize_strategy.py` | Edge drag strategy | `ResizeStrategy.on_drag` |
| `src/iPhoto/gui/ui/widgets/gl_crop/strategies/pan_strategy.py` | Pan strategy | `PanStrategy.on_drag` |

## Test Coverage

The project includes comprehensive unit tests to ensure the black border prevention mechanism works correctly:

| Test File | Test Content |
|---------|---------|
| `tests/test_gl_crop_model.py` | Crop model core functionality tests |
| `tests/test_crop_box_state.py` | Crop box state management tests |
| `tests/test_gl_image_viewer_crop_logic.py` | Crop logic integration tests |
| `tests/test_gl_crop_hit_tester.py` | Click detection tests |

Key Test Cases:
```python
def test_is_crop_inside_quad_initially(model):
    """Test that crop starts inside the unit quad."""
    model.update_perspective(0.0, 0.0, 0.0, 0, False, 1.0)
    assert model.is_crop_inside_quad()

def test_ensure_valid_or_revert_reverts_invalid_crop(model):
    """Test that invalid crop box is automatically rolled back."""
    model.update_perspective(0.0, 0.0, 0.0, 0, False, 1.0)
    snapshot = model.create_snapshot()
    
    # Create invalid state
    crop_state = model.get_crop_state()
    crop_state.cx = -0.5  # Move outside boundary
    crop_state.cy = -0.5
    
    # Should revert to snapshot
    result = model.ensure_valid_or_revert(snapshot, allow_shrink=False)
    assert not result  # Operation failed
    # State has been restored to safe values
```

## Design Advantages

### 1. Coordinate System Separation
- Clear separation of concerns: screen interaction vs logic calculation vs GPU rendering
- Avoids bugs from mixing coordinates
- Easy to maintain and extend

### 2. Snapshot and Rollback Mechanism
- All potentially failing operations first create a snapshot
- Immediate rollback to safe state on validation failure
- Smooth user experience with no unexpected behavior

### 3. Layered Validation
- Boundary constraints (original image range)
- Geometric validation (projected quadrilateral containment)
- Auto-correction (zoom-to-fit scaling)
- Rollback protection (restore snapshot)

### 4. Performance Optimization
- Geometric calculations use normalized coordinates (avoids floating-point precision issues)
- Fast cross product algorithm (O(n) complexity)
- Avoids unnecessary matrix operations

## Summary

iPhotos' black border prevention mechanism ensures crop quality through the following core elements:

1. **Three-coordinate-system architecture**: Clear distinction between texture space, projected space, and viewport space
2. **Geometric validation algorithms**: `rect_inside_quad` ensures crop box is fully within valid region
3. **Interaction strategies**: Real-time validation and rollback during drag and perspective adjustments
4. **Auto-fit**: Automatically scales crop box to avoid black borders during perspective transformations
5. **Layered protection**: Multi-layer validation mechanisms ensure no black borders under any circumstances

This mechanism, built on the core algorithms in `perspective_math.py` and state management in `gl_crop/model.py`, implements robust and smooth user interaction through the strategy pattern.

## References

- `README.md` - Project overview and coordinate system definitions
- `AGENT.md` - OpenGL development specifications and detailed coordinate system description
- `demo/crop_final.py` - Crop functionality demo code
- `demo/perspective.py` - Perspective transformation demo code
