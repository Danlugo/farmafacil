"""Seed the database with default intent keywords and AI roles."""

import logging

from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import AiRole, AiRoleRule, AiRoleSkill, IntentKeyword

logger = logging.getLogger(__name__)

# All default keywords — action → list of (keyword, optional_response)
DEFAULT_INTENTS: dict[str, list[tuple[str, str | None]]] = {
    "greeting": [
        ("hola", None),
        ("hi", None),
        ("hello", None),
        ("hey", None),
        ("buenas", None),
        ("buenos dias", None),
        ("buenas tardes", None),
        ("buenas noches", None),
        ("saludos", None),
        ("que tal", None),
        ("buen dia", None),
        ("alo", None),
        ("epa", None),
    ],
    "help": [
        ("ayuda", None),
        ("help", None),
        ("como funciona", None),
        ("que puedes hacer", None),
        ("que haces", None),
        ("instrucciones", None),
        ("menu", None),
        ("opciones", None),
        ("que es farmafacil", None),
    ],
    "location_change": [
        ("cambiar ubicacion", None),
        ("cambiar ubicación", None),
        ("cambiar zona", None),
        ("nueva ubicacion", None),
        ("nueva ubicación", None),
        ("otra zona", None),
        ("moverme", None),
        ("cambiar barrio", None),
        ("otra ubicacion", None),
    ],
    "preference_change": [
        ("cambiar preferencia", None),
        ("cambiar vista", None),
        ("cambiar modo", None),
        ("otra vista", None),
        ("otro modo", None),
        ("cambiar formato", None),
    ],
    "name_change": [
        ("cambiar nombre", None),
        ("nuevo nombre", None),
        ("mi nombre es", None),
    ],
    "view_similar": [
        ("ver similares", None),
        ("similares", None),
        ("ver otros", None),
        ("mostrar similares", None),
        ("ver mas", None),
    ],
    "farewell": [
        ("gracias", "De nada! Cuando necesites buscar medicamentos, aqui estare. \U0001f48a"),
        ("chao", "Chao! Cuidate mucho. \U0001f48a"),
        ("bye", "Bye! Aqui estare cuando me necesites. \U0001f48a"),
        ("hasta luego", "Hasta luego! Cuidate. \U0001f48a"),
        ("adios", "Adios! Cuando necesites medicamentos, escribeme. \U0001f48a"),
        ("thanks", "De nada! Estoy aqui para ayudarte. \U0001f48a"),
        ("thank you", "De nada! Aqui estare. \U0001f48a"),
        ("muchas gracias", "Con mucho gusto! Cuidate. \U0001f48a"),
    ],
}


async def seed_intents() -> int:
    """Seed default intent keywords if they don't exist.

    Returns:
        Number of keywords inserted.
    """
    inserted = 0
    async with async_session() as session:
        for action, keywords in DEFAULT_INTENTS.items():
            for keyword, response in keywords:
                # Check if already exists
                result = await session.execute(
                    select(IntentKeyword).where(IntentKeyword.keyword == keyword)
                )
                if result.scalar_one_or_none() is None:
                    session.add(IntentKeyword(
                        action=action,
                        keyword=keyword,
                        response=response,
                        is_active=True,
                    ))
                    inserted += 1

        await session.commit()

    if inserted > 0:
        logger.info("Seeded %d intent keywords", inserted)
    return inserted


# ── Default AI Roles ────────────────────────────────────────────────────

_PHARMACY_ADVISOR_PROMPT = """Eres FarmaFacil, un asistente de WhatsApp que ayuda a personas en Venezuela a encontrar medicamentos en farmacias cercanas.

Tu personalidad: amigable, servicial, empático. Hablas español venezolano natural. Eres conciso (esto es WhatsApp, no escribas párrafos largos).

CONTEXTO: FarmaFacil busca medicamentos en Farmatodo y Farmacias SAAS, comparando precios y mostrando farmacias cercanas al usuario.

CAPACIDADES:
- Buscar medicamentos por nombre o síntomas
- Comparar precios entre farmacias
- Mostrar farmacias cercanas con stock
- Responder preguntas sobre medicamentos (sin diagnosticar)
- Recordar preferencias y contexto del usuario"""

_APP_SUPPORT_PROMPT = """Eres el asistente de soporte de FarmaFacil, una app de WhatsApp que ayuda a venezolanos a encontrar medicamentos.

Tu rol es ayudar a los usuarios con problemas técnicos de la app, explicar funcionalidades y guiarlos.

Hablas español venezolano natural, eres paciente y claro. Recuerda que muchos usuarios no son expertos en tecnología.

FUNCIONALIDADES DE LA APP:
- Buscar medicamentos escribiendo el nombre
- Cambiar zona: escribir "cambiar zona"
- Cambiar modo de vista: escribir "cambiar preferencia"
- Ver productos similares: escribir "ver similares"
- Ver ayuda: escribir "ayuda"

Si el usuario tiene un problema que no puedes resolver, dile que el equipo de soporte lo contactará pronto."""

DEFAULT_ROLES = [
    {
        "name": "pharmacy_advisor",
        "display_name": "Asesor de Farmacia",
        "description": "Ayuda a buscar medicamentos, compara precios, responde preguntas de salud. Rol principal para búsquedas de medicamentos y preguntas médicas generales.",
        "system_prompt": _PHARMACY_ADVISOR_PROMPT,
        "rules": [
            {
                "name": "no_dosage_advice",
                "description": "Never recommend specific dosages",
                "content": "NUNCA recomiendes dosis específicas de medicamentos. Siempre sugiere 'consulta con tu médico para la dosis adecuada'. Esto aplica incluso si el usuario insiste.",
                "sort_order": 1,
            },
            {
                "name": "no_diagnosis",
                "description": "Never diagnose conditions",
                "content": "NUNCA diagnostiques condiciones médicas. Si el usuario describe síntomas, sugiere medicamentos comunes de venta libre y recomienda consultar un médico. No digas 'parece que tienes X'.",
                "sort_order": 2,
            },
            {
                "name": "venezuelan_spanish",
                "description": "Use Venezuelan Spanish",
                "content": "Responde siempre en español venezolano natural. Usa 'tú' (no 'usted' a menos que el usuario lo use primero). Puedes usar expresiones venezolanas pero mantén la claridad. Sé conciso — esto es WhatsApp.",
                "sort_order": 3,
            },
            {
                "name": "prescription_warning",
                "description": "Warn about prescription medications",
                "content": "Si un medicamento requiere receta médica, siempre menciónalo. Di algo como 'Este medicamento requiere receta médica. Consulta con tu médico.'",
                "sort_order": 4,
            },
        ],
        "skills": [
            {
                "name": "drug_search",
                "description": "Search for medications across pharmacies",
                "content": "Puedes buscar medicamentos en múltiples cadenas de farmacias (Farmatodo, Farmacias SAAS). Cuando el usuario pida un medicamento, dile que lo escriba como mensaje y el sistema lo buscará automáticamente. No necesitas hacer la búsqueda tú mismo — el bot la hace por ti.",
            },
            {
                "name": "symptom_translation",
                "description": "Translate symptoms to common medications",
                "content": "Si el usuario describe síntomas en vez de un medicamento específico, traduce a medicamentos comunes:\n- Dolor de cabeza / fiebre → Acetaminofén, Ibuprofeno\n- Presión alta → Losartán, Enalapril, Amlodipino\n- Diabetes → Metformina\n- Dolor de estómago / gastritis → Omeprazol\n- Gripe → Antigripales, Acetaminofén\n- Alergia → Loratadina, Cetirizina\nSiempre sugiere consultar con el médico.",
            },
        ],
    },
    {
        "name": "app_support",
        "display_name": "Soporte de App",
        "description": "Ayuda con problemas técnicos de FarmaFacil, explica funcionalidades, guía al usuario con comandos de la app.",
        "system_prompt": _APP_SUPPORT_PROMPT,
        "rules": [
            {
                "name": "patient_explanations",
                "description": "Be patient with non-technical users",
                "content": "Muchos usuarios no son expertos en tecnología. Explica paso a paso, usa lenguaje simple. Si no entienden, reformula con otras palabras. Nunca hagas sentir al usuario que su pregunta es tonta.",
                "sort_order": 1,
            },
            {
                "name": "escalation",
                "description": "Escalate when needed",
                "content": "Si el usuario reporta un problema que no puedes resolver (errores del sistema, pagos, cuenta bloqueada), dile: 'Voy a escalar esto al equipo de soporte. Te contactarán pronto.' No inventes soluciones para problemas técnicos que no conoces.",
                "sort_order": 2,
            },
        ],
        "skills": [],
    },
]


async def seed_ai_roles() -> int:
    """Seed default AI roles, rules, and skills if they don't exist.

    Returns:
        Number of roles inserted.
    """
    inserted = 0
    async with async_session() as session:
        for role_data in DEFAULT_ROLES:
            result = await session.execute(
                select(AiRole).where(AiRole.name == role_data["name"])
            )
            if result.scalar_one_or_none() is not None:
                continue

            role = AiRole(
                name=role_data["name"],
                display_name=role_data["display_name"],
                description=role_data["description"],
                system_prompt=role_data["system_prompt"],
                is_active=True,
            )
            session.add(role)
            await session.flush()  # Get the role.id

            for rule_data in role_data.get("rules", []):
                session.add(AiRoleRule(
                    role_id=role.id,
                    name=rule_data["name"],
                    description=rule_data["description"],
                    content=rule_data["content"],
                    sort_order=rule_data["sort_order"],
                    is_active=True,
                ))

            for skill_data in role_data.get("skills", []):
                session.add(AiRoleSkill(
                    role_id=role.id,
                    name=skill_data["name"],
                    description=skill_data["description"],
                    content=skill_data["content"],
                    is_active=True,
                ))

            inserted += 1

        await session.commit()

    if inserted > 0:
        logger.info("Seeded %d AI roles with rules and skills", inserted)
    return inserted
