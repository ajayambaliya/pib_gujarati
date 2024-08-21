import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from docx import Document
from io import BytesIO
from telegram import Bot
import subprocess
import os

# Configuration
google_docs_url = os.getenv('google_docs_url')
telegram_bot_token =  os.getenv('telegram_bot_token')
telegram_channel_id =  os.getenv('telegram_channel_id')

# Download the DOCX template from Google Docs
response = requests.get(google_docs_url)
template_path = "template.docx"
with open(template_path, "wb") as file:
    file.write(response.content)

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
            links.append(full_link)

    # Display the links and ask user which one to scrape
    for i, link in enumerate(links, 1):
        print(f"{i}: {link}")

    choice = int(input("Enter the number of the link you want to scrape: ")) - 1
    scrape_url = links[choice]

    response = requests.get(scrape_url)
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

    return title, content, content_gujarati

# Add content to the DOCX template and save it
def add_content_to_docx(template_path, title, content, content_gujarati, promotional_message):
    doc = Document(template_path)
    doc.add_heading(GoogleTranslator(source='en', target='gu').translate(title), level=1)
    doc.add_heading(title, level=1)

    for eng_paragraph, guj_paragraph in zip(content, content_gujarati):
        doc.add_paragraph(guj_paragraph, style='List Bullet')
        doc.add_paragraph(eng_paragraph, style='List Bullet')
        doc.add_paragraph('')  # Add spacing

    # Add promotional message and Telegram channel link
    doc.add_paragraph(promotional_message)
    doc.add_paragraph('Join our Telegram Channel for more updates: https://t.me/pib_gujarati')

    output_docx = "output.docx"
    doc.save(output_docx)
    return output_docx

# Convert DOCX to PDF using LibreOffice
def convert_docx_to_pdf(input_docx):
    output_pdf = "output.pdf"
    subprocess.run(['libreoffice', '--convert-to', 'pdf', '--outdir', '.', input_docx])
    return output_pdf

# Send the PDF to the Telegram channel
def send_to_telegram(pdf_path, caption, bot_token, channel_id):
    bot = Bot(token=bot_token)
    with open(pdf_path, 'rb') as pdf_file:
        bot.send_document(chat_id=channel_id, document=pdf_file, caption=caption)

# Main script
if __name__ == "__main__":
    title, content, content_gujarati = scrape_content()
    promotional_message = "Don't miss out on the latest updates! Stay informed with our channel."
    
    docx_file = add_content_to_docx(template_path, title, content, content_gujarati, promotional_message)
    pdf_file = convert_docx_to_pdf(docx_file)
    
    caption = f"📄 {GoogleTranslator(source='en', target='gu').translate(title)}\n\n{promotional_message}"
    send_to_telegram(pdf_file, caption, telegram_bot_token, telegram_channel_id)

    # Clean up
    os.remove(docx_file)
    os.remove(pdf_file)
    os.remove(template_path)

    print("Process completed successfully!")
