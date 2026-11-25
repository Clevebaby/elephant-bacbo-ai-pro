import os
import json
import sqlite3
import asyncio
from io import BytesIO

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from google import genai
from google.genai import types


# CONFIGURAÇÕES
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN não configurado")

if not GEMINI_KEY:
    raise RuntimeError("GEMINI_API_KEY não configurada")

client = genai.Client(api_key=GEMINI_KEY)

DB = "bacbo.db"


# BASE DE DADOS
def init_db():
    with sqlite3.connect(DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                outcome TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


def save_result(user_id, outcome):
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT INTO results (user_id, outcome) VALUES (?, ?)",
            (user_id, outcome)
        )


def get_history(user_id, limit=50):
    with sqlite3.connect(DB) as conn:
        rows = conn.execute(
            """
            SELECT outcome FROM results
            WHERE user_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (user_id, limit)
        ).fetchall()

    return [row[0] for row in rows]


# ANÁLISE ESTATÍSTICA
def statistics(results):
    total = len(results)

    if total == 0:
        return "Ainda não existem resultados registados."

    counts = {
        "BANKER": results.count("BANKER"),
        "PLAYER": results.count("PLAYER"),
        "TIE": results.count("TIE")
    }

    text = f"RESULTADOS ANALISADOS: {total}\n\n"

    for name, count in counts.items():
        percentage = count / total * 100
        text += f"{name}: {count} ({percentage:.1f}%)\n"

    return text


# ANÁLISE DE IMAGEM COM GEMINI
def analyze_image(image_bytes):
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/jpeg"
            ),
            """
            Analisa esta captura de ecrã de Bac Bo.

            Identifica apenas resultados que estejam claramente
            visíveis no histórico de resultados.

            Não inventes resultados.
            Não confundas animações com resultados concluídos.

            Devolve exclusivamente JSON neste formato:

            {
              "results": [
                {
                  "outcome": "BANKER",
                  "confidence": 0.95
                }
              ]
            }

            Valores permitidos:
            BANKER, PLAYER, TIE.

            Se não conseguires identificar resultados,
            devolve {"results": []}.
            """
        ]
    )

    raw = response.text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    data = json.loads(raw)

    return data.get("results", [])


# COMANDO START
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐘 ELEPHANT BAC BO AI PRO\n\n"
        "Bem-vindo ao sistema de análise estatística!\n\n"
        "📸 Envia uma captura do Bac Bo para análise.\n\n"
        "/analisar - Consultar estatísticas\n"
        "/historico - Ver resultados\n"
        "/estatisticas - Ver frequências\n"
        "/limpar - Limpar histórico\n"
        "/ajuda - Ajuda"
    )


# RECEBER IMAGENS
async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    await update.message.reply_text(
        "🔍 A analisar a imagem... Aguarda."
    )

    try:
        photo = update.message.photo[-1]

        telegram_file = await photo.get_file()
        image_data = BytesIO()

        await telegram_file.download_to_memory(image_data)

        image_bytes = image_data.getvalue()

        detected = await asyncio.to_thread(
            analyze_image,
            image_bytes
        )

        valid = []

        for item in detected:
            outcome = str(item.get("outcome", "")).upper()

            if outcome in ["BANKER", "PLAYER", "TIE"]:
                valid.append(outcome)

        if not valid:
            await update.message.reply_text(
                "⚠️ Não consegui identificar resultados "
                "com clareza. Envia uma captura mais nítida."
            )
            return

        for outcome in valid:
            save_result(user_id, outcome)

        history = get_history(user_id, 20)

        await update.message.reply_text(
            "✅ ANÁLISE CONCLUÍDA\n\n"
            f"Resultados identificados: {len(valid)}\n"
            f"Novos resultados: {', '.join(valid)}\n\n"
            + statistics(history)
            + "\n⚠️ Frequências históricas não garantem "
            "o próximo resultado."
        )

    except Exception as error:
        print("ERRO:", error)

        await update.message.reply_text(
            "❌ Não foi possível analisar a imagem.\n"
            "Tenta novamente com uma captura mais nítida."
        )


# CONSULTAR HISTÓRICO
async def historico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = get_history(update.effective_user.id, 20)

    if not results:
        await update.message.reply_text(
            "Ainda não existem resultados."
        )
        return

    await update.message.reply_text(
        "📊 ÚLTIMOS RESULTADOS:\n\n"
        + " → ".join(reversed(results))
    )


# ESTATÍSTICAS
async def estatisticas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = get_history(update.effective_user.id, 50)

    await update.message.reply_text(
        "📊 ESTATÍSTICAS BAC BO\n\n"
        + statistics(results)
    )


# ANÁLISE
async def analisar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = get_history(update.effective_user.id, 20)

    if len(results) < 10:
        await update.message.reply_text(
            f"⏳ Temos apenas {len(results)} resultados.\n"
            "Precisamos de pelo menos 10 para apresentar "
            "uma análise descritiva."
        )
        return

    await update.message.reply_text(
        "📊 ANÁLISE ESTATÍSTICA\n\n"
        + statistics(results)
        + "\n⚪ SEM ENTRADA AUTOMÁTICA\n"
        "As frequências passadas não permitem determinar "
        "com segurança o próximo resultado."
    )


# LIMPAR HISTÓRICO
async def limpar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "DELETE FROM results WHERE user_id = ?",
            (update.effective_user.id,)
        )

    await update.message.reply_text(
        "🗑 Histórico apagado com sucesso."
    )


# AJUDA
async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🐘 ELEPHANT BAC BO AI PRO\n\n"
        "Envia uma captura do Bac Bo para reconhecer "
        "os resultados visíveis.\n\n"
        "/start - Iniciar\n"
        "/analisar - Analisar histórico\n"
        "/historico - Consultar resultados\n"
        "/estatisticas - Ver frequências\n"
        "/limpar - Apagar histórico"
    )


# INICIALIZAÇÃO
def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analisar", analisar))
    app.add_handler(CommandHandler("historico", historico))
    app.add_handler(CommandHandler("estatisticas", estatisticas))
    app.add_handler(CommandHandler("limpar", limpar))
    app.add_handler(CommandHandler("ajuda", ajuda))

    app.add_handler(
        MessageHandler(filters.PHOTO, receive_photo)
    )

    print("ELEPHANT BAC BO AI PRO iniciado!")

    app.run_polling()


if __name__ == "__main__":
    main()
