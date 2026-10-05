import sys
import json
import os
import re
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from normalizer import Normalizer


class FincaRaizScraper:

    def __init__(self, url: str):
        self.url = url
        self.id_inmueble = self._extract_id(url)
        self.folder = f"fincaraiz_{self.id_inmueble}"
        self.images_folder = os.path.join(self.folder, "imagenes")
        os.makedirs(self.images_folder, exist_ok=True)

    def _extract_id(self, url: str) -> str:
        match = re.search(r"(\d{5,10})", url)
        return match.group(1) if match else "desconocido"

    def fetch_rendered_html_and_images(self) -> tuple[str, list]:
        """Método de interfaz estandarizada para compatibilidad con main.py."""
        return self.fetch_data_and_extract_images()

    def fetch_page(self) -> str:
        """Alias para invocaciones simples que solo requieren el HTML."""
        html, _ = self.fetch_data_and_extract_images()
        return html

    def fetch_data_and_extract_images(self) -> tuple[str, list]:
        print(f"[1/4] Iniciando navegador (ID Inmueble: {self.id_inmueble})...")

        gallery_images = []

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-web-security",
                ],
            )

            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="es-CO",
            )

            page = context.new_page()

            try:
                page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                # --- ESTRATEGIA 1: EXTRAER LISTA OFICIAL DESDE NEXT.JS DATA ---
                print("  [+] Intentando extraer lista oficial de imágenes desde el estado de Next.js...")
                next_data_raw = page.evaluate("() => window.__NEXT_DATA__ ? JSON.stringify(window.__NEXT_DATA__) : null")
                
                if next_data_raw:
                    found_urls = re.findall(r'https?://[^\s"\'\\]+?\.(?:jpg|jpeg|png|webp)', next_data_raw, re.I)
                    
                    clean_found = []
                    for u in found_urls:
                        u_clean = u.replace("\\", "").split("?")[0]
                        if not re.search(r"(logo|avatar|icon|map|tile|static|assets|\.svg|banner|user|agent|popup|promo|ads)", u_clean, re.I):
                            if u_clean not in clean_found:
                                clean_found.append(u_clean)
                    
                    if clean_found:
                        print(f"  [OK] Se encontraron {len(clean_found)} URLs puras en el estado interno.")
                        gallery_images = clean_found

                # --- ESTRATEGIA 2: EXTRAER DESDE MODAL SI NO HAY NEXT_DATA ---
                if not gallery_images:
                    print("  [+] Abriendo galería modal para aislar las fotos del inmueble...")
                    page.evaluate("window.scrollTo(0, 0)")
                    page.wait_for_timeout(1000)

                    click_targets = [
                        "button:has-text('fotos')",
                        "button:has-text('Fotos')",
                        "button:has-text('Ver todas')",
                        "[data-testid*='gallery']",
                        "[class*='gallery']",
                    ]

                    for selector in click_targets:
                        try:
                            element = page.query_selector(selector)
                            if element and element.is_visible():
                                element.click(force=True)
                                page.wait_for_timeout(2000)
                                break
                        except Exception:
                            continue

                    modal_images = page.evaluate("""() => {
                        const modal = document.querySelector('[role="dialog"], [class*="modal"], [class*="lightbox"], [class*="gallery-viewer"]');
                        const scope = modal ? modal : document.querySelector('main') || document.body;
                        const imgs = Array.from(scope.querySelectorAll('img'));
                        return imgs.map(img => img.src || img.getAttribute('data-src')).filter(Boolean);
                    }""")

                    for u in modal_images:
                        clean_u = u.split("?")[0]
                        if not re.search(r"(logo|avatar|icon|map|tile|static|assets|\.svg|google|facebook|popup|promo|ads)", clean_u, re.I):
                            if clean_u not in gallery_images:
                                gallery_images.append(clean_u)

                html_content = page.content()
                browser.close()

                with open(os.path.join(self.folder, "pagina_renderizada.html"), "w", encoding="utf-8") as f:
                    f.write(html_content)

                return html_content, gallery_images

            except Exception as e:
                print(f"  [Error en navegación]: {e}")
                browser.close()
                return None, gallery_images

    def _extract_tipo_inmueble(
        self, soup: BeautifulSoup, schema_data: dict, next_data: dict
    ) -> str:
        try:
            props = next_data.get("props", {}).get("pageProps", {}).get("property", {})
            if props.get("type", {}).get("name"):
                return props["type"]["name"].strip().title()
        except Exception:
            pass

        meta_tipo = (
            soup.find("meta", {"name": re.compile(r"tipo|property_type", re.I)})
            or soup.find("meta", {"property": re.compile(r"og:type|tipo", re.I)})
        )
        if meta_tipo and meta_tipo.get("content"):
            content = meta_tipo["content"].strip().title()
            if content not in ["Article", "Website", "Object"]:
                return content

        match_url = re.search(r"^https?://[^/]+/([a-z\-]+)-en-(venta|arriendo)", self.url, re.I)
        if match_url:
            return match_url.group(1).replace("-", " ").capitalize()

        schema_type = str(schema_data.get("@type", "")).lower()
        if "apartment" in schema_type:
            return "Apartamento"
        elif "house" in schema_type:
            return "Casa"

        return "Inmueble"
    
    def _extract_operacion(self, soup: BeautifulSoup, next_data: dict) -> str:
        try:
            props = next_data.get("props", {}).get("pageProps", {}).get("property", {})
            if props.get("operationType", {}).get("name"):
                op_name = props["operationType"]["name"].lower()
                if "arriendo" in op_name or "alquiler" in op_name:
                    return "Arriendo"
                elif "venta" in op_name:
                    return "Venta"
        except Exception:
            pass

        if "arriendo" in self.url.lower() or "alquiler" in self.url.lower():
            return "Arriendo"
        return "Venta"

    def parse_data(self, html: str, raw_urls: list) -> dict:
        print("[2/4] Procesando metadatos y depurando catálogo de imágenes...")
        soup = BeautifulSoup(html, "lxml") if html else BeautifulSoup("", "lxml")

        next_data = {}
        next_script = soup.find("script", id="__NEXT_DATA__")
        if next_script and next_script.string:
            try:
                next_data = json.loads(next_script.string)
            except json.JSONDecodeError:
                pass

        schema_data = {}
        if html:
            schema_scripts = soup.find_all("script", type="application/ld+json")
            for script in schema_scripts:
                if script.string:
                    try:
                        data = json.loads(script.string)
                        if isinstance(data, list):
                            data = data[0]
                        if data.get("@type") in ["RealEstateListing", "Product", "SingleFamilyResidence", "Apartment"]:
                            schema_data = data
                            break
                    except json.JSONDecodeError:
                        continue

        titulo = schema_data.get("name")
        if not titulo and html:
            h1_el = soup.find("h1")
            titulo = h1_el.get_text(strip=True) if h1_el else "Inmueble en Finca Raíz"

        tipo = self._extract_tipo_inmueble(soup, schema_data, next_data)
        operacion = self._extract_operacion(soup, next_data)

        monto_precio = None
        price_info = schema_data.get("offers", {})
        if isinstance(price_info, dict) and price_info.get("price"):
            monto_precio = price_info.get("price")

        if not monto_precio and html:
            precio_el = soup.find(text=re.compile(r"\$\s*[\d\.\,]+"))
            if precio_el:
                digits = re.sub(r"[^\d]", "", precio_el)
                if digits:
                    monto_precio = digits

        address_info = schema_data.get("address", {})
        ciudad, departamento, barrio_sector = None, None, None

        if isinstance(address_info, dict):
            ciudad = address_info.get("addressLocality")
            departamento = address_info.get("addressRegion")

        url_match = re.search(r"en-venta-en-([a-z0-9-]+)-([a-z0-9-]+)/", self.url, re.I) or re.search(r"en-arriendo-en-([a-z0-9-]+)-([a-z0-9-]+)/", self.url, re.I)
        if url_match:
            barrio_sector = url_match.group(1).replace("-", " ").title()
            if not ciudad:
                ciudad = url_match.group(2).replace("-", " ").title()

        ubicacion = {
            "ciudad": ciudad,
            "departamento": departamento,
            "barrio_sector": barrio_sector,
        }

        descripcion = schema_data.get("description", "")
        if not descripcion and html:
            desc_el = (
                soup.find("section", id=re.compile(r"description", re.I))
                or soup.find("div", class_=re.compile(r"description|descripcion|read-more", re.I))
                or soup.find("div", attrs={"data-testid": re.compile(r"description", re.I)})
            )
            if desc_el:
                descripcion = desc_el.get_text(separator="\n", strip=True)

        caracteristicas = {
            "area_construida_m2": None,
            "habitaciones": None,
            "banos": None,
            "estrato": None,
            "parqueaderos": None,
            "piso": None,
            "ano_construccion": None,
            "cocina": None,
            "ascensores": None,
            "administracion": None
        }

        if next_data:
            try:
                page_props = next_data.get("props", {}).get("pageProps", {})
                prop_info = page_props.get("realEstate", {}) or page_props.get("property", {})
                
                if prop_info.get("stratum"):
                    caracteristicas["estrato"] = int(prop_info["stratum"])
                if prop_info.get("area"):
                    caracteristicas["area_construida_m2"] = float(prop_info["area"])
                if prop_info.get("rooms"):
                    caracteristicas["habitaciones"] = int(prop_info["rooms"])
                if prop_info.get("bathrooms"):
                    caracteristicas["banos"] = int(prop_info["bathrooms"])
                
                # Extracción de administración desde el JSON de Next.js si existe
                for k in ["administration", "admin", "administrationFee", "valorAdministracion", "administrationPrice"]:
                    if prop_info.get(k):
                        val = prop_info.get(k)
                        if isinstance(val, (int, float)):
                            caracteristicas["administracion"] = float(val)
                        elif isinstance(val, str):
                            digits = re.sub(r"[^\d]", "", val)
                            if digits:
                                caracteristicas["administracion"] = float(digits)
            except Exception:
                pass

        texto_pagina = soup.get_text(separator=" ", strip=True)

        if not caracteristicas["habitaciones"]:
            m_hab = re.search(
                r'(?:habitacion(?:es)?|alcoba[s]?|dormitorio[s]?|cuarto[s]?)\D{0,15}\b([1-8])\b|\b([1-8])\s*(?:habitacion(?:es)?|alcoba[s]?|dormitorio[s]?|cuarto[s])',
                texto_pagina,
                re.I
            )
            if m_hab:
                caracteristicas["habitaciones"] = int(m_hab.group(1) or m_hab.group(2))

        if not caracteristicas["banos"]:
            m_banos = re.search(
                r'(?:baño[s]?|bano[s]?)\D{0,15}\b([1-8])\b|\b([1-8])\s*(?:baño[s]?|bano[s])',
                texto_pagina,
                re.I
            )
            if m_banos:
                caracteristicas["banos"] = int(m_banos.group(1) or m_banos.group(2))

        if not caracteristicas["area_construida_m2"]:
            m_area = re.search(
                r'\b(\d{2,4}(?:[\.,]\d+)?)\s*(?:m2|m²|mts2|mt2|metros cuadrados)\b',
                texto_pagina,
                re.I
            )
            if m_area:
                val_area = float(m_area.group(1).replace(",", "."))
                if 10 <= val_area <= 2000:
                    caracteristicas["area_construida_m2"] = val_area

        if not caracteristicas["estrato"]:
            m_estrato = re.search(r'\bestrato\b[^\d]*([1-6])', texto_pagina, re.I)
            if m_estrato:
                caracteristicas["estrato"] = int(m_estrato.group(1))

        # Extracción de administración por respaldo usando Regex en el texto visible
        if not caracteristicas["administracion"]:
            m_admin = re.search(
                r'(?:administración|administracion|admón|admon)\D{0,25}\$\s*([\d\.,]+)',
                texto_pagina,
                re.I
            )
            if m_admin:
                digits = re.sub(r"[^\d]", "", m_admin.group(1))
                if digits:
                    caracteristicas["administracion"] = float(digits)

        imagenes = []
        seen_urls = set()

        idx = 1
        for u in raw_urls:
            clean_url = u.split("?")[0]
            if clean_url not in seen_urls:
                seen_urls.add(clean_url)
                imagenes.append(
                    {
                        "orden": idx,
                        "url": clean_url,
                        "archivo_local": f"imagenes/{idx:02d}.jpg",
                    }
                )
                idx += 1

        raw_data = {
            "fuente": {
                "portal": "FincaRaiz",
                "url": self.url,
                "id_inmueble": self.id_inmueble,
            },
            "inmueble": {
                "id_propiedad": self.id_inmueble,
                "titulo": titulo or "Inmueble FincaRaiz",
                "operacion": operacion,
                "tipo": tipo,
                "precio": monto_precio,
                "ubicacion": ubicacion,
                "descripcion": descripcion,
                "caracteristicas": caracteristicas,
                "imagenes": imagenes,
            },
        }

        return Normalizer.normalize_record(raw_data)

    def download_images(self, data: dict):
        images = data["inmueble"]["imagenes"]
        print(f"[3/4] Descargando {len(images)} imágenes filtradas...")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.fincaraiz.com.co/",
        }

        for img in images:
            url_target = img["url"]
            local_path = os.path.join(self.folder, img["archivo_local"])
            try:
                res = requests.get(url_target, headers=headers, timeout=15)
                if res.status_code == 200:
                    with open(local_path, "wb") as f:
                        f.write(res.content)
                    print(f"  [OK] Guardada: {img['archivo_local']}")
                else:
                    print(f"  [HTTP {res.status_code}] Falló: {url_target}")
            except Exception as e:
                print(f"  [Error descarga]: {e}")

    def run(self):
        print("=" * 60)
        print("EXTRACTOR FINCA RAÍZ")
        print("=" * 60)

        html, raw_urls = self.fetch_data_and_extract_images()
        data = self.parse_data(html, raw_urls)

        print("\n--- DATOS EXTRAÍDOS Y ESTRUCTURADOS ---")
        print(f"ID:             {data['inmueble']['id_propiedad']}")
        print(f"Título:         {data['inmueble']['titulo']}")
        print(f"Tipo:           {data['inmueble']['tipo']}")
        print(f"Operación:      {data['inmueble']['operacion']}")
        print(f"Precio:         {data['inmueble']['precio']['monto']} {data['inmueble']['precio']['moneda']}")
        print(f"Administración: {data['inmueble']['caracteristicas'].get('administracion')}")
        print(f"Ubicación:      {data['inmueble']['ubicacion']['ciudad']}, {data['inmueble']['ubicacion']['barrio_sector']}")
        print(f"Estrato:        {data['inmueble']['caracteristicas']['estrato']}")
        print(f"Total Imágenes: {data['inmueble']['total_imagenes']}")
        print("---------------------------------------\n")

        self.download_images(data)

        json_path = os.path.join(self.folder, "datos.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

        print(f"[4/4] Proceso finalizado exitosamente. Guardado en: {json_path}\n")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_url = sys.argv[1]
    else:
        target_url = "https://www.fincaraiz.com.co/apartamento-en-venta-en-modelia-occidental-bogota/194233386"

    scraper = FincaRaizScraper(target_url)
    scraper.run()