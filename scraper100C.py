import sys
import os
import re
import json
import base64
import requests
from bs4 import BeautifulSoup
from normalizer import Normalizer


class CiencuadrasScraper:

    def __init__(self, url: str):
        self.url = url
        self.id_inmueble = self._extract_id(url)
        self.folder = f"ciencuadras_{self.id_inmueble}"
        self.images_folder = os.path.join(self.folder, "imagenes")
        os.makedirs(self.images_folder, exist_ok=True)

    def _extract_id(self, url: str) -> str:
        match = re.search(r"-(\d+)\?", url) or re.search(
            r"-(\d+)$", url.rstrip("/")
        )
        return match.group(1) if match else "desconocido"

    def _generate_cdn_url(self, s3_url: str) -> str:
        try:
            path = s3_url.split(".com/")[1]
            config = {
                "bucket": "www-img-cc",
                "key": path,
                "edits": {
                    "resize": {"width": 1024, "height": 1024, "fit": "cover"}
                },
            }
            json_str = json.dumps(config, separators=(",", ":"))
            b64_encoded = base64.b64encode(json_str.encode()).decode()
            return f"https://images.ciencuadras.com/{b64_encoded}"
        except Exception:
            return s3_url

    def fetch_page(self) -> str | None:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Referer": "https://www.google.com/",
        }
        try:
            res = requests.get(self.url, headers=headers, timeout=15)
            if res.status_code == 200:
                html_content = res.text
                html_path = os.path.join(self.folder, "pagina_renderizada.html")
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html_content)
                return html_content
            print(f"  [Error HTTP {res.status_code}] Al obtener {self.url}")
            return None
        except Exception as e:
            print(f"  [Error de conexión] {e}")
            return None

    def parse_data(self, html: str, dom_images: list = None) -> dict:
        soup = BeautifulSoup(html, "lxml") if html else BeautifulSoup("", "lxml")
        state_script = soup.find("script", id="detail-state")
        if not state_script:
            raise ValueError("No se encontró el script de estado detail-state en el HTML.")

        try:
            json_text = state_script.string.strip().replace("&q;", '"')
            full_state = json.loads(json_text)
        except Exception as e:
            raise ValueError(f"Error parseando JSON interno: {e}")

        detail_key = next(
            (
                k
                for k in full_state.keys()
                if "detail-property" in k and self.id_inmueble in k
            ),
            None,
        )
        if not detail_key:
            raise ValueError(f"No se encontró la llave para el inmueble ID: {self.id_inmueble}")

        data = full_state[detail_key]
        gen = data.get("generalData", {})
        gal = data.get("galleryData", {}).get("flatPhotos", [])

        imagenes = []
        for idx, img in enumerate(gal, 1):
            s3_url = img.get("url")
            if s3_url:
                imagenes.append(
                    {
                        "orden": idx,
                        "url": self._generate_cdn_url(s3_url),
                        "espacio": img.get("physicalSpace"),
                        "archivo_local": f"imagenes/{idx:02d}.jpg",
                    }
                )

        # Extracción robusta del valor de administración probando múltiples llaves posibles
        admin_fee = (
            gen.get("adminValue")
            or gen.get("administrationFee")
            or gen.get("administrationValue")
            or gen.get("valorAdministracion")
            or gen.get("administration")
        )

        raw_data = {
            "fuente": {
                "portal": "Ciencuadras",
                "url": self.url,
                "id_inmueble": self.id_inmueble,
            },
            "inmueble": {
                "id_propiedad": self.id_inmueble,
                "titulo": (
                    soup.find("h1").text.strip()
                    if soup.find("h1")
                    else "Inmueble Ciencuadras"
                ),
                "operacion": gen.get("businessType"),
                "tipo": gen.get("propertyType"),
                "precio": (
                    gen.get("leaseFee")
                    if gen.get("leaseFee") and str(gen.get("leaseFee")) != "0"
                    else gen.get("price")
                ),
                "valor_administracion": admin_fee,
                "area_m2": gen.get("privateArea"),
                "habitaciones": gen.get("bedRoomNum"),
                "banos": gen.get("bathRoomNum"),
                "parqueaderos": gen.get("parkingNum"),
                "estrato": gen.get("stratum"),
                "ubicacion": {
                    "ciudad": gen.get("cityName"),
                    "departamento": gen.get("departmentName"),
                    "barrio": gen.get("neighborhoodName")
                    or gen.get("neighborhoodUser"),
                    "direccion": gen.get("address"),
                },
                "descripcion": gen.get("description"),
                "imagenes": imagenes,
            },
        }
        return Normalizer.normalize_record(raw_data)

    def download_images(self, data: dict):
        images = data.get("inmueble", {}).get("imagenes", [])
        if not images:
            print("  No se encontraron imágenes para descargar.")
            return

        print(f"  Descargando {len(images)} imágenes...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": self.url,
        }

        for img in images:
            url_target = img.get("url")
            if not url_target:
                continue
            local_path = os.path.join(self.folder, img["archivo_local"])
            try:
                res = requests.get(url_target, headers=headers, timeout=10)
                if res.status_code == 200:
                    with open(local_path, "wb") as f:
                        f.write(res.content)
                    print(f"    [OK] {img['archivo_local']}")
                else:
                    print(f"    [HTTP {res.status_code}] {url_target}")
            except Exception as e:
                print(f"    [Error] {e}")

    def run(self):
        print("==================================================")
        print(f" SCRAPER CIENCUADRAS | ID: {self.id_inmueble}")
        print("==================================================")

        html = self.fetch_page()
        if not html:
            return

        data = self.parse_data(html)
        inm = data["inmueble"]
        print(f"  Título: {inm['titulo']}")
        print(f"  Precio: {inm['precio']['monto']} {inm['precio']['moneda']}")
        
        if inm.get('valor_administracion'):
            print(f"  Administración: {inm['valor_administracion']}")
        else:
            print("  [Aviso] No se detectó valor de administración en las llaves evaluadas.")

        print(f"  Imágenes encontradas: {inm['total_imagenes']}")

        self.download_images(data)

        json_path = os.path.join(self.folder, "datos.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        print(f"  Proceso completado. Datos guardados en: {json_path}\n")


if __name__ == "__main__":
    if key_len := len(sys.argv) > 1:
        target_url = sys.argv[1]
    else:
        target_url = "https://www.ciencuadras.com/inmueble/apartamento-en-arriendo-en-chico-norte-et-iii-bogota-3886337?q=bogota"

    scraper = CiencuadrasScraper(target_url)
    scraper.run()