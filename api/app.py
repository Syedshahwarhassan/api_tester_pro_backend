import os
from flask import Flask, jsonify, request
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
import smtplib
from email.mime.text import MIMEText
import json
import firebase_admin
from firebase_admin import credentials, db
import logging
import re
from datetime import datetime

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

# Initialize Flask app
app = Flask(__name__)

# Initialize Firebase Admin
try:
    cred = credentials.Certificate('../api-tester-pro-175f3-firebase-adminsdk-fbsvc-bf6d4bd54b.json')
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

# Initialize OpenRouter API with LangChain
try:
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        openai_api_key=os.getenv("OPENROUTER_API_KEY"),
        openai_api_base="https://openrouter.ai/api/v1"
    )
    logger.info("OpenRouter API initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize OpenRouter API: {str(e)}")
    raise

# Define the prompt template for blog generation
prompt_template = PromptTemplate(
    input_variables=["topic", "main_page_url"],
    template="""
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
)
logger.info("Prompt template defined")

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

# Function to generate, save, and notify
def generate_and_save_blog(topic, main_page_url):
    logger.info(f"Starting blog generation for topic: {topic}")

    try:
        # Create the chain
        chain = prompt_template | llm
        logger.info("LangChain pipeline created")

        # Generate the blog post
        logger.info("Sending request to OpenRouter API")
        response = chain.invoke({
            "topic": topic,
            "main_page_url": main_page_url
        })
        logger.info(f"Received response from OpenRouter API: {response.content[:100]}...")

        # Clean and parse the JSON response
        try:
            cleaned_response = clean_markdown_response(response.content)
            blog_data = json.loads(cleaned_response)
            logger.info("JSON response parsed successfully")
            logger.debug(f"Blog data: {json.dumps(blog_data, indent=2)}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {str(e)}")
            logger.debug(f"Raw response content: {response.content}")
            send_email("Blog Generation Error", f"Failed to parse JSON response: {str(e)}\nRaw response: {response.content[:200]}...")
            return {"error": "Failed to parse JSON response"}, 500

        # Validate required fields
        required_fields = ["title", "description", "meta_title", "meta_description", "keywords", "content"]
        if not all(field in blog_data for field in required_fields):
            missing = [f for f in required_fields if f not in blog_data]
            logger.error(f"Missing required fields: {missing}")
            send_email("Blog Generation Error", f"Missing required fields: {missing}")
            return {"error": f"Missing required fields: {missing}"}, 500
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
            return {"error": "Failed to save to Firebase"}, 500

        # Send email notification
        notification_message = (
            f"New Blog Post!\n"
            f"Title: {blog_data['title']}\n"
            f"Description: {blog_data['description'][:100]}...\n"
            f"View: {firebase_url}"
        )
        send_email("New Blog Post Generated", notification_message)
        
        logger.info(f"Blog post '{blog_data['title']}' saved to Firebase at {blog_data['created_at']}")
        return {"message": "Blog post generated and saved successfully", "blog_id": blog_id, "firebase_url": firebase_url}, 200

    except Exception as e:
        logger.error(f"General error in blog generation: {str(e)}")
        send_email("Blog Generation Error", f"General error: {str(e)}")
        return {"error": "Internal server error"}, 500

# Flask API endpoint to generate blog
@app.route('/generate-blog', methods=['POST'])
def generate_blog_endpoint():
    if request.content_type != 'application/json':
        logger.error(f"Invalid Content-Type: {request.content_type}")
        return jsonify({"error": "Content-Type must be application/json"}), 415

    try:
        data = request.get_json()
        topic = data.get('topic', "API testing techniques and best practices")
        main_page_url = data.get('main_page_url', "https://apitester-pro.vercel.app")
        
        result, status_code = generate_and_save_blog(topic, main_page_url)
        return jsonify(result), status_code
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        return jsonify({"error": "Invalid JSON payload"}), 400

# Run Flask app
if __name__ == "__main__":
    logger.info("Starting Flask application")
    app.run( port=int(os.getenv("PORT", 5000)))