import sys
import os
import re
import json
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from normalizer import Normalizer

try:
    from dotenv import load_dotenv
    _base = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    load_dotenv(os.path.join(_base, ".env"))
except ImportError:
    pass

BASE_URL = "https://21online.century21colombia.com"


class Century21OnlineScraper:

    def __init__(self, url: str):
        self.url = url
        self.id_inmueble = self._extract_id(url)
        self.folder = f"century21online_{self.id_inmueble}"
        self.images_folder = os.path.join(self.folder, "imagenes")
        os.makedirs(self.images_folder, exist_ok=True)

    def _extract_id(self, url: str) -> str:
        match = re.search(r"/ver/(\d+)", url) or re.search(r"/(\d{4,})", url)
        return match.group(1) if match else "desconocido"

    def _login(self, page) -> None:
        email = os.environ.get("C21_EMAIL", "")
        password = os.environ.get("C21_PASSWORD", "")
        if not email or not password:
            raise ValueError("Faltan credenciales C21_EMAIL / C21_PASSWORD en .env")

        print(f"  [21online] Iniciando sesión como {email}...")
        page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
        page.fill('input[name="_username"]', email)
        page.fill('input[name="_password"]', password)
        page.click('button[type="submit"]:has-text("Iniciar sesión")')
        page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        print("  [21online] Sesión iniciada correctamente.")

    def fetch_rendered_html_and_images(self, browser=None) -> tuple[str, list]:
        print(f"  [21online] Abriendo Chromium (ID: {self.id_inmueble})...")
        _pw = None
        _own = browser is None

        if _own:
            _pw = sync_playwright().start()
            browser = _pw.chromium.launch(headless=True, args=["--no-sandbox"])

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            locale="es-CO",
        )
        page = context.new_page()

        try:
            self._login(page)
            page.goto(self.url, wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(2000)

            html_content = page.content()
            with open(os.path.join(self.folder, "pagina_renderizada.html"), "w", encoding="utf-8") as f:
                f.write(html_content)

            return html_content, []

        except Exception as e:
            print(f"  [Error]: {e}")
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

    def parse_data(self, html: str, dom_images: list = None) -> dict:
        soup = BeautifulSoup(html, "lxml") if html else BeautifulSoup("", "lxml")
        texto = soup.get_text(separator=" ", strip=True)

        # --- Precio: última fila de historial con precioRenta ---
        precio_raw = None
        for row in soup.find_all("tr"):
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if "precioRenta" in cells:
                # columna 4 = nuevo valor (el más reciente es la primera fila)
                for val in reversed(cells):
                    if re.match(r"^\d{4,}$", val):
                        precio_raw = val
                        break
                if precio_raw:
                    break

        # Fallback: precio mostrado como $X,XXM
        if not precio_raw:
            m = re.search(r"\$\s*([\d,\.]+)M\b", texto)
            if m:
                precio_raw = str(int(float(m.group(1).replace(",", ".")) * 1_000_000))

        # --- Operación ---
        operacion = "Venta"
        m_op = re.search(r"Tipo de Operaci[oó]n[:\s]+(\w+)", texto, re.I)
        if m_op:
            op = m_op.group(1).lower()
            operacion = "Arriendo" if op in ("renta", "arriendo", "alquiler") else "Venta"

        # --- Tipo de inmueble ---
        tipo = "Apartamento"
        m_sub = re.search(r"Subtipo de Propiedad[:\s]+(\w+)", texto, re.I)
        if m_sub:
            t = m_sub.group(1).lower()
            tipo_map = {"apartamento": "Apartamento", "casa": "Casa", "local": "Local",
                        "oficina": "Oficina", "bodega": "Bodega", "lote": "Lote",
                        "apartaestudio": "Apartaestudio"}
            tipo = tipo_map.get(t, t.capitalize())

        # --- Características ---
        caract = {}

        m_area = re.search(r'm2TResumen[^>]*>(\d+(?:[\.,]\d+)?)', html)
        if m_area:
            caract["area_construida_m2"] = m_area.group(1)

        for label, key in [("Habitaciones", "habitaciones"), ("Baños", "banos"),
                            ("Piso En Que Se Encuentra", "piso"), ("Estrato", "estrato")]:
            m = re.search(rf"{label}[:\s]+(\d+)", texto, re.I)
            if m:
                caract[key] = m.group(1)

        # Administración
        m_adm = re.search(r"administraci[oó]n[^\d]*([\d\.,]+)", texto, re.I)
        if m_adm:
            caract["administracion"] = m_adm.group(1).replace(".", "").replace(",", "")

        # --- Ubicación ---
        # El gantt embebe la dirección como: "text":"Calle X Barrio Ciudad Departamento"
        ciudad, departamento, barrio, direccion = None, None, None, None

        m_dir = re.search(r'"text"\s*:\s*"([^"]{10,})"', html)
        if m_dir:
            partes = m_dir.group(1).strip().split()
            # Últimas dos palabras = ciudad y departamento; resto = dirección
            if len(partes) >= 4:
                departamento = partes[-1]
                ciudad = partes[-2]
                direccion = " ".join(partes[:-2])

        # --- Descripción ---
        # El HTML tiene <strong>Descripción:</strong> seguido de texto en el mismo contenedor
        descripcion = None
        strong_desc = soup.find("strong", string=re.compile(r"Descripci[oó]n", re.I))
        if strong_desc:
            container = strong_desc.parent
            parts = []
            for node in strong_desc.next_siblings:
                t = node.get_text(strip=True) if hasattr(node, "get_text") else str(node).strip()
                if t:
                    parts.append(t)
            descripcion = " ".join(parts).strip()[:500] or None

        # --- Título ---
        titulo = f"Inmueble 21Online {self.id_inmueble}"
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            titulo = h1.get_text(strip=True)
        elif descripcion:
            titulo = descripcion[:80].strip()

        # --- Imágenes: <a href> al CDN de 21online ---
        imagenes = []
        seen = set()
        idx = 1
        for a in soup.find_all("a", href=re.compile(r"cdn\.21online\.lat.*propiedades", re.I)):
            img_url = a["href"]
            if img_url not in seen:
                seen.add(img_url)
                imagenes.append({
                    "orden": idx,
                    "url": img_url,
                    "archivo_local": f"imagenes/{idx:02d}.jpg",
                })
                idx += 1

        raw_result = {
            "fuente": {
                "portal": "Century21 Online",
                "url": self.url,
                "id_inmueble": self.id_inmueble,
            },
            "inmueble": {
                "id_propiedad": self.id_inmueble,
                "titulo": titulo,
                "operacion": operacion,
                "tipo": tipo,
                "precio": precio_raw,
                "ubicacion": {
                    "ciudad": ciudad,
                    "departamento": departamento,
                    "barrio_sector": barrio,
                    "direccion": direccion,
                },
                "descripcion": descripcion,
                "caracteristicas": caract,
                "imagenes": imagenes,
            },
        }
        return Normalizer.normalize_record(raw_result)

    def download_images(self, data: dict):
        images = data.get("inmueble", {}).get("imagenes", [])
        print(f"  Descargando {len(images)} imágenes...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": BASE_URL,
        }
        for img in images:
            url_target = img["url"]
            local_path = os.path.join(self.folder, img["archivo_local"])
            try:
                res = requests.get(url_target, headers=headers, timeout=15)
                if res.status_code == 200:
                    with open(local_path, "wb") as f:
                        f.write(res.content)
                    print(f"  [OK] {img['archivo_local']}")
                else:
                    print(f"  [HTTP {res.status_code}] {url_target}")
            except Exception as e:
                print(f"  [Error] {e}")
