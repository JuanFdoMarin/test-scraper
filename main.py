import os
import sys
import csv
import json
import logging
import subprocess
from typing import List, Dict, Any, Optional


def _ensure_playwright():
    """Instala el browser de Playwright y fija PLAYWRIGHT_BROWSERS_PATH a un path estable."""
    browsers_path = os.path.join(os.path.expanduser("~"), ".playwright-browsers")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path
    os.makedirs(browsers_path, exist_ok=True)

    chromium_installed = any(
        "chromium" in d
        for d in os.listdir(browsers_path)
        if os.path.isdir(os.path.join(browsers_path, d))
    )

    if not chromium_installed:
        print("Primera ejecución: instalando Chromium (esto tarda ~1 minuto)...")
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=False,
        )
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

            # Sincronización de llamadas según el método disponible
            if hasattr(scraper, "fetch_rendered_html_and_images"):
                html, imgs = scraper.fetch_rendered_html_and_images()
            elif hasattr(scraper, "fetch_page"):
                html, imgs = scraper.fetch_page(), []
            elif hasattr(scraper, "fetch_data_and_extract_images"):
                html, imgs = scraper.fetch_data_and_extract_images()
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

        for idx, url in enumerate(urls, 1):
            logger.info(f"--- Inmueble [{idx}/{len(urls)}] ---")
            data = self.procesar_url(url.strip())
            if data:
                resultados.append(data)

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