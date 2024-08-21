import os
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from pymongo import MongoClient
from docx import Document
from io import BytesIO
from telegram import Bot
from telegram.ext import ContextTypes
from telegram.utils.helpers import create_deep_linked_url
from telegram.error import TelegramError
import subprocess
import shutil
import logging
import asyncio

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)

# Load environment variables
DB_NAME = os.getenv("DB_NAME")
COLLECTION_NAME = os.getenv("COLLECTION_NAME")
MONGO_CONNECTION_STRING = os.getenv("MONGO_CONNECTION_STRING")
TEMPLATE_URL = "https://docs.google.com/document/d/1GoHxD3FSM8-RhIJu_WGr4NVjVthCzpfx/export?format=docx"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# Initialize MongoDB client
client = MongoClient(MONGO_CONNECTION_STRING)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

async def download_template(url):
    try:
        logging.info(f"Downloading template from: {url}")
        response = await requests.get(url)
        response.raise_for_status()
        logging.info("Template downloaded successfully")
        template_bytes = BytesIO(response.content)
        return template_bytes
    except requests.exceptions.RequestException as e:
        logging.error(f"Error downloading template: {e}")
        return None

async def scrape_content():
    logging.info("Starting scraping process")
    base_url = "https://pib.gov.in"
    main_url = f"{base_url}/allRel.aspx"

    logging.info(f"Fetching content from: {main_url}")
    response = await requests.get(main_url)
    soup = BeautifulSoup(response.text, "html.parser")

    links = []
    content_area = soup.find("div", class_="content-area")

    if content_area:
        logging.info("Found content area")
        for a_tag in content_area.find_all("a", href=True):
            href = a_tag["href"]
            full_link = f"{base_url}{href}"
            if not collection.find_one({"link": full_link}):
                logging.info(f"Adding new link to the list: {full_link}")
                links.append(full_link)
            else:
                logging.info(f"Link already scraped: {full_link}")

    for link in links:
        logging.info(f"Processing link: {link}")
        response = await requests.get(link)
        soup = BeautifulSoup(response.text, "html.parser")

        title = soup.find("h2").get_text(strip=True)
        content = []
        content_gujarati = []

        for paragraph in soup.find_all("p"):
            if paragraph.get("style") == "text-align:justify":
                text = paragraph.get_text(strip=True)
                content.append(text)
                content_gujarati.append(GoogleTranslator(source="en", target="gu").translate(text))
            elif paragraph.get("style") == "text-align:center" and paragraph.get_text(strip=True) == "***":
                logging.info("Stopping scrape as end pattern is found.")
                break

        collection.insert_one({"link": link})
        logging.info(f"Link added to MongoDB: {link}")

        if len(content) > 0:
            logging.info(f"Generating and sending document for link: {link}")
            await generate_and_send_document(title, content, content_gujarati)
        else:
            logging.info(f"Sending small post to Telegram for link: {link}")
            await send_small_post_to_telegram(title, content, content_gujarati)

async def generate_and_send_document(title, content, content_gujarati):
    logging.info("Downloading DOCX template")
    template_bytes = await download_template(TEMPLATE_URL)

    if not template_bytes:
        logging.error("Template not available. Exiting.")
        return

    try:
        logging.info("Creating DOCX document")
        doc = Document(template_bytes)
        doc.add_heading(GoogleTranslator(source="en", target="gu").translate(title), 0)
        doc.add_heading(title, 0)

        for eng_paragraph, guj_paragraph in zip(content, content_gujarati):
            doc.add_paragraph(guj_paragraph)
            doc.add_paragraph(eng_paragraph)
            doc.add_paragraph("")  # Add spacing

        promotional_message = "Don't miss out on the latest updates! Stay informed with our channel."
        doc.add_paragraph(promotional_message)
        doc.add_paragraph("Join our Telegram Channel for more updates: https://t.me/pib_gujarati")

        output_docx = "output.docx"
        logging.info(f"Saving DOCX document to: {output_docx}")
        doc.save(output_docx)

        pdf_file = await convert_docx_to_pdf(output_docx)
        logging.info("Sending PDF to Telegram")
        await send_to_telegram(pdf_file, f"📄 {GoogleTranslator(source='en', target='gu').translate(title)}\n\n{promotional_message}")

    except Exception as e:
        logging.error(f"Error processing document: {e}")

async def send_small_post_to_telegram(title, content, content_gujarati):
    try:
        logging.info("Sending small post to Telegram")
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        message = f"🗞️ {GoogleTranslator(source='en', target='gu').translate(title)}\n\n"
        for eng_paragraph, guj_paragraph in zip(content, content_gujarati):
            message += f"{guj_paragraph}\n{eng_paragraph}\n\n"
        message += "Don't miss out on the latest updates! Stay informed with our channel.\nJoin our Telegram Channel for more updates: https://t.me/pib_gujarati"
        await ContextTypes().bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=message)
    except TelegramError as e:
        logging.error(f"Error sending small post to Telegram: {e}")
    except Exception as e:
        logging.error(f"Unexpected error sending small post to Telegram: {e}")

async def convert_docx_to_pdf(input_docx):
    output_pdf = "output.pdf"
    logging.info(f"Converting DOCX to PDF: {input_docx} -> {output_pdf}")
    subprocess.run(["libreoffice", "--convert-to", "pdf", "--outdir", ".", input_docx])
    return output_pdf

async def send_to_telegram(pdf_path, caption):
    async with ContextTypes().bot.typing():
        try:
            logging.info(f"Sending PDF to Telegram: {pdf_path}")
            with open(pdf_path, "rb") as pdf_file:
                await ContextTypes().bot.send_document(chat_id=TELEGRAM_CHANNEL_ID, document=pdf_file, caption=caption)
        except TelegramError as e:
            logging.error(f"Error sending PDF to Telegram: {e}")
        except Exception as e:
            logging.error(f"Unexpected error sending PDF to Telegram: {e}")

async def main():
    logging.info("Starting script execution")
    await scrape_content()

    if os.path.exists("output.docx"):
        logging.info("Removing output.docx")
        os.remove("output.docx")
    if os.path.exists("output.pdf"):
        logging.info("Removing output.pdf")
        os.remove("output.pdf")
    logging.info("Script execution completed")

if __name__ == "__main__":
    asyncio.run(main())
