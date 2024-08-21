import os
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from pymongo import MongoClient
from docx import Document
from io import BytesIO
from telegram import Bot
import subprocess
import shutil

# Load environment variables
DB_NAME = os.getenv('DB_NAME')
COLLECTION_NAME = os.getenv('COLLECTION_NAME')
MONGO_CONNECTION_STRING = os.getenv('MONGO_CONNECTION_STRING')
TEMPLATE_URL = "https://docs.google.com/document/d/1GoHxD3FSM8-RhIJu_WGr4NVjVthCzpfx/edit?usp=sharing&ouid=108520131839767724661&rtpof=true&sd=true"
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')

# Initialize MongoDB client
client = MongoClient(MONGO_CONNECTION_STRING)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

# Download the DOCX template from the provided URL
def download_template(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        template_path = "template.docx"
        with open(template_path, "wb") as file:
            file.write(response.content)
        return template_path
    except requests.exceptions.RequestException as e:
        print(f"Error downloading template: {e}")
        return None

# Scraping function
def scrape_content():
    base_url = "https://pib.gov.in"
    main_url = f"{base_url}/allRel.aspx"

    response = requests.get(main_url)
    soup = BeautifulSoup(response.text, 'html.parser')

    # Find all <a> tags within <div class="content-area">
    links = []
    content_area = soup.find('div', class_='content-area')

    if content_area:
        for a_tag in content_area.find_all('a', href=True):
            href = a_tag['href']
            full_link = f"{base_url}{href}"
            # Check if link is already in MongoDB
            if not collection.find_one({"link": full_link}):
                links.append(full_link)

    # Process each unscraped link
    for link in links:
        response = requests.get(link)
        soup = BeautifulSoup(response.text, 'html.parser')

        title = soup.find('h2').get_text(strip=True)
        content = []
        content_gujarati = []

        for paragraph in soup.find_all('p'):
            if paragraph.get('style') == "text-align:justify":
                text = paragraph.get_text(strip=True)
                content.append(text)
                content_gujarati.append(GoogleTranslator(source='en', target='gu').translate(text))
            elif paragraph.get('style') == "text-align:center" and paragraph.get_text(strip=True) == "***":
                print("Stopping scrape as end pattern is found.")
                break

        # Add scraped link to MongoDB
        collection.insert_one({"link": link})
        
        # Generate document and send to Telegram
        if len(content) > 0:
            generate_and_send_document(title, content, content_gujarati)
        else:
            send_small_post_to_telegram(title, content, content_gujarati)

# Add content to the DOCX template and save it
def generate_and_send_document(title, content, content_gujarati):
    template_path = download_template(TEMPLATE_URL)
    
    if not template_path:
        print("Template not available. Exiting.")
        return
    
    try:
        doc = Document(template_path)
        doc.add_heading(GoogleTranslator(source='en', target='gu').translate(title), level=1)
        doc.add_heading(title, level=1)

        for eng_paragraph, guj_paragraph in zip(content, content_gujarati):
            doc.add_paragraph(guj_paragraph, style='List Bullet')
            doc.add_paragraph(eng_paragraph, style='List Bullet')
            doc.add_paragraph('')  # Add spacing

        # Add promotional message and Telegram channel link
        promotional_message = "Don't miss out on the latest updates! Stay informed with our channel."
        doc.add_paragraph(promotional_message)
        doc.add_paragraph('Join our Telegram Channel for more updates: https://t.me/pib_gujarati')

        output_docx = "output.docx"
        doc.save(output_docx)
        
        # Convert DOCX to PDF and send to Telegram
        pdf_file = convert_docx_to_pdf(output_docx)
        send_to_telegram(pdf_file, f"📄 {GoogleTranslator(source='en', target='gu').translate(title)}\n\n{promotional_message}")
    
    except Exception as e:
        print(f"Error processing document: {e}")

# Send small post directly to Telegram
def send_small_post_to_telegram(title, content, content_gujarati):
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        message = f"🗞️ {GoogleTranslator(source='en', target='gu').translate(title)}\n\n"
        for eng_paragraph, guj_paragraph in zip(content, content_gujarati):
            message += f"{guj_paragraph}\n{eng_paragraph}\n\n"
        message += "Don't miss out on the latest updates! Stay informed with our channel.\nJoin our Telegram Channel for more updates: https://t.me/pib_gujarati"
        bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=message)
    except Exception as e:
        print(f"Error sending small post to Telegram: {e}")

# Convert DOCX to PDF using LibreOffice
def convert_docx_to_pdf(input_docx):
    output_pdf = "output.pdf"
    subprocess.run(['libreoffice', '--convert-to', 'pdf', '--outdir', '.', input_docx])
    return output_pdf

# Send the PDF to the Telegram channel
def send_to_telegram(pdf_path, caption):
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    with open(pdf_path, 'rb') as pdf_file:
        bot.send_document(chat_id=TELEGRAM_CHANNEL_ID, document=pdf_file, caption=caption)

# Main script
if __name__ == "__main__":
    scrape_content()
    
    # Clean up temporary files
    if os.path.exists("template.docx"):
        os.remove("template.docx")
    if os.path.exists("output.docx"):
        os.remove("output.docx")
    if os.path.exists("output.pdf"):
        os.remove("output.pdf")
