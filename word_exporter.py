import os
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT


class WordExporter:

    def __init__(self, output_path: str = "resultados_consolidados/Informe_Inmuebles.docx"):
        self.output_path = output_path
        self.doc = Document()
        self._configurar_estilos()

    def _configurar_estilos(self):
        for section in self.doc.sections:
            section.top_margin = Inches(0.8)
            section.bottom_margin = Inches(0.8)
            section.left_margin = Inches(0.8)
            section.right_margin = Inches(0.8)

    def _formatear_moneda(self, valor) -> str:
        """Formatea un número o string numérico a formato de moneda con puntos de miles."""
        if valor is None:
            return "N/A"
        try:
            # Limpiar por si viene con decimales o texto extra
            numerolimpio = float(str(valor).replace(",", ""))
            # Formatear con separador de miles usando puntos (estilo colombiano/español)
            return f"{numerolimpio:,.0f}".replace(",", ".")
        except (ValueError, TypeError):
            return str(valor)

    def agregar_portada_encabezado(self, total_inmuebles: int):
        title_p = self.doc.add_paragraph()
        title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title_p.add_run("INFORME CONSOLIDADO DE INMUEBLES")
        run.font.size = Pt(22)
        run.font.bold = True
        run.font.color.rgb = RGBColor(31, 78, 121)

        sub_p = self.doc.add_paragraph()
        sub_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_sub = sub_p.add_run(
            f"Reporte ({total_inmuebles} propiedades procesadas)"
        )
        run_sub.font.size = Pt(12)
        run_sub.font.italic = True
        run_sub.font.color.rgb = RGBColor(89, 89, 89)

        self.doc.add_paragraph().paragraph_format.space_after = Pt(20)

    def agregar_inmueble(self, registro: dict):
        fuente = registro.get("fuente", {})
        inmueble = registro.get("inmueble", {})
        precio = inmueble.get("precio", {})
        ubicacion = registro.get("ubicacion", {}) or inmueble.get("ubicacion", {})
        caracteristicas = inmueble.get("caracteristicas", {})
        imagenes = inmueble.get("imagenes", [])

        # Encabezado del Inmueble
        h_p = self.doc.add_paragraph()
        run_h = h_p.add_run(f"📌 {inmueble.get('titulo', 'Inmueble sin título')}")
        run_h.font.size = Pt(14)
        run_h.font.bold = True
        run_h.font.color.rgb = RGBColor(31, 78, 121)

        # Tabla de Ficha Técnica
        table = self.doc.add_table(rows=7, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False

        # Aplicar formato de moneda al monto principal y a la administración
        monto_fmt = self._formatear_moneda(precio.get('monto'))
        admin_raw = precio.get('administracion')
        admin_str = f" (Admin: ${self._formatear_moneda(admin_raw)})" if admin_raw else ""
        
        datos_tabla = [
            ("Portal / ID:", f"{fuente.get('portal', 'N/A')} — (ID: {inmueble.get('id_propiedad', 'N/A')})"),
            ("Operación / Tipo:", f"{inmueble.get('operacion', 'N/A')} | {inmueble.get('tipo', 'N/A')}"),
            ("Precio:", f"${monto_fmt} {precio.get('moneda', 'COP')}{admin_str}"),
            ("Ubicación:", (
                f"{ubicacion.get('barrio_sector')}, " if ubicacion.get('barrio_sector') else ""
            ) + f"{ubicacion.get('ciudad') or 'N/A'} - {ubicacion.get('departamento') or ''}"),
            ("Área / Estrato:", f"{caracteristicas.get('area_construida_m2')} m² | Estrato {caracteristicas.get('estrato')}"),
            ("Distribución:", f"{caracteristicas.get('habitaciones')} Habs | {caracteristicas.get('banos')} Baños | {caracteristicas.get('parqueaderos', 'N/A')} Parqueaderos"),
            ("Enlace:", fuente.get("url", "N/A")),
        ]

        for idx, (label, val) in enumerate(datos_tabla):
            row = table.rows[idx]
            cell_lbl, cell_val = row.cells[0], row.cells[1]

            cell_lbl.text = label
            cell_val.text = str(val) if val is not None else "N/A"

            p_lbl = cell_lbl.paragraphs[0]
            if p_lbl.runs:
                p_lbl.runs[0].font.bold = True
                p_lbl.runs[0].font.size = Pt(10)

            p_val = cell_val.paragraphs[0]
            if p_val.runs:
                p_val.runs[0].font.size = Pt(10)

        # Descripción
        desc = inmueble.get("descripcion", "")
        if desc:
            p_desc_lbl = self.doc.add_paragraph()
            run_d = p_desc_lbl.add_run("Descripción:")
            run_d.bold = True
            run_d.font.size = Pt(10)

            p_desc_txt = self.doc.add_paragraph(desc[:400] + ("..." if len(desc) > 400 else ""))
            p_desc_txt.paragraph_format.space_after = Pt(10)

        # Galería de TODAS las imágenes
        if imagenes:
            p_img_lbl = self.doc.add_paragraph()
            run_img = p_img_lbl.add_run(f"Galería de fotos extraídas ({len(imagenes)} en total):")
            run_img.bold = True
            run_img.font.size = Pt(10)

            portal_slug = fuente.get('portal', '').lower().replace(' ', '')
            portal_folder = f"{portal_slug}_{inmueble.get('id_propiedad')}"
            p_imgs = self.doc.add_paragraph()
            p_imgs.alignment = WD_ALIGN_PARAGRAPH.LEFT

            for img in imagenes:
                local_rel_path = img.get("archivo_local")
                full_img_path = os.path.join(portal_folder, local_rel_path)

                if os.path.exists(full_img_path):
                    try:
                        p_imgs.add_run().add_picture(full_img_path, width=Inches(3.2))
                    except Exception:
                        pass

        self.doc.add_paragraph("─" * 55)

    def generar_documento(self, datos: list) -> str:
        self.agregar_portada_encabezado(len(datos))
        for reg in datos:
            self.agregar_inmueble(reg)

        os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
        self.doc.save(self.output_path)
        return self.output_path