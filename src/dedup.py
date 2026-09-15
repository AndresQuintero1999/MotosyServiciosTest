"""Detección y resolución de leads duplicados.

Hallazgo central del perfilado: de 141 grupos de leads que comparten
teléfono, 91 CRUZAN empresa_id. `punto_venta_id -> empresa_id` es 1:1 y
consistente en el 100% de los 1.503 leads y los 2.200 registros históricos,
así que esos 91 grupos son la misma persona cotizando en dos
comercializadoras distintas del mismo grupo empresarial — no son
duplicados a fusionar. Hacerlo filtraría al cliente de una empresa hacia
otra, justo lo que la condición de aislamiento del enunciado prohíbe.

Regla de oro: la llave de deduplicación es (telefono_e164, empresa_id),
nunca el teléfono solo. El nombre se usa únicamente como señal de
confirmación secundaria (hay apellidos compartidos por hasta 8 personas
distintas en el dataset, así que solo el nombre nunca es suficiente).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.normalizacion import clave_nombre


@dataclass
class LeadParaDedup:
    lead_id: str
    empresa_id: str
    telefono_e164: str | None
    nombre: str | None
    fecha_registro: str | None          # ISO, puede ser None
    fecha_primer_contacto: str | None   # ISO, puede ser None


@dataclass
class GrupoDedup:
    lead_maestro_id: str
    miembros: list[str] = field(default_factory=list)  # incluye al maestro
    fecha_registro_maestro: str | None = None
    fecha_primer_contacto_maestro: str | None = None
    motivo: str = ""


def _clave(lead: LeadParaDedup) -> tuple[str, str] | None:
    if not lead.telefono_e164:
        return None
    return (lead.empresa_id, lead.telefono_e164)


def agrupar_duplicados(leads: list[LeadParaDedup]) -> dict[str, GrupoDedup]:
    """Agrupa leads por (empresa_id, telefono_e164).

    Devuelve un mapa lead_id -> GrupoDedup al que pertenece (incluye a los
    singletons, cuyo grupo tiene un único miembro: ellos mismos). Esto
    simplifica el resto del pipeline: siempre se opera sobre "el grupo del
    lead", exista o no duplicado real.
    """
    por_clave: dict[tuple[str, str], list[LeadParaDedup]] = {}
    sin_telefono: list[LeadParaDedup] = []

    for lead in leads:
        k = _clave(lead)
        if k is None:
            sin_telefono.append(lead)
            continue
        por_clave.setdefault(k, []).append(lead)

    resultado: dict[str, GrupoDedup] = {}

    for _clave_grupo, miembros in por_clave.items():
        # Maestro = registro más antiguo por fecha_registro: es el momento
        # real en que la persona entró en contacto por primera vez, y es la
        # definición que usa historico_cierres.csv para medir
        # horas_al_primer_contacto (desde fecha_registro). Usar la fecha más
        # reciente subestimaría cuánto tiempo lleva esperando.
        ordenados = sorted(
            miembros, key=lambda x: (x.fecha_registro or "9999", x.lead_id)
        )
        maestro = ordenados[0]

        fechas_contacto = [m.fecha_primer_contacto for m in miembros if m.fecha_primer_contacto]
        primer_contacto = min(fechas_contacto) if fechas_contacto else None

        if len(miembros) > 1:
            nombres = {clave_nombre(m.nombre) for m in miembros}
            solapan = any(
                a & b for i, a in enumerate(nombres) for b in list(nombres)[i + 1 :]
            ) or len(nombres) <= 1
            motivo = (
                f"Mismo teléfono ({maestro.telefono_e164}) y misma empresa "
                f"({maestro.empresa_id}); {len(miembros)} registros consolidados"
                f"{' — nombres consistentes' if solapan else ' — nombres distintos, se revisa igual por el teléfono'}."
            )
        else:
            motivo = "Lead único: sin otro registro con el mismo teléfono en la misma empresa."

        grupo = GrupoDedup(
            lead_maestro_id=maestro.lead_id,
            miembros=[m.lead_id for m in ordenados],
            fecha_registro_maestro=maestro.fecha_registro,
            fecha_primer_contacto_maestro=primer_contacto,
            motivo=motivo,
        )
        for m in miembros:
            resultado[m.lead_id] = grupo

    # Sin teléfono válido: no se puede deduplicar con evidencia, cada uno es
    # su propio grupo. Fusionar por nombre solo produciría falsos positivos
    # (ver test de apellidos compartidos).
    for lead in sin_telefono:
        resultado[lead.lead_id] = GrupoDedup(
            lead_maestro_id=lead.lead_id,
            miembros=[lead.lead_id],
            fecha_registro_maestro=lead.fecha_registro,
            fecha_primer_contacto_maestro=lead.fecha_primer_contacto,
            motivo="Sin teléfono normalizable: no se pudo evaluar duplicidad.",
        )

    return resultado
