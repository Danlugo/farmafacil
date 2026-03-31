"""Seed the database with default intent keywords."""

import logging

from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import IntentKeyword

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
