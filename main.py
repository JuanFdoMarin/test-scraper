import os
import sys
import csv
import json
import logging
import subprocess
from typing import List, Dict, Any, Optional


def _find_chromium_exe(browsers_path: str) -> str | None:
    """Retorna la ruta al ejecutable de Chromium si existe, o None."""
    import platform
    exe_name = "chrome-headless-shell.exe" if platform.system() == "Windows" else "chrome-headless-shell"
    for item in os.listdir(browsers_path):
        if "chromium" in item:
            for root, _, files in os.walk(os.path.join(browsers_path, item)):
                if exe_name in files:
                    return os.path.join(root, exe_name)
    return None


def _playwright_driver_exe() -> str | None:
    """Ruta al driver de Playwright; funciona tanto en venv como en bundle PyInstaller."""
    try:
        import pathlib
        import playwright as _pw
        pkg_dir = pathlib.Path(_pw.__file__).parent
        driver = pkg_dir / "driver" / ("playwright.cmd" if sys.platform == "win32" else "playwright.sh")
        if driver.exists():
            return str(driver)
    except Exception:
        pass
    return None


def _default_ms_playwright_path() -> str:
    """Ruta donde playwright instala navegadores por defecto según el SO."""
    import platform
    system = platform.system()
    if system == "Windows":
        local_app = os.environ.get(
            "LOCALAPPDATA",
            os.path.join(os.path.expanduser("~"), "AppData", "Local"),
        )
        return os.path.join(local_app, "ms-playwright")
    if system == "Darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Caches", "ms-playwright")
    return os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright")


def _ensure_playwright():
    """Fija PLAYWRIGHT_BROWSERS_PATH al directorio que ya tiene Chromium.
    Si no se encuentra en ningún lado, intenta instalarlo."""
    candidates = [
        _default_ms_playwright_path(),
        os.path.join(os.path.expanduser("~"), ".playwright-browsers"),
    ]

    for path in candidates:
        if os.path.isdir(path) and _find_chromium_exe(path):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = path
            return

    # No se encontró — instalar en la ruta personalizada
    install_path = candidates[1]
    os.makedirs(install_path, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = install_path

    print("Primera ejecución: instalando Chromium (esto tarda ~1 minuto)...")
    driver = _playwright_driver_exe()
    cmd = [driver, "install", "chromium"] if driver else [sys.executable, "-m", "playwright", "install", "chromium"]

    result = subprocess.run(cmd, capture_output=False)
    if result.returncode == 0:
        print("Chromium instalado correctamente.\n")
    else:
        print("Advertencia: no se pudo instalar Chromium automáticamente.")
        print("Ejecutá manualmente: playwright install chromium\n")


_ensure_playwright()

from scraper100C import CiencuadrasScraper
from scraperFR import FincaRaizScraper
from scraperM2 import MetroCuadradoScraper
from scraper21 import Century21Scraper
from word_exporter import WordExporter
from playwright.sync_api import sync_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("pipeline.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("PipelineOrquestador")


class PipelineOrquestador:

    def __init__(self, output_dir: str = "resultados_consolidados"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self._pw = None
        self._browser = None

    def _get_browser(self):
        """Crea el browser de Playwright la primera vez que se necesita (lazy)."""
        if self._browser is None:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-web-security",
                ],
            )
        return self._browser

    def _close_browser(self):
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    def _resolver_scraper(self, url: str):
        url_lower = url.lower()
        if "ciencuadras.com" in url_lower:
            return CiencuadrasScraper(url)
        elif "fincaraiz.com.co" in url_lower:
            return FincaRaizScraper(url)
        elif "metrocuadrado.com" in url_lower:
            return MetroCuadradoScraper(url)
        elif "century21colombia.com/" in url_lower:
            return Century21Scraper(url)
        else:
            raise ValueError(f"Portal no soportado para la URL: {url}")

    def procesar_url(self, url: str) -> Optional[Dict[str, Any]]:
        logger.info(f"Procesando URL: {url}")
        try:
            scraper = self._resolver_scraper(url)

            if hasattr(scraper, "fetch_rendered_html_and_images"):
                html, imgs = scraper.fetch_rendered_html_and_images(browser=self._get_browser())
            elif hasattr(scraper, "fetch_page"):
                html, imgs = scraper.fetch_page(), []
            elif hasattr(scraper, "fetch_data_and_extract_images"):
                html, imgs = scraper.fetch_data_and_extract_images(browser=self._get_browser())
            else:
                raise AttributeError("El scraper no define un método de extracción válido.")

            data_normalizada = scraper.parse_data(html, imgs)
            scraper.download_images(data_normalizada)

            logger.info(
                f"✓ Éxito procesando ID: {data_normalizada['inmueble']['id_propiedad']} "
                f"({data_normalizada['fuente']['portal']})"
            )
            return data_normalizada

        except Exception as e:
            logger.exception(f"✗ Error al procesar {url}: {e}")
            return None

    def procesar_lote(self, urls: List[str]) -> List[Dict[str, Any]]:
        logger.info(f"Iniciando procesamiento en lote de {len(urls)} URLs...")
        resultados = []
        try:
            for idx, url in enumerate(urls, 1):
                logger.info(f"--- Inmueble [{idx}/{len(urls)}] ---")
                data = self.procesar_url(url.strip())
                if data:
                    resultados.append(data)
        finally:
            self._close_browser()
        return resultados

    def exportar_dataset(self, datos: List[Dict[str, Any]]):
        if not datos:
            logger.warning("No hay datos para exportar.")
            return

        # 1. Exportación JSON
        json_path = os.path.join(self.output_dir, "inmuebles_consolidado.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=4, ensure_ascii=False)
        logger.info(f"Dataset JSON guardado en: {json_path}")

        # 2. Exportación CSV
        filas_aplanadas = []
        for reg in datos:
            f = reg.get("fuente", {})
            i = reg.get("inmueble", {})
            p = i.get("precio", {})
            u = i.get("ubicacion", {})
            c = i.get("caracteristicas", {})

            filas_aplanadas.append(
                {
                    "portal": f.get("portal"),
                    "id_propiedad": i.get("id_propiedad"),
                    "titulo": i.get("titulo"),
                    "operacion": i.get("operacion"),
                    "tipo": i.get("tipo"),
                    "precio_monto": p.get("monto"),
                    "precio_moneda": p.get("moneda"),
                    "administracion": p.get("administracion"),
                    "ciudad": u.get("ciudad"),
                    "departamento": u.get("departamento"),
                    "barrio_sector": u.get("barrio_sector"),
                    "area_m2": c.get("area_construida_m2"),
                    "habitaciones": c.get("habitaciones"),
                    "banos": c.get("banos"),
                    "estrato": c.get("estrato"),
                    "parqueaderos": c.get("parqueaderos"),
                    "piso": c.get("piso"),
                    "total_imagenes": i.get("total_imagenes"),
                    "url": f.get("url"),
                }
            )

        csv_path = os.path.join(self.output_dir, "inmuebles_consolidado.csv")
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=filas_aplanadas[0].keys())
            writer.writeheader()
            writer.writerows(filas_aplanadas)
        logger.info(f"Dataset CSV guardado en: {csv_path}")

        # 3. Exportación Word (.docx)
        try:
            word_exp = WordExporter(
                os.path.join(self.output_dir, "Informe_Inmuebles.docx")
            )
            word_path = word_exp.generar_documento(datos)
            logger.info(f"Informe Word generado en: {word_path}")
        except Exception as e:
            logger.exception(f"Error al generar informe Word: {e}")


if __name__ == "__main__":
    lista_urls = [
        "https://www.ciencuadras.com/inmueble/apartamento-en-arriendo-en-chico-norte-et-iii-bogota-3886337?q=bogota"
    ]

    if os.path.exists("urls.txt"):
        with open("urls.txt", "r", encoding="utf-8") as f:
            lista_urls = [
                line.strip()
                for line in f
                if line.strip() and not line.startswith("#")
            ]

    orquestador = PipelineOrquestador()
    resultados = orquestador.procesar_lote(lista_urls)
    orquestador.exportar_dataset(resultados)