import os
import aiohttp
import logging
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from pymongo import MongoClient
from docx import Document
from io import BytesIO
from telegram import Bot
from telegram.error import TelegramError
import subprocess
import asyncio
from PIL import Image
from urllib.parse import urlparse

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
TELEGRAM_CHANNEL_URL = "https://t.me/pib_gujarati"

# Initialize MongoDB client
client = MongoClient(MONGO_CONNECTION_STRING)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

async def download_template(url):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                response.raise_for_status()
                template_bytes = BytesIO(await response.read())
                logging.info("Template downloaded successfully")
                return template_bytes
    except aiohttp.ClientError as e:
        logging.error(f"Error downloading template: {e}")
        return None

async def scrape_content():
    base_url = "https://pib.gov.in"
    main_url = f"{base_url}/allRel.aspx"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(main_url) as response:
                response.raise_for_status()
                soup = BeautifulSoup(await response.text(), "html.parser")

        links = []
        content_area = soup.find("div", class_="content-area")
        if content_area:
            for a_tag in content_area.find_all("a", href=True):
                href = a_tag["href"]
                full_link = f"{base_url}{href}"
                if not collection.find_one({"link": full_link}):
                    links.append(full_link)

        for link in links:
            async with aiohttp.ClientSession() as session:
                async with session.get(link) as response:
                    response.raise_for_status()
                    soup = BeautifulSoup(await response.text(), "html.parser")

            title = soup.find("h2").get_text(strip=True)
            content, content_gujarati = [], []

            for paragraph in soup.find_all("p", style="text-align:justify"):
                text = paragraph.get_text(strip=True)
                content.append(text)
                content_gujarati.append(GoogleTranslator(source="en", target="gu").translate(text))

            if not content:
                for paragraph in soup.find_all("p", style="margin-left:0cm; margin-right:0cm; text-align:justify"):
                    text = paragraph.get_text(strip=True)
                    content.append(text)
                    content_gujarati.append(GoogleTranslator(source="en", target="gu").translate(text))

            img_tags = soup.find_all("img")
            images = []
            for img_tag in img_tags:
                img_src = img_tag.get("src")
                if img_src:
                    images.append({"src": img_src, "alt": img_tag.get("alt", "")})

            collection.insert_one({"link": link})

            if content:
                await generate_and_send_document(title, content, content_gujarati, images, link)
            else:
                await send_small_post_to_telegram(title, content, content_gujarati, link)

    except aiohttp.ClientError as e:
        logging.error(f"Error scraping content: {e}")

async def generate_and_send_document(title, content, content_gujarati, images, source_url):
    template_bytes = await download_template(TEMPLATE_URL)
    if not template_bytes:
        return

    try:
        doc = Document(template_bytes)
        doc.paragraphs[0].text = ""
        doc.add_heading(GoogleTranslator(source="en", target="gu").translate(title), 0)
        doc.paragraphs[1].text = title

        for eng_paragraph, guj_paragraph in zip(content, content_gujarati):
            if eng_paragraph.strip() and guj_paragraph.strip():
                para = doc.add_paragraph()
                para.add_run("• ").bold = True
                para.add_run(guj_paragraph)
                doc.add_paragraph("• " + eng_paragraph)
                doc.add_paragraph()

        for image in images:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(image["src"]) as response:
                        image_bytes = await response.read()
                        image_obj = Image.open(BytesIO(image_bytes))
                        image_obj.thumbnail((image_obj.width * 0.3, image_obj.height * 0.3), resample=Image.BICUBIC)
                        image_file = BytesIO()
                        image_obj.save(image_file, format="PNG")
                        image_file.seek(0)
                        doc.add_picture(image_file, width=docx.shared.Inches(5))
                        doc.add_paragraph(image["alt"])
            except Exception as e:
                logging.error(f"Error processing image: {e}")

        promotional_message = "Don't miss out on the latest updates! Stay informed with our channel."
        doc.add_paragraph(promotional_message)
        doc.add_paragraph(f"Join our Telegram Channel for more updates: {TELEGRAM_CHANNEL_URL}")

        output_docx = "output.docx"
        doc.save(output_docx)

        pdf_file = await convert_docx_to_pdf(output_docx)
        pdf_name = f"{get_truncated_title(title)}.pdf"
        await send_to_telegram(pdf_file, pdf_name, f"🔖 {GoogleTranslator(source='en', target='gu').translate(title)}\n\n🔗 Source: {shorten_url(source_url)}\n\n{promotional_message}\n\n📥 Join our channel to get the latest updates: {TELEGRAM_CHANNEL_URL}")

    except Exception as e:
        logging.error(f"Error processing document: {e}")
    finally:
        cleanup_files(["output.docx", "output.pdf"])

async def send_small_post_to_telegram(title, content, content_gujarati, source_url):
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        message = f"🗞️ {GoogleTranslator(source='en', target='gu').translate(title)}\n\n"
        for eng_paragraph, guj_paragraph in zip(content, content_gujarati):
            if eng_paragraph.strip() and guj_paragraph.strip():
                message += f"• {guj_paragraph}\n• {eng_paragraph}\n\n"

        promotional_message = "Don't miss out on the latest updates! Stay informed with our channel."
        message += f"🔗 Source: {shorten_url(source_url)}\n\n{promotional_message}\n📥 Join our Telegram Channel for more updates: {TELEGRAM_CHANNEL_URL}"
        await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=message)

    except TelegramError as e:
        logging.error(f"Error sending small post to Telegram: {e}")
    except Exception as e:
        logging.error(f"Unexpected error sending small post to Telegram: {e}")

async def convert_docx_to_pdf(input_docx):
    output_pdf = "output.pdf"
    try:
        subprocess.run(["libreoffice", "--convert-to", "pdf", "--outdir", ".", input_docx], check=True)
        return output_pdf
    except subprocess.CalledProcessError as e:
        logging.error(f"Error converting DOCX to PDF: {e}")
        return None

async def send_to_telegram(pdf_path, pdf_name, caption):
    if pdf_path:
        try:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            with open(pdf_path, "rb") as pdf_file:
                await bot.send_document(chat_id=TELEGRAM_CHANNEL_ID, document=pdf_file, caption=caption, filename=pdf_name)
        except TelegramError as e:
            logging.error(f"Error sending PDF to Telegram: {e}")
        except Exception as e:
            logging.error(f"Unexpected error sending PDF to Telegram: {e}")

def cleanup_files(file_list):
    for file_path in file_list:
        if os.path.exists(file_path):
            os.remove(file_path)
            logging.info(f"Removed file: {file_path}")

def get_truncated_title(title, max_length=50):
    if len(title) > max_length:
        return title[:max_length] + "..."
    else:
        return title

def shorten_url(url, max_length=30):
    parsed_url = urlparse(url)
    full_path = parsed_url.path + (f"?{parsed_url.query}" if parsed_url.query else "")
    shortened_url = f"{parsed_url.netloc}{full_path}"
    
    if len(shortened_url) > max_length:
        return shortened_url[:max_length] + "..."
    else:
        return shortened_url


async def main():
    await scrape_content()

if __name__ == "__main__":
    asyncio.run(main())
