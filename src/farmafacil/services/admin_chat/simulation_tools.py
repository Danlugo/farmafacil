"""Admin chat tools: batch simulation."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import User

logger = logging.getLogger(__name__)


async def _tool_batch_simulate(args: dict[str, Any]) -> str:
    """Run a batch of questions through the pharmacy AI and save results.

    Reads a file with one question per line, runs each through
    classify_with_ai (pharmacy_advisor role), and saves the results
    to an output file in the admin's user folder.
    """
    from farmafacil.services.ai_responder import classify_with_ai
    from farmafacil.services.file_manager import read_file, write_file

    input_path = args.get("input_file", "")
    output_path = args.get("output_file", "batch_results.txt")

    admin_id = args.get("_admin_user_id")
    phone = None
    user_name = "TestUser"
    if admin_id:
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.id == admin_id)
            )
            user = result.scalar_one_or_none()
            if user:
                phone = user.phone_number
                user_name = user.name or "TestUser"

    if not input_path:
        return "Error: input_file es requerido (path al archivo con preguntas, una por línea)."

    content = read_file(input_path, phone=phone)
    if content.startswith("Error:") or content.startswith("File not found"):
        return content

    questions = [q.strip() for q in content.strip().split("\n") if q.strip()]
    if not questions:
        return "Error: el archivo está vacío o no tiene preguntas."

    lines = [f"Batch simulation — {len(questions)} questions\n{'='*50}\n"]

    for i, question in enumerate(questions, 1):
        try:
            result = await classify_with_ai(
                question, admin_id or 0, user_name,
            )
            lines.append(f"Q{i}: {question}")
            lines.append(f"ACTION: {result.action}")
            if result.drug_query:
                lines.append(f"DRUG: {result.drug_query}")
            if result.text:
                lines.append(f"RESPONSE: {result.text}")
            if result.clarify_question:
                lines.append(f"CLARIFY: {result.clarify_question}")
            lines.append("")
        except Exception as exc:
            lines.append(f"Q{i}: {question}")
            lines.append(f"ERROR: {exc}")
            lines.append("")

    output = "\n".join(lines)
    write_result = write_file(output_path, output, phone=phone)

    return f"Simulación completada: {len(questions)} preguntas. {write_result}"
