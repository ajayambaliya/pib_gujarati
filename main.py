import os
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from pymongo import MongoClient
from docx import Document
from docx.shared import Pt
from telegram import Bot
import subprocess
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load and validate environment variables
required_env_vars = ['DB_NAME', 'COLLECTION_NAME', 'MONGO_CONNECTION_STRING', 'TEMPLATE_URL', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHANNEL_ID']
missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    raise EnvironmentError(f"Missing environment variables: {', '.join(missing_vars)}")

DB_NAME = os.getenv('DB_NAME')
COLLECTION_NAME = os.getenv('COLLECTION_NAME')
MONGO_CONNECTION_STRING = os.getenv('MONGO_CONNECTION_STRING')
TEMPLATE_URL = os.getenv('TEMPLATE_URL')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')

# Initialize MongoDB client and collection
client = MongoClient(MONGO_CONNECTION_STRING)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]
collection.create_index("link", unique=True)

# Download the DOCX template from the provided URL
def download_template(url):
    template_path = "template.docx"
    if not os.path.exists(template_path):
        try:
            response = requests.get(url)
            response.raise_for_status()
            with open(template_path, "wb") as file:
                file.write(response.content)
            logging.info("Template downloaded successfully.")
        except requests.exceptions.RequestException as e:
            logging.error(f"Error downloading template: {e}")
            return None
    return template_path

# Scraping function
def scrape_content():
    base_url = "https://pib.gov.in"
    main_url = f"{base_url}/allRel.aspx"

    try:
        response = requests.get(main_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching main page: {e}")
        return

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
        try:
            response = requests.get(link)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
        except requests.exceptions.RequestException as e:
            logging.error(f"Error fetching {link}: {e}")
            continue

        title = soup.find('h2').get_text(strip=True)
        content = []
        content_gujarati = []

        for paragraph in soup.find_all('p'):
            text = paragraph.get_text(strip=True)
            if paragraph.get('style') == "text-align:justify":
                content.append(text)
                try:
                    content_gujarati.append(GoogleTranslator(source='en', target='gu').translate(text))
                except Exception as e:
                    logging.error(f"Translation error for text: {text}. Error: {e}")
                    content_gujarati.append(text)  # Fallback to English if translation fails
            elif paragraph.get('style') == "text-align:center" and paragraph.get_text(strip=True) == "***":
                logging.info("Stopping scrape as end pattern is found.")
                break

        # Add scraped link to MongoDB
        collection.insert_one({"link": link})
        
        # Generate document and send to Telegram
        generate_and_send_document(title, content, content_gujarati)

# Add content to the DOCX template and save it
def generate_and_send_document(title, content, content_gujarati):
    template_path = download_template(TEMPLATE_URL)
    
    if not template_path:
        logging.error("Template not available. Exiting.")
        return
    
    try:
        doc = Document(template_path)
        
        # Formatting title
        title_gujarati = GoogleTranslator(source='en', target='gu').translate(title)
        add_formatted_heading(doc, title_gujarati)
        add_formatted_heading(doc, title)

        # Formatting bullet points
        for eng_paragraph, guj_paragraph in zip(content, content_gujarati):
            add_formatted_bullet(doc, guj_paragraph)
            add_formatted_bullet(doc, eng_paragraph)
            doc.add_paragraph('')  # Add spacing

        # Add promotional message and Telegram channel link
        promotional_message = "Don't miss out on the latest updates! Stay informed with our channel."
        doc.add_paragraph(promotional_message)
        doc.add_paragraph('Join our Telegram Channel for more updates: https://t.me/pib_gujarati')

        output_docx = "output.docx"
        doc.save(output_docx)
        
        # Convert DOCX to PDF and send to Telegram
        pdf_file = convert_docx_to_pdf(output_docx)
        if pdf_file:
            send_to_telegram(pdf_file, f"📄 {title_gujarati}\n\n{promotional_message}")
    
    except Exception as e:
        logging.error(f"Error processing document: {e}")

# Convert DOCX to PDF using LibreOffice
def convert_docx_to_pdf(input_docx):
    output_pdf = input_docx.replace('.docx', '.pdf')
    try:
        subprocess.run(['libreoffice', '--convert-to', 'pdf', '--outdir', '.', input_docx], check=True)
        if not os.path.exists(output_pdf):
            raise FileNotFoundError("PDF conversion failed.")
    except subprocess.CalledProcessError as e:
        logging.error(f"LibreOffice conversion error: {e}")
        return None
    except FileNotFoundError as e:
        logging.error(e)
        return None
    return output_pdf

# Send the PDF to the Telegram channel
def send_to_telegram(pdf_path, caption):
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        with open(pdf_path, 'rb') as pdf_file:
            bot.send_document(chat_id=TELEGRAM_CHANNEL_ID, document=pdf_file, caption=caption)
        logging.info(f"Document sent to Telegram channel {TELEGRAM_CHANNEL_ID}.")
    except Exception as e:
        logging.error(f"Error sending document to Telegram: {e}")

# Function to format and add a heading to the document
def add_formatted_heading(doc, text):
    heading = doc.add_heading(level=1)
    run = heading.add_run(text)
    run.bold = True
    run.font.size = Pt(16)  # Adjust title font size here

# Function to format and add a bullet point to the document
def add_formatted_bullet(doc, text):
    paragraph = doc.add_paragraph(text, style='List Bullet')
    run = paragraph.runs[0]
    run.font.size = Pt(12)  # Adjust bullet point font size here

# Main script
if __name__ == "__main__":
    scrape_content()
