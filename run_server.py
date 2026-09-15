"""Lanzador de despliegue: un solo servicio, dos procesos.

Render (y hostings similares de un solo "web service") exponen un único
puerto público. Este script arranca la API internamente en 127.0.0.1
(no expuesta a internet) y el tablero de Streamlit en el puerto público
($PORT), que le habla a la API por localhost — así se comparte el mismo
disco (y por lo tanto el mismo archivo SQLite) sin depender de un segundo
servicio ni de credenciales cruzadas entre plataformas.

Si el pipeline aún no ha corrido, lo ejecuta una vez antes de levantar los
servidores, para no publicar un tablero vacío.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from src.config import CFG

PUERTO_API_INTERNO = CFG.api_port
PUERTO_PUBLICO = int(os.getenv("PORT", "8501"))


def _asegurar_datos() -> None:
    if not CFG.db_path.exists():
        print("[run_server] No hay base de datos: ejecutando el pipeline por primera vez...")
        from src.pipeline import ejecutar

        ejecutar(disparador="deploy")


def main() -> None:
    _asegurar_datos()

    env = os.environ.copy()
    env["API_URL"] = f"http://127.0.0.1:{PUERTO_API_INTERNO}"

    api_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app",
         "--host", "127.0.0.1", "--port", str(PUERTO_API_INTERNO)],
        env=env,
    )
    print(f"[run_server] API arrancando en 127.0.0.1:{PUERTO_API_INTERNO} (pid={api_proc.pid})")
    time.sleep(2)  # margen simple para que la API esté lista antes del primer request del tablero

    try:
        streamlit_proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "tablero/app.py",
             "--server.address", "0.0.0.0", "--server.port", str(PUERTO_PUBLICO),
             "--server.headless", "true"],
            env=env,
        )
        print(f"[run_server] Tablero público en 0.0.0.0:{PUERTO_PUBLICO} (pid={streamlit_proc.pid})")
        streamlit_proc.wait()
    finally:
        api_proc.terminate()
        api_proc.wait(timeout=10)


if __name__ == "__main__":
    main()
