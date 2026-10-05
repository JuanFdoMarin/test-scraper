import sys
import json
import os
import re
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from normalizer import Normalizer


class Century21Scraper:

    def __init__(self, url: str):
        self.url = url
        self.id_inmueble = self._extract_id(url)
        self.folder = f"century21_{self.id_inmueble}"
        self.images_folder = os.path.join(self.folder, "imagenes")
        os.makedirs(self.images_folder, exist_ok=True)

    def _extract_id(self, url: str) -> str:
        match = re.search(r"propiedad/(\d+)", url) or re.search(r"(\d+)", url)
        return match.group(1) if match else "desconocido"

    def fetch_rendered_html_and_images(self, browser=None) -> tuple[str, list]:
        """Método principal llamado por main.py para scrapers basados en Playwright."""
        print(f"[1/4] Abriendo Chromium para Century 21 (ID: {self.id_inmueble})...")
        _pw = None
        _own = browser is None

        if _own:
            _pw = sync_playwright().start()
            browser = _pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            locale="es-CO",
        )
        page = context.new_page()

        try:
            page.goto(self.url, wait_until="networkidle", timeout=45000)
            page.evaluate("window.scrollBy(0, 1000)")
            page.wait_for_timeout(2000)

            html_content = page.content()

            with open(
                os.path.join(self.folder, "pagina_renderizada.html"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(html_content)

            return html_content, []

        except Exception as e:
            print(f"  [Error al cargar página]: {e}")
            return "", []

        finally:
            try:
                context.close()
            except Exception:
                pass
            if _own:
                try:
                    browser.close()
                except Exception:
                    pass
                if _pw:
                    try:
                        _pw.stop()
                    except Exception:
                        pass

    def fetch_page(self) -> str:
        """Alias para mantener compatibilidad con invocaciones simples de HTML."""
        html, _ = self.fetch_rendered_html_and_images()
        return html

    def _extract_tipo_inmueble(self, soup: BeautifulSoup, titulo: str = "", schema_data: dict = None, html_raw: str = "") -> str:
        """Extrae el tipo de inmueble priorizando la URL y el título, con respaldos en metadatos."""
        
        # 1. PRIORIDAD ABSOLUTA: Buscar en la URL (ej: .../venta-apartamento-... o .../casa-...)
        match_url = re.search(r"\b(apartamento|apartestudio|apto|casa|local|oficina|bodega|lote|terreno|finca|penthouse|consultorio|consultorio)\b", self.url, re.I)
        if match_url:
            tipo = match_url.group(1).lower()
            return "Apartamento" if tipo in ["apto", "apartestudio"] else tipo.capitalize()

        # 2. SEGUNDA PRIORIDAD: Buscar en el Título de la página
        if titulo:
            match_tit = re.search(r"\b(apartamento|apartestudio|apto|casa|local|oficina|bodega|lote|terreno|finca|penthouse|consultorio)\b", titulo, re.I)
            if match_tit:
                tipo = match_tit.group(1).lower()
                return "Apartamento" if tipo in ["apto", "apartestudio"] else tipo.capitalize()

        # 3. TERCERA PRIORIDAD: Búsqueda en el HTML Raw (metadatos)
        if html_raw:
            match_meta = re.search(
                r'<meta\s+[^>]*?(?:name|property)=["\'](?:tipoInmueble|tipo_inmueble|tipo)["\']\s+[^>]*?content=["\']([^"\']+)["\']',
                html_raw,
                re.IGNORECASE
            )
            if match_meta and match_meta.group(1).strip():
                val = match_meta.group(1).strip().title()
                if val.lower() not in ["inmueble", "propiedad"]:
                    return val

        # 4. ÚLTIMO RESPALDO GENERAL
        return "Apartamento"   
    
    def parse_data(self, html: str, dom_images: list = None) -> dict:
        print("[2/4] Extrayendo y corrigiendo datos del inmueble...")
        soup = BeautifulSoup(html, "lxml") if html else BeautifulSoup("", "lxml")

        # 1. Extraer Metadatos Schema.org (fuente primaria)
        schema_data = {}
        schema_script = soup.find("script", type="application/ld+json")
        if schema_script and schema_script.string:
            try:
                schema_data = json.loads(schema_script.string)
            except json.JSONDecodeError:
                pass

        # 2. Título
        titulo = schema_data.get("name")
        if not titulo:
            titulo_el = soup.find("h1")
            titulo = (
                titulo_el.text.strip()
                if titulo_el
                else "Inmueble Century 21"
            )

        # 3. Tipo y Operación extraídos de forma dinámica
        tipo = self._extract_tipo_inmueble(soup, titulo, schema_data, html_raw=html)

        operacion_text = f"{titulo} {self.url}".lower()
        if (
            "arriendo" in operacion_text
            or "alquiler" in operacion_text
            or "renta" in operacion_text
        ):
            operacion = "Arriendo"
        else:
            operacion = "Venta"

        # 4. Precio
        monto_precio = None
        price_info = schema_data.get("offers", {})
        if isinstance(price_info, dict) and price_info.get("price"):
            monto_precio = price_info.get("price")

        if monto_precio is None:
            meta_precio = soup.find("meta", {"name": "precio"}) or soup.find("meta", {"property": "og:price:amount"})
            if meta_precio and meta_precio.get("content"):
                monto_precio = meta_precio.get("content")

        # 5. Ubicación
        address_info = schema_data.get("address", {})
        if isinstance(address_info, dict):
            ciudad = address_info.get("addressLocality")
            departamento = address_info.get("addressRegion")
        else:
            ciudad, departamento = None, None

        ubicacion = {
            "ciudad": ciudad,
            "departamento": departamento,
            "barrio_sector": None,
        }

        # 6. Descripción
        descripcion = schema_data.get("description", "")

        if not descripcion:
            heading = soup.find(
                lambda tag: tag.name in ["h2", "h3", "h4", "h5", "b", "strong"]
                and "descripci" in tag.text.lower()
            )
            if heading:
                parent = heading.parent
                if parent and len(parent.get_text(strip=True)) > 50:
                    descripcion = parent.get_text(separator="\n", strip=True)
                elif heading.find_next_sibling():
                    descripcion = heading.find_next_sibling().get_text(
                        separator="\n", strip=True
                    )

        if not descripcion:
            desc_el = (
                soup.find("div", id=re.compile(r"desc|observacio", re.I))
                or soup.find(
                    "div",
                    class_=re.compile(
                        r"desc|observacio|detail-text|property-description|property-details|observaciones-propiedad",
                        re.I,
                    ),
                )
                or soup.find("p", class_=re.compile(r"desc|text", re.I))
            )
            if desc_el:
                descripcion = desc_el.get_text(separator="\n", strip=True)

        if not descripcion:
            meta_desc = soup.find(
                "meta", property="og:description"
            ) or soup.find("meta", attrs={"name": "description"})
            if meta_desc and meta_desc.get("content"):
                descripcion = meta_desc["content"].strip()

        if descripcion:
            descripcion = re.sub(
                r"^(descripción|descripcion)\s*[:\-]*\s*",
                "",
                descripcion,
                flags=re.I,
            ).strip()

        # 7. Características Técnicas estructuradas
        caracteristicas = {}

        for item in soup.find_all(
            ["li", "div", "span", "td", "p"],
            class_=re.compile(r"feature|caracteristica|spec|item|detail|property-info", re.I),
        ):
            text = item.get_text(separator=" ", strip=True).lower()

            if ("área" in text or "area" in text or "m2" in text or "m²" in text) and "area_construida_m2" not in caracteristicas:
                num = re.search(r"\b(\d{1,3}(?:[\.,]\d+)?)\s*(?:m2|m²)?\b", text)
                if num:
                    val = float(num.group(1).replace(",", "."))
                    if val < 500:
                        caracteristicas["area_construida_m2"] = num.group(1)

            elif ("habitación" in text or "habitaciones" in text or "alcoba" in text) and "habitaciones" not in caracteristicas:
                num = re.search(r"\b([1-9])\b", text)
                if num:
                    caracteristicas["habitaciones"] = num.group(1)

            elif ("baño" in text or "baños" in text) and "banos" not in caracteristicas:
                num = re.search(r"\b([1-9])\b", text)
                if num:
                    caracteristicas["banos"] = num.group(1)

            elif "estrato" in text and "estrato" not in caracteristicas:
                num = re.search(r"\b([1-6])\b", text)
                if num:
                    caracteristicas["estrato"] = num.group(1)

        if "area_construida_m2" in caracteristicas:
            try:
                if float(str(caracteristicas["area_construida_m2"]).replace(",", ".")) >= 500:
                    del caracteristicas["area_construida_m2"]
            except ValueError:
                pass

        if schema_data and "area_construida_m2" not in caracteristicas:
            if "floorSize" in schema_data:
                val_area = schema_data["floorSize"]
                raw_val = val_area.get("value") if isinstance(val_area, dict) else val_area
                if raw_val:
                    try:
                        if float(str(raw_val).replace(",", ".")) < 500:
                            caracteristicas["area_construida_m2"] = raw_val
                    except ValueError:
                        pass

        if schema_data:
            if "numberOfRooms" in schema_data and "habitaciones" not in caracteristicas:
                caracteristicas["habitaciones"] = schema_data["numberOfRooms"]
            if "numberOfBathroomsTotal" in schema_data and "banos" not in caracteristicas:
                caracteristicas["banos"] = schema_data["numberOfBathroomsTotal"]

        texto_pagina = soup.get_text(separator=" ", strip=True)

        if "estrato" not in caracteristicas or not caracteristicas["estrato"]:
            for el in soup.find_all(string=re.compile(r'estrato', re.I)):
                parent = el.parent
                if parent:
                    parent_text = parent.get_text(" ", strip=True)
                    m_est = re.search(r'estrato[^\d]*([1-6])', parent_text, re.I)
                    if m_est:
                        caracteristicas["estrato"] = m_est.group(1)
                        break

            if "estrato" not in caracteristicas or not caracteristicas["estrato"]:
                m_global_est = re.search(r'estrato\s*(?:[:\-]?\s*([1-6]))', texto_pagina, re.I)
                if m_global_est:
                    caracteristicas["estrato"] = m_global_est.group(1)

        if "area_construida_m2" not in caracteristicas or not caracteristicas["area_construida_m2"]:
            m_area = re.search(r'(\d{1,3}(?:[\.,]\d+)?)\s*(?:m2|m²|mts2|metros cuadrados)', texto_pagina, re.I)
            if m_area:
                val = float(m_area.group(1).replace(",", "."))
                if val < 500:
                    caracteristicas["area_construida_m2"] = m_area.group(1)

        if "banos" not in caracteristicas or not caracteristicas["banos"]:
            m_banos = re.search(r'\b([1-9])\s*(?:baño|bano|baños|banos)', texto_pagina, re.I)
            if m_banos:
                caracteristicas["banos"] = m_banos.group(1)

        if "habitaciones" not in caracteristicas or not caracteristicas["habitaciones"]:
            m_hab = re.search(r'\b([1-9])\s*(?:habita|alcoba|dormitorio|cuarto)', texto_pagina, re.I)
            if m_hab:
                caracteristicas["habitaciones"] = m_hab.group(1)

        if "administración" in text or "administracion" in text:
                num = re.search(r"([\d\.,]+)", text)
                if num and "administracion" not in caracteristicas:
                    caracteristicas["administracion"] = num.group(1).replace(".", "").replace(",", "")

        # Respaldo general por texto si no se encontró en contenedores específicos
        texto_pagina = soup.get_text(separator=" ", strip=True)

        if "administracion" not in caracteristicas:
            m_adm = re.search(r'administraci[oó]n[^\d]*([\d\.,]+)', texto_pagina, re.I)
            if m_adm:
                caracteristicas["administracion"] = m_adm.group(1).replace(".", "").replace(",", "")

        # 8. Galería de Imágenes
        raw_images = schema_data.get("image", [])
        if isinstance(raw_images, str):
            raw_images = [raw_images]

        if not raw_images:
            gallery_container = soup.find(id="fotos") or soup.find(
                "div", class_=re.compile(r"gallery|fotos|slider", re.I)
            )
            search_scope = gallery_container if gallery_container else soup

            for img in search_scope.find_all("img"):
                src = (
                    img.get("src")
                    or img.get("data-src")
                    or img.get("data-lazy")
                )
                if src:
                    if src.startswith("//"):
                        src = f"https:{src}"
                    elif src.startswith("/"):
                        src = f"https://century21colombia.com{src}"
                    raw_images.append(src)

        imagenes = []
        idx = 1
        for img_url in raw_images:
            is_excluded = re.search(
                r"(logo|advisor|agent|avatar|usuario|usuarios|tile|openstreetmap|leaflet|\.svg|c21ColWebRGold)",
                img_url,
                re.IGNORECASE,
            )

            if not is_excluded:
                if "propiedades" in img_url or not schema_data.get("image"):
                    if img_url not in [i["url"] for i in imagenes]:
                        imagenes.append(
                            {
                                "orden": idx,
                                "url": img_url,
                                "archivo_local": f"imagenes/{idx:02d}.jpg",
                            }
                        )
                        idx += 1

        raw_result = {
            "fuente": {
                "portal": "Century21",
                "url": self.url,
                "id_inmueble": self.id_inmueble,
            },
            "inmueble": {
                "id_propiedad": self.id_inmueble,
                "titulo": titulo,
                "operacion": operacion,
                "tipo": tipo,
                "precio": monto_precio,
                "ubicacion": ubicacion,
                "descripcion": descripcion,
                "caracteristicas": caracteristicas,
                "imagenes": imagenes,
            },
        }

        return Normalizer.normalize_record(raw_result)

    def download_images(self, data: dict):
        images = data.get("inmueble", {}).get("imagenes", [])
        print(f"[3/4] Descargando {len(images)} imágenes reales del inmueble...")

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://century21colombia.com/",
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
        print("=" * 50)
        print("EXTRACTOR DEDICADO - CENTURY 21")
        print("=" * 50)

        html, _ = self.fetch_rendered_html_and_images()
        if not html:
            print("Error: No se pudo obtener el HTML de la página.")
            return

        data = self.parse_data(html)

        print("\n--- DATOS EXTRAÍDOS Y ESTRUCTURADOS ---")
        print(f"ID:            {data['inmueble']['id_propiedad']}")
        print(f"Título:        {data['inmueble']['titulo']}")
        print(f"Tipo:          {data['inmueble']['tipo']}")
        print(f"Operación:     {data['inmueble']['operacion']}")
        print(
            f"Precio:        {data['inmueble']['precio']['monto']} {data['inmueble']['precio']['moneda']}"
        )
        print(
            f"Ubicación:     {data['inmueble']['ubicacion']['barrio_sector']}, {data['inmueble']['ubicacion']['ciudad']}"
        )
        print(f"Descripción:   {str(data['inmueble']['descripcion'])[:100]}...")
        print(
            f"Atributos:     {json.dumps(data['inmueble']['caracteristicas'], ensure_ascii=False)}"
        )
        print(f"Imágenes:      {data['inmueble']['total_imagenes']}")
        print("---------------------------------------\n")

        self.download_images(data)

        json_path = os.path.join(self.folder, "datos.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

        print(
            f"[4/4] Proceso finalizado exitosamente. Guardado en: {json_path}\n"
        )


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_url = sys.argv[1]
    else:
        target_url = "https://century21colombia.com/propiedad/152092_venta-apartamento-centro-historico-de-bogota"

    scraper = Century21Scraper(target_url)
    scraper.run()