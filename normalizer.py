import re
from typing import Any, Dict, List, Optional


class Normalizer:

    @staticmethod
    def to_int(val: Any) -> Optional[int]:
        if val is None or val == "":
            return None
        if isinstance(val, (int, float)):
            return int(val)
        cleaned = re.sub(r"[^\d]", "", str(val))
        return int(cleaned) if cleaned else None

    @staticmethod
    def to_float(val: Any) -> Optional[float]:
        if val is None or val == "":
            return None
        if isinstance(val, (int, float)):
            return float(val)
        str_val = str(val).replace(",", ".")
        cleaned = re.sub(r"[^\d\.]", "", str_val)
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None

    @staticmethod
    def clean_text(val: Any) -> Optional[str]:
        if not val:
            return None
        cleaned = re.sub(r"\s+", " ", str(val)).strip()
        return cleaned if cleaned else None

    @classmethod
    def normalize_record(cls, raw_json: Dict[str, Any]) -> Dict[str, Any]:
        """Convierte cualquier JSON de las fuentes al formato estandarizado sin perder información."""
        fuente = raw_json.get("fuente", {})
        inmueble = raw_json.get("inmueble", {})

        # Manejo de Precio y Administración
        precio_raw = inmueble.get("precio", {})
        caract = inmueble.get("caracteristicas", {}) # Mover la lectura de caracteristicas un poco antes
        
        if isinstance(precio_raw, dict):
            monto = precio_raw.get("monto")
            admin = precio_raw.get("administracion") or caract.get("administracion")
        else:
            monto = precio_raw
            admin = caract.get("administracion")

        admin = admin or inmueble.get("valor_administracion") or inmueble.get("administracion")

        # Manejo de Ubicación
        ubicacion = inmueble.get("ubicacion", {})
        barrio = ubicacion.get("barrio_sector") or ubicacion.get("barrio")

        # Manejo de Características
        caract = inmueble.get("caracteristicas", {})
        area = (
            caract.get("area_construida_m2")
            or inmueble.get("area_m2")
            or caract.get("area")
        )
        habs = caract.get("habitaciones") or inmueble.get("habitaciones")
        banos = caract.get("banos") or inmueble.get("banos")
        estrato = caract.get("estrato") or inmueble.get("estrato")
        parq = caract.get("parqueaderos") or inmueble.get("parqueaderos")
        piso = caract.get("piso") or inmueble.get("piso")

        # Atributos extendidos
        ano_const = caract.get("ano_construccion")
        cocina = caract.get("cocina")
        ascensores = caract.get("ascensores")

        # Manejo de Imágenes
        imgs_raw = inmueble.get("imagenes", [])
        imagenes = []
        for idx, img in enumerate(imgs_raw, start=1):
            url = img.get("url") or img.get("url_descarga") or img.get("url_original")
            imagenes.append(
                {
                    "orden": img.get("orden", idx),
                    "url": cls.clean_text(url),
                    "espacio": cls.clean_text(img.get("espacio")),
                    "archivo_local": cls.clean_text(img.get("archivo_local")),
                }
            )

        # Amenidades
        amenidades_raw = inmueble.get("amenidades", [])
        amenidades = list(
            dict.fromkeys(
                [cls.clean_text(a) for a in amenidades_raw if cls.clean_text(a)]
            )
        )

        id_inm = cls.clean_text(fuente.get("id_inmueble") or inmueble.get("id_propiedad"))
        tipo_valor = cls.clean_text(inmueble.get("tipo") or inmueble.get("tipo_inmueble"))

        return {
            "fuente": {
                "portal": cls.clean_text(fuente.get("portal")),
                "url": cls.clean_text(fuente.get("url")),
                "id_inmueble": id_inm,
            },
            "inmueble": {
                "id_propiedad": id_inm,
                "titulo": cls.clean_text(inmueble.get("titulo")) or "Inmueble sin título",
                "operacion": cls.clean_text(inmueble.get("operacion")),
                "tipo": tipo_valor,
                "tipo_inmueble": tipo_valor,
                "precio": {
                    "monto": cls.to_int(monto),
                    "administracion": cls.to_int(admin),
                    "moneda": "COP",
                },
                "ubicacion": {
                    "ciudad": cls.clean_text(ubicacion.get("ciudad")),
                    "departamento": cls.clean_text(ubicacion.get("departamento")),
                    "barrio_sector": cls.clean_text(barrio),
                    "direccion": cls.clean_text(ubicacion.get("direccion")),
                    "pais": "Colombia",
                },
                "descripcion": cls.clean_text(inmueble.get("descripcion")),
                "caracteristicas": {
                    "area_construida_m2": cls.to_float(area),
                    "habitaciones": cls.to_int(habs),
                    "banos": cls.to_int(banos),
                    "estrato": cls.to_int(estrato),
                    "parqueaderos": cls.to_int(parq),
                    "piso": cls.to_int(piso),
                    "ano_construccion": cls.to_int(ano_const),
                    "cocina": cls.clean_text(cocina),
                    "ascensores": cls.to_int(ascensores),
                },
                "amenidades": amenidades,
                "total_imagenes": len(imagenes),
                "imagenes": imagenes,
            },
        }