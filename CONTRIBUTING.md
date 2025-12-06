# Contributing to iPhoto

Thank you for your interest in contributing to iPhoto! We are building a folder-native, non-destructive photo manager that respects your data and filesystem.

## 1. Introduction

### Welcome & Purpose
iPhoto aims to bring the polished experience of macOS *Photos* to Windows and Linux, adhering to a strict "Folder = Album" philosophy. We prioritize data integrity, performance, and a seamless user experience without locking you into a proprietary database.

### Code of Conduct
Please note that this project is released with a [Code of Conduct](CODE_OF_CONDUCT.md). By participating in this project you agree to abide by its terms.

## 2. Development Setup

### Prerequisites
*   **Python**: Version 3.10 or higher.
*   **External Tools**: You must have the following tools installed and available in your system `PATH`:
    *   `ExifTool`: For reading/writing metadata.
    *   `FFmpeg` (and `ffprobe`): For video processing and thumbnail generation.

### Installation

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/OliverZhaohaibin/iPhotos.git
    cd iPhotos
    ```

2.  **Install dependencies**:
    Install the package in editable mode along with development dependencies:
    ```bash
    pip install -e .[dev]
    ```
    This command installs `pytest`, `ruff`, `black`, `mypy`, and other necessary tools.

## 3. Core Philosophy & Data Integrity

Our design philosophy is strict to ensure user trust and data safety:

*   **Folder-Native Principle**: "Folder = Album". We do not import photos into a database. The filesystem is the source of truth.
*   **Immutability of Originals**: We **never** modify source files (HEIC, JPG, MOV, etc.) directly. All edits must be non-destructive.
*   **Manifest Files**: All user decisions (e.g., setting a cover, starring, reordering, cropping) are stored in sidecar files like `.iphoto.album.json` or `.ipo` XML files.
*   **Disposable Cache**: The system must be robust enough to rebuild `index.jsonl`, `links.json`, and `thumbnails` folders at any time. Do not treat cache as persistent storage.

## 4. Project Architecture

The project follows a layered architecture to separate core logic from the GUI.

### Layered Architecture
*   **Core Backend** (`src/iPhoto/`): Pure Python logic. It has **no** dependencies on PySide6 or any GUI libraries. It handles data models, file I/O, and business logic.
*   **GUI Layer** (`src/iPhoto/gui/`): The frontend implementation using PySide6 (Qt6).
*   **Facade Pattern**: `app.py` acts as the backend facade, while `gui/facade.py` bridges the backend to the frontend using Qt signals/slots.

### Module Responsibilities
*   `models/`: Data classes (dataclasses) and manifest I/O.
*   `io/`: Filesystem scanning, metadata reading, and sidecar writing.
*   `core/`: Algorithms for pairing Live Photos, sorting, and filtering.
*   `cache/`: Management of index files (`index.jsonl`) and concurrency locks.
*   `utils/`: General utilities and wrappers for `ExifTool` and `FFmpeg`.

## 5. Coding Standards

### Style Guide
*   **Linting & Formatting**: We use `ruff` for linting and `black` for formatting.
*   **Line Length**: Limit lines to **100 characters**.
*   **Compliance**: Ensure your code passes `ruff check .` and `black --check .`.

### Typing
*   **Strict Type Hints**: All functions and methods must have type annotations.
    *   Use `Optional[str]`, `list[Path]`, etc.
    *   Run `mypy .` to verify type safety.

### Error Handling
*   **Custom Exceptions**: Use the exceptions defined in `errors.py`. Do not raise bare `Exception` or `ValueError` unless absolutely necessary for internal logic.

### File I/O Safety
*   **Atomic Writes**: Always write to a temporary file (e.g., `.tmp`) and then rename it to the target filename to prevent data corruption during crashes.
*   **Locking**: Before writing to manifests or index files, check `.lexiphoto/locks/` (or equivalent) to avoid race conditions.
*   **Cross-Platform**: Use `pathlib.Path` for all file path manipulations to ensure compatibility with Windows, macOS, and Linux.

## 6. Performance & Optimization Guidelines

Performance is critical for handling large photo libraries.

### Optimization Hierarchy
1.  **NumPy Vectorization** (Highest Priority): Use NumPy array operations for full-image manipulations or batch data processing. This utilizes SIMD and is much faster than loops.
2.  **Numba JIT**: Use `@jit(nopython=True)` for pixel-level loops or complex logic that cannot be vectorized.
3.  **Pure Python/Qt** (Last Resort): Use standard Python loops or Qt API calls only when the above are not applicable.

### Memory Efficiency
*   **In-Place Operations**: Use the `out=` argument in NumPy functions (e.g., `np.clip(..., out=arr)`) to avoid creating unnecessary copies of large image arrays.

### Benchmarks
*   Always measure performance before and after optimization to ensure your changes actually provide a benefit.

## 7. OpenGL & Graphics Guidelines

The detail view and map components use OpenGL for high-performance rendering.

### Coordinate Systems
We define four distinct coordinate spaces. **Do not mix them up.**

1.  **A. Texture Space** (0-1): The persistent storage space. Used in `.ipo` sidecars. Unaffected by rotation.
2.  **B. Logical Space** (0-1, with Aspect Ratio): The space for user interaction (Python UI layer). Handles rotation and flips.
3.  **C. Projected Space**: The space **after** perspective transform but **before** rotation. **Crucial** for black-border detection.
4.  **D. Viewport Space**: Screen coordinates (pixels). Used only for handling mouse inputs.

### Crop Logic
*   **Projected Space**: All crop validation (ensuring the crop box is inside the image) must happen in **Projected Space**.
*   **Shader Pipeline**: The Fragment Shader handles geometric transformations in this order: Perspective -> Crop Test -> Rotation -> Texture Sampling.

### GL Standards
*   Use **OpenGL 3.3 Core Profile**.
*   Use `QSurfaceFormat` to request the correct version.

## 8. Testing Strategy

### Running Tests
Run the test suite using `pytest`:
```bash
pytest
```

### Robustness
*   Tests must simulate missing or corrupt files to ensure the application handles them gracefully without crashing.
*   **Rebuildability**: Verify that deleting `index.jsonl` or `links.json` results in them being correctly rebuilt by the system.

## 9. Submitting Issues

We use GitHub issues to track bugs and features.

### Bug Reports
When reporting a bug, please include:
1.  **Summary**: A concise description of the issue.
2.  **Steps to Reproduce**: Detailed steps to help us see the problem.
    *   Example: "Open album -> Right click photo -> Select 'Crop'..."
3.  **Expected vs. Actual Behavior**: What you thought would happen vs. what actually happened.
4.  **Environment**: OS version, Python version, and iPhoto version.

### Feature Requests
Please describe the feature you would like to see, why you need it, and how it should work.

## 10. Commit Message Guidelines

We follow a standard commit message format to ensure history is readable.

*   **Structure**:
    ```text
    <type>(<scope>): <subject>

    <body>
    ```
*   **Subject Line**:
    *   Use the imperative mood ("Add feature" not "Added feature").
    *   Limit to 50 characters.
    *   No period at the end.
*   **Body**:
    *   Wrap lines at 72 characters.
    *   Explain *what* and *why*, not *how*.

## 11. Code Review Guidelines

All submissions will be reviewed by maintainers. We look for:

*   **Architectural Consistency**: Adherence to the layered architecture (Core vs. GUI) and Facade pattern.
*   **Data Safety**: Strict compliance with non-destructive editing and file locking rules.
*   **Test Coverage**: New features must include unit tests; bug fixes must include regression tests.
*   **Readability**: Clean, typed, and well-documented code following our style guide.

## 12. Contribution Areas

We welcome contributions across the entire stack:

*   **Core Backend**: Filesystem logic, pairing algorithms, and performance optimization (NumPy/Numba).
*   **GUI (PySide6)**: New widgets, view controllers, and interaction improvements.
*   **OpenGL/Maps**: Shader development, map rendering, and high-performance image viewers.
*   **Documentation & Tooling**: Improving guides, adding docstrings, and enhancing CI/CD scripts.

## 13. Pull Request Process

### Branching Strategy
Please use the following naming convention for your branches:
*   `feat/description`: New features.
*   `fix/issue-id`: Bug fixes.
*   `docs/update-readme`: Documentation updates.
*   `refactor/cleanup`: Code refactoring.

### PR Checklist
Before submitting a Pull Request, please ensure:
- [ ] You have run `ruff check .` and `black .` to format your code.
- [ ] You have run `mypy .` to check for type errors.
- [ ] You have added unit tests for your changes.
- [ ] You have verified that `pytest` passes locally.
- [ ] (If applicable) You have verified OpenGL coordinate logic matches the spec.
- [ ] (If applicable) You have benchmarked performance critical changes.
