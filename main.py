import os
import schedule
import time
from datetime import datetime
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
import json
import firebase_admin
from firebase_admin import credentials, db
import logging
import re
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
logger.info("Environment variables loaded")

# Initialize Firebase Admin
try:
    cred = credentials.Certificate('./api-tester-pro-175f3-firebase-adminsdk-fbsvc-bf6d4bd54b.json')
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://api-tester-pro-175f3-default-rtdb.firebaseio.com'
    })
    logger.info("Firebase Admin initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Firebase Admin: {str(e)}")
    raise

# SMTP configuration
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")
logger.info(f"SMTP configured for {SMTP_EMAIL} to {RECIPIENT_EMAIL}")

# OpenRouter API key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Function to clean Markdown from response
def clean_markdown_response(response_text):
    """Remove Markdown code fences and other unwanted text from the response."""
    logger.info("Cleaning response from Markdown")
    cleaned = re.sub(r'^```json\s*|\s*```$', '', response_text, flags=re.MULTILINE)
    cleaned = cleaned.strip()
    logger.debug(f"Cleaned response: {cleaned[:100]}...")
    return cleaned

# Function to send email notification
def send_email(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_EMAIL
        msg["To"] = RECIPIENT_EMAIL

        logger.info(f"Sending email to {RECIPIENT_EMAIL} with subject: {subject}")
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        logger.info(f"Email sent successfully to {RECIPIENT_EMAIL}")
    except Exception as e:
        logger.error(f"Error sending email: {str(e)}")

# Function to generate blog using OpenRouter
def generate_blog(topic, main_page_url):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    prompt = f"""
    You are an expert content writer specializing in technical blogs. Write a 700-word blog post about {topic}. 
    The blog should include:
    - A catchy title
    - A 100-word description summarizing the article
    - A meta title (up to 60 characters)
    - A meta description (up to 160 characters)
    - A list of 5-7 relevant keywords
    - A Python code snippet related to API testing
    - A call-to-action at the end linking to {main_page_url} for more API testing resources.
    Format the output as a JSON object with fields: title, description, meta_title, meta_description, keywords, content.
    Ensure the content is engaging, informative, exactly 700 words (excluding metadata), and optimized for SEO.
    Return ONLY the JSON object, without any additional text, Markdown, code fences, or explanations.
    """
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        logger.info("Sending request to OpenRouter API")
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        content = result["choices"][0]["message"]["content"]

        cleaned_response = clean_markdown_response(content)
        return json.loads(cleaned_response)
    except Exception as e:
        logger.error(f"Blog generation failed: {e}")
        return None

# Function to generate, save, and notify
def generate_and_save_blog():
    topic = "API testing techniques and best practices"
    main_page_url = "https://apitester-pro.vercel.app"
    logger.info(f"Starting blog generation for topic: {topic}")

    try:
        blog_data = generate_blog(topic, main_page_url)
        if not blog_data:
            send_email("Blog Generation Error", "Failed to generate blog content.")
            return

        # Validate required fields
        required_fields = ["title", "description", "meta_title", "meta_description", "keywords", "content"]
        if not all(field in blog_data for field in required_fields):
            missing = [f for f in required_fields if f not in blog_data]
            logger.error(f"Missing required fields: {missing}")
            send_email("Blog Generation Error", f"Missing required fields: {missing}")
            return
        logger.info("All required fields present in blog data")

        # Add timestamp
        blog_data["created_at"] = datetime.utcnow().isoformat()
        logger.info(f"Added timestamp: {blog_data['created_at']}")

        # Push to Firebase Realtime Database
        try:
            blog_ref = db.reference("blog_posts")
            result = blog_ref.push(blog_data)
            blog_id = result.key
            firebase_url = f"https://api-tester-pro-175f3-default-rtdb.firebaseio.com/blog_posts/{blog_id}.json"
            logger.info(f"Blog post saved to Firebase with ID: {blog_id}")
        except Exception as e:
            logger.error(f"Failed to save to Firebase: {str(e)}")
            send_email("Blog Generation Error", f"Failed to save to Firebase: {str(e)}")
            return

        # Send email notification
        notification_message = (
            f"New Blog Post!\n"
            f"Title: {blog_data['title']}\n"
            f"Description: {blog_data['description'][:100]}...\n"
            f"View: {firebase_url}"
        )
        send_email("New Blog Post Generated", notification_message)

        logger.info(f"Blog post '{blog_data['title']}' saved to Firebase at {blog_data['created_at']}")

    except Exception as e:
        logger.error(f"General error in blog generation: {str(e)}")
        send_email("Blog Generation Error", f"General error: {str(e)}")

# Schedule the task every 30 hours (fix: not 30 seconds)
# Schedule the task daily at 11:50 AM
schedule.every().day.at("11:50").do(generate_and_save_blog)
logger.info("Scheduled blog generation daily at 11:50 AM")


# Main loop to run the scheduler
def run_scheduler():
    logger.info("Starting blog generation scheduler...")
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    logger.info("Script started")
    generate_and_save_blog()  # Run once immediately
    run_scheduler()
