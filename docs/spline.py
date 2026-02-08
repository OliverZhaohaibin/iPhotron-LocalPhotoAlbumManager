import numpy as np

class MonotoneCubicSpline:
    """
    Monotone Cubic Hermite Interpolation (PCHIP equivalent).
    Ensures the interpolated curve is monotonic (no overshoots) and C1 continuous.
    """
    def __init__(self, x, y):
        """
        Args:
            x (array-like): X coordinates of control points. Must be sorted.
            y (array-like): Y coordinates of control points.
        """
        self.x = np.array(x, dtype=np.float64)
        self.y = np.array(y, dtype=np.float64)
        self.n = len(self.x)

        if self.n < 2:
            raise ValueError("At least 2 points are required.")

        # 1. Compute Secant Slopes (d)
        # d[k] = (y[k+1] - y[k]) / (x[k+1] - x[k])
        dx = self.x[1:] - self.x[:-1]
        dy = self.y[1:] - self.y[:-1]

        # Avoid division by zero
        # If dx is very small, we might have issues, but UI prevents overlapping points.
        self.d = np.zeros_like(dx)
        valid = dx > 1e-10
        self.d[valid] = dy[valid] / dx[valid]

        # 2. Compute Tangents (m)
        self.m = np.zeros(self.n, dtype=np.float64)

        # Interior points
        # Weighted harmonic mean for PCHIP
        # m[k] = (w1 + w2) / (w1/d[k-1] + w2/d[k])
        # where w1 = 2*h[k] + h[k-1], w2 = h[k] + 2*h[k-1]
        if self.n > 2:
            h = dx
            d = self.d

            # Vectorized calculation for interior points 1..n-2
            # indices of interest for m[k] are k=1 to n-2
            # corresponding h are h[k-1] and h[k]
            # corresponding d are d[k-1] and d[k]

            hk = h[1:]      # h[k]
            hk_1 = h[:-1]   # h[k-1]
            dk = d[1:]      # d[k]
            dk_1 = d[:-1]   # d[k-1]

            w1 = 2 * hk + hk_1
            w2 = hk + 2 * hk_1

            # Check for sign changes (if slopes have different signs, tangent is 0)
            # or if either slope is 0
            # PCHIP condition: if d[k-1]*d[k] <= 0 then m[k] = 0

            mask = (dk_1 * dk) > 0

            # Only compute where signs match
            # harmonic mean formula
            m_interior = np.zeros_like(dk)

            denom = (w1[mask] / dk_1[mask]) + (w2[mask] / dk[mask])
            m_interior[mask] = (w1[mask] + w2[mask]) / denom

            self.m[1:-1] = m_interior

        # Endpoints (One-sided or simple projection)
        # PCHIP typically uses a specific shape preserving formula for endpoints
        # "Non-centered, shape-preserving three-point formula"

        # Start point (k=0)
        self.m[0] = self._edge_tangent(0, 1)

        # End point (k=n-1)
        self.m[-1] = self._edge_tangent(self.n-1, self.n-2)

    def _edge_tangent(self, k, k_next):
        """
        Compute the tangent (first derivative) at an endpoint for PCHIP interpolation.
        This method implements the endpoint formula from the Piecewise Cubic Hermite Interpolating Polynomial (PCHIP)
        algorithm, following the SciPy implementation. The endpoint tangent is computed using a three-point formula:
            m0 = ((2*h0 + h1)*d0 - h0*d1) / (h0 + h1)
        where:
            - h0 = x[1] - x[0] (distance from endpoint to its neighbor)
            - h1 = x[2] - x[1] (distance between the next two points)
            - d0 = (y[1] - y[0]) / h0 (secant slope at the endpoint)
            - d1 = (y[2] - y[1]) / h1 (secant slope of the next interval)
        Monotonicity constraints are applied:
            - If the computed tangent m0 has a different sign than d0, it is set to zero.
            - If d0 and d1 have different signs and |m0| > 3*|d0|, then m0 is limited to 3*d0.
        Args:
            k (int): Index of the endpoint (0 for start, n-1 for end).
            k_next (int): Index of the neighbor to the endpoint (1 for start, n-2 for end).
        Returns:
            float: The computed tangent (first derivative) at the endpoint, constrained to preserve monotonicity.
        """


        # If n=2, linear interpolation
        if self.n == 2:
            d0 = self.d[0] if k == 0 else self.d[-1]
            return d0

        # Use 3-point formula if n > 2
        # But for simplicity and robustness in this specific UI context (0,0 to 1,1 box),
        # standard PCHIP endpoint:
        # ((2*h0 + h1)*d0 - h0*d1) / (h0 + h1)
        # constrained to be monotonic.

        # Let's use a simpler heuristic for endpoints that guarantees monotonicity:
        # equal to the secant slope (linear start/end)?
        # Scipy PCHIP algorithm:
        # d = slope of secant
        # if sign(d) != sign(d_next), m=0
        # else... formula.

        # Re-implementing full scipy logic for endpoints:
        # Let h0 = x[1]-x[0], h1 = x[2]-x[1]
        # d0 = (y[1]-y[0])/h0, d1 = (y[2]-y[1])/h1
        # m0 = ((2h0+h1)d0 - h0d1)/(h0+h1)
        # if sign(m0) != sign(d0): m0=0
        # elif (sign(d0)!=sign(d1)) and (abs(m0) > abs(3*d0)): m0 = 3*d0

        idx_0, idx_1, idx_2 = (0, 1, 2) if k == 0 else (self.n-1, self.n-2, self.n-3)

        # Actually distances are always positive if sorted.
        h0 = abs(self.x[idx_1] - self.x[idx_0])
        h1 = abs(self.x[idx_2] - self.x[idx_1])

        # Ensure correct direction for d0 and d1 regardless of endpoint
        if idx_1 > idx_0:
            d0 = (self.y[idx_1] - self.y[idx_0]) / (self.x[idx_1] - self.x[idx_0])
        else:
            d0 = (self.y[idx_0] - self.y[idx_1]) / (self.x[idx_0] - self.x[idx_1])

        if idx_2 > idx_1:
            d1 = (self.y[idx_2] - self.y[idx_1]) / (self.x[idx_2] - self.x[idx_1])
        else:
            d1 = (self.y[idx_1] - self.y[idx_2]) / (self.x[idx_1] - self.x[idx_2])

        denom = h0 + h1
        # Safety check: avoid division by zero or near-zero denominator
        if np.isclose(denom, 0.0, atol=1e-12):
            m = 0.0
        else:
            m = ((2*h0 + h1)*d0 - h0*d1) / denom

        # Use a tolerance to check if d0 is effectively zero
        if np.isclose(d0, 0.0, atol=1e-12):
            m = 0
        elif np.sign(m) != np.sign(d0):
            m = 0
        elif (np.sign(d0) != np.sign(d1)) and (abs(m) > abs(3*d0)):
            m = 3 * d0

        return m

    def evaluate(self, x_eval):
        """
        Evaluate the spline at points x_eval.
        """
        x_eval = np.asarray(x_eval)
        scalar = x_eval.ndim == 0
        if scalar:
            x_eval = np.array([x_eval])

        # Locate intervals
        # indices i such that x[i] <= x_eval < x[i+1]
        # np.searchsorted finds indices where elements should be inserted to maintain order.
        # side='right': a[i-1] <= v < a[i]

        indices = np.searchsorted(self.x, x_eval, side='right') - 1
        indices = np.clip(indices, 0, self.n - 2)

        # Prepare variables
        # x_eval in [xi, xi+1]
        # t = (x - xi) / h

        xi = self.x[indices]
        xi1 = self.x[indices+1]
        yi = self.y[indices]
        yi1 = self.y[indices+1]
        mi = self.m[indices]
        mi1 = self.m[indices+1]

        h = xi1 - xi

        # Avoid division by zero (shouldn't happen if x is unique)
        # If h=0, just return yi
        mask = h > 1e-12

        result = np.zeros_like(x_eval, dtype=np.float64)

        # Calculate for valid intervals
        # Cubic Hermite Basis
        # H00(t) = 2t^3 - 3t^2 + 1
        # H10(t) = t^3 - 2t^2 + t
        # H01(t) = -2t^3 + 3t^2
        # H11(t) = t^3 - t^2
        # y(t) = y_i * H00 + h*m_i * H10 + y_{i+1} * H01 + h*m_{i+1} * H11

        if np.any(mask):
            dx = x_eval[mask] - xi[mask]
            t = dx / h[mask]
            t2 = t * t
            t3 = t2 * t

            h00 = 2*t3 - 3*t2 + 1
            h10 = t3 - 2*t2 + t
            h01 = -2*t3 + 3*t2
            h11 = t3 - t2

            _yi = yi[mask]
            _yi1 = yi1[mask]
            _mi = mi[mask]
            _mi1 = mi1[mask]
            _h = h[mask]

            result[mask] = (_yi * h00) + (_h * _mi * h10) + (_yi1 * h01) + (_h * _mi1 * h11)

        # Handle h=0 (degenerate intervals) or out of bounds (though clip handles it)
        # Just return yi for safety where mask is false
        if np.any(~mask):
            result[~mask] = yi[~mask]

        if scalar:
            return result[0]
        return result
