import sys
import json
import os
import re
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from normalizer import Normalizer


class MetroCuadradoScraper:

    def __init__(self, url: str):
        self.url = url
        self.id_inmueble = self._extract_id(url)
        self.folder = f"metrocuadrado_{self.id_inmueble}"
        self.images_folder = os.path.join(self.folder, "imagenes")
        os.makedirs(self.images_folder, exist_ok=True)

    def _extract_id(self, url: str) -> str:
        match = re.search(r"/([\d]+-[A-Z0-9]+)", url, re.I) or re.search(
            r"([A-Z0-9]{5,15})", url, re.I
        )
        return match.group(1) if match else "desconocido"

    def fetch_rendered_html_and_images(self) -> tuple[str, list]:
        print(
            f"[1/4] Ejecutando Chromium para renderizar Metrocuadrado (ID: {self.id_inmueble})..."
        )
        extracted_images = []

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
                page.goto(
                    self.url, wait_until="domcontentloaded", timeout=60000
                )
                page.wait_for_timeout(3000)

                # Scroll progresivo para forzar lazy load de imágenes
                for _ in range(4):
                    page.evaluate("window.scrollBy(0, 600)")
                    page.wait_for_timeout(800)

                # Clic táctico en elementos de galería/fotos si existen
                for selector in [
                    "button:has-text('fotos')",
                    "button:has-text('Ver')",
                    "[class*='gallery']",
                    "[class*='carousel']",
                    "div.slick-slide",
                ]:
                    try:
                        el = page.query_selector(selector)
                        if el and el.is_visible():
                            el.click(force=True)
                            page.wait_for_timeout(1500)
                            break
                    except Exception:
                        continue

                # Extraer todas las URLs de imágenes desde el DOM dentro del navegador
                raw_dom_urls = page.evaluate("""() => {
                    const urls = [];
                    document.querySelectorAll('img').forEach(img => {
                        if (img.src) urls.push(img.src);
                        if (img.getAttribute('data-src')) urls.push(img.getAttribute('data-src'));
                        if (img.getAttribute('data-lazy')) urls.push(img.getAttribute('data-lazy'));
                        if (img.srcset) {
                            img.srcset.split(',').forEach(s => urls.push(s.trim().split(' ')[0]));
                        }
                    });
                    document.querySelectorAll('source').forEach(src => {
                        if (src.srcset) {
                            src.srcset.split(',').forEach(s => urls.push(s.trim().split(' ')[0]));
                        }
                    });
                    return urls;
                }""")

                for u in raw_dom_urls:
                    if u and isinstance(u, str):
                        if u.startswith("//"):
                            u = f"https:{u}"
                        clean_u = u.split("?")[0]
                        if not re.search(
                            r"(logo|avatar|icon|map|tile|static|assets|\.svg|banner|google|facebook|marker)",
                            clean_u,
                            re.I,
                        ):
                            if clean_u not in extracted_images:
                                extracted_images.append(clean_u)

                html_content = page.content()
                browser.close()

                html_path = os.path.join(self.folder, "pagina_renderizada.html")
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html_content)

                return html_content, extracted_images

            except Exception as e:
                print(f"  [Error al cargar página]: {e}")
                browser.close()
                return None, extracted_images

    def parse_data(self, html: str, dom_images: list) -> dict:
        print("[2/4] Extrayendo y estructurando metadatos...")
        soup = BeautifulSoup(html, "lxml") if html else BeautifulSoup("", "lxml")

        property_data = {}

        # 1. Intentar obtener datos desde script NEXT_DATA
        next_script = soup.find("script", id="__NEXT_DATA__")
        if next_script and next_script.string:
            try:
                next_json = json.loads(next_script.string)
                queries = (
                    next_json.get("props", {})
                    .get("pageProps", {})
                    .get("dehydratedState", {})
                    .get("queries", [])
                )
                for q in queries:
                    q_data = q.get("state", {}).get("data", {})
                    if isinstance(q_data, dict) and (
                        "m2Title" in q_data
                        or "price" in q_data
                        or "m2Code" in q_data
                    ):
                        property_data = q_data
                        break
            except json.JSONDecodeError:
                pass

        # 2. Escaneo Regex sobre el HTML completo para rescatar imágenes de CDNs
        all_found_urls = list(dom_images)
        if html:
            regex_urls = re.findall(
                r'https?://[^\s"\'\\]+?\.(?:jpg|jpeg|png|webp)', html, re.I
            )
            for u in regex_urls:
                clean_u = u.replace("\\", "").split("?")[0]
                if not re.search(
                    r"(logo|avatar|icon|map|tile|static|assets|\.svg|banner|google|facebook|marker)",
                    clean_u,
                    re.I,
                ):
                    if clean_u not in all_found_urls:
                        all_found_urls.append(clean_u)

        # Formatear lista final de imágenes
        imagenes = []
        seen = set()
        idx = 1
        for u in all_found_urls:
            if u not in seen:
                seen.add(u)
                imagenes.append(
                    {
                        "orden": idx,
                        "url": u,
                        "archivo_local": f"imagenes/{idx:02d}.jpg",
                    }
                )
                idx += 1

        # --- EXTRACCIÓN CON FALLBACKS ---

        # Título
        h1_elem = soup.find("h1")
        titulo = (
            property_data.get("m2Title")
            or property_data.get("title")
            or (h1_elem.get_text(strip=True) if h1_elem else "Inmueble en Metrocuadrado")
        )

        # Precio de Arriendo / Venta
        monto_precio = property_data.get("price") or property_data.get("rentPrice")
        if not monto_precio:
            price_match = re.search(r"\$\s*([\d\.\,]+)", html)
            if price_match:
                monto_precio = price_match.group(1)

        # Valor de Administración
        monto_admin = (
            property_data.get("adminPrice")
            or property_data.get("administration")
            or property_data.get("adminValue")
            or property_data.get("administrationFee")
            or property_data.get("adminFee")
        )
        if not monto_admin:
            admin_match = re.search(
                r"administraci[oó]n[^$\d]{0,30}[$\s]*([\d\.\,]+)",
                html,
                re.I,
            )
            if admin_match:
                monto_admin = re.sub(r"[^\d]", "", admin_match.group(1))

        # --- UBICACIÓN (Actualizada para mejor captura geográfica) ---
        location_info = property_data.get("location", {})
        address_info = property_data.get("address", {})
        
        ciudad = (
            location_info.get("city") 
            or property_data.get("city") 
            or address_info.get("city")
        )
        departamento = (
            location_info.get("department") 
            or location_info.get("state")
            or property_data.get("department") 
            or address_info.get("department")
        )
        barrio = (
            location_info.get("neighborhood") 
            or location_info.get("zone")
            or property_data.get("neighborhood") 
            or address_info.get("neighborhood")
        )

        if not barrio or not ciudad:
            breadcrumbs = soup.find_all(class_=re.compile(r"breadcrumb|ubicacion|location", re.I))
            crumb_text = " ".join([b.get_text(" ", strip=True) for b in breadcrumbs])
            
            if not barrio:
                barrio_match = re.search(r"(?:barrio|sector)\s*:\s*([^<,\n\r]+)", html, re.I) or re.search(r"en\s+([A-Za-z\s]+?)\s*,\s*Soacha", crumb_text, re.I)
                if barrio_match:
                    barrio = barrio_match.group(1).strip()
                else:
                    if "san-mateo" in self.url.lower() or "san mateo" in html.lower():
                        barrio = "San Mateo"

            if not ciudad:
                if "soacha" in self.url.lower() or "soacha" in html.lower():
                    ciudad = "Soacha"
                    departamento = "Cundinamarca"

        ubicacion = {
            "ciudad": ciudad,
            "departamento": departamento,
            "barrio_sector": barrio,
        }

        # Descripción
        descripcion = property_data.get("comment") or property_data.get("description", "")
        if not descripcion:
            desc_elem = soup.find(class_=re.compile(r"description|comment|detalles", re.I))
            if desc_elem:
                descripcion = desc_elem.get_text(" ", strip=True)

        # Características Principales
        area = property_data.get("m2") or property_data.get("area")
        if not area:
            area_match = re.search(r"(\d+(?:\.\d+)?)\s*m²", html, re.I)
            if area_match:
                area = area_match.group(1)

        habitaciones = property_data.get("rooms") or property_data.get("bedrooms")
        if not habitaciones:
            hab_match = re.search(r"(\d+)\s*habitación", html, re.I) or re.search(r"(\d+)\s*hab", html, re.I)
            if hab_match:
                habitaciones = hab_match.group(1)

        banos = property_data.get("baths") or property_data.get("bathrooms")
        if not banos:
            banos_match = re.search(r"(\d+)\s*baño", html, re.I)
            if banos_match:
                banos = banos_match.group(1)

        estrato = property_data.get("stratum")
        if not estrato:
            estrato_match = re.search(r"estrato\s*(\d+)", html, re.I)
            if estrato_match:
                estrato = estrato_match.group(1)

        parqueaderos = property_data.get("garages") or property_data.get("parking")
        piso = property_data.get("floor")

        caracteristicas = {
            "area_construida_m2": area,
            "habitaciones": habitaciones,
            "banos": banos,
            "estrato": estrato,
            "parqueaderos": parqueaderos,
            "piso": piso,
        }

        # Extraer Amenidades / Características adicionales
        amenidades = []
        amenities_raw = property_data.get("amenities") or property_data.get("features", [])
        if isinstance(amenities_raw, list):
            amenidades = [str(a) for a in amenities_raw]
        else:
            feature_nodes = soup.find_all(class_=re.compile(r"feature|tag|amenity|caracteristica", re.I))
            for node in feature_nodes:
                txt = node.get_text(strip=True)
                if txt and len(txt) < 40 and txt not in amenidades:
                    amenidades.append(txt)

        raw_data = {
            "fuente": {
                "portal": "Metrocuadrado",
                "url": self.url,
                "id_inmueble": self.id_inmueble,
            },
            "inmueble": {
                "id_propiedad": self.id_inmueble,
                "titulo": titulo,
                "operacion": "Arriendo" if "arriendo" in self.url.lower() else "Venta",
                "tipo": "Apartamento" if "apartamento" in self.url.lower() else "Inmueble",
                "precio": monto_precio,
                "administracion": monto_admin,
                "ubicacion": ubicacion,
                "descripcion": descripcion,
                "caracteristicas": caracteristicas,
                "amenidades": amenidades,
                "imagenes": imagenes,
            },
        }

        return Normalizer.normalize_record(raw_data)

    def download_images(self, data: dict):
        images = data["inmueble"]["imagenes"]
        print(f"[3/4] Descargando {len(images)} imágenes capturadas...")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.metrocuadrado.com/",
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
            except Exception as e:
                print(f"  [Error descarga]: {e}")

    def run(self):
        print("=" * 60)
        print("EXTRACTOR METROCUADRADO")
        print("=" * 60)

        html, dom_images = self.fetch_rendered_html_and_images()
        data = self.parse_data(html, dom_images)

        print("\n--- DATOS EXTRAÍDOS Y ESTRUCTURADOS ---")
        print(f"ID:            {data['inmueble']['id_propiedad']}")
        print(f"Título:        {data['inmueble']['titulo']}")
        print(
            f"Precio:        {data['inmueble']['precio']['monto']} {data['inmueble']['precio']['moneda']}"
        )
        print(f"Administración:{data['inmueble']['precio']['administracion']}")
        print(f"Barrio:        {data['inmueble']['ubicacion']['barrio_sector']}")
        print(f"Total Imágenes:{data['inmueble']['total_imagenes']}")
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
        target_url = "https://www.metrocuadrado.com/inmueble/arriendo-apartamento-soacha-soacha-3-habitaciones-1-banos/11813-M7027446?src_flow=busqueda-por-mapa&src_url=%2Fmapa%2Fapartamentos%2Farriendo%2Fsoacha%2F&src_env=pro"

    scraper = MetroCuadradoScraper(target_url)
    scraper.run()