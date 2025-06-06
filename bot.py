import os
import requests
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
from dotenv import load_dotenv

load_dotenv()

SLACK_BOT_TOKEN = "."
SLACK_SIGNING_SECRET = "."  # You MUST replace this with actual signing secret
MCP_URL = "http://localhost:5000/api/book"

# Initialize Flask app FIRST
flask_app = Flask(__name__)

# Initialize Slack app with proper error handling
try:
    app = App(
        token=SLACK_BOT_TOKEN,
        signing_secret=SLACK_SIGNING_SECRET,
        process_before_response=True  # Important for HTTP mode
    )
except Exception as e:
    print(f"Failed to initialize Slack app: {e}")
    print("Make sure SLACK_SIGNING_SECRET is set correctly!")
    exit(1)

# Initialize request handler
handler = SlackRequestHandler(app)

# Listen for app mentions
@app.event("app_mention")
def handle_app_mention_events(body, say, logger):
    try:
        logger.info(f"Received app mention: {body}")
        user = body["event"]["user"]
        text = body["event"]["text"]
        
        payload = {
            "message": text
        }
        
        logger.info(f"Sending to MCP: {payload}")
        res = requests.post(MCP_URL, json=payload, timeout=30)
        res.raise_for_status()
        
        reply = res.json().get("reply", "❌ No response from MCP server")
        logger.info(f"MCP response: {reply}")
        
        say(reply)
        
    except requests.exceptions.RequestException as e:
        logger.error(f"MCP request failed: {e}")
        say(f"⚠️ MCP Server Error: {str(e)}")
    except Exception as e:
        logger.error(f"General error: {e}")
        say(f"⚠️ Error: {str(e)}")

# Add a test event handler to debug
@app.event("message")
def handle_message_events(body, logger):
    logger.info(f"Received message event: {body}")

# Flask routes
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    try:
        # Log the incoming request
        print(f"Received POST to /slack/events")
        print(f"Headers: {dict(request.headers)}")
        print(f"Body: {request.get_data()}")
        
        # Let SlackRequestHandler handle everything
        response = handler.handle(request)
        print(f"Handler response: {response}")
        return response
        
    except Exception as e:
        print(f"Error in slack_events: {e}")
        return {"error": str(e)}, 500

@flask_app.route("/health", methods=["GET"])
def health_check():
    return {"status": "healthy", "slack_configured": SLACK_SIGNING_SECRET != "your_signing_secret_here"}, 200

@flask_app.route("/test", methods=["POST"])
def test_post():
    return {"message": "POST is working", "data": request.get_json()}, 200

if __name__ == "__main__":
    print("Starting Slack bot...")
    print(f"Bot token: {SLACK_BOT_TOKEN[:20]}...")
    print(f"Signing secret configured: {SLACK_SIGNING_SECRET != 'your_signing_secret_here'}")
    
    # Run Flask app
    flask_app.run(
        host="0.0.0.0",
        port=3002,
        debug=True
    )