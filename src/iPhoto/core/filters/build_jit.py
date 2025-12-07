import os
import sys

# Force AOT build mode in jit_executor to ensure we get raw python functions
# instead of AOT-compiled extensions (which lack .py_func).
os.environ["IPHOTO_BUILD_AOT"] = "1"

from numba.pycc import CC

# Ensure we can import from src.
# Assuming build_jit.py is at src/iPhoto/core/filters/build_jit.py
# We want to add 'src' to sys.path, which is 3 levels up.
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
sys.path.insert(0, src_path)

try:
    from iPhoto.core.filters.jit_kernels import (
        _apply_adjustments_fast,
        _apply_color_adjustments_inplace,
    )
except ImportError as e:
    print(f"Error importing jit_kernels: {e}")
    sys.exit(1)

# Validate that the imported functions have the .py_func attribute (Numba JIT-compiled)
for func_name, func in [
    ("_apply_adjustments_fast", _apply_adjustments_fast),
    ("_apply_color_adjustments_inplace", _apply_color_adjustments_inplace),
]:
    if not hasattr(func, "py_func"):
        print(
            f"Error: The function '{func_name}' does not have a '.py_func' attribute. "
            "This usually means Numba is not installed or JIT is not properly initialized. "
            "Numba must be installed and available for AOT compilation."
        )
        sys.exit(1)
cc = CC("_jit_compiled")
cc.verbose = True

# _apply_adjustments_fast signature
# Arguments: buffer(u1[:]), width(i8), height(i8), bytes_per_line(i8),
#            ...13 float params..., apply_color(b1), apply_bw(b1), ...4 float params...
cc.export(
    "_apply_adjustments_fast",
    "void(u1[:], i8, i8, i8, f8, f8, f8, f8, f8, f8, f8, f8, f8, f8, f8, f8, f8, b1, b1, f8, f8, f8, f8)",
)(_apply_adjustments_fast.py_func)

# _apply_color_adjustments_inplace signature
# Arguments: buffer(u1[:]), width(i8), height(i8), bytes_per_line(i8), ...6 float params...
cc.export(
    "_apply_color_adjustments_inplace", "void(u1[:], i8, i8, i8, f8, f8, f8, f8, f8, f8)"
)(_apply_color_adjustments_inplace.py_func)


if __name__ == "__main__":
    print("Compiling AOT module _jit_compiled...")
    # Output to the same directory as this script
    output_dir = os.path.dirname(os.path.abspath(__file__))
    cc.output_dir = output_dir
    cc.compile()
    print(f"Compilation complete. Module saved to {output_dir}")
