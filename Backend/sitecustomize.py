"""Matplotlib compatibility for ReportLab Color objects used by the PDF gauge."""

try:
    import matplotlib.colors as _mcolors

    _orig_to_rgba = _mcolors.to_rgba
    _orig_to_rgba_array = _mcolors.to_rgba_array

    def _is_reportlab_color(value):
        cls = type(value)
        return (
            cls.__module__.startswith("reportlab.lib.colors")
            and all(hasattr(value, attr) for attr in ("red", "green", "blue"))
        )

    def _to_rgba(value, alpha=None):
        if _is_reportlab_color(value):
            rgba = (
                float(value.red),
                float(value.green),
                float(value.blue),
                float(getattr(value, "alpha", 1.0)),
            )
            if alpha is not None:
                rgba = (rgba[0], rgba[1], rgba[2], float(alpha))
            return rgba
        return _orig_to_rgba(value, alpha=alpha)

    def _to_rgba_array(value, alpha=None):
        if _is_reportlab_color(value):
            return _orig_to_rgba_array(_to_rgba(value, alpha=alpha))
        if isinstance(value, (list, tuple)):
            value = [
                _to_rgba(item, alpha=alpha) if _is_reportlab_color(item) else item
                for item in value
            ]
        return _orig_to_rgba_array(value, alpha=alpha)

    _mcolors.to_rgba = _to_rgba
    _mcolors.to_rgba_array = _to_rgba_array
except Exception:
    pass
