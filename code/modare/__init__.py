"""Prototipo de optimización de rutas de reparto de Moda re-."""
import os as _os

if _os.name == "nt":
    # Anaconda trae un msvcp140.dll antiguo (14.27) junto a python.exe que se carga antes que el del sistema;
    # OR-Tools 9.15 necesita el nuevo y, con el viejo, el solver CP-SAT/routing hace *segfault* sin mensaje
    # (la web "crasheaba" al pulsar «calcular rutas»). Precargar el del sistema lo evita.
    # Debe ejecutarse antes de importar ortools, por eso va aquí.
    import ctypes as _ctypes

    _sys32 = _os.path.join(_os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    for _dll in ("vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll"):
        try:
            _ctypes.WinDLL(_os.path.join(_sys32, _dll))
        except OSError:
            pass
